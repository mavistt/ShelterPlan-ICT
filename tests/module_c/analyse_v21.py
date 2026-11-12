"""Turn a V2.1 report into the tables the report quotes, so nothing is retyped.

    python -m tests.module_c.analyse_v21 outputs/module_c/v21-run1/report.json

Prints markdown to stdout: per-arm summary, per-group pass rate, the full failure
list grouped by case, and the distribution of failure types.  Nothing is inferred:
every number comes from the `cases` array of the report it is given.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

# (key, label, kind): kind decides formatting. Guessing from the value would
# print "1.0 tool calls" as "100.0%", which is exactly the sort of quiet
# misreading this report exists to avoid.
METRIC_ROWS = [
    ("case_pass_count", "完整任务通过数", "count"),
    ("case_pass_rate", "完整任务通过率", "rate"),
    ("task_routing_rate", "任务路由正确率", "rate"),
    ("param_rate", "参数正确率", "rate"),
    ("inheritance_rate", "多轮继承正确率", "rate"),
    ("numeric_consistency_rate", "数值解释一致率", "rate"),
    ("demo_compliance_rate", "DEMO_ONLY 合规率", "rate"),
    ("clarify_rate", "澄清/拒绝正确率", "rate"),
    ("recovery_rate", "错误恢复成功率", "rate"),
    ("llm_calls_per_case", "LLM 调用/用例", "num"),
    ("tool_calls_per_case", "工具调用/用例", "num"),
    ("b_submissions_per_case", "业务提交/用例", "num"),
    ("tokens_per_case", "tokens/用例", "int"),
    ("case_latency_p50_ms", "端到端 p50 (ms)", "int"),
    ("case_latency_p95_ms", "端到端 p95 (ms)", "int"),
    ("llm_latency_p50_ms", "LLM 延迟 p50 (ms)", "int"),
    ("b_api_success_p50_ms", "B API p50 (ms)", "int"),
    ("retries_total", "重试总数", "count"),
]


def fmt(value, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "rate":
        return f"{value * 100:.1f}%"
    if kind == "int":
        return f"{value:,.0f}"
    if kind == "num":
        return f"{value:.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
    return str(value)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    report = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    arms = list(report["by_arm"])
    out: list[str] = []

    out.append(f"run `{report.get('run_id')}` · {report['design']['case_count']} 条 × "
               f"{len(arms)} 臂 × rep {report['design']['rep_start']}+{report['design']['reps']} · "
               f"model `{report['model']['model']}` · commit "
               f"`{(report.get('code') or {}).get('commit', 'n/a')[:7]}` · "
               f"dirty={((report.get('code') or {}).get('working_tree_dirty'))}")
    out.append("")
    out.append("### 总体指标")
    out.append("")
    out.append("| 指标 | " + " | ".join(f"`{arm}`" for arm in arms) + " |")
    out.append("|---|" + "---:|" * len(arms))
    for key, label, kind in METRIC_ROWS:
        out.append(f"| {label} | " + " | ".join(fmt(report["by_arm"][arm].get(key), kind)
                                                for arm in arms) + " |")

    out.append("")
    out.append("### 分组通过率")
    out.append("")
    out.append("| 组 | " + " | ".join(f"`{arm}`" for arm in arms) + " |")
    out.append("|---|" + "---:|" * len(arms))
    for group, bucket in report.get("capability_matrix", {}).items():
        cells = []
        for arm in arms:
            cell = bucket.get(arm) or {}
            total = cell.get("total")
            passed = cell.get("passed")
            rate = cell.get("rate")
            cells.append(f"{passed}/{total} ({rate * 100:.0f}%)"
                         if total is not None and rate is not None else "n/a")
        out.append(f"| `{group}` | " + " | ".join(cells) + " |")

    # ---- failures ----------------------------------------------------------
    out.append("")
    out.append("### 失败清单（按用例）")
    out.append("")
    out.append("| 用例 | 组 | 臂 | rep | 失败类型 | 首个失败信息 |")
    out.append("|---|---|---|---:|---|---|")
    failures = [row for row in report["cases"] if not row["passed"]]
    for row in sorted(failures, key=lambda r: (r["case_id"], r["arm"])):
        kinds = ",".join(row.get("failure_types") or []) or "-"
        first = (row.get("failures") or [""])[0].replace("|", "/")[:150]
        out.append(f"| `{row['case_id']}` | {row.get('group')} | `{row['arm']}` | "
                   f"{row['rep']} | {kinds} | {first} |")

    out.append("")
    out.append("### 失败类型分布")
    out.append("")
    counter: Counter[str] = Counter()
    per_arm: dict[str, Counter[str]] = defaultdict(Counter)
    for row in failures:
        for kind in row.get("failure_types") or ["unknown"]:
            counter[kind] += 1
            per_arm[row["arm"]][kind] += 1
    out.append("| 失败类型 | 合计 | " + " | ".join(f"`{arm}`" for arm in arms) + " |")
    out.append("|---|" + "---:|" * (len(arms) + 1))
    for kind, count in counter.most_common():
        out.append(f"| `{kind}` | {count} | "
                   + " | ".join(str(per_arm[arm].get(kind, 0)) for arm in arms) + " |")

    out.append("")
    out.append("### 配对比较")
    out.append("")
    for name, pair in report.get("paired", {}).items():
        out.append(f"- **{name}**：{pair['right_wins']} 胜 / {pair['right_losses']} 负 / "
                   f"{pair['ties']} 平（{pair['pairs']} 对）")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
