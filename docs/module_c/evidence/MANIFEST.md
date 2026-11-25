# Module C 证据清单（自动生成）

由 `python -m tests.module_c.evidence_manifest` 生成。文件**只被读取，从未被改写**；
sha256 取前 16 位便于肉眼比对，需要完整值时直接 `sha256sum` 对应文件。

- 用例集 sha256 与冻结记录一致：**是**
- 冻结用例条数：47；用例文件字节：19173
- 缺失的必需证据：0 个

## 1. 文件清单

| 文件 | sha256（前16） | 字节 | 证明什么 |
|---|---|---:|---|
| `docs/module_c/evidence/v0_report.json` | `452440d33aefe0b8` | 48489 | V0 基线成绩：判定口径是工具序列，用于划出'工程基线'的下界 |
| `docs/module_c/evidence/v0_trace.jsonl` | `fcc45143d06b1ddd` | 176328 | V0 逐条工具调用 Trace（640 条事件级的原始记录之一） |
| `docs/module_c/evidence/v1_dev_report.json` | `c54ec092035c2382` | 86477 | V1 开发集成绩（10 条用例 / 20 条判定记录，仅用于调 prompt，不作泛化证据） |
| `docs/module_c/evidence/v1_holdout_report.json` | `6b5411e740b6060d` | 92651 | V1 留出集成绩（14 条用例 / 28 条判定记录，调参时不看，属盲测） |
| `docs/module_c/evidence/v1_holdout_trace.jsonl` | `2ccc31e486fa0321` | 45437 | V1 留出集逐例 Trace（86 次 LLM 调用 / 28 次任务） |
| `docs/module_c/evidence/v2_trace.jsonl` | `38b2e17e00a0ce70` | 180037 | V2 **首次运行（修复前）**逐例 Trace：92 条 task 记录，C/D 两臂各 21/23，失败项为 V2-P-04（wrong_task）与 V2-E-01（param_error） |
| `docs/module_c/evidence/v2_fix_trace.jsonl` | `d710fba7cd68ceb8` | 17168 | 仅 V2-P-04 / V2-E-01 两例 × 四臂的重跑 Trace（8 条 task 记录），是 merged 记录集里 8 条 overrides 的来源；不覆盖首轮文件 |
| `docs/module_c/evidence/v2_report.json` | `09096fda3a707fb0` | 217182 | V2 **merged** 四臂记录集（92 条 = 23 × 4，含 8 条 overrides），C/D 各 23/23——这是'修复后一轮'的结果，不是两次独立重复 |
| `docs/module_c/evidence/v2_rep2_report.json` | `b2c46070737b6cc7` | 110969 | V2 第二轮重复：**只有** C/D 两臂、46 条记录，各 23/23，用于说明不是单次运气 |
| `docs/module_c/evidence/v2_rep2_trace.jsonl` | `24107d6c0c1566a5` | 78590 | V2 第二轮重复逐例 Trace（46 条 task 记录） |
| `docs/module_c/evidence/v21_run1_report.json` | `5b87562be7789648` | 225004 | V2.1 首次计分运行（盲测）原始报告：47 条 × 2 臂 = 94 条记录 |
| `docs/module_c/evidence/v21_run1_summary.md` | `591d1cc74c68da2b` | 6189 | V2.1 首轮 metric 汇总（runner 自带输出） |
| `docs/module_c/evidence/v21_run1_tables.md` | `62d8b11fa3c023d7` | 6828 | V2.1 首轮分析表（`analyse_v21` 生成：总体/分组/失败清单/失败类型分布） |
| `docs/module_c/evidence/v21_run2_report.json` | `fca8c41f82e60042` | 221113 | V2.1 第二轮重复原始报告：47 条 × 2 臂 = 94 条记录 |
| `docs/module_c/evidence/v21_run2_summary.md` | `c40ce6240fd7a354` | 6769 | V2.1 第二轮 metric 汇总 |
| `docs/module_c/evidence/v21_run2_tables.md` | `ce46c03f2bc6ab04` | 7816 | V2.1 第二轮分析表 + 与首轮的逐例一致性（翻转清单） |
| `docs/module_c/evidence/v21_recheck_report.json` | `d262c3b40c180f65` | 7434 | 记录器修复后的定向复核（只跑 V21-N-01），**不计入盲测成绩** |
| `docs/module_c/evidence/v2_tables.md` | `f97d68f6a1e93b45` | 5940 | V2 报告表格快照，便于比对报告正文与原始统计 |
| `tests/module_c/cases/v21_cases.json` | `fea06b63d135ff90` | 19173 | V2.1 新独立用例集（47 条 / 10 组），首次计分前冻结，计分后未修改 |
| `tests/module_c/cases/v21_freeze.json` | `5b044c6ee9dce2a9` | 1930 | 冻结记录：用例 sha256 + 判定代码 sha256 + 开发集声明 + 声明'只能新建 v21b 而非改写' |
| `tests/module_c/cases/v21_dev_cases.json` | `8ab91383289aed37` | 2354 | 冻结前使用的 8 条开发集，只用于修复通用缺陷，明确不作为泛化证据 |
| `outputs/module_c/v21-run1/report.json` | `5b87562be7789648` | 225004 | V2.1 首次计分运行（盲测） |
| `outputs/module_c/v21-run1/trace.jsonl` | `1e488746a6208339` | 145286 | V2.1 首次计分运行逐例 Trace |
| `outputs/module_c/v21-run1/summary.md` | `591d1cc74c68da2b` | 6189 | V2.1 首次计分运行汇总 |
| `outputs/module_c/v21-run2/report.json` | `fca8c41f82e60042` | 221113 | V2.1 第二轮重复 |
| `outputs/module_c/v21-run2/trace.jsonl` | `bff19026d2baafe7` | 139876 | V2.1 第二轮重复 Trace |
| `outputs/module_c/v21-run2/summary.md` | `c40ce6240fd7a354` | 6769 | V2.1 第二轮重复汇总 |
| `outputs/module_c/v21-recheck2/report.json` | `d262c3b40c180f65` | 7434 | V2.1 记录器修复后的定向复核 |
| `outputs/module_c/v21-dev/report.json` | `67216572eb25e967` | 43488 | V2.1 开发集运行（8 条 × 2 臂 = 16 条记录，修复依据） |
| `outputs/module_c/v2-run1/report.json` | `d9f5fec3e2db9994` | 217250 | V2 首次运行（修复前）原始报告，C/D 各 21/23 |
| `outputs/module_c/v2-fix1/report.json` | `672ed426541d7844` | 28173 | V2 两例重跑原始报告（overrides 来源） |
| `outputs/module_c/v2-final/report.json` | `fdfc7a1617726db4` | 224601 | V2 merged 记录集原始报告（含 overrides 字段） |
| `outputs/module_c/v2-rep2/report.json` | `491a4495901dd7e7` | 114775 | V2 第二轮重复原始报告（C/D 两臂） |

