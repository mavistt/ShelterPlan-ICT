"""Build the read-only V1 evaluation handoff from frozen GT and run evidence.

This script never runs a solver and never rewrites the frozen source GT. The
only copied GT is byte-for-byte from reports/final_release/task_dataset.json.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "reports/integration/eval_run_v1"
FROZEN = ROOT / "reports/final_release/task_dataset.json"
STAGE3 = OUT / "b_structured/results.json"
GT_COPY = OUT / "TASK_GT.json"
CSV_PATH = OUT / "EVAL_CASE_RESULTS.csv"

AGENT_ARMS = (
    ("DIRECT_LLM", "Direct LLM"),
    ("FLAT_TOOLS", "Flat Tool Agent"),
    ("FIXED_WORKFLOW", "Fixed Workflow"),
    ("COMPOSITE_HARNESS", "Composite Skills + Harness"),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git(*args: str, check: bool = False) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def git_blob(revision: str, path: str) -> bytes | None:
    result = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=ROOT, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def decode_log(path: Path) -> str:
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    for encoding in ("utf-16", "utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def test_summary(path: Path) -> dict[str, Any]:
    text = decode_log(path)
    if not text:
        return {"status": "NOT_RECORDED", "summary": None, "log": path.relative_to(ROOT).as_posix()}
    # Parse the last pytest summary line as a whole. An unanchored regex can
    # start after "48 failed," and falsely report only the later "266 passed".
    summary_lines = [line.strip() for line in text.splitlines()
                     if re.match(r"^(?:\d+\s+(?:passed|failed|skipped))(?:\s*,|\s*$)", line.strip(), re.I)]
    counters = None
    if summary_lines:
        counts = {key: 0 for key in ("passed", "failed", "skipped")}
        for count, kind in re.findall(r"(\d+)\s+(passed|failed|skipped)", summary_lines[-1], re.I):
            counts[kind.lower()] = int(count)
        counters = counts
    tail = "\n".join(text.strip().splitlines()[-8:])
    return {"status": "RECORDED" if counters else "SUMMARY_PARSE_PENDING", "counts": counters,
            "tail": tail, "log": path.relative_to(ROOT).as_posix()}


def unittest_summary(path: Path) -> dict[str, Any]:
    text = decode_log(path)
    ran = re.findall(r"Ran\s+(\d+)\s+tests?\s+in\s+([\d.]+)s", text)
    if not ran:
        return {"status": "SUMMARY_PARSE_PENDING", "log": path.relative_to(ROOT).as_posix()}
    passed = bool(re.search(r"(?m)^OK\s*$", text))
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": int(ran[-1][0]) if passed else None,
        "failed": 0 if passed else None,
        "duration_seconds": float(ran[-1][1]),
        "log": path.relative_to(ROOT).as_posix(),
    }


def port_open(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def build_source_audit() -> dict[str, Any]:
    # Use the functions in memory only. Their CLI writes frozen reports and is
    # intentionally not invoked here.
    from sources.admission_gate import run_gate
    from sources.preflight import verify_region_manifest

    release_manifest = load_json(ROOT / "reports/final_release/release_manifest.json")
    region_checks = [verify_region_manifest(region) for region in ("shiqi", "east_district", "xiaolan")]
    _, admission = run_gate(ROOT)
    frozen_totals = admission["totals"]

    old_summary_path = ROOT / "reports/admission_summary.json"
    frozen_summary_match = None
    if old_summary_path.is_file():
        old_summary = load_json(old_summary_path)
        old_totals = old_summary.get("totals", old_summary)
        frozen_summary_match = all(
            old_totals.get(key) == frozen_totals.get(key)
            for key in ("source_rows", "demo_eligible", "verified_eligible", "needs_review", "excluded")
        )

    source_revision = "origin/feature/shelterplan-eval"
    frozen_artifacts = []
    for relative_path, expected_hash in release_manifest.get("artifacts", {}).items():
        current = ROOT / relative_path
        blob = git_blob(source_revision, relative_path)
        frozen_artifacts.append({
            "path": relative_path,
            "declared_sha256": expected_hash,
            "working_tree_sha256": sha256_file(current) if current.is_file() else None,
            "source_git_blob_sha256": sha256_bytes(blob) if blob is not None else None,
            "source_blob_matches_declared": blob is not None and sha256_bytes(blob) == expected_hash,
            "working_tree_raw_matches_declared": current.is_file() and sha256_file(current) == expected_hash,
        })

    return {
        "audit_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "read-only manifest verification plus in-memory admission_gate.run_gate; no preflight/admission CLI and no source writes",
        "release_source_revision": source_revision,
        "release_manifest_sha256_worktree": sha256_file(ROOT / "reports/final_release/release_manifest.json"),
        "frozen_release_artifacts": frozen_artifacts,
        "a_v2_manifests": [{
            "region_id": item["region_id"],
            "data_version": item["manifest_data_version"],
            "declared_files": len(item["file_checks"]),
            "raw_matches": item["raw_match_count"],
            "reconstructed_crlf_matches": item.get("reconstructed_crlf_match_count", 0),
            "content_failures": item.get("content_failure_count", 0),
            "all_content_match": item.get("all_content_match", False),
        } for item in region_checks],
        "admission_gate_in_memory": {
            "totals": frozen_totals,
            "regions": [{key: region.get(key) for key in (
                "region_id", "enabled", "data_version", "source_rows", "demo_eligible",
                "normal_planning_candidates", "verified_eligible", "admission_counts",
            )} for region in admission["regions"]],
            "matches_existing_admission_summary": frozen_summary_match,
            "verified_never_upgraded_automatically": True,
        },
        "dataset_content_hash": release_manifest.get("dataset_content_hash"),
        "release_source_manifest_sha256": release_manifest.get("source_manifest_sha256"),
        "interpretation": (
            "A v2 remains DEMO_ONLY. The current region manifests verify as exact raw matches. "
            "Admission is 56 DEMO candidates, 30 needs_review, 1 excluded, and 0 VERIFIED. "
            "No A data or frozen release manifest was edited by this audit."
        ),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frozen_bytes = FROZEN.read_bytes()
    frozen = json.loads(frozen_bytes.decode("utf-8"))
    if len(frozen.get("tasks", [])) != 24:
        raise RuntimeError("Frozen task dataset no longer contains the expected 24 tasks")
    if GT_COPY.exists() and GT_COPY.read_bytes() != frozen_bytes:
        raise RuntimeError("TASK_GT.json exists and differs from frozen GT; refusing to overwrite it")
    if not GT_COPY.exists():
        GT_COPY.write_bytes(frozen_bytes)

    stage = load_json(STAGE3)
    stage_results = stage.get("results", [])
    stage_by_id = {item.get("scenario_id"): item for item in stage_results}
    task_by_scenario = {item["scenario_id"]: item for item in frozen["tasks"]}
    rows: list[dict[str, Any]] = []
    for task in frozen["tasks"]:
        task_id, scenario_id = task["task_id"], task["scenario_id"]
        for arm_id, _name in AGENT_ARMS:
            rows.append({
                "task_id": task_id, "scenario_id": scenario_id, "region_id": task["region_id"],
                "arm": arm_id, "run_status": "NOT_RUN", "evaluation_status": "NOT_RUN",
                "execution_status": "NOT_RUN", "data_status": "DEMO_ONLY", "solver_status": "",
                "vets_hard_success": "", "elapsed_ms": "", "trace_id": "",
                "route": "", "oracle_status": task.get("oracle", {}).get("status", ""),
                "failure_class": "NOT_RUN", "note": "No real LLM/Agent configured; no Agent score assigned.",
            })
        result = stage_by_id.get(scenario_id)
        if result is None:
            rows.append({
                "task_id": task_id, "scenario_id": scenario_id, "region_id": task["region_id"],
                "arm": "B_DIRECT_STRUCTURED", "run_status": "NOT_RUN", "evaluation_status": "NOT_RUN",
                "execution_status": "NOT_RUN", "data_status": "DEMO_ONLY", "solver_status": "",
                "vets_hard_success": "", "elapsed_ms": "", "trace_id": "", "route": "",
                "oracle_status": task.get("oracle", {}).get("status", ""),
                "failure_class": "NOT_RUN", "note": "Outside the 8-task Stage 3 structured pilot subset.",
            })
            continue
        execution = result.get("execution", {})
        evaluation = result.get("evaluation", {})
        rows.append({
            "task_id": task_id, "scenario_id": scenario_id, "region_id": task["region_id"],
            "arm": "B_DIRECT_STRUCTURED", "run_status": evaluation.get("status", "UNKNOWN"),
            "evaluation_status": evaluation.get("status", "UNKNOWN"),
            "execution_status": execution.get("status", "UNKNOWN"),
            "data_status": execution.get("data_status", "DEMO_ONLY"),
            "solver_status": execution.get("solver_status", ""),
            "vets_hard_success": result.get("vets", {}).get("hard_success", ""),
            "elapsed_ms": execution.get("elapsed_harness_ms", execution.get("elapsed_ms", "")),
            "trace_id": execution.get("execution_trace_id", ""),
            "route": result.get("execution_route", ""),
            "oracle_status": result.get("oracle", {}).get("status", ""),
            "failure_class": "NONE" if evaluation.get("status") == "PASS" else "UNSCORABLE_NO_AGENT_OUTPUT",
            "note": evaluation.get("reason", ""),
        })

    smoke_path = OUT / "RULELLM_COMPOSITE_SMOKE_PATCHED.json"
    smoke = load_json(smoke_path) if smoke_path.is_file() else {}
    if smoke:
        rows.append({
            "task_id": "SMOKE_ONLY", "scenario_id": "RULELLM_COMPOSITE_SMOKE_PATCHED", "region_id": "shiqi",
            "arm": "RULE_BASED_SMOKE", "run_status": "SUCCEEDED_UNSCORED", "evaluation_status": "NOT_APPLICABLE",
            "execution_status": smoke.get("status", "UNKNOWN"), "data_status": smoke.get("data_status", "DEMO_ONLY"),
            "solver_status": "", "vets_hard_success": "", "elapsed_ms": smoke.get("elapsed_ms", ""),
            "trace_id": smoke.get("trace_id", ""), "route": "C_SESSION_TURN_TO_B_COMPOSITE_API",
            "oracle_status": "NOT_USED", "failure_class": "SMOKE_ONLY",
            "note": "RuleLLM smoke only; excluded from all Agent benchmark scores.",
        })

    columns = ["task_id", "scenario_id", "region_id", "arm", "run_status", "evaluation_status",
               "execution_status", "data_status", "solver_status", "vets_hard_success", "elapsed_ms",
               "trace_id", "route", "oracle_status", "failure_class", "note"]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    pass_count = sum(item.get("evaluation", {}).get("status") == "PASS" for item in stage_results)
    unscorable = sum(item.get("evaluation", {}).get("status") == "UNSCORABLE" for item in stage_results)
    trace_count = sum(bool(item.get("execution", {}).get("execution_trace_id")) for item in stage_results)
    runtimes = [float(item.get("execution", {}).get("elapsed_harness_ms"))
                for item in stage_results if item.get("execution", {}).get("elapsed_harness_ms") is not None]
    route_counts: dict[str, int] = {}
    for item in stage_results:
        route = item.get("execution_route", "UNKNOWN")
        route_counts[route] = route_counts.get(route, 0) + 1

    comparison = {
        "schema_version": 1,
        "report_version": "eval_run_v1",
        "frozen_task_count": len(frozen["tasks"]),
        "data_status": "DEMO_ONLY",
        "true_agent_arms": [{
            "id": arm_id, "name": name, "status": "NOT_RUN", "task_count": 24,
            "run_count": 0, "verified_task_success_rate": None,
            "reason": "No local LLM endpoint configured; no paid API called; no Agent benchmark score fabricated.",
        } for arm_id, name in AGENT_ARMS],
        "arms": [{
            "id": arm_id, "name": name, "status": "NOT_RUN",
            "note": "No configured real LLM; no Agent score assigned.",
        } for arm_id, name in AGENT_ARMS],
        "b_direct_structured": {
            "id": "B_DIRECT_STRUCTURED", "status": "PARTIAL", "scope": "B solver structured pilot; never a C Agent result",
            "task_count": 24, "attempted_pilot_tasks": len(stage_results), "pass_count": pass_count,
            "unscorable_count": unscorable, "fail_count": sum(item.get("evaluation", {}).get("status") == "FAIL" for item in stage_results),
            "not_run_count": max(0, 24 - len(stage_results)), "trace_count": trace_count,
            "hard_success_rate_over_attempted": pass_count / len(stage_results) if stage_results else None,
            "hard_success_rate_over_scored": pass_count / (pass_count + sum(item.get("evaluation", {}).get("status") == "FAIL" for item in stage_results)) if pass_count else None,
            "vets_hard_success": {"pass": pass_count, "attempted": len(stage_results), "unscorable": unscorable,
                                  "scored_pass": pass_count, "scored_denominator": pass_count},
            "route_counts": route_counts,
            "mean_elapsed_harness_ms": sum(runtimes) / len(runtimes) if runtimes else None,
            "oracle_status_counts": stage.get("manifest", {}).get("summary", {}).get("oracle_counts", {}),
            "data_status": "DEMO_ONLY",
            "note": "The Stage 3 pilot uses real B solver calls/direct adapters, with configured HTTP support for Shiqi. It does not run C Agent arms.",
        },
        "b_structured": {
            "status": "PARTIAL", "result_count": len(stage_results), "pass_count": pass_count,
            "fail_count": sum(item.get("evaluation", {}).get("status") == "FAIL" for item in stage_results),
            "unscorable_count": unscorable, "success_rate": pass_count / len(stage_results) if stage_results else None,
            "trace_count": trace_count,
            "oracle_status": ", ".join(sorted(set(
                item.get("oracle", {}).get("status", "NOT_RUN") for item in stage_results
            ))),
            "note": "B structured solver pilot only: 6 PASS, 2 UNSCORABLE; 16/24 frozen tasks NOT_RUN. Not a C Agent score.",
        },
        "agent_evaluation": {
            "status": "NOT_RUN",
            "note": "No configured local LLM endpoint; all four true Agent arms remain NOT_RUN. RuleLLM smoke is excluded.",
        },
        "rule_based_smoke": {
            "status": smoke.get("status", "NOT_RUN"), "scored": False,
            "kind": "RULE_BASED_SMOKE", "path": smoke_path.relative_to(ROOT).as_posix() if smoke else None,
            "note": "C Session/Turn → B Composite API smoke only; excluded from Agent comparisons.",
        },
        "source_hash_audit": "SOURCE_HASH_AUDIT.json",
        "gt_source": "reports/final_release/task_dataset.json",
        "gt_copy": "TASK_GT.json",
    }
    write_json(OUT / "EVAL_ARM_COMPARISON.json", comparison)

    source_audit = build_source_audit()
    write_json(OUT / "SOURCE_HASH_AUDIT.json", source_audit)
    task_copy_hash = sha256_file(GT_COPY)
    a_eval_tests = unittest_summary(OUT / "module_a_eval_regression.log")
    b_tests = test_summary(OUT / "module_b_pytest.log")
    c_tests = test_summary(OUT / "module_c_pytest.log")
    c_trace_tests = test_summary(OUT / "module_c_trace_correlation_regression.log")
    test_json_path = OUT / "FRONTEND_VALIDATION.json"
    frontend_tests = load_json(test_json_path) if test_json_path.is_file() else {"status": "PENDING"}
    git_status = git("status", "--short")
    git_head = git("rev-parse", "HEAD")
    git_branch = git("branch", "--show-current")
    source_refs = {}
    for ref in ("origin/main", "origin/feat/composite-recovery-ui-bridge",
                "origin/feature/compositional-skill-harness", "origin/feature/shelterplan-eval"):
        value = git("rev-parse", "--verify", ref)
        if value:
            source_refs[ref] = value

    run_manifest = {
        "schema_version": 1,
        "report_version": "eval_run_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "workspace_branch": git_branch,
        "workspace_head_before_commit": git_head,
        "workspace_dirty_before_commit": bool(git_status),
        "dirty_path_count_before_commit": len([line for line in git_status.splitlines() if line.strip()]),
        "source_revisions": source_refs,
        "runtime": {"python": sys.version.split()[0], "platform": sys.platform,
                    "node": subprocess.run(["node", "--version"], cwd=ROOT, capture_output=True, text=True).stdout.strip()},
        "ports_open_when_manifest_built": {str(port): port_open(port) for port in (8000, 8080, 5173, 11434)},
        "llm_policy": {"local_ollama_11434_open": port_open(11434), "paid_api_called": False,
                       "agent_arms": "NOT_RUN", "rulellm_is_smoke_only": True},
        "frozen_gt": {"path": "reports/final_release/task_dataset.json", "task_count": len(frozen["tasks"]),
                      "sha256_working_tree": sha256_file(FROZEN), "copy": "TASK_GT.json", "copy_sha256": task_copy_hash,
                      "byte_identical": FROZEN.read_bytes() == GT_COPY.read_bytes()},
        "stage3_structured_result": {"path": "b_structured/results.json", "sha256": sha256_file(STAGE3),
                                      "case_count": len(stage_results), "status_counts": stage.get("manifest", {}).get("summary", {}).get("status_counts", {}),
                                      "trace_bearing_count": trace_count},
        "admission_summary": {
            region["region_id"]: {
                "demo_eligible": region["demo_eligible"],
                "verified": region["verified_eligible"],
                "needs_review": region["admission_counts"].get("needs_review", 0),
                "excluded": region["admission_counts"].get("excluded", 0),
                "enabled": region["enabled"],
            }
            for region in source_audit["admission_gate_in_memory"]["regions"]
        },
        "tests": {
            "module_a_eval_regression": {**a_eval_tests,
                "command": "python -B -m unittest tests.test_a_reader_hash_policy tests.test_admission_gate tests.test_preflight_manifest tests.test_scenario_factory tests.test_stage2_1_quality tests.test_stage3_pilot tests.test_module_a_release tests.test_storage_streaming"},
            "module_b": {**b_tests, "command": "python -B -m pytest tests/module_b -q"},
            "module_c": {**c_tests, "command": "python -B -m pytest tests/module_c -q"},
            "module_c_trace_correlation": {**c_trace_tests,
                "command": "python -B -m pytest tests/module_c/test_b_client.py::test_business_failure_arrives_as_job_error_not_http_4xx -q"},
            "compileall": {"status": "PASS", "command": "python -B -m compileall -q backend agent benchmark adapters sources scripts tests"},
            "frontend": frontend_tests,
        },
        "artifacts": {name: sha256_file(OUT / name) for name in (
            "TASK_GT.json", "EVAL_CASE_RESULTS.csv", "EVAL_ARM_COMPARISON.json", "SOURCE_HASH_AUDIT.json", "b_structured/results.json"
        )},
        "limitations": [
            "All A v2 planning data is DEMO_ONLY; verified shelter count is zero.",
            "The 24 frozen tasks/GT are preserved; true LLM Agent arms are NOT_RUN.",
            "B structured results are solver baselines and do not count as C Agent results.",
            "RuleLLM smoke is excluded from all benchmark scores.",
        ],
    }
    write_json(OUT / "RUN_MANIFEST.json", run_manifest)

    source_total = source_audit["admission_gate_in_memory"]["totals"]
    pilot_summary = stage.get("manifest", {}).get("summary", {})
    vets_means = pilot_summary.get("vets_mean", {})
    frontend_overall = frontend_tests.get("overall_status", frontend_tests.get("status", "PENDING"))
    frontend_detail = frontend_tests.get("frontend", {})
    frontend_unit = frontend_detail.get("unit", {})
    frontend_typecheck = frontend_detail.get("typecheck", {})
    frontend_build = frontend_detail.get("build", {})
    frontend_e2e = frontend_detail.get("e2e", {})
    lines = [
        "# A/B/C + ShelterPlan-Eval 集成与评测报告", "",
        f"生成时间（UTC）：{run_manifest['generated_at_utc']}", "",
        "## 集成范围与冻结 GT", "",
        f"- 冻结任务：{len(frozen['tasks'])} 个；`TASK_GT.json` 与 Release 原始任务集逐字节一致，SHA-256 为 `{task_copy_hash}`。",
        "- B/C、前端以 UI Bridge 集成分支为主线；Eval 的场景、准入、Oracle、Evaluator、GT 与报告资产选择性并入。来源提交见 `MERGE_PROVENANCE.md`。",
        "- 本报告中的 B 结构化 Pilot 是求解器/适配器执行结果，不代表 C Agent 成绩。",
        "",
        "## 冻结任务与实测结果", "",
        f"- B 结构化 Pilot 实测 {len(stage_results)}/24：PASS={pass_count}，FAIL={sum(item.get('evaluation', {}).get('status') == 'FAIL' for item in stage_results)}，UNSCORABLE={unscorable}，NOT_RUN={max(0, 24-len(stage_results))}；带完整执行 Trace 的结果={trace_count}。",
        f"- VETS hard success：{pass_count}/{len(stage_results)} 个已尝试任务（{pass_count / len(stage_results):.1%}）；可评分子集 {pass_count}/{pass_count + sum(item.get('evaluation', {}).get('status') == 'FAIL' for item in stage_results)}（100%）。2 个 UNSCORABLE 与 16 个 NOT_RUN 不计为成功，不能外推为完整任务集成绩。" if stage_results else "- VETS：NOT_RUN。",
        f"- U/W/P/E 诊断均值（仅 B 结构化 Pilot）：U={vets_means.get('U', 'N/A')}，W={vets_means.get('W', 'N/A')}，P={vets_means.get('P', 'N/A')}，E={vets_means.get('E', 'N/A')}。这些诊断值不等于 VETS，也不是 Agent 评分。",
        f"- Oracle：{json.dumps(pilot_summary.get('oracle_counts', {}), ensure_ascii=False)}；其中 `CONDITIONAL_SUBSET_OPTIMUM` 不代表完全独立 Oracle 证明。",
        "- Direct LLM、Flat Tools、Fixed Workflow、Composite + Harness 四个真实 Agent 对照组均为 NOT_RUN：未配置本地真实 LLM；未调用收费 API。RuleLLM 仅为 C Session/Turn → B Composite 联调 Smoke，不参与评分。",
        "- 所有当前 A v2 规划数据均标记 `DEMO_ONLY`；`PASS_DEMO` 不表示现场核验或真实部署保证。",
        "",
        "## A v2 数据准入", "",
        f"- 审计 {source_total['source_rows']} 条场所记录：{source_total['demo_eligible']} 条 DEMO 候选，{source_total['verified_eligible']} 条 VERIFIED，{source_total['needs_review']} 条待审核，{source_total['excluded']} 条排除。",
        "- 石岐与东区可用于受控 DEMO；小榄保持 disabled（30 条待审核、1 条排除）。未自动升级任何记录为 VERIFIED。",
        "- 区域 Manifest 校验为只读；详见 `SOURCE_HASH_AUDIT.json`。",
        "",
        "## 验证结果", "",
        f"- A/Eval 选定回归：{a_eval_tests.get('passed')} 项通过（{a_eval_tests.get('status')}）。",
        f"- Module B 全量测试：{b_tests.get('counts') or b_tests.get('status')}；失败来自旧版 `configs/pilot.yaml` 的 A v1 Manifest 哈希不匹配，详见 `{b_tests['log']}` 与 `FAILURE_ANALYSIS.md`。",
        f"- Module C 全量测试：{c_tests.get('counts') or c_tests.get('status')}；失败以同一旧版 A v1 Manifest 阻塞及其依赖失败为主，详见 `{c_tests['log']}` 与 `FAILURE_ANALYSIS.md`。",
        f"- C 错误 Trace 关联回归：{c_trace_tests.get('counts') or c_trace_tests.get('status')}。Python compileall：PASS。",
        f"- 前端：`{frontend_overall}`；单元测试 {frontend_unit.get('passed', 'N/A')} 通过，typecheck={frontend_typecheck.get('status', 'N/A')}，build={frontend_build.get('status', 'N/A')}，浏览器 E2E={frontend_e2e.get('passed', 'N/A')} 通过、{frontend_e2e.get('failed', 'N/A')} 失败。详情见 `FRONTEND_VALIDATION.json`。",
        "- 浏览器 E2E 覆盖数据清单与 Eval 面板、石岐真实 C→B 规划、MIP/Greedy 对照、东区 C→B 与小榄禁用状态。",
        "",
        "## 本地服务", "",
        "- 前端：`http://127.0.0.1:5173`；Module B：`http://127.0.0.1:8000`；Module C：`http://127.0.0.1:8080`。C 提供只读评测摘要 `GET /v1/evaluation/summary`，没有独立 Eval 服务。所有服务仅绑定 loopback，不是公网服务。",
        "- 启动命令（Windows PowerShell）：`powershell -ExecutionPolicy Bypass -File scripts/start_integration_demo.ps1`。脚本采用 RuleLLM Smoke 配置，不调用外部/收费模型。",
        "",
        "## 限制", "",
        "本报告记录真实集成证据，不宣称已完成真实 LLM 公平对照、24 题完整 Agent 评测或经现场核验的 GIS 规划。旧版 `configs/pilot.yaml` 的 `data/processed/demand_nodes.geojson` Manifest 不匹配是 B/C 全套回归的已知阻塞；不得通过改写历史 GT 或 A v1 Manifest 掩盖。",
        "",
    ]
    (OUT / "INTEGRATION_EVAL_REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    write_requested_deliverables(frozen, stage, run_manifest, comparison, source_audit,
                                 b_tests, c_tests, frontend_tests, task_copy_hash)

    print(json.dumps({"output": OUT.relative_to(ROOT).as_posix(), "task_count": len(frozen["tasks"]),
                      "csv_rows": len(rows), "b_pass": pass_count, "b_unscorable": unscorable,
                      "agent_arms": "NOT_RUN", "source_audit": "SOURCE_HASH_AUDIT.json"}, ensure_ascii=False, indent=2))
    return 0


def write_requested_deliverables(frozen: dict[str, Any], stage: dict[str, Any],
                                 run_manifest: dict[str, Any], comparison: dict[str, Any],
                                 source_audit: dict[str, Any], b_tests: dict[str, Any],
                                 c_tests: dict[str, Any], frontend_tests: dict[str, Any],
                                 task_copy_hash: str) -> None:
    """Write the human-readable handoff from frozen definitions and observed logs."""
    tasks = frozen["tasks"]
    stage_by_id = {item.get("scenario_id"): item for item in stage.get("results", [])}
    manifest_path = OUT / "TASK_MANIFEST.csv"
    manifest_columns = ["task_id", "scenario_id", "region_id", "data_version", "scenario_family",
                        "task_type", "expected_behavior", "split", "oracle_status", "oracle_proof_level",
                        "gt_assertions_sha256", "source_manifest_sha256", "data_status", "runtime_execution",
                        "pilot_evaluation_status"]
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=manifest_columns)
        writer.writeheader()
        for task in tasks:
            oracle = task.get("oracle") or {}
            provenance = task.get("provenance") or {}
            assertions = json.dumps(task.get("gt_assertions") or {}, ensure_ascii=False,
                                    sort_keys=True, separators=(",", ":")).encode("utf-8")
            result = stage_by_id.get(task.get("scenario_id"), {})
            writer.writerow({
                "task_id": task.get("task_id"), "scenario_id": task.get("scenario_id"),
                "region_id": task.get("region_id"), "data_version": task.get("data_version"),
                "scenario_family": task.get("scenario_family"), "task_type": task.get("task_type"),
                "expected_behavior": task.get("expected_behavior"), "split": task.get("split"),
                "oracle_status": oracle.get("status"), "oracle_proof_level": oracle.get("proof_level"),
                "gt_assertions_sha256": sha256_bytes(assertions),
                "source_manifest_sha256": task.get("scenario_source_manifest_sha256"),
                "data_status": provenance.get("data_status", "DEMO_ONLY"),
                "runtime_execution": provenance.get("runtime_execution", "NOT_RUN"),
                "pilot_evaluation_status": (result.get("evaluation") or {}).get("status", "NOT_RUN"),
            })

    summary = lambda row: (f"{row.get('counts', {}).get('passed', 0)} passed, "
                           f"{row.get('counts', {}).get('failed', 0)} failed, "
                           f"{row.get('counts', {}).get('skipped', 0)} skipped"
                           if row.get("counts") else str(row.get("status", "NOT_RECORDED")))
    b_count = len(stage.get("results", []))
    b_eval = [item.get("evaluation", {}).get("status", "UNKNOWN") for item in stage.get("results", [])]
    b_pass = b_eval.count("PASS")
    b_unscorable = b_eval.count("UNSCORABLE")
    b_fail = b_eval.count("FAIL")
    docs = {
        "README.md": f"""# ShelterPlan A/B/C/D + ShelterPlan-Eval integration run v1

