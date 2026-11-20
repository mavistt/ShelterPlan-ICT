"""Generate reproducible Stage 1A shelter review packages.

This script is deliberately limited to source-record preparation.  It does not
call geocoding/network APIs and does not modify raw or processed GIS inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CLEAN_PATH = ROOT / "data/processed/shelters_clean.csv"
RAW_PATH = ROOT / "data/raw/zhongshan_shelters.xls"
OUTPUT_ROOT = ROOT / "data/interim/regions"
REPORT_PATH = ROOT / "docs/region_data_readiness.md"

REGIONS = {
    "east_district": "东区街道",
    "shiqi": "石岐街道",
    "xiaolan": "小榄镇",
}

REVIEW_COLUMNS = [
    "confirmed_shelter_name",
    "confirmed_address",
    "field_mapping_status",
    "identity_status",
    "review_decision",
    "exclusion_reason",
    "capacity_verification_status",
    "capacity_evidence",
    "coordinate_status",
    "coordinate_evidence",
    "current_open_status",
    "reviewer",
    "review_date",
    "reviewer_notes",
]

DISPOSITION_COLUMNS = [
    "region_id",
    "shelter_id",
    "source_row_excel",
    "source_town",
    "review_decision",
    "identity_status",
    "exclusion_reason",
    "reviewer_notes",
]


def norm(value) -> str:
    if pd.isna(value):
        return ""
    return " ".join(str(value).split()).strip()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    if not CLEAN_PATH.is_file():
        raise FileNotFoundError(CLEAN_PATH)
    if not RAW_PATH.is_file():
        raise FileNotFoundError(RAW_PATH)

    clean = pd.read_csv(CLEAN_PATH, dtype=str, keep_default_na=False)
    raw = pd.read_excel(RAW_PATH, dtype=object)
    raw.columns = [str(column).strip() for column in raw.columns]
    raw.insert(0, "source_row_excel", range(2, len(raw) + 2))

    required_clean = {
        "shelter_id",
        "source_row_excel",
        "town",
        "source_name_raw",
        "source_specific_address_raw",
        "address_candidate",
        "shelter_name_candidate",
        "field_semantics_status",
        "capacity_persons",
        "duplicate_exact_candidate",
        "duplicate_source_name_candidate",
        "duplicate_source_specific_address_candidate",
    }
    missing_clean = sorted(required_clean - set(clean.columns))
    if missing_clean:
        raise ValueError(f"Clean table is missing required fields: {missing_clean}")

    clean["source_row_excel"] = pd.to_numeric(
        clean["source_row_excel"], errors="raise"
    ).astype(int)
    if clean["shelter_id"].duplicated().any():
        raise ValueError("shelter_id is duplicated in shelters_clean.csv")
    if clean["source_row_excel"].duplicated().any():
        raise ValueError("source_row_excel is duplicated in shelters_clean.csv")
    if len(clean) != len(raw):
        raise ValueError(f"Clean/raw row count mismatch: clean={len(clean)}, raw={len(raw)}")

    raw_columns = [column for column in raw.columns if column != "source_row_excel"]
    merged = clean.merge(
        raw[["source_row_excel", *raw_columns]],
        on="source_row_excel",
        how="left",
        validate="one_to_one",
        suffixes=("", "__raw"),
    )
    if merged[raw_columns].isna().all(axis=1).any():
        raise ValueError("Some clean rows did not match the raw workbook by source_row_excel")

    # The raw Chinese town field must agree with the normalized clean town field.
    raw_town = merged["所在镇区"].map(norm)
    clean_town = merged["town"].map(norm)
    if not raw_town.eq(clean_town).all():
        mismatches = merged.loc[~raw_town.eq(clean_town), ["source_row_excel", "town", "所在镇区"]]
        raise ValueError(f"Town mismatch between clean and raw inputs:\n{mismatches.to_string(index=False)}")
    return clean, merged, raw_columns


def review_note(row: pd.Series, region_id: str) -> str:
    row_number = int(row["source_row_excel"])
    if region_id == "east_district" and row_number in {72, 75}:
        return "候选地址相同不等于同一物理场所；名称、具体地址和电话不同，必须人工核验。"
    if region_id == "shiqi" and norm(row["capacity_persons"]) == "":
        return "源表容纳人数为空；不得根据建筑面积推断或补填容量。"
    if region_id == "xiaolan" and row["field_semantics_status"] in {
        "likely_swapped",
        "both_address_like_review",
    }:
        return "保留现有启发式语义标签；未完成人工字段映射确认。"
    return ""


def build_review_frame(
    rows: pd.DataFrame, raw_columns: list[str], region_id: str
) -> pd.DataFrame:
    # Keep all raw workbook fields and every existing clean-table field.  The
    # region_id is added; original values are never overwritten.
    prefix = rows[["shelter_id", "source_row_excel"]].copy()
    prefix.insert(0, "region_id", region_id)
    clean_columns = [
        column
        for column in rows.columns
        if column not in {"shelter_id", "source_row_excel", *raw_columns}
    ]
    output = pd.concat(
        [prefix.reset_index(drop=True), rows[raw_columns].reset_index(drop=True), rows[clean_columns].reset_index(drop=True)],
        axis=1,
    )
    for column in REVIEW_COLUMNS:
        if column in output:
            raise ValueError(f"Review field collides with source field: {column}")
        output[column] = ""

    output["field_mapping_status"] = "needs_review"
    output["identity_status"] = "needs_review"
    output["review_decision"] = "needs_review"
    output["capacity_verification_status"] = "needs_review"
    output["coordinate_status"] = "needs_review"
    output["current_open_status"] = "needs_review"
    output["reviewer_notes"] = [review_note(row, region_id) for _, row in rows.iterrows()]
    return output


def write_workbook(frame: pd.DataFrame, path: Path) -> None:
    from openpyxl import load_workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_excel(path, index=False, sheet_name="shelter_review")
    workbook = load_workbook(path)
    sheet = workbook["shelter_review"]
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[1].height = 34

    for index, column in enumerate(frame.columns, start=1):
        values = [str(value) if value is not None else "" for value in frame[column].head(100)]
        width = min(max(max([len(column), *map(len, values)]) + 2, 12), 42)
        sheet.column_dimensions[get_column_letter(index)].width = width

    validations = {
        "field_mapping_status": ["needs_review", "confirmed", "conflict"],
        "identity_status": ["needs_review", "confirmed_same_site", "confirmed_distinct", "conflict"],
        "review_decision": ["included", "excluded", "needs_review"],
        "capacity_verification_status": ["needs_review", "verified_current", "verified_historical", "missing", "conflict"],
        "coordinate_status": ["needs_review", "verified", "approximate_only", "missing", "conflict"],
        "current_open_status": ["needs_review", "open", "closed", "unknown"],
    }
    for field, values in validations.items():
        column_index = list(frame.columns).index(field) + 1
        letter = get_column_letter(column_index)
        validation = DataValidation(
            type="list",
            formula1='"' + ",".join(values) + '"',
            allow_blank=False,
        )
        validation.error = "请选择下拉选项。"
        validation.errorTitle = "状态值无效"
        validation.prompt = "请根据人工证据更新审核状态。"
        validation.promptTitle = "审核状态"
        sheet.add_data_validation(validation)
        validation.add(f"{letter}2:{letter}{len(frame) + 1}")

    # Visually flag the two East District identity cases and missing capacity,
    # without changing any values.
    row_number_col = list(frame.columns).index("source_row_excel") + 1
    capacity_col = list(frame.columns).index("容纳人数（万人）") + 1
    row_letter = get_column_letter(row_number_col)
    capacity_letter = get_column_letter(capacity_col)
    yellow = PatternFill("solid", fgColor="FFF2CC")
    red = PatternFill("solid", fgColor="FCE4D6")
    sheet.conditional_formatting.add(
        f"A2:{get_column_letter(len(frame.columns))}{len(frame) + 1}",
        FormulaRule(formula=[f"OR(${row_letter}2=72,${row_letter}2=75)"], fill=yellow),
    )
    sheet.conditional_formatting.add(
        f"{capacity_letter}2:{capacity_letter}{len(frame) + 1}",
        FormulaRule(formula=[f'{capacity_letter}2=""'], fill=red),
    )
    # Fix workbook properties before saving, then normalize ZIP member
    # timestamps.  This keeps repeated package generation byte-reproducible,
    # rather than changing only because XLSX metadata used the current time.
    fixed_time = datetime(2000, 1, 1, 0, 0, 0)
    workbook.properties.created = fixed_time
    workbook.properties.modified = fixed_time
    workbook.save(path)
    normalized_path = path.with_name(path.name + ".normalized")
    with zipfile.ZipFile(path, "r") as source_zip, zipfile.ZipFile(
        normalized_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as target_zip:
        for info in source_zip.infolist():
            normalized_info = zipfile.ZipInfo(
                filename=info.filename,
                date_time=(2000, 1, 1, 0, 0, 0),
            )
            normalized_info.compress_type = zipfile.ZIP_DEFLATED
            normalized_info.comment = info.comment
            normalized_info.create_system = info.create_system
            normalized_info.external_attr = info.external_attr
            normalized_info.internal_attr = info.internal_attr
            normalized_info.flag_bits = info.flag_bits
            payload = source_zip.read(info.filename)
            if info.filename == "docProps/core.xml":
                payload = re.sub(
                    rb"<dcterms:modified[^>]*>.*?</dcterms:modified>",
                    b'<dcterms:modified xsi:type="dcterms:W3CDTF">2000-01-01T00:00:00Z</dcterms:modified>',
                    payload,
                )
            target_zip.writestr(normalized_info, payload)
    os.replace(normalized_path, path)


def duplicate_candidates(clean: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def append_flag(flag_column: str, duplicate_type: str, key_column: str) -> None:
        flagged = clean[clean[flag_column].astype(str).str.lower().eq("true")]
        for _, row in flagged.iterrows():
            key = norm(row[key_column])
            if duplicate_type == "exact_duplicate_candidate":
                key = "source-row-group-" + key
            rows.append(
                {
                    "duplicate_type": duplicate_type,
                    "duplicate_key": key,
                    "region_id": next((rid for rid, town in REGIONS.items() if town == row["town"]), ""),
                    "shelter_id": row["shelter_id"],
                    "source_row_excel": int(row["source_row_excel"]),
                    "town": row["town"],
                    "source_name_raw": row["source_name_raw"],
                    "source_specific_address_raw": row["source_specific_address_raw"],
                    "address_candidate": row["address_candidate"],
                    "shelter_name_candidate": row["shelter_name_candidate"],
                    "field_semantics_status": row["field_semantics_status"],
                    "review_note": "身份核验候选；不得自动删除。",
                }
            )

    append_flag("duplicate_exact_candidate", "exact_duplicate_candidate", "source_row_excel")
    append_flag("duplicate_source_name_candidate", "repeated_source_name_candidate", "source_name_raw")
    append_flag(
        "duplicate_source_specific_address_candidate",
        "repeated_source_address_candidate",
        "source_specific_address_raw",
    )
    return pd.DataFrame(rows).sort_values(
        ["duplicate_type", "source_row_excel"], kind="stable"
    ).reset_index(drop=True)


def source_disposition(rows: pd.DataFrame) -> pd.DataFrame:
    records = []
    for region_id, town in REGIONS.items():
        selected = rows[rows["town"].eq(town)]
        for _, row in selected.iterrows():
            records.append(
                {
                    "region_id": region_id,
                    "shelter_id": row["shelter_id"],
                    "source_row_excel": int(row["source_row_excel"]),
                    "source_town": row["town"],
                    "review_decision": "needs_review",
                    "identity_status": "needs_review",
                    "exclusion_reason": "",
                    "reviewer_notes": review_note(row, region_id),
                }
            )
    result = pd.DataFrame(records, columns=DISPOSITION_COLUMNS)
    if result["shelter_id"].duplicated().any():
        raise ValueError("Target-region source disposition contains duplicate shelter_id")
    return result.sort_values(["region_id", "source_row_excel"], kind="stable").reset_index(drop=True)


def boundary_candidates() -> tuple[list[str], str]:
    candidates = []
    for base in (ROOT / "configs", ROOT / "data", ROOT / "docs"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".geojson", ".gpkg", ".shp"}:
                lower = str(path.relative_to(ROOT)).lower()
                if any(token in lower for token in ("boundary", "行政", "east", "shiqi", "xiaolan", "东区", "石岐", "小榄")):
                    candidates.append(str(path.relative_to(ROOT)))
    pilot = ROOT / "configs/pilot_analysis_area.geojson"
    if pilot.is_file():
        note = "configs/pilot_analysis_area.geojson exists, but its properties declare a non-administrative demo rectangle; it is not accepted as a target-town boundary."
    else:
        note = "No existing pilot analysis polygon was found."
    return sorted(set(candidates)), note


def write_report(
    clean: pd.DataFrame,
    dispositions: pd.DataFrame,
    duplicate_frame: pd.DataFrame,
    counts: dict[str, int],
    raw_columns: list[str],
) -> None:
    portal_paths = [ROOT / "data/raw/portal_shelters_batch_1.xls", ROOT / "data/raw/portal_shelters_batch_2.xls"]
    portal_lines = [
        f"- `{path.relative_to(ROOT)}`: **{'present' if path.is_file() else 'missing'}**"
        for path in portal_paths
    ]
    boundaries, boundary_note = boundary_candidates()
    lines = [
        "# Stage 1A：区域场所数据准备就绪报告",
        "",
        "本报告由 `scripts/prepare_region_review_packages.py` 根据当前源文件生成。它只整理源记录和人工审核输入，不执行地理编码、OSM 查询或 GIS 流水线。",
        "",
        "## 1. 输入核对",
        "",
        f"- 清洗输入：`data/processed/shelters_clean.csv`，{len(clean)} 行；SHA256 `{file_sha256(CLEAN_PATH)}`。",
        f"- 原始输入：`data/raw/zhongshan_shelters.xls`，{len(clean)} 行；SHA256 `{file_sha256(RAW_PATH)}`。",
        f"- 原始 Excel 字段数：{len(raw_columns)}；清洗表字段数：{len(clean.columns)}。",
        "- 原始字段通过 `source_row_excel` 与清洗表一对一对齐；未覆盖或修改原始字段。",
        "",
        "## 2. 三个区域统计",
        "",
        "| region_id | 镇街 | 记录数 | 待审核数 | 容量缺失 | likely_swapped | both_address_like_review | address_candidate 非空 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for region_id, town in REGIONS.items():
        group = clean[clean["town"].eq(town)]
        status = group["field_semantics_status"].value_counts().to_dict()
        candidate_count = int(group["address_candidate"].map(norm).ne("").sum())
        capacity_missing = int(group["capacity_persons"].map(norm).eq("").sum())
        lines.append(
            f"| `{region_id}` | {town} | {len(group)} | {len(group)} | {capacity_missing} | {status.get('likely_swapped', 0)} | {status.get('both_address_like_review', 0)} | {candidate_count} |"
        )
    lines += [
        "",
        "所有 87 条区域记录的 `review_decision`、`identity_status`、`field_mapping_status`、坐标、开放状态和容量核验状态均初始化为 `needs_review`；没有自动确认场所名称、地址、容量或开放状态。",
        "",
        "## 3. 专项问题",
        "",
        "- 东区源行 72、75 均保留；两行候选地址相同，但名称、具体地址和电话不同，审核备注要求人工确认，未自动去重。",
        "- 石岐容量缺失记录保留原始空值，审核备注明确禁止根据建筑面积推断容量。",
        "- 小榄的 30 条 `likely_swapped` 和 1 条 `both_address_like_review` 标签均来自现有清洗结果，未被改写成已确认字段反转。",
        "",
        "## 4. 全市重复候选",
        "",
        f"重复候选清单：`data/interim/regions/duplicate_candidates_all.csv`，共 {len(duplicate_frame)} 条候选标记行。它按重复类型保留记录，不执行删除。",
        "",
        "重点身份核验候选：东区 72/75；三乡 387/408；阜沙 267/275；港口镇多组重复地址。",
        "",
        "## 5. 外部数据依赖",
        "",
        *portal_lines,
        "- 本阶段未运行 `scripts/12_reconcile_portal_batches.py`；两个附件缺失时，453 行不能被认定为完整名录。",
        f"- 目标区域边界候选文件：{boundaries if boundaries else '未发现'}。",
        f"- {boundary_note}",
        "- 下一阶段需要补充三个区域的权威行政边界（GeoJSON/GeoPackage，含来源、日期、CRS 和版本/哈希）；若无法取得，只能使用明确标注为非行政的示范分析范围。",
        "",
        "## 6. 已生成文件",
        "",
        "- `data/interim/regions/east_district/shelter_review.xlsx`",
        "- `data/interim/regions/shiqi/shelter_review.xlsx`",
        "- `data/interim/regions/xiaolan/shelter_review.xlsx`",
        "- `data/interim/regions/source_disposition.csv`",
        "- `data/interim/regions/duplicate_candidates_all.csv`",
        "",
        "## 7. 当前无法完成的工作",
        "",
        "- 未完成人工字段语义确认、场所身份确认、坐标核验、当前开放状态核验和当前容量核验。",
        "- 未进行地理编码、步行网络构建、人口裁剪、OD 计算或地图展示。",
        "- 未把三个区域声明为已通过边界、坐标或道路网络验证的正式试点。",
        "",
        "## 8. 下一阶段人工输入",
        "",
        "1. 逐条填写三个工作簿中的确认名称、确认地址和字段映射状态。",
        "2. 对重复候选确认是同一物理场所、不同场所还是无法判断；不得只按相同地址删除。",
        "3. 补充当前容量证据、坐标证据、开放状态证据和审核日期/人员。",
        "4. 提供两个门户附件和三个目标区域边界后，再进入 Stage 1B 的参数化 GIS 准备。",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    clean, merged, raw_columns = load_inputs()
    counts = {}
    for region_id, town in REGIONS.items():
        selected = merged[merged["town"].eq(town)].copy()
        if selected.empty:
            raise ValueError(f"No source rows found for {region_id} / {town}")
        counts[region_id] = len(selected)
        output = build_review_frame(selected, raw_columns, region_id)
        write_workbook(output, OUTPUT_ROOT / region_id / "shelter_review.xlsx")

    disposition = source_disposition(merged)
    if len(disposition) != sum(counts.values()) or len(disposition) != 87:
        raise ValueError(f"Expected 87 target records, got {len(disposition)} with counts {counts}")
    disposition.to_csv(OUTPUT_ROOT / "source_disposition.csv", index=False, encoding="utf-8-sig")

    duplicates = duplicate_candidates(clean)
    duplicates.to_csv(
        OUTPUT_ROOT / "duplicate_candidates_all.csv", index=False, encoding="utf-8-sig"
    )
    write_report(clean, disposition, duplicates, counts, raw_columns)

    print(json.dumps({"region_counts": counts, "target_total": len(disposition), "duplicate_rows": len(duplicates), "report": str(REPORT_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
