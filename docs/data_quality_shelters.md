# Shelter register quality report

- Source file: `zhongshan_shelters.xls`
- Sheet: `中山市应急避难场所基础信息表.xlsx`
- Actual rows: `453`
- Unique towns: `23`
- Missing/invalid capacity rows: `[39]`
- Exact duplicate candidate Excel rows: `[267, 275]`
- Phone missing/placeholders: `24`
- Updated-at values: `['2024年度']`

## Field semantics screening (heuristic only)
- `likely_swapped`: `362`
- `ambiguous_review`: `71`
- `both_address_like_review`: `15`
- `same_text_review`: `5`
- `likely_normal`: `0`

Original `名称` and `具体地址` are retained verbatim. Candidate address/name fields are heuristic suggestions only. A human must confirm each pilot record before geocoding.

## Repeated source values (review candidates; not automatically deleted)
### Repeated `名称` values
- `中山市东区新村大街8号`: Excel rows [72, 75]
- `三墩村正涌下街祥丰横巷1号之1`: Excel rows [146, 147]
- `阜沙镇长兴街33号`: Excel rows [267, 275]
- `广东省中山市三乡镇小琅环路38号桂山中学`: Excel rows [387, 408]

### Repeated `具体地址` values
- `中山市阜沙中学`: Excel rows [267, 275]
- `港口镇石特社区室内应急避护场所`: Excel rows [295, 307]
- `港口镇西街社区室内应急避护场所`: Excel rows [296, 305, 306]
- `港口镇群乐社区室内应急避护场所`: Excel rows [298, 304]
- `港口镇中南村室内应急避护场所`: Excel rows [300, 309]
- `港口镇下南村室内应急避护场所`: Excel rows [310, 311]

## Unit conversions
- `容纳人数（万人） * 10000 -> capacity_persons`.
- `避护场所建筑面积（万㎡） * 10000 -> area_sqm`.
- Raw capacity sum: `20.028100` 万人 = `200281` 人; this is only an arithmetic sum of the downloaded rows, not a validated citywide capacity.
- Raw area sum: `85.903660` 万㎡ = `859036.60` ㎡; this is only an arithmetic sum of the downloaded rows.

## Admission rules
- Keep exact duplicates and repeated-name/address candidates for manual resolution; do not auto-delete.
- Missing capacity records are not eligible for capacity-constrained planning.
- `/` phone placeholders are converted to empty `phone`, while `phone_raw` is retained.
- All records remain `verified=false` and `admission_status=needs_review`.
- Do not treat the workbook as a complete citywide inventory until its 453 rows are reconciled with the source portal metadata and current official counts.