## 2. 记录集指纹（比文件哈希更能说明'跑的是同一批记录'）

同一份记录集若经过重新格式化，文件哈希会变而 `cases` 数组不变；
因此下表直接对 `cases` 数组取哈希。

| 报告 | run_id / 记录数 / cases sha256 | 说明 |
|---|---|---|
| `docs/module_c/evidence/v0_report.json` | `v0-1794422750` / 25 条 / `efa0b7671adf7dbb` | V0 基线成绩：判定口径是工具序列，用于划出'工程基线'的下界 |
| `docs/module_c/evidence/v1_dev_report.json` | `v1-1794427837` / 20 条 / `1b000e3b64eb84a4` | V1 开发集成绩（10 条用例 / 20 条判定记录，仅用于调 prompt，不作泛化证据） |
| `docs/module_c/evidence/v1_holdout_report.json` | `v1-1794428725` / 28 条 / `173958236afaac5a` | V1 留出集成绩（14 条用例 / 28 条判定记录，调参时不看，属盲测） |
| `docs/module_c/evidence/v2_report.json` | `v2-merged` / 92 条 / `7d55f78294288ad5` | V2 **merged** 四臂记录集（92 条 = 23 × 4，含 8 条 overrides），C/D 各 23/23——这是'修复后一轮'的结果，不是两次独立重复 |
| `docs/module_c/evidence/v2_rep2_report.json` | `v2-1794472707` / 46 条 / `5d0cb2358ee580f1` | V2 第二轮重复：**只有** C/D 两臂、46 条记录，各 23/23，用于说明不是单次运气 |
| `docs/module_c/evidence/v21_run1_report.json` | `v21-1794487124` / 94 条 / `a604061d823ca8e0` | V2.1 首次计分运行（盲测）原始报告：47 条 × 2 臂 = 94 条记录 |
| `docs/module_c/evidence/v21_run2_report.json` | `v21-1794489748` / 94 条 / `e2b05ff936c33e09` | V2.1 第二轮重复原始报告：47 条 × 2 臂 = 94 条记录 |
| `docs/module_c/evidence/v21_recheck_report.json` | `v21-1794492559` / 1 条 / `58c352f79f931a22` | 记录器修复后的定向复核（只跑 V21-N-01），**不计入盲测成绩** |
| `outputs/module_c/v21-run1/report.json` | `v21-1794487124` / 94 条 / `a604061d823ca8e0` | V2.1 首次计分运行（盲测） |
| `outputs/module_c/v21-run2/report.json` | `v21-1794489748` / 94 条 / `e2b05ff936c33e09` | V2.1 第二轮重复 |
| `outputs/module_c/v21-recheck2/report.json` | `v21-1794492559` / 1 条 / `58c352f79f931a22` | V2.1 记录器修复后的定向复核 |
| `outputs/module_c/v21-dev/report.json` | `v21-1794486068` / 16 条 / `b4ecb27ecda9c4bc` | V2.1 开发集运行（8 条 × 2 臂 = 16 条记录，修复依据） |
| `outputs/module_c/v2-run1/report.json` | `v2-1794468909` / 92 条 / `71a7ac00ca97d838` | V2 首次运行（修复前）原始报告，C/D 各 21/23 |
| `outputs/module_c/v2-fix1/report.json` | `v2-1794472370` / 8 条 / `a1b16072eb0c4df4` | V2 两例重跑原始报告（overrides 来源） |
| `outputs/module_c/v2-final/report.json` | `v2-merged` / 92 条 / `7d55f78294288ad5` | V2 merged 记录集原始报告（含 overrides 字段） |
| `outputs/module_c/v2-rep2/report.json` | `v2-1794472707` / 46 条 / `5d0cb2358ee580f1` | V2 第二轮重复原始报告（C/D 两臂） |

已核对：`docs/module_c/evidence/v2_report.json` 与 `outputs/module_c/v2-final/report.json` 的 `cases` 与 `by_arm` 完全一致（同为 `v2-merged`），前者是后者的归档副本；两个文件的字节数不同仅源于序列化格式。

## 3. 复核方式

```bash
export PYTHONPATH='E:\ICT\_sp\pylibs18;E:\ICT\_sp\pylibs'

# 1. 重新生成本清单并逐文件核对哈希
python -m tests.module_c.evidence_manifest

# 2. 从原始记录重算 V2 的全部指标（只读 raw records，不读报告结论）
python -m tests.module_c.recompute_v2

# 3. 校验冻结用例集与判定代码未被改动
python -m pytest tests/module_c/test_v21_cases.py -q
```

`recompute_v2` 会按 (case, arm, rep) 三元组从 `outputs/module_c/v2-run1` 重建记录集，
再用 `eval_lib.metrics` 重算通过率、LLM 调用数、任务数、token 与 p50；
任一项与归档值不符即 exit 1。