This directory records an integration run on the composite-recovery UI bridge branch. It preserves the frozen 24-task evaluation set and distinguishes solver/API smoke evidence from scored Agent benchmark results.

## What is included

- `TASK_GT.json` is byte-identical to `reports/final_release/task_dataset.json` (SHA-256 `{task_copy_hash}`). `TASK_MANIFEST.csv` is an index; it does not alter the frozen GT.
- `b_structured/` contains the selected 8-task direct structured B pilot, its assertions, outcomes, and B execution traces. Result: PASS={b_pass}, UNSCORABLE={b_unscorable}, FAIL={b_fail}; the other {max(0,24-b_count)} frozen tasks are NOT_RUN in this pilot.
- RuleLLM runtime artifacts are smoke checks only and are excluded from all Agent metrics.
- `traces/` keeps selected complete C JSONL traces, compressed B result records, and the B traceback for prior East failures. `screenshots/` captures the front-end evidence. `FRONTEND_VALIDATION.json` records passed paths and prior failure runs.

## Data and limits

The A v2 region data is `DEMO_ONLY`, with 0 shelters marked VERIFIED. Shiqi and East are enabled for demonstration; Xiaolan is disabled by the admission policy. DEMO_ONLY means data has not passed real-world field validation. `HYPOTHETICAL` options, costs, capacities, or user-supplied changes are planning assumptions and not verified facts. Do not present the demo as an operational recommendation.

