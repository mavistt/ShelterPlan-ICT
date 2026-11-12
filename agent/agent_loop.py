"""The real-LLM agent loop and its two experimental arms.

`AgentRunner` is provider-agnostic: it works with any object exposing
`chat(messages, tools=...)` (the OpenAI-compatible adapter, or a scripted
LLM for deterministic offline tests).

Two arms share the model, temperature, tool names, cases and data:

* harness  — V0 machinery: session snapshot, guards, projection, retry, gates.
* baseline — raw Module B endpoints, stateless, single attempt, elided output.

The runner NEVER invents planning numbers: every metric is computed from tool
outputs the model actually received.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any

from agent.config import HarnessConfig
from agent.errors import HarnessError
from agent.harness.trace import TraceRecorder
from agent.numeric_audit import check_citations, extract_planning_numbers

FORBIDDEN_DEMO_PHRASES = ("真实验证", "已核验", "现场核实", "可执行建议", "已验证的应急")
LLM_TOOL_CHAR_CAP = 12000


@dataclass
class LLMCall:
    index: int
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    tool_call_names: list[str]
    finish_reason: str | None
    phase: str = "agent"


@dataclass
class ToolExecution:
    name: str
    args: dict[str, Any]
    ok: bool
    error_code: str | None
    payload: dict[str, Any]
    result_id: str | None
    chars: int
    latency_ms: float


@dataclass
class AgentRun:
    mode: str
    case_id: str
    final_text: str | None = None
    llm_calls: list[LLMCall] = field(default_factory=list)
    tool_calls: list[ToolExecution] = field(default_factory=list)
    submitted: list[dict[str, Any]] = field(default_factory=list)
    citations: dict[str, Any] | None = None
    demo_violation: bool = False
    rewrite_used: bool = False
    max_turns_hit: bool = False
    error: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    # Structured arms only (C/D): the intent envelope, the plan and the
    # controller outcome for every turn. Empty for the tool-calling arms.
    intents: list[dict[str, Any]] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)
    controls: list[dict[str, Any]] = field(default_factory=list)
    clarification_fields: list[str] = field(default_factory=list)

    @property
    def prompt_tokens(self) -> int:
        return sum(call.prompt_tokens for call in self.llm_calls)

    @property
    def completion_tokens(self) -> int:
        return sum(call.completion_tokens for call in self.llm_calls)

    @property
    def llm_latency_ms(self) -> float:
        return sum(call.latency_ms for call in self.llm_calls)

    @property
    def tool_latency_ms(self) -> float:
        return sum(call.latency_ms for call in self.tool_calls)

    @property
    def b_api_ms(self) -> float:
        """Time spent inside Module B for the structured arms (0 for tool arms,
        whose latency is already inside the tool calls)."""
        return round(sum(row.get("b_api_ms") or 0.0 for row in self.controls), 1)

    @property
    def successful_tasks(self) -> list[dict[str, Any]]:
        return [row for row in self.submitted if row.get("ok")]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "case_id": self.case_id,
            "final_text": self.final_text,
            "llm_call_count": len(self.llm_calls),
            "llm_latency_ms": round(self.llm_latency_ms, 1),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tool_call_count": len(self.tool_calls),
            "tool_failures": sum(1 for call in self.tool_calls if not call.ok),
            "submitted": self.submitted,
            "citations": self.citations,
            "demo_violation": self.demo_violation,
            "rewrite_used": self.rewrite_used,
            "max_turns_hit": self.max_turns_hit,
            "error": self.error,
            "intents": self.intents,
            "plans": self.plans,
            "controls": self.controls,
            "clarification_fields": self.clarification_fields,
            "b_api_ms": self.b_api_ms,
        }


class AgentRunner:
    """One runner per (mode, case): fresh conversation, bounded LLM turns."""

    def __init__(self, mode: str, registry, system_prompt: str, llm: Any, *,
                 config: HarnessConfig | None = None, trace: TraceRecorder | None = None,
                 service: Any | None = None, session_id: str = "default",
                 max_llm_calls: int = 8) -> None:
        self.mode = mode
        self.registry = registry
        self.system_prompt = system_prompt
        self.llm = llm
        self.config = config or HarnessConfig()
        self.trace = trace
        self.service = service
        self.session_id = session_id
        self.max_llm_calls = max_llm_calls
        self.rewrite_budget = 1
        self.nudge_budget = 2
        self.retry_budget = 2
        self.user_numbers: list[float] = []

    # Parameter-shaped refusals the model can fix by itself. Semantic gaps
    # (missing budget) are deliberately absent: the model must ask the user.
    CORRECTABLE_ERRORS = {
        "TOOL_ARGUMENT_ERROR",
        "GUARD_INVALID_PARAM",
        "GUARD_INVALID_OPTION",
        "B_SCENARIO_INVALID",
        "B_CANDIDATE_INVALID",
        "B_INVALID_SKILL_DAG",
    }

    # ------------------------------------------------------------------ #
    def run_case(self, case_id: str, turns: list[dict[str, Any]]) -> AgentRun:
        run = AgentRun(mode=self.mode, case_id=case_id)
        self.user_numbers = []
        for turn in turns:
            self.user_numbers.extend(extract_planning_numbers(turn.get("user") or ""))
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        run.messages = messages
        call_index = 0

        try:
            for turn in turns:
                messages.append({"role": "user", "content": turn["user"]})
                while True:
                    if call_index >= self.max_llm_calls:
                        run.max_turns_hit = True
                        return self._finish(run)
                    result = self.llm.chat(messages, tools=self.registry.schemas)
                    call_index += 1
                    call = LLMCall(
                        index=call_index, latency_ms=result.latency_ms,
                        prompt_tokens=result.usage.get("prompt_tokens", 0),
                        completion_tokens=result.usage.get("completion_tokens", 0),
                        tool_call_names=[row["name"] for row in result.tool_calls],
                        finish_reason=result.finish_reason,
                    )
                    run.llm_calls.append(call)
                    self._trace("llm_call", case_id=case_id, call_index=call.index,
                                tool_call_names=call.tool_call_names,
                                prompt_tokens=call.prompt_tokens,
                                completion_tokens=call.completion_tokens,
                                latency_ms=round(call.latency_ms, 1),
                                finish_reason=call.finish_reason)

                    if not result.wants_tool:
                        answer = (result.content or "").strip()
                        if not answer and self.nudge_budget > 0:
                            # Thinking models sometimes stop without emitting the
                            # visible answer; ask once for the final reply.
                            self.nudge_budget -= 1
                            messages.append({"role": "user",
                                             "content": "请给出最终回答；若任务尚未完成，请继续调用工具。"})
                            continue
                        run.final_text = answer
                        break

                    messages.append(result.raw_message)
                    for tool_call in result.tool_calls:
                        execution = self._execute(case_id, tool_call, run)
                        run.tool_calls.append(execution)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": json.dumps(execution.payload,
                                                  ensure_ascii=False, default=str)[:LLM_TOOL_CHAR_CAP],
                        })
                    # Harness recovery: parameter-shaped refusals are fed back to
                    # the model with the structured error so it can fix the call.
                    # (Bounded; semantic gaps like a missing budget are NOT
                    # auto-filled - the model must ask the user instead.)
                    if self.mode == "harness" and run.submitted:
                        last_error = run.submitted[-1].get("error_code")
                        if last_error in self.CORRECTABLE_ERRORS and self.retry_budget > 0:
                            self.retry_budget -= 1
                            error_detail = self._last_error_detail(run)
                            messages.append({"role": "user", "content": (
                                f"submit_task 返回错误 {last_error}："
                                f"{error_detail.get('message', '')[:200]}。"
                                f"required_user_action：{error_detail.get('user_action') or '按工具参数示例修正参数后重试'}。"
                                "请修正参数后重新调用 submit_task。")})
                            continue

            if run.final_text:
                self._audit(run, messages)
        except HarnessError as exc:
            run.error = f"{exc.code}: {exc.message[:300]}"
        except Exception as exc:  # noqa: BLE001
            run.error = f"{exc.__class__.__name__}: {exc}"[:300]
        return self._finish(run)

    # ------------------------------------------------------------------ #
    def _execute(self, case_id: str, tool_call: dict[str, Any], run: AgentRun) -> ToolExecution:
        name = tool_call.get("name") or "unknown"
        args = tool_call.get("arguments") or {}
        tick = time.perf_counter()
        payload = self.registry.execute(name, args)
        latency = (time.perf_counter() - tick) * 1000
        ok = bool(payload.get("ok"))
        error_code = (payload.get("error") or {}).get("code")
        result_id = None
        chars = len(json.dumps(payload, ensure_ascii=False, default=str))

        if name == "submit_task" and ok:
            inner = payload.get("result") if isinstance(payload.get("result"), dict) else payload
            if self.mode == "harness":
                task = inner
                result_id = task.get("result_id")
                run.submitted.append({
                    "ok": bool(task.get("ok")),
                    "task_type": task.get("task_type"),
                    "error_code": (task.get("error") or {}).get("code"),
                    "error_message": (task.get("error") or {}).get("message"),
                    "user_action": (task.get("error") or {}).get("user_action"),
                    "result_id": task.get("result_id"),
                    "scenario_id": (task.get("scenario") or {}).get("scenario_id"),
                    "params": (task.get("scenario") or {}).get("params"),
                    "solver_status": task.get("solver_status"),
                    "verification_status": task.get("verification_status"),
                    "metrics": (task.get("summary") or {}).get("metrics"),
                    "summary": task.get("summary"),
                    "summary_bytes": task.get("summary_bytes"),
                })
            else:
                job = inner
                result_id = job.get("job_id")
                run.submitted.append({
                    "ok": None,  # 202 accepted; completion unknown until get_result
                    "task_type": args.get("task_type"),
                    "job_id": job.get("job_id"),
                    "status": job.get("status"),
                })

        if name == "get_result" and ok:
            document = payload.get("result") if isinstance(payload.get("result"), dict) else payload
            result_id = document.get("result_id") or args.get("result_id")
            if self.mode == "harness":
                run.submitted.append({
                    "ok": document.get("verification_status") in {"PASS_DEMO", "PASS_VERIFIED"},
                    "task_type": document.get("task_type"),
                    "result_id": document.get("result_id"),
                    "verification_status": document.get("verification_status"),
                    "data_status": document.get("data_status"),
                    "solver_status": document.get("solver_status"),
                    "metrics": document.get("metrics"),
                    "params": document.get("scenario_params"),
                })
            else:
                run.submitted.append({
                    "ok": document.get("verification_status") in {"PASS_DEMO", "PASS_VERIFIED"},
                    "task_type": None,
                    "result_id": document.get("result_id"),
                    "verification_status": document.get("verification_status"),
                    "data_status": document.get("data_status"),
                    "solver_status": document.get("solver_status"),
                    "metrics": document.get("result_summary"),
                })

        self._trace("tool_call", case_id=case_id, tool_name=name, ok=ok,
                    error_code=error_code, result_id=result_id,
                    latency_ms=round(latency, 1), chars=chars,
                    args_digest=json.dumps(args, ensure_ascii=False, default=str)[:400])
        return ToolExecution(name=name, args=args, ok=ok, error_code=error_code,
                             payload=payload, result_id=result_id, chars=chars,
                             latency_ms=latency)

    # ------------------------------------------------------------------ #
    def _last_error_detail(self, run: AgentRun) -> dict[str, Any]:
        row = run.submitted[-1] if run.submitted else {}
        return {"message": row.get("error_message") or "", "user_action": row.get("user_action")}

    def _audit(self, run: AgentRun, messages: list[dict[str, Any]]) -> None:
        """Harness-only gates: numeric citations and DEMO_ONLY wording."""
        if self.mode != "harness":
            return
        authoritative = self._authoritative_values(run)
        audit = check_citations(run.final_text or "", authoritative)
        run.citations = audit
        text = run.final_text or ""
        demo_violation = any(phrase in text for phrase in FORBIDDEN_DEMO_PHRASES)
        if self._is_demo_only() and run.successful_tasks:
            demo_violation = demo_violation or "DEMO_ONLY" not in text and "未经" not in text
        run.demo_violation = demo_violation

        if (audit["violations"] or demo_violation) and self.rewrite_budget > 0:
            self.rewrite_budget -= 1
            run.rewrite_used = True
            problems = []
            if audit["violations"]:
                problems.append(f"回答中出现了工具结果里不存在的规划数字: {audit['violations']}。"
                                "只能引用工具 JSON 里的数值（允许四舍五入）。")
            if demo_violation:
                problems.append("输出违规：回答中不得出现“已核验”“可执行建议”“真实验证”等表述；"
                                "必须在回答中包含字面字符串 “DEMO_ONLY”，并说明数据未经真实现场核验、"
                                "仅供演示与算法评估。")
            messages.append({"role": "user", "content": "你的上一条回答违反了输出规则："
                             + "；".join(problems) + " 请重新给出最终回答。"})
            result = self.llm.chat(messages, tools=self.registry.schemas)
            if not result.wants_tool:
                run.final_text = (result.content or "").strip()
                audit = check_citations(run.final_text or "", authoritative)
                run.citations = audit
                text = run.final_text or ""
                demo_violation = any(phrase in text for phrase in FORBIDDEN_DEMO_PHRASES)
                if self._is_demo_only() and run.successful_tasks:
                    demo_violation = demo_violation or "DEMO_ONLY" not in text and "未经" not in text
                run.demo_violation = demo_violation

    def _is_demo_only(self) -> bool:
        if self.mode != "harness" or not self.service:
            return True
        try:
            state = self.service.session_state(self.session_id)
            return (state.get("current", {}).get("params", {}) or {}).get("analysis_mode") != "verified"
        except Exception:  # pragma: no cover
            return True

    def _authoritative_values(self, run: AgentRun) -> dict[str, Any]:
        """Everything the model could legitimately cite: the projected summaries
        its own tool calls returned (harness arm)."""
        if self.mode == "harness":
            return {"summaries": [row["summary"] for row in run.submitted if row.get("summary")],
                    "user_given": list(self.user_numbers)}
        return {"user_given": list(self.user_numbers)}

    def _trace(self, event: str, **fields: Any) -> None:
        if self.trace:
            self.trace.log(event, mode=self.mode, **fields)

    def _finish(self, run: AgentRun) -> AgentRun:
        self._trace("task", case_id=run.case_id, final_ok=bool(run.final_text),
                    llm_calls=len(run.llm_calls), tool_calls=len(run.tool_calls),
                    prompt_tokens=run.prompt_tokens, completion_tokens=run.completion_tokens,
                    error=run.error)
        return run
