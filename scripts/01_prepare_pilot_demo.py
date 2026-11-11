"""Build reproducible pilot input from the existing review workbook for demo use."""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "data/interim/east_district_shelters_review.xlsx"
CLEAN = ROOT / "data/processed/shelters_clean.csv"
OUT = ROOT / "data/processed/shelters_pilot_raw.csv"

def main():
    review = pd.read_excel(REVIEW, sheet_name="东区街道审核", dtype={"shelter_id": str})
    source = pd.read_csv(CLEAN, dtype={"shelter_id": str})
    required = ["shelter_id", "source_row_excel", "shelter_name_candidate", "address_candidate", "confirmed_shelter_name", "confirmed_address", "capacity_persons"]
    missing = [name for name in required if name not in review]
    if missing:
        raise ValueError(f"Missing columns in review workbook: {missing}")
    if review["shelter_id"].isna().any() or review["shelter_id"].duplicated().any():
        raise ValueError("Review workbook contains missing or duplicate shelter IDs")
    pilot = source[source["town"].fillna("").astype(str).str.strip().eq("东区街道")].copy()
    if pilot["shelter_id"].duplicated().any() or set(pilot.shelter_id) != set(review.shelter_id):
        raise ValueError("Review and source records do not match; check before proceeding")
    review["source_row_excel"] = pd.to_numeric(review["source_row_excel"], errors="raise").astype(int)
    src_rows = pilot.set_index("shelter_id")["source_row_excel"].astype(int)
    if not review.set_index("shelter_id")["source_row_excel"].eq(src_rows).all():
        raise ValueError("Source row identifiers differ between review and source")
    if not {72, 75}.issubset(set(review.source_row_excel)):
        raise ValueError("Expected same-address candidate rows 72 and 75 are missing")
    pair = review.set_index("source_row_excel").loc[[72, 75], "address_candidate"].fillna("").astype(str).str.strip()
    if not pair.iloc[0] or pair.iloc[0] != pair.iloc[1]:
        raise ValueError("Rows 72 and 75 no longer have the same candidate address; review the demo assumption")
    review = review[review.source_row_excel != 75].copy()
    def pick(approved, candidate):
        a = review[approved].fillna("").astype(str).str.strip()
        b = review[candidate].fillna("").astype(str).str.strip()
        return a.where(a.ne(""), b)
    review["shelter_name"] = pick("confirmed_shelter_name", "shelter_name_candidate")
    review["address"] = pick("confirmed_address", "address_candidate")
    review["capacity_persons"] = pd.to_numeric(review["capacity_persons"], errors="coerce")
    bad = review.shelter_name.eq("") | review.address.eq("") | review.capacity_persons.isna() | (review.capacity_persons <= 0)
    if bad.any():
        raise ValueError(f"Missing name/address or invalid capacity in source rows: {review.loc[bad, 'source_row_excel'].tolist()}")
    review["town"] = "东区街道"
    review["source_file"] = "zhongshan_shelters.xls"
    review["source_row"] = review["source_row_excel"]
    review["data_status"] = "provisional_demo_not_verified"
    review["data_note"] = "2024 source values; candidate fields where confirmed values absent; not for emergency operations"
    review.loc[review.source_row_excel == 72, "data_note"] += "; source row 75 excluded as assumed same site, unverified"
    cols = ["shelter_id", "town", "shelter_name", "address", "capacity_persons", "source_file", "source_row", "data_status", "data_note"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    review[cols].to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"Pilot demo: {len(review)} facilities (row 75 excluded by explicit same-site assumption)")
    print(f"Saved {OUT}")
    print("All results are provisional demonstration data, not verified current facilities.")

if __name__ == "__main__":
    main()