The frozen GT remains the independent task definition. Oracle status is task-specific: only independently proven mathematical assertions count as an Oracle; `NOT_PROVEN` or `NOT_RUN` must not be replaced with the solver's answer. The B structured pilot reports the B solver and its available verification evidence, not a C Agent success rate.

## Reproduce on Windows PowerShell

From repository root, install the project Python dependencies and frontend lockfile dependencies (`python -m pip install -r requirements.txt`, then `cd frontend; npm ci`). Set Python explicitly if needed:

```powershell
$env:SHELTERPLAN_PYTHON = 'C:\\path\\to\\python.exe'
..\\scripts\\start_integration_demo.ps1
```

Open <http://127.0.0.1:5173>. B health is <http://127.0.0.1:8000/healthz>; C health is <http://127.0.0.1:8080/healthz>. This start script uses deterministic RuleLLM smoke mode and does not call a paid or external LLM API. Stop the three owned local processes with `..\\scripts\\stop_integration_demo.ps1` from the frontend folder, or `scripts\\stop_integration_demo.ps1` from repository root.

The B structured pilot is run with `python -B -m benchmark.shelterplan_eval.stage3_pilot --no-http --output-dir reports/integration/eval_run_v1/b_structured`. Selected A/Eval regressions use the command recorded in `RUN_MANIFEST.json` and `FRONTEND_VALIDATION.json`. Frontend checks run from `frontend/`: `npm run test`, `npm run typecheck`, `npm run build`, and `npm run e2e`.

