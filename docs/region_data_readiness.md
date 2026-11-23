# Stage 1A：区域场所数据准备就绪报告

本报告由 `scripts/prepare_region_review_packages.py` 根据当前源文件生成。它只整理源记录和人工审核输入，不执行地理编码、OSM 查询或 GIS 流水线。

## 1. 输入核对

- 清洗输入：`data/processed/shelters_clean.csv`，453 行；SHA256 `c65a445f7fbbfedcbbddec889df7dfe7c5ebf8d837e77602aaff0734c91761c6`。
- 原始输入：`data/raw/zhongshan_shelters.xls`，453 行；SHA256 `15e2e779a73a67deb0a858eba9dae9ae5c5d196d6f2206bfe539f79a45e4a87b`。
- 原始 Excel 字段数：8；清洗表字段数：36。
- 原始字段通过 `source_row_excel` 与清洗表一对一对齐；未覆盖或修改原始字段。

## 2. 三个区域统计

| region_id | 镇街 | 记录数 | 待审核数 | 容量缺失 | likely_swapped | both_address_like_review | address_candidate 非空 |
|---|---|---:|---:|---:|---:|---:|---:|
| `east_district` | 东区街道 | 31 | 31 | 0 | 31 | 0 | 31 |
| `shiqi` | 石岐街道 | 25 | 25 | 1 | 23 | 0 | 25 |
| `xiaolan` | 小榄镇 | 31 | 31 | 0 | 30 | 1 | 31 |

所有 87 条区域记录的 `review_decision`、`identity_status`、`field_mapping_status`、坐标、开放状态和容量核验状态均初始化为 `needs_review`；没有自动确认场所名称、地址、容量或开放状态。

## 3. 专项问题

- 东区源行 72、75 均保留；两行候选地址相同，但名称、具体地址和电话不同，审核备注要求人工确认，未自动去重。
- 石岐容量缺失记录保留原始空值，审核备注明确禁止根据建筑面积推断容量。
- 小榄的 30 条 `likely_swapped` 和 1 条 `both_address_like_review` 标签均来自现有清洗结果，未被改写成已确认字段反转。

## 4. 全市重复候选

重复候选清单：`data/interim/regions/duplicate_candidates_all.csv`，共 23 条候选标记行。它按重复类型保留记录，不执行删除。

重点身份核验候选：东区 72/75；三乡 387/408；阜沙 267/275；港口镇多组重复地址。

## 5. 外部数据依赖

- `data/raw/portal_shelters_batch_1.xls`: **missing**
- `data/raw/portal_shelters_batch_2.xls`: **missing**
- 本阶段未运行 `scripts/12_reconcile_portal_batches.py`；两个附件缺失时，453 行不能被认定为完整名录。
- 目标区域边界候选文件：未发现。
- configs/pilot_analysis_area.geojson exists, but its properties declare a non-administrative demo rectangle; it is not accepted as a target-town boundary.
- 下一阶段需要补充三个区域的权威行政边界（GeoJSON/GeoPackage，含来源、日期、CRS 和版本/哈希）；若无法取得，只能使用明确标注为非行政的示范分析范围。

## 6. 已生成文件

- `data/interim/regions/east_district/shelter_review.xlsx`
- `data/interim/regions/shiqi/shelter_review.xlsx`
- `data/interim/regions/xiaolan/shelter_review.xlsx`
- `data/interim/regions/source_disposition.csv`
- `data/interim/regions/duplicate_candidates_all.csv`

## 7. 当前无法完成的工作

- 未完成人工字段语义确认、场所身份确认、坐标核验、当前开放状态核验和当前容量核验。
- 未进行地理编码、步行网络构建、人口裁剪、OD 计算或地图展示。
- 未把三个区域声明为已通过边界、坐标或道路网络验证的正式试点。

## 8. 下一阶段人工输入

1. 逐条填写三个工作簿中的确认名称、确认地址和字段映射状态。
2. 对重复候选确认是同一物理场所、不同场所还是无法判断；不得只按相同地址删除。
3. 补充当前容量证据、坐标证据、开放状态证据和审核日期/人员。
4. 提供两个门户附件和三个目标区域边界后，再进入 Stage 1B 的参数化 GIS 准备。
