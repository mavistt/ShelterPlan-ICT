# ShelterPlan-Eval Stage 0 基线报告

生成日期：2026-11-25

## 基线与 Git 状态

- 远端仓库：https://github.com/mavistt/ShelterPlan-ICT
- 起始分支：origin/integration/abc-shiqi-v2
- 起始提交：14d686aa0043dad5139ebc165b873f04a6b5e80f
- 当前分支：feature/shelterplan-eval-v1
- 当前 HEAD：14d686aa0043dad513eb9c165b873f04a6b5e80f
- 创建前工作区：干净
- 历史参考：origin/feature/benchmark-grounded，提交 a873701ada56b43289c84851d38fee80fc7e4a3d
- 历史 merge_data：只读参考，没有 merge、复制或覆盖到新评测目录
- 现有 A/B/C/D 生产代码：本 Stage 未修改

普通 Git fetch 初次受 Windows Schannel 凭据错误影响；使用用户指定的 127.0.0.1:6064 代理并切换 Git OpenSSL backend 后完成远端核对。临时 checkout 使用归档内容恢复工作树，LFS 自动 smudge 未运行。

## 分支和 PR 快照

| 位置 | 状态 |
| --- | --- |
| PR #1 A → main | OPEN，未合并 |
| PR #6 ABC 集成 | OPEN、Draft，base 为 feature/module-a-stage1-research |
| PR #7 B → A | OPEN，未合并 |
| PR #8 C → B | OPEN，未合并，当前 GitHub 标记 mergeable=false |

## LFS 与源文件

执行命令：

~~~powershell
git lfs ls-files
python -B -m sources.preflight
~~~

结果：data/raw/chn_pop_2025_CN_100m_R2025A_v1.tif 和 data/raw/guangdong-latest.osm.pbf 仍是 LFS pointer；本轮没有读取 LFS 对象，也没有重建原始 GIS。该项为 NOT_RUN，不冒充原始数据重建成功。

其余 A v2 审计输入文件均存在，区域 manifest、质量报告和适配器的 data_version 一致，三地区 shelter 行数和 OD expected_rows 一致。

## 三地区基线

| 区域 | enabled | 场所 | 需求节点 | OD | data_version | 发布状态 | 边界 |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| shiqi | true | 25 | 7,336 | 183,400 | shelterplan_module_a_v2.0-demo__shiqi | PASS_DEMO | demo_analysis_boundary |
| east_district | true | 31 | 38,469 | 1,192,539 | shelterplan_module_a_v2.0-demo__east_district | PASS_DEMO | demo_analysis_boundary |
| xiaolan | false | 31 | 60,406 | 1,872,586 | shelterplan_module_a_v2.0-demo__xiaolan | PASS_DEMO | demo_analysis_boundary |

当前 PASS_DEMO 只表示数据包工程结构可读取和内部数量/版本校验通过，不代表场所身份、精确坐标、容量、开放状态或官方行政边界已被现场核实。

## Stage 0/1 交付物

- sources/source_manifest.json：输入文件存在性、字节数、SHA-256、LFS pointer 和区域一致性基线；
- sources/preflight.py：可重复运行的只读基线脚本；
- sources/admission_gate.py：逐场所 demo / verified 双准入门禁；
- sources/eligibility.csv：87 条逐场所准入记录；
- reports/admission_summary.json：机器可读的分区域和逐条结果；
- reports/quality_audit.md：数据质量和后续处置报告；
- benchmark/shelterplan_eval/README.md：评测目录边界和运行说明；
- configs/suite_v1.yaml：评测套件草案；
- adapters/protocol.md：A/Eval 与 B/C 的契约草案；
- tests/test_admission_gate.py：正例、负例和回归测试。

## 测试结果

### PASS

~~~powershell
python -B -m unittest tests.test_admission_gate
~~~

结果：5 tests OK。

~~~powershell
python -B -m pytest -q tests/test_module_a_v2.py tests/test_gis_data.py tests/test_region_review_packages.py -p no:cacheprovider
~~~

结果：21 passed，23 warnings。

~~~powershell
python -B -m unittest discover -s tests/module_b -p "test*.py"
~~~