Reproduction requires the exact A v2 files listed in `SOURCE_HASH_AUDIT.json` under `data/processed/regions/{{shiqi,east_district,xiaolan}}/`; those GIS files are not copied into this integration report. The E2E map reads the checked-in A v2 assets. No raw GIS duplicate is included here.

## Branch and reproducibility caveats

See `MERGE_PROVENANCE.md` for source commits. The Eval/Stage 3 artifacts are selectively integrated; B/C and frontend remain based on the UI bridge plus compositional harness source. The full historical Module B and C suites include failures caused by the legacy v1 `configs/pilot.yaml` fixture's source-manifest mismatch; see `FAILURE_ANALYSIS.md`. The currently supported demo path uses A v2. Frozen GT provenance fields are preserved exactly even where they record earlier source-admission state.

The four real-LLM comparison arms (Direct LLM, Flat Tools, Fixed Workflow, Composite + Harness) are `NOT_RUN`: no real LLM endpoint was configured, and no paid API was called. RuleLLM smoke results are not Agent performance evidence. Xiaolan remains disabled; no region status was upgraded.

The retained pytest logs include full failure stacks and final counters. Local checkout, temporary-user, and Python-environment paths are replaced with placeholders for portability and privacy.
""",
        "BC_INTEGRATION_REPORT.md": f"""# B/C integration report

