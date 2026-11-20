from pathlib import Path

import pandas as pd
import pytest
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
REGIONS = {
    "east_district": ("东区街道", 31),
    "shiqi": ("石岐街道", 25),
    "xiaolan": ("小榄镇", 31),
}
RAW_COLUMNS = [
    "所在镇区",
    "所属社区（村）",
    "名称",
    "具体地址",
    "避护场所建筑面积（万㎡）",
    "容纳人数（万人）",
    "办公电话",
    "更新时间",
]


def norm(value):
    if pd.isna(value):
        return ""
    return " ".join(str(value).split()).strip()


@pytest.fixture(scope="module")
def source():
    clean = pd.read_csv(ROOT / "data/processed/shelters_clean.csv", dtype=str, keep_default_na=False)
    raw = pd.read_excel(ROOT / "data/raw/zhongshan_shelters.xls", dtype=object)
    raw.columns = [str(column).strip() for column in raw.columns]
    raw.insert(0, "source_row_excel", range(2, len(raw) + 2))
    return clean, raw


@pytest.fixture(scope="module")
def disposition():
    return pd.read_csv(ROOT / "data/interim/regions/source_disposition.csv", dtype=str, keep_default_na=False)


def test_region_counts_and_workbooks(source):
    clean, _ = source
    for region_id, (town, expected) in REGIONS.items():
        selected = clean[clean["town"].eq(town)]
        assert len(selected) == expected
        workbook = ROOT / "data/interim/regions" / region_id / "shelter_review.xlsx"
        assert workbook.is_file()
        frame = pd.read_excel(workbook, sheet_name="shelter_review", dtype=object)
        assert len(frame) == expected
        assert frame["region_id"].eq(region_id).all()
        assert frame["shelter_id"].is_unique
        assert frame["review_decision"].eq("needs_review").all()
        assert frame["identity_status"].eq("needs_review").all()
        assert frame["field_mapping_status"].eq("needs_review").all()
        assert frame["capacity_verification_status"].eq("needs_review").all()
        assert frame["coordinate_status"].eq("needs_review").all()
        assert frame["current_open_status"].eq("needs_review").all()
        assert frame["confirmed_shelter_name"].fillna("").eq("").all()
        assert frame["confirmed_address"].fillna("").eq("").all()


def test_target_disposition_is_complete_and_disjoint(disposition, source):
    clean, _ = source
    assert len(disposition) == 87
    assert disposition["shelter_id"].is_unique
    assert disposition["source_row_excel"].is_unique
    assert set(disposition["region_id"]) == set(REGIONS)
    assert disposition["review_decision"].eq("needs_review").all()
    assert disposition["identity_status"].eq("needs_review").all()
    expected_ids = set(clean[clean["town"].isin([town for town, _ in REGIONS.values()])]["shelter_id"])
    assert set(disposition["shelter_id"]) == expected_ids


def test_raw_fields_are_preserved(source):
    clean, raw = source
    raw_by_row = raw.set_index("source_row_excel")
    for region_id, (town, _) in REGIONS.items():
        path = ROOT / "data/interim/regions" / region_id / "shelter_review.xlsx"
        frame = pd.read_excel(path, sheet_name="shelter_review", dtype=object)
        for _, row in frame.iterrows():
            raw_row = raw_by_row.loc[int(row["source_row_excel"])]
            for column in RAW_COLUMNS:
                assert norm(row[column]) == norm(raw_row[column])


def test_special_records_and_missing_capacity(source):
    _, raw = source
    east = pd.read_excel(ROOT / "data/interim/regions/east_district/shelter_review.xlsx", dtype=object)
    pair = east[east["source_row_excel"].isin([72, 75])]
    assert set(pair["source_row_excel"].astype(int)) == {72, 75}
    assert len(pair) == 2
    assert pair["identity_status"].eq("needs_review").all()
    assert pair["review_decision"].eq("needs_review").all()

    shiqi = pd.read_excel(ROOT / "data/interim/regions/shiqi/shelter_review.xlsx", dtype=object)
    missing = shiqi[shiqi["source_row_excel"].astype(str).eq("?")]
    raw_shiqi = raw[raw["所在镇区"].map(norm).eq("石岐街道")]
    raw_capacity_missing = raw_shiqi[raw_shiqi["容纳人数（万人）"].isna()]
    assert len(raw_capacity_missing) == 1
    row_no = int(raw_capacity_missing.iloc[0]["source_row_excel"])
    target = shiqi[shiqi["source_row_excel"].astype(int).eq(row_no)]
    assert len(target) == 1
    assert norm(target.iloc[0]["容纳人数（万人）"]) == ""
    assert norm(target.iloc[0]["capacity_persons"]) == ""


def test_duplicates_are_reported_not_deleted():
    path = ROOT / "data/interim/regions/duplicate_candidates_all.csv"
    assert path.is_file()
    duplicates = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert not duplicates.empty
    assert set(duplicates["duplicate_type"]) == {
        "exact_duplicate_candidate",
        "repeated_source_name_candidate",
        "repeated_source_address_candidate",
    }
    assert {72, 75}.issubset(set(duplicates.loc[duplicates["town"].eq("东区街道"), "source_row_excel"].astype(int)))
    assert {267, 275}.issubset(set(duplicates["source_row_excel"].astype(int)))


def test_workbooks_have_filter_freeze_and_validations():
    for region_id in REGIONS:
        path = ROOT / "data/interim/regions" / region_id / "shelter_review.xlsx"
        workbook = load_workbook(path)
        sheet = workbook["shelter_review"]
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref
        assert len(sheet.data_validations.dataValidation) >= 6
