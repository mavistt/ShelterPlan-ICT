"""Read-only status check for the East District review workbook."""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "data/interim/east_district_shelters_review.xlsx"
CLEAN = ROOT / "data/processed/shelters_clean.csv"

def main():
    df = pd.read_excel(REVIEW, sheet_name="东区街道审核", dtype={"shelter_id": str})
    source = pd.read_csv(CLEAN, dtype={"shelter_id": str})
    required = ["shelter_id", "source_row_excel", "confirmed_shelter_name", "confirmed_address", "field_review_status", "capacity_review_status", "same_site_review_status"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing review columns: {missing}")
    if df["shelter_id"].isna().any() or df["shelter_id"].duplicated().any():
        raise ValueError("Missing or duplicate shelter_id in review workbook")
    east = source[source["town"].fillna("").str.strip() == "东区街道"]
    if set(df.shelter_id) != set(east.shelter_id) or len(df) != len(east):
        raise ValueError("Review records differ from East District source; inspect before proceeding")
    print(f"东区街道记录：{len(df)}")
    for col in ["confirmed_shelter_name", "confirmed_address"]:
        populated = df[col].fillna("").astype(str).str.strip().ne("")
        print(f"{col}: {int(populated.sum())}/{len(df)}")
    for col in ["field_review_status", "capacity_review_status", "same_site_review_status"]:
        print("\n" + col + ":\n" + df[col].fillna("空白").value_counts().to_string())
    pair = df[df["source_row_excel"].isin([72, 75])]
    print("\n原始行 72/75 同址记录：")
    print(pair[["source_row_excel", "same_site_review_status"]].to_string(index=False))
    print("\n此检查不认定位置、容量或同址关系已核实，也不生成 GIS 输入。")

if __name__ == "__main__":
    main()