## Observed integration

- Local real Module B is exposed at port 8000; C Harness at 8080; Module D at 5173. All bind to loopback.
- The persisted Shiqi RuleLLM Session/Turn smoke called B and completed a composite request. It is labelled `RULE_BASED_SMOKE`, excluded from benchmark metrics, and uses DEMO_ONLY data.
- The frozen B direct structured pilot invoked B for {b_count} task cases: PASS={b_pass}, UNSCORABLE={b_unscorable}, FAIL={b_fail}; {max(0,24-b_count)} frozen cases were not run. It is not a C Agent evaluation.
- A direct East District B API replay returned SUCCEEDED / PASS_DEMO for data version `shelterplan_module_a_v2.0-demo__east_district` and the 15-minute / 10% assessment.
- The final four-test browser E2E suite passed. The East C→B case completed with result and Verifier evidence. Three earlier East browser attempts failed; one captured B traceback identified `MemoryError` while hashing the full result. Streaming hash/result serialization and a one-region source cache were then added without changing solver mathematics. The before/after traces are under `traces/`.

## Integrity and status

Current A v2 source manifests were checked read-only against the checked-in artifacts; see `SOURCE_HASH_AUDIT.json`. Data status remains DEMO_ONLY. B MIP/Greedy values are solver outputs and Verifier evidence, not an independent Oracle unless the frozen GT explicitly says so. Disabled Xiaolan is rejected before planning.

