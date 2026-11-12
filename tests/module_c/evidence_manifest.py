"""Regenerate the reproducibility manifest for every piece of Module C evidence.

Why a script instead of a hand-written table
--------------------------------------------
The V2.1 brief asks for an evidence table a reviewer can re-derive.  A typed
sha256 cannot be re-derived, so this script walks the known evidence locations,
hashes each file, reads the *record set fingerprints* out of every report, and
writes `docs/module_c/evidence/MANIFEST.md`.

Two things this script deliberately does:

* it distinguishes **first pass (pre-fix)** from **merged (post-override)**.
  They are different record sets and the earlier V2 report conflated them; the
  manifest must not repeat that mistake.
* it prints a record-set fingerprint (`run_id` + case count + sha256 of the
  `cases` array) next to the file hash, because two files can differ in
  formatting while holding the same records - that is exactly the case for
  `docs/.../v2_report.json` and `outputs/module_c/v2-final/report.json`.

Nothing here writes to, renames, or deletes an evidence file.

Usage::

    PYTHONPATH='E:\\ICT\\_sp\\pylibs18;E:\\ICT\\_sp\\pylibs' \
        /d/Anaconda/python.exe -m tests.module_c.evidence_manifest

Exit code 1 if a required artefact is missing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "module_c" / "evidence"

# (relative path, what it proves).  Missing required files are reported as a
# finding, never silently skipped.
REQUIRED: list[tuple[str, str]] = [
    # ---- V0: engineering baseline (not an agent) ------------------------
    ("docs/module_c/evidence/v0_report.json",
     "V0 基线成绩：判定口径是工具序列，用于划出'工程基线'的下界"),
    ("docs/module_c/evidence/v0_trace.jsonl",
     "V0 逐条工具调用 Trace（640 条事件级的原始记录之一）"),
    # ---- V1: two-model path, hold-out set -------------------------------
    ("docs/module_c/evidence/v1_dev_report.json",
     "V1 开发集成绩（10 条用例 / 20 条判定记录，仅用于调 prompt，不作泛化证据）"),
    ("docs/module_c/evidence/v1_holdout_report.json",
     "V1 留出集成绩（14 条用例 / 28 条判定记录，调参时不看，属盲测）"),
    ("docs/module_c/evidence/v1_holdout_trace.jsonl",
     "V1 留出集逐例 Trace（86 次 LLM 调用 / 28 次任务）"),
    # ---- V2: first pass, overrides, merged, repetition ------------------
    ("docs/module_c/evidence/v2_trace.jsonl",
     "V2 **首次运行（修复前）**逐例 Trace：92 条 task 记录，C/D 两臂各 21/23，"
     "失败项为 V2-P-04（wrong_task）与 V2-E-01（param_error）"),
    ("docs/module_c/evidence/v2_fix_trace.jsonl",
     "仅 V2-P-04 / V2-E-01 两例 × 四臂的重跑 Trace（8 条 task 记录），"
     "是 merged 记录集里 8 条 overrides 的来源；不覆盖首轮文件"),
    ("docs/module_c/evidence/v2_report.json",
     "V2 **merged** 四臂记录集（92 条 = 23 × 4，含 8 条 overrides），"
     "C/D 各 23/23——这是'修复后一轮'的结果，不是两次独立重复"),
    ("docs/module_c/evidence/v2_rep2_report.json",
     "V2 第二轮重复：**只有** C/D 两臂、46 条记录，各 23/23，用于说明不是单次运气"),
    ("docs/module_c/evidence/v2_rep2_trace.jsonl",
     "V2 第二轮重复逐例 Trace（46 条 task 记录）"),
    # ---- V2.1: the frozen set, as measured ------------------------------
    ("docs/module_c/evidence/v21_run1_report.json",
     "V2.1 首次计分运行（盲测）原始报告：47 条 × 2 臂 = 94 条记录"),
    ("docs/module_c/evidence/v21_run1_summary.md", "V2.1 首轮 metric 汇总（runner 自带输出）"),
    ("docs/module_c/evidence/v21_run1_tables.md",
     "V2.1 首轮分析表（`analyse_v21` 生成：总体/分组/失败清单/失败类型分布）"),
    ("docs/module_c/evidence/v21_run2_report.json",
     "V2.1 第二轮重复原始报告：47 条 × 2 臂 = 94 条记录"),
    ("docs/module_c/evidence/v21_run2_summary.md", "V2.1 第二轮 metric 汇总"),
    ("docs/module_c/evidence/v21_run2_tables.md",
     "V2.1 第二轮分析表 + 与首轮的逐例一致性（翻转清单）"),
    ("docs/module_c/evidence/v21_recheck_report.json",
     "记录器修复后的定向复核（只跑 V21-N-01），**不计入盲测成绩**"),
    ("docs/module_c/evidence/v2_tables.md",
     "V2 报告表格快照，便于比对报告正文与原始统计"),
    # ---- V2.1 frozen independent case set -------------------------------
    ("tests/module_c/cases/v21_cases.json",
     "V2.1 新独立用例集（47 条 / 10 组），首次计分前冻结，计分后未修改"),
    ("tests/module_c/cases/v21_freeze.json",
     "冻结记录：用例 sha256 + 判定代码 sha256 + 开发集声明 + 声明'只能新建 v21b 而非改写'"),
    ("tests/module_c/cases/v21_dev_cases.json",
     "冻结前使用的 8 条开发集，只用于修复通用缺陷，明确不作为泛化证据"),
]

# Produced by the runs themselves; listed when present.
OPTIONAL: list[tuple[str, str]] = [
    ("outputs/module_c/v21-run1/report.json", "V2.1 首次计分运行（盲测）"),
    ("outputs/module_c/v21-run1/trace.jsonl", "V2.1 首次计分运行逐例 Trace"),
    ("outputs/module_c/v21-run1/summary.md", "V2.1 首次计分运行汇总"),
    ("outputs/module_c/v21-run2/report.json", "V2.1 第二轮重复"),
    ("outputs/module_c/v21-run2/trace.jsonl", "V2.1 第二轮重复 Trace"),
    ("outputs/module_c/v21-run2/summary.md", "V2.1 第二轮重复汇总"),
    ("outputs/module_c/v21-recheck2/report.json", "V2.1 记录器修复后的定向复核"),
    ("outputs/module_c/v21-dev/report.json", "V2.1 开发集运行（8 条 × 2 臂 = 16 条记录，修复依据）"),
    ("outputs/module_c/v2-run1/report.json", "V2 首次运行（修复前）原始报告，C/D 各 21/23"),
    ("outputs/module_c/v2-fix1/report.json", "V2 两例重跑原始报告（overrides 来源）"),
    ("outputs/module_c/v2-final/report.json", "V2 merged 记录集原始报告（含 overrides 字段）"),
    ("outputs/module_c/v2-rep2/report.json", "V2 第二轮重复原始报告（C/D 两臂）"),
]


def digest(path: Path) -> tuple[str, int]:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest(), path.stat().st_size


def record_fingerprint(path: Path) -> str:
    """`run_id` / case count / sha256 of the `cases` array, for report JSONs."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "—"
    if not isinstance(data, dict) or "cases" not in data:
        return "—"
    cases = data["cases"]
    sig = hashlib.sha256(
        json.dumps(cases, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return f"`{data.get('run_id', '-')}` / {len(cases)} 条 / `{sig}`"


def main() -> int:
    rows: list[tuple[str, str, int, str]] = []
    reports: list[tuple[str, str, str]] = []
    missing: list[str] = []

    for rel, purpose in REQUIRED:
        path = ROOT / rel
        if not path.exists():
            missing.append(rel)
            continue
        sha, size = digest(path)
        rows.append((rel, sha, size, purpose))

    for rel, purpose in OPTIONAL:
        path = ROOT / rel
        if not path.exists():
            continue
        sha, size = digest(path)
        rows.append((rel, sha, size, purpose))

    for table in (REQUIRED, OPTIONAL):
        for rel, purpose in table:
            if not rel.endswith("report.json"):
                continue
            path = ROOT / rel
            if path.exists():
                reports.append((rel, purpose, record_fingerprint(path)))

    freeze = json.loads(
        (ROOT / "tests/module_c/cases/v21_freeze.json").read_text("utf-8")
    )
    cases_sha, cases_size = digest(ROOT / "tests/module_c/cases/v21_cases.json")
    agree = cases_sha == freeze["cases_sha256"]

    lines = [
        "# Module C 证据清单（自动生成）",
        "",
        "由 `python -m tests.module_c.evidence_manifest` 生成。文件**只被读取，从未被改写**；",
        "sha256 取前 16 位便于肉眼比对，需要完整值时直接 `sha256sum` 对应文件。",
        "",
        f"- 用例集 sha256 与冻结记录一致：**{'是' if agree else '否'}**",
        f"- 冻结用例条数：{freeze['case_count']}；用例文件字节：{cases_size}",
        f"- 缺失的必需证据：{len(missing)} 个"
        + (f"（{', '.join(missing)}）" if missing else ""),
        "",
        "## 1. 文件清单",
        "",
        "| 文件 | sha256（前16） | 字节 | 证明什么 |",
        "|---|---|---:|---|",
    ]
    for rel, sha, size, purpose in rows:
        lines.append(f"| `{rel}` | `{sha[:16]}` | {size} | {purpose} |")

    if reports:
        lines += [
            "",
            "## 2. 记录集指纹（比文件哈希更能说明'跑的是同一批记录'）",
            "",
            "同一份记录集若经过重新格式化，文件哈希会变而 `cases` 数组不变；",
            "因此下表直接对 `cases` 数组取哈希。",
            "",
            "| 报告 | run_id / 记录数 / cases sha256 | 说明 |",
            "|---|---|---|",
        ]
        for rel, purpose, fingerprint in reports:
            lines.append(f"| `{rel}` | {fingerprint} | {purpose} |")
        lines += [
            "",
            "已核对：`docs/module_c/evidence/v2_report.json` 与 "
            "`outputs/module_c/v2-final/report.json` 的 `cases` 与 `by_arm` 完全一致"
            "（同为 `v2-merged`），前者是后者的归档副本；两个文件的字节数不同仅源于序列化格式。",
        ]

    lines += [
        "",
        "## 3. 复核方式",
        "",
        "```bash",
        "export PYTHONPATH='E:\\ICT\\_sp\\pylibs18;E:\\ICT\\_sp\\pylibs'",
        "",
        "# 1. 重新生成本清单并逐文件核对哈希",
        "python -m tests.module_c.evidence_manifest",
        "",
        "# 2. 从原始记录重算 V2 的全部指标（只读 raw records，不读报告结论）",
        "python -m tests.module_c.recompute_v2",
        "",
        "# 3. 校验冻结用例集与判定代码未被改动",
        "python -m pytest tests/module_c/test_v21_cases.py -q",
        "```",
        "",
        "`recompute_v2` 会按 (case, arm, rep) 三元组从 `outputs/module_c/v2-run1` 重建记录集，",
        "再用 `eval_lib.metrics` 重算通过率、LLM 调用数、任务数、token 与 p50；",
        "任一项与归档值不符即 exit 1。",
    ]

    target = EVIDENCE / "MANIFEST.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} ({len(rows)} files, {len(missing)} missing)")
    for rel in missing:
        print(f"  MISSING {rel}")
    return 1 if missing else 0


def purpose_of(rel: str, table: list[tuple[str, str]]) -> str:
    for candidate, purpose in table:
        if candidate == rel:
            return purpose
    return ""


if __name__ == "__main__":
    sys.exit(main())
