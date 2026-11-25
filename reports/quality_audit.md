# ShelterPlan-Eval Stage 1 数据质量审计与准入

- 生成时间（UTC）：2026-11-25T11:51:51.140267+00:00
- 输入快照：A v2 已有处理结果，只读
- 准入策略：demo 与 verified 分离；不自动升级 verified；不静默删除源记录
- active 只表示源记录的 active 字段；不等同于当前开放状态。正常候选必须同时满足 region enabled、active 和结构校验；DEMO_ONLY 警告继续保留。

## 总体计数

| 指标 | 数量 |
| --- | ---: |
| 源记录 | 87 |
| 可用于受控 DEMO_ONLY 的记录 | 56 |
| 正常求解候选（受控 DEMO_ONLY） | 56 |
| 可用于 VERIFIED 的记录 | 0 |
| needs_review | 30 |
| excluded | 1 |

## 分区域准入结果

| 区域 | 启用 | 源记录 | Demo 可用 | Verified 可用 | needs_review | excluded |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| shiqi | True | 25 | 25 | 0 | 0 | 0 |
| east_district | True | 31 | 31 | 0 | 0 | 0 |
| xiaolan | False | 31 | 0 | 0 | 30 | 1 |

## 证据与解释

- 石岐和东区可以继续用于明确标注 DEMO_ONLY 的受控软件演示；这不代表身份、精确坐标、容量或开放状态已现场核验。
- 小榄区域保持 disabled；所有小榄记录不能进入普通 Demo admission，只能用于禁用区域安全拒绝或后续人工核验。
- 小榄 shelter_id zs_61811ada3c 的 Snap 距离超过 200m，按记录级 excluded 处理；源记录仍保留。
- 三地区当前没有任何记录获得 verified admission。原因包括 needs_review 身份、approximate 坐标、assumed_open、demo boundary、缺少独立容量证据或 DEMO_ONLY 数据状态。
- 东区重复地址候选和石岐容量中位数插补只作为审计证据，不自动删除、不自动去重、不自动升级。

## 后续处置建议

1. 对 needs_review 记录建立人工核验表，补充身份、字段映射、坐标、开放状态和容量证据。
2. 只有在独立证据补齐后，生成新的 A 数据快照、重新计算受影响 OD/图层并更新 manifest；不得原地改旧 manifest。
3. 小榄先修正异常坐标并重新生成该区域全部受影响派生物；在此之前不用于正常规划题。
4. 官方边界和门户批次数据补齐后，再单独评估是否能进入 verified。
5. A v2 原始 TIFF/PBF 若仍是 LFS pointer，先完成对象可读性和存储迁移，再进行需要原始数据重建的任务。