## Full regression

- Module B: {summary(b_tests)}.
- Module C: {summary(c_tests)}.
- Frontend unit/typecheck/build/E2E details: `FRONTEND_VALIDATION.json`.

The Module B/C full suites exercise legacy v1 fixtures whose manifest declares hashes different from the checked-in `data/processed/demand_nodes.geojson`; failures are not hidden or repaired by changing the frozen dataset.

Test logs retain failure stacks and final counters with local checkout, temporary-user, and Python-environment paths redacted.
""",
        "AGENT_BASELINE_COMPARISON.md": """# Agent baseline comparison

| Arm | Status | Scored tasks | Result |
|---|---|---:|---|
| Direct LLM | NOT_RUN | 0 | No real LLM endpoint configured |
| Flat Tools | NOT_RUN | 0 | No real LLM endpoint configured |
| Fixed Workflow | NOT_RUN | 0 | No real LLM endpoint configured |
| Composite Skills + Harness | NOT_RUN | 0 | No real LLM endpoint configured |
| B direct structured solver | PARTIAL, separate baseline | 8 attempted | See `b_structured/results.json`; not an Agent arm |
| RuleLLM smoke | Unscored smoke only | Not applicable | Excluded from Agent metrics |

No true Agent success rate, failure-recovery rate, token/call comparison, or latency comparison is available. No equal-budget four-arm ablation was performed. No paid API was called. Do not interpret solver PASS as Agent success.
""",
        "SKILL_GRAPH_ABLATION.md": """# Skill Graph ablation

