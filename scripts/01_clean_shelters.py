from pathlib import Path
import re
import hashlib
import pandas as pd
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]

ADDRESS_PATTERNS = [
    (r"\d+\s*(?:号|路|街|巷|大道|道|弄|栋|幢|座|楼|卡|室)", 4),
    (r"(?:广东省|中山市).{0,30}(?:路|街|巷|大道|号)", 3),
    (r"(?:村牌坊旁|学校后门|大门口|路口|市场旁|小区内|公园内|中心内|附近|交界处)", 2),
    (r"(?:工业区|工业园|小区|市场|公园)", 1),
]
FACILITY_PATTERNS = [
    (r"(?:小学|中学|学校|幼儿园|技工学校|职业学校|学院)", 3),
    (r"(?:党群服务中心|党群服务站|村委会|居委会|居民委员会|委员会)", 3),
    (r"(?:体育馆|体育中心|文化中心|文化馆|活动中心|服务中心|办公楼|会堂|敬老院|老人院|羽毛球馆|图书馆|美术馆|纪念堂|广场)", 2),
    (r"(?:应急避护|应急避难|避护场所|避难场所|庇护场所|避护中心|避难中心)", 2),
]

def norm_text(value):
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()

def score(value, patterns):
    text = norm_text(value)
    return sum(weight for pattern, weight in patterns if re.search(pattern, text))

def make_shelter_id(excel_row, town, name, address):
    raw = f"{excel_row}|{town}|{name}|{address}"
    return "zs_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]

