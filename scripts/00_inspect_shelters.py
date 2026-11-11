from pathlib import Path
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]

def main():
    cfg = yaml.safe_load((ROOT / "configs/pilot.yaml").read_text(encoding="utf-8"))
    source = ROOT / cfg["paths"]["shelter_excel"]
    if not source.exists():
        raise FileNotFoundError(f"Missing source workbook: {source}")
    # For .xls install xlrd>=2.0.1; for .xlsx pandas uses openpyxl.
    xls = pd.ExcelFile(source)
    print("File:", source.name)
    print("Sheets:", xls.sheet_names)
    for sheet in xls.sheet_names:
        df = pd.read_excel(source, sheet_name=sheet, dtype=object)
        print(f"\n[{sheet}] rows={len(df)} columns={len(df.columns)}")
        print("Columns:", df.columns.tolist())
        print(df.head(10).to_string(index=False))
    out = ROOT / "docs/shelter_inspection.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"Workbook: {source.name}", f"Sheets: {xls.sheet_names}"]
    for sheet in xls.sheet_names:
        d = pd.read_excel(source, sheet_name=sheet, dtype=object)
        lines += ["", f"## {sheet}", f"Shape: {d.shape}", f"Columns: {d.columns.tolist()}",
                  d.head(10).to_string(index=False)]
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\nSaved:", out)

if __name__ == "__main__":
    main()