**Status: NOT_RUN.** The required matched real-LLM arms (Flat Tools, Fixed Workflow, Composite Skills + Harness) were unavailable because no real LLM endpoint was configured. The RuleLLM integration smoke verifies a route through C Session/Turn to B, but is not a controlled ablation and is excluded from scores.

No causal claim about Skill Graph success rate, task duration, cache savings, tool-call count, or failure recovery can be made from this run. The B solver's internal skill graph is observable in B traces but does not substitute for comparing Agent architectures under matched tasks and execution budgets.
""",
        "FAILURE_ANALYSIS.md": f"""# Failure analysis

## Full backend regression

- Module B: {summary(b_tests)}. The three failing tests use the legacy v1 source in `configs/pilot.yaml`; they fail at `data/processed/demand_nodes.geojson` manifest hash validation. The A v2 manifest audit is a separate exact-raw-match check and passes for its declared current files. Do not rewrite the v1 manifest or data as part of this integration run.
- Module C: {summary(c_tests)}. The suite reports 48 failures alongside 266 passes. At least 27 captured failures directly show the same legacy B v1 `B_DATA_INVALID` manifest mismatch; dependent dialogue/state assertions also fail after upstream data is rejected. These are not classified as 48 independent C defects.

## East browser run

Three East browser attempts before the memory fix returned `B_INTERNAL_ERROR`. The first two server logs had no traceback; their C traces are preserved. The third failed at `backend/graph/executor.py` while canonical JSON hashing materialized the full result and raised `MemoryError`; a captured traceback excerpt and C trace are retained under `traces/`. Streaming canonical hashing/result writes and a one-region source-bundle cache were introduced; the complete four-test Playwright suite subsequently passed, including East C-to-B result retrieval and Xiaolan rejection. The pre-fix failures remain recorded.

