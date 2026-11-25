"""Arms C and D: structured intent -> deterministic route -> controlled task.

Arm C keeps the V1 answer path (the model writes the prose, the harness gates
it and allows one rewrite). Arm D replaces that path with deterministic
composition from bound values, including the DEMO_ONLY declaration, so no
number ever passes through the model on the way out.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import threading
from typing import Any

from agent.agent_loop import AgentRun, FORBIDDEN_DEMO_PHRASES, LLMCall, ToolExecution
from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.errors import HarnessError
from agent.harness import contract
from agent.harness.results import json_bytes
from agent.harness.trace import TraceRecorder
from agent.intent.extractor import IntentExtractor
from agent.intent.normalizer import apply_ops
from agent.intent.schema import TASK_BY_INTENT
from agent.numeric_audit import check_citations, extract_planning_numbers
from agent.workflow.answer import (
    DEMO_LINE,
    compose_answer,
    compose_clarification,
    compose_error,
    compose_refusal,
)
from agent.workflow.controller import ControlResult, TaskController
from agent.workflow.dialogue import (
    MAX_CLARIFY_ATTEMPTS,
    detect_undo,
    undo_intent,
    TASK_TO_INTENT,
    DialogueState,
    detect_task_switch,
    fill_pending,
    is_cancel,
    resume_intent,
)
from agent.workflow.router import (
    CLARIFY_FIELD_NAMES,
    CLARIFY_QUESTIONS,
    RoutePlan,
    TurnMemory,
    route,
)

ANSWER_SYSTEM_PROMPT = (
    "你是 ShelterPlan 应急避难场所规划助手。你会收到用户问题和一次已完成的规划结果 JSON。\n"
    "规则：\n"
    "1. 只能引用 JSON 中出现的数值（允许四舍五入和百分比换算），禁止自行计算或补充新数字。\n"
    "2. 必须包含字面字符串 DEMO_ONLY，并说明结果未经真实现场核验、仅供演示与算法评估。\n"
    "3. solver_status=FEASIBLE 或 optimality_proven=false 时，不得说“已证明最优”。\n"
    "4. 用中文，3-6 句：先给结论，再给关键指标，最后一句写数据限制。不要输出 JSON。"
)

CLARIFY_SYSTEM_PROMPT = (
    "你是 ShelterPlan 规划助手。当前请求缺少必要参数，不能执行。\n"
    "请用一句中文向用户询问缺失信息；只问被列出的字段，不要编造任何数值，也不要执行任务。"
)

ERROR_SYSTEM_PROMPT = (
    "你是 ShelterPlan 规划助手。一次规划请求没有成功执行。\n"
    "请用 2-3 句中文说明原因和用户下一步该提供什么；不要编造任何规划数字，不要声称已经得到结果。"
)

ANSWER_CHAR_CAP = 6000


@dataclass
class TurnRecord:
    """The application-facing view of one completed turn.

    The Web layer does not parse prose: it reads this record. `status` is the
    five-value vocabulary Module D needs, and every identifier is the one the
    planner actually used.
    """

    status: str  # SUCCEEDED | NEEDS_CLARIFICATION | FAILED | CANCELLED
    text: str
    turn: int = 0
    control: ControlResult | None = None
    plan: RoutePlan | None = None
    intent: dict[str, Any] | None = None
    params: ScenarioParams | None = None
    clarification_fields: list[str] = field(default_factory=list)
    resumed: bool = False
    cancelled: bool = False
    job_id: str | None = None
    bindings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "turn": self.turn,
            "task_type": (self.control.plan.task_type if self.control else None),
            "action": (self.control.plan.action if self.control else None),
            "intent": (self.intent or {}).get("intent"),
            "clarification_fields": list(self.clarification_fields),
            "resumed": self.resumed,
            "cancelled": self.cancelled,
            "job_id": self.job_id,
            "error_code": (self.control.error_code if self.control else None),
            "error_class": (self.control.error_class if self.control else None),
            "answer_chars": len(self.text or ""),
        }


class StructuredRunner:
    """One runner per (arm, case): fresh session, deterministic control flow."""

    def __init__(self, arm: str, service: Any, llm: Any, config: HarnessConfig, *,
                 session_id: str, trace: TraceRecorder | None = None,
                 extractor: IntentExtractor | None = None,
                 max_llm_calls: int = 10, max_answer_rewrites: int = 1,
                 dialogue: DialogueState | None = None,
                 dialogue_enabled: bool = False) -> None:
        if arm not in {"harness_struct", "harness_opt"}:
            raise ValueError(f"unknown structured arm: {arm}")
        self.arm = arm
        self.service = service
        self.llm = llm
        self.config = config
        self.session_id = session_id
        self.trace = trace
        self.extractor = extractor or IntentExtractor(llm, reasoning_effort="none")
        self.max_llm_calls = max_llm_calls
        self.rewrite_budget = max_answer_rewrites
        self.controller = TaskController(service, config, session_id=session_id)
        self.user_numbers: list[float] = []
        self._summaries: list[dict[str, Any]] = []
        self._memory = TurnMemory()
        self._pending: dict[str, Any] = {}
        # Cross-turn clarification is opt-in so the V2 arms stay byte-identical
        # to the run the V2 report cites.
        self.dialogue = dialogue or DialogueState()
        self.dialogue_enabled = dialogue_enabled
        self.last_turn: TurnRecord | None = None
        self.last_run: AgentRun | None = None
        self._turn_no = 0
        self._state_lock = threading.RLock()

    def persist_snapshot(self) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """Thread-safe shallow copy of runner dialogue, memory, and summaries for persistence."""
        with self._state_lock:
            return (
                deepcopy(self.dialogue.to_dict()),
                {
                    "last_task_type": self._memory.last_task_type,
                    "last_options": deepcopy(self._memory.last_options),
                    "last_result_id": self._memory.last_result_id,
                    "last_changes": dict(self._memory.last_changes),
                    "actions": list(self._memory.actions[-20:]),
                },
                deepcopy(self._summaries[-5:]),
            )

    # ------------------------------------------------------------------ #
    def run_case(self, case_id: str, turns: list[dict[str, Any]]) -> AgentRun:
        run = AgentRun(mode=self.arm, case_id=case_id)
        self.user_numbers = []
        self._turn_no = 0
        for turn in turns:
            self.user_numbers.extend(extract_planning_numbers(turn.get("user") or ""))

        for index, turn in enumerate(turns, 1):
            if len(run.llm_calls) >= self.max_llm_calls:
                run.max_turns_hit = True
                break
            text = turn.get("user") or ""
            try:
                answer = self._turn(index, text, run)
            except HarnessError as exc:
                run.error = f"{exc.code}: {exc.message[:300]}"
                answer = compose_error(ControlResult(
                    status="error", plan=RoutePlan(action="task"),
                    error=exc.to_dict(), error_class="internal"))
            except Exception as exc:  # noqa: BLE001 - never lose the turn record
                run.error = f"{exc.__class__.__name__}: {exc}"[:300]
                answer = compose_error(ControlResult(
                    status="error", plan=RoutePlan(action="task"),
                    error={"code": "INTERNAL_ERROR", "message": str(exc)[:300]},
                    error_class="internal"))
            run.final_text = answer
            self._trace("turn", case_id=case_id, turn=index, answer_chars=len(answer),
                        clarification=bool(run.clarification_fields),
                        status=(self.last_turn.status if self.last_turn else None))
        return self._finish(run)

    # ------------------------------------------------------------------ #
    def run_turn(self, text: str, *, turn_id: str | None = None) -> TurnRecord:
        """One user utterance on a long-lived runner (application entry point)."""
        self._turn_no += 1
        case_id = turn_id or f"app-{self._turn_no}"
        run = AgentRun(mode=self.arm, case_id=case_id)
        self.user_numbers = (self.user_numbers + extract_planning_numbers(text))[-100:]
        self._turn(self._turn_no, text, run)
        with self._state_lock:
            self._summaries = self._summaries[-20:]
        self.last_run = run
        assert self.last_turn is not None
        return self.last_turn

    # ------------------------------------------------------------------ #
    def _turn(self, index: int, text: str, run: AgentRun) -> str:
        record = self._dialogue_turn(index, text, run)
        self.last_turn = record
        return record.text

    def _dialogue_turn(self, index: int, text: str, run: AgentRun) -> TurnRecord:
        params = self._params()
        switch_target: str | None = None

        # -1. a request to rewrite a trust constraint is refused before anything
        # else looks at it: it must not be consumed as a slot value, and it must
        # not clear a pending clarification (state survives a refusal).
        refusal = contract.detect(text, self.config)
        if refusal is not None:
            return self._refuse(index, text, refusal, run, params)

        # 0. an expired clarification must not silently resume a stale task.
        if self.dialogue_enabled and self.dialogue.expired():
            with self._state_lock:
                self.dialogue.close("expired")
            plan = RoutePlan(
                action="clarify", clarify_fields=["task"], task_type="assess",
                question="上一轮需要补充的信息已超过有效期，请重新说明要做的分析。",
                reason_codes=["clarification_expired"])
            control = ControlResult(status="clarify", plan=plan)
            answer = compose_clarification(plan, params)
            self._trace("dialogue", case_id=run.case_id, turn=index,
                        dialogue_event="expired", answer_chars=len(answer))
            return TurnRecord(status="NEEDS_CLARIFICATION", text=answer, turn=index,
                              control=control, plan=plan, params=params,
                              clarification_fields=["task"])

        # 1. explicit cancellation of the unfinished task.
        if self.dialogue_enabled and self.dialogue.pending and is_cancel(text):
            with self._state_lock:
                pending = self.dialogue.pending
                self.dialogue.close("cancelled")
            plan = RoutePlan(action="cancel", task_type=pending.task_type,
                             reason_codes=["user_cancelled"])
            control = ControlResult(status="cancelled", plan=plan)
            answer = compose_answer(control, params=params, memory=self._memory)
            self._trace("dialogue", case_id=run.case_id, turn=index,
                        dialogue_event="cancelled", task_type=pending.task_type)
            return TurnRecord(status="CANCELLED", text=answer, turn=index, control=control,
                              plan=plan, params=params, cancelled=True)

        # 1b. an undo is never a slot value: handle it before the pending task,
        # or "撤销上一步" would be consumed as a budget.
        undo = detect_undo(text, has_disabled=bool(params.disabled_shelter_ids))
        if undo is not None:
            if undo.ambiguous:
                plan = RoutePlan(
                    action="clarify", clarify_fields=["undo_target"],
                    question=("要撤销哪一项？请说“撤销上一步”“恢复预算”“恢复需求比例”"
                              "或“不要停用 zs_xxx”。"),
                    reason_codes=["undo_target_unclear"])
                control = ControlResult(status="clarify", plan=plan)
                if self.dialogue_enabled:
                    with self._state_lock:
                        self.dialogue.open(plan, params=params, text=text)
                run.intents.append({"turn": index,
                                    "intent": {"intent": "clarify", "ops": [],
                                               "reason": "undo_target_unclear"},
                                    "mode": "undo", "llm_calls": 0,
                                    "llm_latency_ms": 0.0, "error": None})
                self._trace("dialogue", case_id=run.case_id, turn=index,
                            dialogue_event="undo-ambiguous", evidence=undo.evidence)
                return TurnRecord(status="NEEDS_CLARIFICATION",
                                  text=compose_clarification(plan, params), turn=index,
                                  control=control, plan=plan, params=params,
                                  clarification_fields=["undo_target"])
            with self._state_lock:
                self.dialogue.close("undone")
            self._trace("dialogue", case_id=run.case_id, turn=index,
                        dialogue_event="undo", kind=undo.kind, evidence=undo.evidence)
            intent = undo_intent(undo, params, self.service.session_history(self.session_id),
                                 self._memory.last_task_type, text)
            run.intents.append({"turn": index, "intent": intent.to_dict(),
                                "mode": "undo", "llm_calls": 0,
                                "llm_latency_ms": 0.0, "error": None})
            return self._execute_intent(index, text, intent, run,
                                        intent_payload={"intent": intent.to_dict()})

        # 2. resume the pending task when this reply supplies what was missing.
        if self.dialogue_enabled and self.dialogue.pending is not None:
            pending = self.dialogue.pending
            filled = fill_pending(pending, text)
            switch = detect_task_switch(text, pending.task_type)
            if switch:
                switch_target = switch
                with self._state_lock:
                    self.dialogue.close("switched")
                self._trace("dialogue", case_id=run.case_id, turn=index,
                            dialogue_event="switched", from_task=pending.task_type, to_task=switch)
            elif filled.complete:
                with self._state_lock:
                    self.dialogue.close("resumed")
                intent = resume_intent(pending, filled.values)
                self._trace("dialogue", case_id=run.case_id, turn=index, dialogue_event="resumed",
                            task_type=pending.task_type, filled=sorted(filled.values),
                            attempts=pending.attempts)
                return self._execute_intent(index, text, intent, run, resumed=True)
            elif pending.attempts >= MAX_CLARIFY_ATTEMPTS:
                # Stop asking. Three unproductive replies means the question is
                # not working, and repeating it forever is worse than admitting it.
                with self._state_lock:
                    self.dialogue.close("abandoned")
                self._trace("dialogue", case_id=run.case_id, turn=index,
                            dialogue_event="abandoned", attempts=pending.attempts,
                            missing=list(pending.missing))
                plan = RoutePlan(
                    action="clarify", task_type=pending.task_type, clarify_fields=[],
                    question=(f"我已经问了 {pending.attempts} 次，还是没能确定"
                              f"{'、'.join(CLARIFY_FIELD_NAMES.get(n, n) for n in pending.missing)}。"
                              "这次先放下，请把要做的分析连同参数一起重新说一遍。"),
                    reason_codes=["clarification_abandoned"])
                control = ControlResult(status="clarify", plan=plan)
                answer = compose_clarification(plan, params)
                self._record_control(run, index, control, params)
                return TurnRecord(status="NEEDS_CLARIFICATION", text=answer, turn=index,
                                  control=control, plan=plan, params=params,
                                  clarification_fields=[])
            else:
                # Keep confirmed slots while asking for the remaining ones.
                # Without this, a budget supplied on turn two disappears if
                # the candidate is only supplied on turn three.
                carried_changes = dict(pending.changes)
                for field_name in ("budget_units", "population_demand_ratio",
                                   "max_walk_time_min", "disabled_shelter_ids"):
                    if field_name in filled.values:
                        carried_changes[field_name] = filled.values[field_name]
                carried_parameters = dict(pending.parameters)
                if "compare_values" in filled.values:
                    carried_parameters["compare_spec"] = filled.values["compare_values"]
                if "sensitivity_spec" in filled.values:
                    carried_parameters["sensitivity_spec"] = filled.values["sensitivity_spec"]
                plan = RoutePlan(
                    action="clarify", task_type=pending.task_type,
                    clarify_fields=filled.remaining,
                    question=" ".join(CLARIFY_QUESTIONS.get(name, f"请补充 {name}。")
                                       for name in filled.remaining),
                    reason_codes=["clarification_incomplete"],
                    changes=carried_changes,
                    options=list(filled.values.get("improvement_options") or pending.options),
                    parameters=carried_parameters)
                with self._state_lock:
                    self.dialogue.open(plan, params=params, text=text)
                control = ControlResult(status="clarify", plan=plan)
                run.intents.append({"turn": index, "intent": {"intent": "clarify"},
                                    "mode": "dialogue", "llm_calls": 0,
                                    "llm_latency_ms": 0.0, "error": None})
                answer = compose_clarification(plan, params)
                self._record_control(run, index, control, params)
                self._trace("dialogue", case_id=run.case_id, turn=index,
                            dialogue_event="re-ask", missing=filled.remaining)
                return TurnRecord(status="NEEDS_CLARIFICATION", text=answer, turn=index,
                                  control=control, plan=plan, params=params,
                                  clarification_fields=list(filled.remaining))

        # 3. ordinary extraction path.
        extraction = self.extractor.extract(
            text, params=params, last_task=self._memory.last_task_type,
            last_actions=self._memory.actions)
        self._record_llm_calls(run, extraction.call_stats, phase="intent", tools=[])
        if switch_target:
            # The user named the new task in this very sentence; a weaker
            # extractor must not downgrade it back to the abandoned one.
            forced = TASK_TO_INTENT.get(switch_target)
            if forced and extraction.intent.name != forced:
                extraction.intent.warnings.append(
                    f"task switched by user: {extraction.intent.name} -> {forced}")
                extraction.intent.name = forced
        run.intents.append({"turn": index, **extraction.to_dict()})
        self._trace("intent", case_id=run.case_id, turn=index,
                    intent=extraction.intent.name, extract_mode=extraction.mode,
                    ops=[op.to_dict() for op in extraction.intent.ops],
                    llm_calls=extraction.llm_calls,
                    latency_ms=round(extraction.llm_latency_ms, 1))
        return self._execute_intent(index, text, extraction.intent, run,
                                    intent_payload=extraction.to_dict())

    # ------------------------------------------------------------------ #
    def _execute_intent(self, index: int, text: str, intent: Any, run: AgentRun, *,
                        resumed: bool = False,
                        intent_payload: dict[str, Any] | None = None) -> TurnRecord:
        params = self._params()
        if intent_payload is not None:
            run.intents[-1] = {"turn": index, **intent_payload}
        try:
            # Ground ops in the sentence that produced them. Ops replayed from a
            # pending clarification or an undo were justified by an *earlier*
            # sentence (the budget the user gave two turns ago is not in today's
            # reply), so re-checking them against this turn's text would reject
            # perfectly valid state.
            ground_text = text if getattr(intent, "source", "llm") in {"llm", "rules"} else None
            session_defaults = (
                self.service.session_defaults(self.session_id)
                if hasattr(self.service, "session_defaults")
                else self.service.defaults
            )
            normalized = apply_ops(params, intent.ops,
                                   history=self.service.session_history(self.session_id),
                                   defaults=session_defaults, text=ground_text)
            changes = dict(self._pending)
            changes.update(normalized.changes)
            plan = route(intent, params=normalized.params, changes=changes,
                         memory=self._memory, config=self.config,
                         known_result_ids=self._known_results(),
                         previous_params=params, user_goal=text)
        except HarnessError as exc:
            field = (exc.details or {}).get("field")
            if exc.code == "GUARD_UNCLEAR_OPERATION" and isinstance(field, str):
                # The sentence does not justify the arithmetic. Asking is the only
                # honest option: submitting the model's number would be a guess
                # dressed up as a plan. The session state is left untouched.
                plan = RoutePlan(
                    action="clarify", task_type=TASK_BY_INTENT.get(intent.name),
                    clarify_fields=[field],
                    question=(f"“{text.strip()}” 里我没法确定 {CLARIFY_FIELD_NAMES.get(field, field)}"
                              " 应该变成多少。请直接给目标值或明确的倍数，"
                              "例如“改成 20 分钟”“翻倍”“减半”。"),
                    reason_codes=["operation_unclear"])
                control = ControlResult(status="clarify", plan=plan)
                self._record_control(run, index, control, params)
                if self.dialogue_enabled:
                    with self._state_lock:
                        self.dialogue.open(plan, params=params, text=text)
                else:
                    self._pending = {}
                self._trace("dialogue", case_id=run.case_id, turn=index,
                            dialogue_event="operation_unclear", field=field,
                            reason=(exc.details or {}).get("reason"))
                answer = compose_clarification(plan, params)
                return TurnRecord(status="NEEDS_CLARIFICATION", text=answer, turn=index,
                                  control=control, plan=plan, params=params, resumed=resumed,
                                  clarification_fields=[field], intent=intent.to_dict())
            plan = RoutePlan(action="task", task_type=TASK_BY_INTENT.get(intent.name),
                             clarify_fields=[field] if isinstance(field, str) else [],
                             reason_codes=["pre_route_error"])
            control = ControlResult(status="error", plan=plan, error=exc.to_dict(),
                                    error_class="guard")
            self._record_control(run, index, control, params)
            with self._state_lock:
                self._memory.remember_failure(plan, control.error_code)
            answer = self._respond(text, control, params, run, index)
            return TurnRecord(status="FAILED", text=answer, turn=index, control=control,
                              plan=plan, params=params, resumed=resumed,
                              intent=intent.to_dict())

        self._trace("plan", case_id=run.case_id, turn=index, **plan.to_dict())
        if plan.action == "clarify" and changes:
            # A clarifying plan must carry everything collected so far, or the
            # next turn would resume with a hole where the budget used to be.
            plan.changes = dict(changes)
            # The confirmed part of the request is real even though the task is
            # not: commit it as a candidate scenario so the user can see it and
            # so a later "undo that" has something to return to. No business
            # task is submitted from this branch.
            self._stage_candidate(changes, run=run, index=index)

        if plan.action == "task":
            control = self.controller.run(plan)
            run.tool_calls.append(ToolExecution(
                name="submit_task", args={"task_type": plan.task_type,
                                          "changes": plan.changes},
                ok=control.ok, error_code=control.error_code, payload={},
                result_id=(control.summary or {}).get("result_id"),
                chars=len(str(control.summary or "")), latency_ms=control.b_api_ms))
        else:
            control = ControlResult(status=plan.action, plan=plan)

        self._record_control(run, index, control, params)
        with self._state_lock:
            if control.ok:
                self._memory.remember_success(plan, control.bindings.get("result_id"))
                self._pending.clear()
            elif control.status == "clarify":
                self._memory.remember_failure(plan, "CLARIFY")
                if self.dialogue_enabled:
                    self.dialogue.open(plan, params=params, text=text)
                else:
                    self._pending = dict(plan.changes)
            else:
                self._memory.remember_failure(plan, control.error_code)
        answer = self._respond(text, control, params, run, index)
        status = {"ok": "SUCCEEDED", "clarify": "NEEDS_CLARIFICATION",
                  "refuse": "FAILED"}.get(control.status, "FAILED")
        return TurnRecord(status=status, text=answer, turn=index, control=control, plan=plan,
                          params=params, resumed=resumed,
                          clarification_fields=list(plan.clarify_fields),
                          job_id=(control.outcome or {}).get("job_id"),
                          bindings=dict(control.bindings or {}),
                          intent=intent.to_dict())

    def _refuse(self, index: int, text: str, refusal: Any, run: AgentRun,
                params: ScenarioParams) -> TurnRecord:
        """Answer a trust-altering request without touching the session."""
        message = refusal.message(
            data_status=self.config.expected_data_status,
            disclosure=(DEMO_LINE if self.config.expected_data_status == "DEMO_ONLY" else None))
        plan = RoutePlan(action="refuse", reason_codes=[f"contract:{refusal.fact}"],
                         question=message)
        control = ControlResult(
            status="refuse", plan=plan,
            error={"code": "CONTRACT_VIOLATION",
                   "message": f"{refusal.label}不由对话决定：{refusal.reason}",
                   "user_action": "不需要改声明；如需规划结果，请直接说明分析类型与参数。"},
            error_class="policy")
        answer = compose_refusal(plan, data_status=self.config.expected_data_status)
        run.intents.append({"turn": index,
                            "intent": {"intent": "refuse",
                                       "ops": [],
                                       "reason": f"contract:{refusal.fact}"},
                            "mode": "policy", "llm_calls": 0,
                            "llm_latency_ms": 0.0, "error": None})
        self._trace("policy", case_id=run.case_id, turn=index,
                    fact=refusal.fact, matched=refusal.matched, text_chars=len(text))
        return TurnRecord(status="FAILED", text=answer, turn=index, control=control,
                          plan=plan, params=params, intent={"intent": "refuse"})

    def _stage_candidate(self, changes: dict[str, Any], *, run: Any, index: int) -> None:
        """Commit parameter changes without running a task.

        This is what makes "先确认参数、再决定跑不跑" honest: the snapshot moves,
        Module B is never called, and the next turn starts from what the user
        already agreed to instead of re-deriving it.
        """
        if not changes:
            return
        session = self.service.session_object(self.session_id)
        try:
            state = session.plan_turn(changes)
            session.commit(state)
        except Exception as exc:  # noqa: BLE001 - staging must never fail a turn
            self._trace("dialogue", case_id=run.case_id, turn=index,
                        dialogue_event="stage-failed", error=str(exc)[:160])
            return
        self._trace("dialogue", case_id=run.case_id, turn=index,
                    dialogue_event="staged", changes=sorted(changes))

    # ------------------------------------------------------------------ #
    def _respond(self, text: str, control: ControlResult, params: ScenarioParams,
                 run: AgentRun, index: int) -> str:
        with self._state_lock:
            if control.status == "ok" and control.summary:
                self._summaries.append(control.summary)
            if control.status == "ok":
                # Authoritative values = the bounded window of recent summaries this conversation produced.
                authoritative = {"summaries": list(self._summaries[-10:]),
                                 "user_given": list(self.user_numbers[-100:])}
            else:
                authoritative = {"summaries": [], "user_given": list(self.user_numbers[-100:])}

        if self.arm == "harness_opt":
            answer = compose_answer(control, params=params, memory=self._memory)
            self._audit(run, answer, authoritative, allow_rewrite=False, control=control)
            self._trace("answer", case_id=run.case_id, turn=index, answer_mode="deterministic",
                        chars=len(answer))
            return answer

        answer, calls = self._llm_answer(text, control, params)
        self._record_llm_calls(run, calls, phase="answer", tools=[])
        self._trace("answer", case_id=run.case_id, turn=index, answer_mode="llm",
                    chars=len(answer), rewrite=self.rewrite_budget)
        if control.status != "ok":
            # Nothing was computed, so there is nothing to cite: auditing a
            # clarification against an empty evidence set would only trigger a
            # pointless rewrite.
            run.citations = {"cited_numbers": [], "violations": [], "consistent": True,
                             "authoritative_count": 0}
            run.demo_violation = False
            return answer
        answer = self._audit(run, answer, authoritative, allow_rewrite=True, text=text,
                             control=control, params=params)
        return answer

    def _llm_answer(self, text: str, control: ControlResult,
                    params: ScenarioParams) -> tuple[str, list[dict[str, Any]]]:
        calls: list[dict[str, Any]] = []
        if control.status == "ok":
            system = ANSWER_SYSTEM_PROMPT
            payload = _answer_payload(control)
            user = (f"用户问题：{text}\n执行结果 JSON：{payload}\n"
                    "请给出最终回答。")
            fallback = None
        elif control.status == "clarify":
            system = CLARIFY_SYSTEM_PROMPT
            user = (f"用户问题：{text}\n需要用户补充的字段：{control.plan.clarify_fields}\n"
                    f"已知参数：{params.to_dict()}\n请提问。")
            fallback = compose_clarification(control.plan, params)
        else:
            system = ERROR_SYSTEM_PROMPT
            user = (f"用户问题：{text}\n错误 JSON："
                    f"{_compact(control.error or {'code': control.error_code})}\n请解释。")
            fallback = compose_error(control)

        try:
            result = self.llm.chat([{"role": "system", "content": system},
                                    {"role": "user", "content": user}], max_tokens=1500)
        except HarnessError:
            return fallback or "模型服务不可用，请稍后重试。", calls
        calls.append({
            "latency_ms": round(result.latency_ms, 1),
            "prompt_tokens": int(result.usage.get("prompt_tokens") or 0),
            "completion_tokens": int(result.usage.get("completion_tokens") or 0),
        })
        answer = (result.content or "").strip()
        if not answer:
            answer = fallback or "模型没有返回内容，请重试。"
        return answer, calls

    # ------------------------------------------------------------------ #
    def _audit(self, run: AgentRun, answer: str, authoritative: dict[str, Any], *,
               allow_rewrite: bool, text: str = "", control: ControlResult | None = None,
               params: ScenarioParams | None = None) -> str:
        audit = check_citations(answer, authoritative)
        run.citations = audit
        demo_violation = self._demo_violation(answer, control)
        run.demo_violation = demo_violation
        if not (audit["violations"] or demo_violation):
            return answer

        if not allow_rewrite or self.rewrite_budget <= 0 or control is None or params is None:
            return answer
        self.rewrite_budget -= 1
        run.rewrite_used = True
        problems = []
        if audit["violations"]:
            problems.append(f"回答出现了工具结果中不存在的规划数字 {audit['violations']}，"
                            "只能引用结果 JSON 里的数值（允许四舍五入）。")
        if demo_violation:
            problems.append("必须包含字面字符串 DEMO_ONLY，并说明未经真实现场核验；"
                            "不得出现“已核验”“可执行建议”等表述。")
        try:
            result = self.llm.chat([
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"用户问题：{text}\n执行结果 JSON：{_answer_payload(control)}\n"
                    "你上一条回答违反了输出规则：" + "；".join(problems) +
                    " 请重新给出最终回答。")},
            ], max_tokens=1500)
        except HarnessError:
            return answer
        self._record_llm_calls(run, [{
            "latency_ms": round(result.latency_ms, 1),
            "prompt_tokens": int(result.usage.get("prompt_tokens") or 0),
            "completion_tokens": int(result.usage.get("completion_tokens") or 0),
        }], phase="rewrite", tools=[])
        rewritten = (result.content or "").strip()
        if not rewritten:
            return answer
        run.citations = check_citations(rewritten, authoritative)
        run.demo_violation = self._demo_violation(rewritten, control)
        return rewritten

    def _demo_violation(self, answer: str, control: ControlResult | None) -> bool:
        if any(phrase in answer for phrase in FORBIDDEN_DEMO_PHRASES):
            return True
        if control is not None and control.status == "ok":
            data_status = (control.bindings or {}).get("data_status")
            if data_status == "DEMO_ONLY":
                return "DEMO_ONLY" not in answer and "未经" not in answer
        return False

    # ------------------------------------------------------------------ #
    def _record_llm_calls(self, run: AgentRun, stats: list[dict[str, Any]], *,
                          phase: str, tools: list[str]) -> None:
        for row in stats:
            run.llm_calls.append(LLMCall(
                index=len(run.llm_calls) + 1,
                latency_ms=float(row.get("latency_ms") or 0.0),
                prompt_tokens=int(row.get("prompt_tokens") or 0),
                completion_tokens=int(row.get("completion_tokens") or 0),
                tool_call_names=list(tools), finish_reason=None, phase=phase))

    def _record_control(self, run: AgentRun, index: int, control: ControlResult,
                        params: ScenarioParams) -> None:
        run.controls.append({"turn": index, **control.to_dict()})
        if control.plan.clarify_fields:
            for field in control.plan.clarify_fields:
                if field not in run.clarification_fields:
                    run.clarification_fields.append(field)
        if control.status != "ok":
            return
        bindings = control.bindings
        run.submitted.append({
            "ok": True,
            "task_type": bindings.get("task_type") or control.plan.task_type,
            "error_code": None,
            "result_id": bindings.get("result_id"),
            "scenario_id": bindings.get("scenario_id"),
            "params": bindings.get("scenario_params") or params.to_dict(),
            "solver_status": bindings.get("solver_status"),
            "verification_status": bindings.get("verification_status"),
            "metrics": (control.summary or {}).get("metrics"),
            "summary": control.summary,
            "summary_bytes": json_bytes(control.summary) if control.summary else 0,
        })

    # ------------------------------------------------------------------ #
    def _params(self) -> ScenarioParams:
        state = self.service.session_state(self.session_id)
        payload = (state.get("current") or {}).get("params") or {}
        return ScenarioParams(**payload)

    def _known_results(self) -> list[str]:
        return list(self.service.session_state(self.session_id).get("result_ids") or [])

    def _trace(self, event: str, **fields: Any) -> None:
        if self.trace:
            self.trace.log(event, mode=self.arm, **fields)

    def _finish(self, run: AgentRun) -> AgentRun:
        self._trace("task", case_id=run.case_id, final_ok=bool(run.final_text),
                    llm_calls=len(run.llm_calls), tool_calls=len(run.tool_calls),
                    prompt_tokens=run.prompt_tokens, completion_tokens=run.completion_tokens,
                    error=run.error)
        return run


def _answer_payload(control: ControlResult) -> str:
    summary = control.summary or {}
    subset = {
        "task_type": summary.get("task_type") or control.plan.task_type,
        "result_id": summary.get("result_id"),
        "scenario_id": summary.get("scenario_id"),
        "data_status": summary.get("data_status"),
        "solver_status": summary.get("solver_status"),
        "optimality_proven": summary.get("optimality_proven"),
        "verification_status": summary.get("verification_status"),
        "metrics": summary.get("metrics"),
        "scenario_params": summary.get("scenario_params"),
        "detail": summary.get("detail"),
        "warnings": summary.get("warnings"),
    }
    text = _compact(subset)
    return text[:ANSWER_CHAR_CAP]


def _compact(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