结果：32 tests OK。

### FAIL

在用户指定代理和可写临时目录下运行：

~~~powershell
python -B -m pytest -q tests/module_c/test_abc_integration_fixes.py tests/module_c/test_b_client.py tests/module_c/test_mcp_structured.py tests/module_c/test_session.py -p no:cacheprovider --basetemp D:\t_Python_Projects\ShelterPlan-Eval-v1-pytest-elevated
~~~

结果：18 passed，3 failed，1 warning。

基线失败点：

1. agent/regions.py 在 _load_v2_regions() 构造 RegionInfo 时没有传 enabled 和 exclusion_reason，导致 C 的区域结构化 runner 失败；
2. C B-client 真实 in-process B 测试触发 Manifest artifact size or SHA-256 does not match，说明当前远端集成基线仍有 manifest/实际工作树一致性问题；
3. 该问题出现在 Eval 分支创建前的基线提交，本 Stage 不修改 C/B 核心来掩盖它。

### NOT_RUN

- C 全量回归：本 Stage 只做最小基线，不重复运行 297 项全量回归；
- 真实 LLM 路径：没有接入真实 LLM；
- LFS 对象读取后的原始 TIFF/PBF 全量重建：当前只有 pointer；
- Scenario Factory、Task Factory、GT/Oracle、VETS：明确留到 Stage 2/3。

## Stage 1 数据准入结果

实际运行：

~~~powershell
python -B -m sources.preflight
python -B -m sources.admission_gate
~~~

结果：

| 分类 | 数量 |
| --- | ---: |
| 源记录 | 87 |
| 可用于受控 DEMO_ONLY | 56 |
| 可用于 VERIFIED | 0 |
| needs_review | 30 |
| excluded | 1 |

分区域：

| 区域 | 源记录 | Demo 可用 | Verified 可用 | needs_review | excluded |
| --- | ---: | ---: | ---: | ---: | ---: |
| shiqi | 25 | 25 | 0 | 0 | 0 |
| east_district | 31 | 31 | 0 | 0 | 0 |
| xiaolan | 31 | 0 | 0 | 30 | 1 |

关键风险：

- 三地区原始审核状态均为 needs_review；
- 三地区坐标均为 approximate，地理编码记录是 candidate/unresolved；
- 三地区开放状态均为 assumed_open；
- 三地区边界均为 demo_analysis_boundary，不是官方行政边界；
- 石岐有 1 条容量使用 regional_median demo imputation；
- 东区有 2 条重复地址候选，不能自动去重；
- 小榄 shelter_id zs_61811ada3c 的 snap 距离是 424.726m，超过 200m；
- 小榄区域保持 disabled，其他 30 条小榄记录保留为 needs_review，异常记录单独 excluded；
- 没有任何记录自动升级为 verified，没有删除任何源记录。

## 是否需要重新生成当前 A v2 数据

本轮结论：不立即重建，也不覆盖旧 A v2。

只有在以下证据补齐后才建立新的数据版本并重算受影响派生物：

1. 小榄异常场所重新核验坐标或确认其真实道路连接；
2. 场所身份、字段映射、容量和开放状态获得独立证据；
3. 官方边界和门户批次数据可用；
4. LFS 原始 TIFF/PBF 对象可读；
5. 453/906 来源数量差异完成对账。

重建时必须生成新的 data_version、source hash、OD、空间层和 manifest；不能修改旧 manifest 以消除问题。

## Stage 2 是否可以开始

可以开始有限的 Stage 2 设计，但有前置条件：

- 只使用 shiqi 和 east_district 的 DEMO_ONLY admission；
- 场景必须绑定 region_id、data_version、source manifest hash、eligibility_status 和 provenance；
- 小榄只用于 disabled-region 安全拒绝或人工核验负例；
- 不能把当前 56 条 demo-eligible 记录当作 verified 真实场所；
- 必须先修复/单独记录 C 基线的 RegionInfo 构造和 manifest 校验失败，再做端到端 Eval；

- 继续保持历史 benchmark 只读，不把旧 GT 混入新数据。