def main():
    cfg = yaml.safe_load((ROOT / "configs/pilot.yaml").read_text(encoding="utf-8"))
    source = ROOT / cfg["paths"]["shelter_excel"]
    if not source.exists():
        raise FileNotFoundError(f"Missing source workbook: {source}")
    sheet = cfg["project"].get("shelter_sheet", 0)
    raw = pd.read_excel(source, sheet_name=sheet, dtype=object)
    raw.columns = [str(c).strip() for c in raw.columns]

    required = [
        "所在镇区", "所属社区（村）", "名称", "具体地址",
        "避护场所建筑面积（万㎡）", "容纳人数（万人）", "办公电话", "更新时间"
    ]
    missing_cols = [c for c in required if c not in raw.columns]
    if missing_cols:
        raise KeyError(
            f"Missing expected columns: {missing_cols}. Actual columns: {raw.columns.tolist()}"
        )

    out = []
    for idx, row in raw.iterrows():
        excel_row = idx + 2
        town = norm_text(row["所在镇区"])
        community = norm_text(row["所属社区（村）"])
        name_raw = norm_text(row["名称"])
        address_raw = norm_text(row["具体地址"])
        name_addr_score = score(name_raw, ADDRESS_PATTERNS)
        address_addr_score = score(address_raw, ADDRESS_PATTERNS)
        name_fac_score = score(name_raw, FACILITY_PATTERNS)
        address_fac_score = score(address_raw, FACILITY_PATTERNS)

        if name_raw and name_raw == address_raw:
            status = "same_text_review"
            address_candidate, shelter_name_candidate, source_field = name_raw, address_raw, "名称+具体地址"
        elif name_addr_score >= 3 and address_fac_score >= 2 and address_addr_score < 3:
            status = "likely_swapped"
            address_candidate, shelter_name_candidate, source_field = name_raw, address_raw, "名称"
        elif address_addr_score >= 3 and name_fac_score >= 2 and name_addr_score < 3:
            status = "likely_normal"
            address_candidate, shelter_name_candidate, source_field = address_raw, name_raw, "具体地址"
        elif name_addr_score >= 3 and address_addr_score >= 3:
            status = "both_address_like_review"
            if name_addr_score >= address_addr_score:
                address_candidate, shelter_name_candidate, source_field = name_raw, address_raw, "名称"
            else:
                address_candidate, shelter_name_candidate, source_field = address_raw, name_raw, "具体地址"
        else:
            status = "ambiguous_review"
            if name_addr_score > address_addr_score:
                address_candidate, shelter_name_candidate, source_field = name_raw, address_raw, "名称"
            elif address_addr_score > name_addr_score:
                address_candidate, shelter_name_candidate, source_field = address_raw, name_raw, "具体地址"
            else:
                address_candidate, shelter_name_candidate, source_field = "", "", "unresolved"

        capacity_10k = pd.to_numeric(pd.Series([row["容纳人数（万人）"]]), errors="coerce").iloc[0]
        area_10k = pd.to_numeric(pd.Series([row["避护场所建筑面积（万㎡）"]]), errors="coerce").iloc[0]
        phone_raw = norm_text(row["办公电话"])
        phone = "" if phone_raw in {"/", "／", "-", "—", ""} else phone_raw

        out.append({
            "shelter_id": make_shelter_id(excel_row, town, name_raw, address_raw),
            "source_row_excel": excel_row,
            "town": town,
            "community_name": community,
            "source_name_raw": name_raw,
            "source_specific_address_raw": address_raw,
            "address_candidate": address_candidate,
            "shelter_name_candidate": shelter_name_candidate,
            # Final canonical fields stay blank until a human approves the candidates.
            "shelter_name": "",
            "address": "",
            "address_candidate_source_field": source_field,
            "name_address_score": name_addr_score,
            "name_facility_score": name_fac_score,
            "specific_address_address_score": address_addr_score,
            "specific_address_facility_score": address_fac_score,
            "field_semantics_status": status,
            "area_10k_sqm": area_10k,
            "area_sqm": area_10k * 10000 if pd.notna(area_10k) else np.nan,
            "capacity_10k_persons": capacity_10k,
            "capacity_persons": capacity_10k * 10000 if pd.notna(capacity_10k) else np.nan,
            "phone_raw": phone_raw,
            "phone": phone,
            "updated_at": norm_text(row["更新时间"]),
            "source_file": source.name,
            "source_sheet": str(sheet),
            "field_semantics_verified": False,
            "field_semantics_note": "",
            "coordinate_verified": False,
            "verified": False,
            "admission_status": "needs_review"
        })

    clean = pd.DataFrame(out)
    raw_normalized = raw.apply(lambda r: tuple(norm_text(r[c]) for c in raw.columns), axis=1)
    clean["duplicate_exact_candidate"] = raw_normalized.duplicated(keep=False).values
    clean["duplicate_source_name_candidate"] = clean["source_name_raw"].map(norm_text).duplicated(keep=False).values
    clean["duplicate_source_specific_address_candidate"] = clean["source_specific_address_raw"].map(norm_text).duplicated(keep=False).values
    clean["capacity_status"] = np.where(
        clean["capacity_persons"].notna() & (clean["capacity_persons"] > 0),
        "valid", "missing_or_invalid"
    )
    clean["area_status"] = np.where(
        clean["area_sqm"].notna() & (clean["area_sqm"] > 0),
        "valid", "missing_or_invalid"
    )
    clean["phone_status"] = np.where(clean["phone"] != "", "present", "missing_or_placeholder")

    processed = ROOT / "data/processed/shelters_clean.csv"
    processed.parent.mkdir(parents=True, exist_ok=True)
    clean.to_csv(processed, index=False, encoding="utf-8-sig")

    review_cols = [
        "shelter_id", "source_row_excel", "town", "community_name",
        "source_name_raw", "source_specific_address_raw",
        "address_candidate", "shelter_name_candidate", "address_candidate_source_field",
        "shelter_name", "address", "field_semantics_status", "capacity_persons",
        "area_sqm", "phone_raw", "updated_at",
        "duplicate_exact_candidate", "duplicate_source_name_candidate",
        "duplicate_source_specific_address_candidate", "field_semantics_verified",
        "field_semantics_note", "coordinate_verified", "verified", "admission_status"
    ]
    review = clean[review_cols]
    review_path = ROOT / "data/interim/shelters_review_required.csv"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review.to_csv(review_path, index=False, encoding="utf-8-sig")
    review_xlsx_path = ROOT / "data/interim/shelters_review_required.xlsx"
    review.to_excel(review_xlsx_path, index=False, engine="openpyxl")

    # Add filters and freeze the header row for manual review in Excel.
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    wb = load_workbook(review_xlsx_path)
    ws = wb.active
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for col_idx, col_cells in enumerate(ws.columns, start=1):
        max_len = max(len(str(c.value or "")) for c in list(col_cells)[:100])
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 2, 12), 42)
    ws.row_dimensions[1].height = 32
    wb.save(review_xlsx_path)

    # Build duplicate-group details without dropping records.
    def duplicate_groups(series):
        values = series.map(norm_text)
        groups = {}
        for idx, value in values.items():
            if value and (values == value).sum() > 1:
                groups.setdefault(value, []).append(int(idx) + 2)
        return {k: v for k, v in groups.items() if len(v) > 1}

    exact_rows = clean.loc[clean["duplicate_exact_candidate"], "source_row_excel"].astype(int).tolist()
    missing_capacity_rows = clean.loc[
        clean["capacity_status"] != "valid", "source_row_excel"
    ].astype(int).tolist()
    name_groups = duplicate_groups(clean["source_name_raw"])
    address_groups = duplicate_groups(clean["source_specific_address_raw"])
    status_counts = clean["field_semantics_status"].value_counts().to_dict()
    cap_total_10k = pd.to_numeric(clean["capacity_10k_persons"], errors="coerce").sum()
    area_total_10k = pd.to_numeric(clean["area_10k_sqm"], errors="coerce").sum()

    lines = [
        "# Shelter register quality report",
        "",
        f"- Source file: `{source.name}`",
        f"- Sheet: `{sheet}`",
        f"- Actual rows: `{len(clean)}`",
        f"- Unique towns: `{clean['town'].nunique()}`",
        f"- Missing/invalid capacity rows: `{missing_capacity_rows}`",
        f"- Exact duplicate candidate Excel rows: `{exact_rows}`",
        f"- Phone missing/placeholders: `{int((clean['phone_status']=='missing_or_placeholder').sum())}`",
        f"- Updated-at values: `{sorted(clean['updated_at'].dropna().unique().tolist())}`",
        "",
        "## Field semantics screening (heuristic only)",
        *[f"- `{k}`: `{status_counts.get(k,0)}`" for k in [
            "likely_swapped", "ambiguous_review", "both_address_like_review",
            "same_text_review", "likely_normal"
        ]],
        "",
        "Original `名称` and `具体地址` are retained verbatim. Candidate address/name fields are "
        "heuristic suggestions only. A human must confirm each pilot record before geocoding.",
        "",
        "## Repeated source values (review candidates; not automatically deleted)",
        "### Repeated `名称` values",
        *([f"- `{k}`: Excel rows {v}" for k, v in name_groups.items()] or ["- None"]),
        "",
        "### Repeated `具体地址` values",
        *([f"- `{k}`: Excel rows {v}" for k, v in address_groups.items()] or ["- None"]),
        "",
        "## Unit conversions",
        "- `容纳人数（万人） * 10000 -> capacity_persons`.",
        "- `避护场所建筑面积（万㎡） * 10000 -> area_sqm`.",
        f"- Raw capacity sum: `{cap_total_10k:.6f}` 万人 = `{cap_total_10k*10000:.0f}` 人; "
        "this is only an arithmetic sum of the downloaded rows, not a validated citywide capacity.",
        f"- Raw area sum: `{area_total_10k:.6f}` 万㎡ = `{area_total_10k*10000:.2f}` ㎡; "
        "this is only an arithmetic sum of the downloaded rows.",
        "",
        "## Admission rules",
        "- Keep exact duplicates and repeated-name/address candidates for manual resolution; do not auto-delete.",
        "- Missing capacity records are not eligible for capacity-constrained planning.",
        "- `/` phone placeholders are converted to empty `phone`, while `phone_raw` is retained.",
        "- All records remain `verified=false` and `admission_status=needs_review`.",
        "- Do not treat the workbook as a complete citywide inventory until its 453 rows are reconciled "
        "with the source portal metadata and current official counts.",
        ""
    ]
    report = ROOT / "docs/data_quality_shelters.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved: {processed}")
    print(f"Rows: {len(clean)}")
    print(clean["field_semantics_status"].value_counts().to_string())
    print(f"Missing capacity rows: {missing_capacity_rows}")
    print(f"Exact duplicate candidate Excel rows: {exact_rows}")
    print(f"Review sheet: {review_path}")

if __name__ == "__main__":
    main()
