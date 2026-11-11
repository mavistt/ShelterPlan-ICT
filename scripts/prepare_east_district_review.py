from pathlib import Path
import pandas as pd
import numpy as np
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data/processed/shelters_clean.csv"
OUT_DIR = ROOT / "data/interim"
CSV_OUT = OUT_DIR / "east_district_shelters_review.csv"
XLSX_OUT = OUT_DIR / "east_district_shelters_review.xlsx"
EXPECTED_ROWS = 31

def main():
    if XLSX_OUT.exists() or CSV_OUT.exists():
        raise FileExistsError("Review files already exist; regeneration would overwrite manual review.")

    if not INPUT.exists():
        raise FileNotFoundError(f"Cannot find {INPUT}. Run scripts/01_clean_shelters.py first.")

    df = pd.read_csv(INPUT, dtype={"shelter_id": str})
    df["town"] = df["town"].fillna("").astype(str).str.strip()
    east = df[df["town"] == "东区街道"].copy().sort_values("source_row_excel")

    if len(east) != EXPECTED_ROWS:
        raise ValueError(
            f"Expected {EXPECTED_ROWS} records for 东区街道, got {len(east)}. "
            "Check the cleaned CSV and exact town name before proceeding."
        )

    columns = [
        "shelter_id", "source_row_excel", "community_name",
        "source_name_raw", "source_specific_address_raw",
        "address_candidate", "shelter_name_candidate",
        "confirmed_shelter_name", "confirmed_address",
        "capacity_persons", "capacity_review_status", "area_sqm",
        "phone_raw", "updated_at", "field_semantics_status",
        "field_review_status", "same_site_review_status",
        "duplicate_source_name_candidate",
        "duplicate_source_specific_address_candidate", "review_notes"
    ]
    review = pd.DataFrame(index=east.index)
    for col in columns:
        review[col] = east[col] if col in east.columns else ""

    # Human-approved canonical fields are intentionally blank.
    review["confirmed_shelter_name"] = ""
    review["confirmed_address"] = ""
    review["capacity_review_status"] = "待核验"
    review["field_review_status"] = "待核验"
    duplicate_flag = review["duplicate_source_name_candidate"].astype(str).str.lower().isin(["true", "1"])
    review["same_site_review_status"] = "待核验"
    review["review_notes"] = np.where(
        duplicate_flag,
        "原始Excel第72/75行地址候选相同，需核实是否同一物理场所；不要自动删除。",
        ""
    )
    review = review[columns].reset_index(drop=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    review.to_csv(CSV_OUT, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(XLSX_OUT, engine="openpyxl") as writer:
        review.to_excel(writer, sheet_name="东区街道审核", index=False)
        pd.DataFrame({
            "项目": ["用途", "记录范围", "重要说明", "场所名称核验", "地址核验",
                     "容量核验", "同址记录核验", "审核状态填写", "禁止操作", "下一步"],
            "说明": [
                "只审核本次试点镇区的避难场所原始记录，不修改原始总表。",
                "东区街道，共31条，来源于 shelters_clean.csv。",
                "原始字段保留；候选字段只是规则推断，不是官方确认结果。",
                "确认后填写 confirmed_shelter_name；不要直接相信 shelter_name_candidate。",
                "确认后填写 confirmed_address；不要直接相信 address_candidate。",
                "capacity_persons 是原表“容纳人数（万人）”乘以10000得到的换算值；核对后改状态为已核验。",
                "Excel原始第72、75行地址候选相同，但场所名称不同。核实是否同一物理场所或同一建筑内不同场所，不可仅因同址删除。",
                "field_review_status：待核验、已核验、需修改、无法确认。其余审核状态按下拉选项填写。",
                "不要删除行、覆盖原始字段，也不要将未核验记录直接用于地理编码或容量优化。",
                "审核完成后保存并交回，再生成最终地理编码输入表。"
            ]
        }).to_excel(writer, sheet_name="填写说明", index=False)

    wb = load_workbook(XLSX_OUT)
    ws = wb["东区街道审核"]
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[1].height = 42

    widths = {
        "shelter_id": 16, "source_row_excel": 14, "community_name": 16,
        "source_name_raw": 34, "source_specific_address_raw": 42,
        "address_candidate": 34, "shelter_name_candidate": 42,
        "confirmed_shelter_name": 38, "confirmed_address": 42,
        "capacity_persons": 16, "capacity_review_status": 16, "area_sqm": 14,
        "phone_raw": 16, "updated_at": 14, "field_semantics_status": 24,
        "field_review_status": 16, "same_site_review_status": 24,
        "duplicate_source_name_candidate": 20,
        "duplicate_source_specific_address_candidate": 28, "review_notes": 36
    }
    for idx, name in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = widths.get(name, 18)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[row[0].row].height = 42

    field_dv = DataValidation(type="list", formula1='"待核验,已核验,需修改,无法确认"', allow_blank=False)
    capacity_dv = DataValidation(type="list", formula1='"待核验,已核验,需确认"', allow_blank=False)
    site_dv = DataValidation(type="list", formula1='"待核验,确认不同场所,确认同一物理场所,同址不同楼栋/入口,需进一步核实"', allow_blank=False)
    for dv in (field_dv, capacity_dv, site_dv):
        ws.add_data_validation(dv)
    col_index = {name: i + 1 for i, name in enumerate(columns)}
    for name, dv in [("field_review_status", field_dv),
                     ("capacity_review_status", capacity_dv),
                     ("same_site_review_status", site_dv)]:
        letter = get_column_letter(col_index[name])
        dv.add(f"{letter}2:{letter}{len(review)+1}")

    # Highlight the two records with the same source-name/address candidate.
    for r in range(2, len(review) + 2):
        flag = ws.cell(r, col_index["duplicate_source_name_candidate"]).value
        if str(flag).lower() in ("true", "1"):
            for c in range(1, len(columns) + 1):
                ws.cell(r, c).fill = PatternFill("solid", fgColor="FFF2CC")

    ws2 = wb["填写说明"]
    ws2.freeze_panes = "A2"
    ws2.sheet_view.showGridLines = False
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 110
    for cell in ws2[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for row in ws2.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws2.row_dimensions[row[0].row].height = 42
    wb.save(XLSX_OUT)

    print(f"Pilot town: 东区街道")
    print(f"Records: {len(review)}")
    print(f"Capacity missing: {int(pd.to_numeric(review['capacity_persons'], errors='coerce').isna().sum())}")
    # `review` has reset_index(drop=True), but duplicate_flag still carries east's
    # original row labels. Use a positional NumPy mask after resetting the index.
    same_address_candidate_rows = review.loc[
        duplicate_flag.to_numpy(), "source_row_excel"
    ].astype(int).tolist()
    print(f"Same-address candidate rows: {same_address_candidate_rows}")
    print(f"Saved: {CSV_OUT}")
    print(f"Saved: {XLSX_OUT}")

if __name__ == "__main__":
    main()
