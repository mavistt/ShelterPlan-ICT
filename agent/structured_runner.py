"""Arms C and D: structured intent -> deterministic route -> controlled task.

Arm C keeps the V1 answer path (the model writes the prose, the harness gates
it and allows one rewrite). Arm D replaces that path with deterministic
composition from bound values, including the DEMO_ONLY declaration, so no
number ever passes through the model on the way out.
"""
from __future__ import annotations

from typing import Any

from agent.agent_loop import AgentRun, FORBIDDEN_DEMO_PHRASES, LLMCall, ToolExecution
from agent.config import HarnessConfig
from agent.contracts import ScenarioParams
from agent.errors import HarnessError
from agent.harness.results import json_bytes
from agent.harness.trace import TraceRecorder
from agent.intent.extractor import IntentExtractor
from agent.intent.normalizer import apply_ops
from agent.intent.schema import TASK_BY_INTENT
from agent.numeric_audit import check_citations, extract_planning_numbers
from agent.workflow.answer import compose_answer, compose_clarification, compose_error
from agent.workflow.controller import ControlResult, TaskController
from agent.workflow.router import RoutePlan, TurnMemory, route

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


class StructuredRunner:
    """One runner per (arm, case): fresh session, deterministic control flow."""

    def __init__(self, arm: str, service: Any, llm: Any, config: HarnessConfig, *,
                 session_id: str, trace: TraceRecorder | None = None,
                 extractor: IntentExtractor | None = None,
                 max_llm_calls: int = 10, max_answer_rewrites: int = 1) -> None:
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

    # ------------------------------------------------------------------ #
    def run_case(self, case_id: str, turns: list[dict[str, Any]]) -> AgentRun:
        run = AgentRun(mode=self.arm, case_id=case_id)
        self.user_numbers = []
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
                        clarification=bool(run.clarification_fields))
        return self._finish(run)

    # ------------------------------------------------------------------ #
    def _turn(self, index: int, text: str, run: AgentRun) -> str:
        params = self._params()
        extraction = self.extractor.extract(
            text, params=params, last_task=self._memory.last_task_type,
            last_actions=self._memory.actions)
        self._record_llm_calls(run, extraction.call_stats, phase="intent",
                               tools=[])
        run.intents.append({"turn": index, **extraction.to_dict()})
        self._trace("intent", case_id=run.case_id, turn=index,
                    intent=extraction.intent.name, extract_mode=extraction.mode,
                    ops=[op.to_dict() for op in extraction.intent.ops],
                    llm_calls=extraction.llm_calls,
                    latency_ms=round(extraction.llm_latency_ms, 1))

        intent = extraction.intent
        try:
            normalized = apply_ops(params, intent.ops,
                                   history=self.service.session_history(self.session_id),
                                   defaults=self.service.defaults)
            changes = dict(self._pending)
            changes.update(normalized.changes)
            plan = route(intent, params=normalized.params, changes=changes,
                         memory=self._memory, config=self.config,
                         known_result_ids=self._known_results())
        except HarnessError as exc:
            field = (exc.details or {}).get("field")
            plan = RoutePlan(action="task", task_type=TASK_BY_INTENT.get(intent.name),
                             clarify_fields=[field] if isinstance(field, str) else [],
                             reason_codes=["pre_route_error"])
            control = ControlResult(status="error", plan=plan, error=exc.to_dict(),
                                    error_class="guard")
            self._record_control(run, index, control, params)
            self._memory.remember_failure(plan, control.error_code)
            return self._respond(text, control, params, run, index)

        self._trace("plan", case_id=run.case_id, turn=index, **plan.to_dict())

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
        if control.ok:
            self._memory.remember_success(plan, control.bindings.get("result_id"))
            self._pending.clear()
        elif control.status == "clarify":
            self._memory.remember_failure(plan, "CLARIFY")
            self._pending = dict(plan.changes)
        else:
            self._memory.remember_failure(plan, control.error_code)
        return self._respond(text, control, params, run, index)

    # ------------------------------------------------------------------ #
    def _respond(self, text: str, control: ControlResult, params: ScenarioParams,
                 run: AgentRun, index: int) -> str:
        if control.status == "ok" and control.summary:
            self._summaries.append(control.summary)
        if control.status == "ok":
            # Authoritative values = the summaries this conversation produced.
            authoritative = {"summaries": list(self._summaries),
                             "user_given": list(self.user_numbers)}
        else:
            authoritative = {"summaries": [], "user_given": list(self.user_numbers)}

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