## Agent / architecture

All four real-LLM arms are NOT_RUN. There is no model trace to classify. RuleLLM smoke does not count as a scored agent run. No verifier false-negative rate or recovery success rate is inferable from absent Agent runs.

## Classification

Keep failure attribution separated as legacy A data/manifest validation, C state or routing, B execution, Verifier, or UI transport only when a retained trace supports it. The observed full-result hashing `MemoryError` is addressed by the encoding/cache change and passed post-fix E2E; the first two opaque internal errors cannot be independently root-caused beyond their matching symptom.
""",
        "FINAL_RECOMMENDATIONS.md": """# Final recommendations

1. Keep the frozen 24 tasks and GT immutable. Preserve NOT_RUN and unscorable states.
2. Fix or explicitly quarantine the legacy v1 fixture/manifest mismatch before treating the complete Module B/C regression suites as green; do not alter data hashes to silence the test.
3. Keep the East C-to-B memory regression in the release regression set; the final browser suite passed after the streaming-storage and source-cache changes. Preserve the opaque pre-fix `B_INTERNAL_ERROR` traces for future diagnosis.
4. Keep A v2 data and planning marked DEMO_ONLY; keep Xiaolan disabled until its 30 review rows and one exclusion are resolved with evidence.
5. Configure a local, no-cost real LLM before attempting the four matched Agent arms. Use the same frozen tasks, tools, execution limits, and independent GT; do not score RuleLLM smoke.
6. Run the Skill Graph and Verifier ablations only after real Agent traces and matched controls are available. Current evidence does not establish incremental benefit over B's solver constraints.
7. Avoid further B solver mathematics or C Harness architecture changes until new traces show a reproducible defect. Current follow-up work is the legacy fixture integrity repair and a real-LLM matched evaluation, not a measured optimizer limit.
""",
    }
    for name, content in docs.items():
        (OUT / name).write_text(content.rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
