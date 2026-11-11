from pathlib import Path
"""Reconcile the two public portal attachment batches against each other and the local workbook."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import unicodedata

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BATCH_PATHS = [ROOT / "data/raw/portal_shelters_batch_1.xls", ROOT / "data/raw/portal_shelters_batch_2.xls"]
SOURCE_PATH = ROOT / "data/raw/zhongshan_shelters.xls"
EXPECTED_COLUMNS = [
    "所在镇区", "所属社区（村）", "名称", "具体地址",
    "避护场所建筑面积（万㎡）", "容纳人数（万人）", "办公电话", "更新时间",
]
NUMERIC_COLUMNS = {"避护场所建筑面积（万㎡）", "容纳人数（万人）"}
BATCH_IDS = ["ff8080819daf4f90019e678d78683d50", "ff80808196a71e2901975c8dd57b21e4"]
PORTAL_URL = "https://zsdata.zs.gov.cn/web/dataServerView?id=ff80808171f8c2d2017211d225c2039a"


def normalize(value, numeric=False):
    if pd.isna(value):
        return ""
    if numeric:
        try:
            number = float(value)
            if not math.isfinite(number):
                return ""
            return format(number, ".15g")
        except (TypeError, ValueError):
            pass
    value = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", " ", value).strip()


def read_rows(path):
    frame = pd.read_excel(path, dtype=object)
    frame.columns = [normalize(c) for c in frame.columns]
    missing = sorted(set(EXPECTED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"{path.name} is missing official fields: {missing}")
    frame = frame[EXPECTED_COLUMNS]
    fingerprints = []
    for values in frame.itertuples(index=False, name=None):
        row = [normalize(value, numeric=(column in NUMERIC_COLUMNS)) for column, value in zip(EXPECTED_COLUMNS, values)]
        payload = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        fingerprints.append(hashlib.sha256(payload.encode("utf-8")).hexdigest())
    return frame, Counter(fingerprints)


def main():
    missing = [str(path) for path in [*BATCH_PATHS, SOURCE_PATH] if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Download both public portal batch XLS files to data/raw/portal_shelters_batch_1.xls "
            "and portal_shelters_batch_2.xls before reconciliation. Missing: " + "; ".join(missing)
        )

    batches = [read_rows(path) for path in BATCH_PATHS]
    local_frame, local_counts = read_rows(SOURCE_PATH)
    frames = [item[0] for item in batches]
    counts = [item[1] for item in batches]
    exact_batch_match = counts[0] == counts[1]
    local_matches = [local_counts == batch_counts for batch_counts in counts]
    union_payloads = set(counts[0]) | set(counts[1])
    combined_rows = sum(sum(counter.values()) for counter in counts)
    duplicate_rows_within = [sum(counter.values()) - len(counter) for counter in counts]

    input_paths = [*BATCH_PATHS, SOURCE_PATH]
    input_sha256 = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in input_paths}
    result = {
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_sha256": input_sha256,
        "portal_url": PORTAL_URL,
        "batch_ids": BATCH_IDS,
        "batch_files": [path.name for path in BATCH_PATHS],
        "source_file": SOURCE_PATH.name,
        "batch_row_counts": [int(len(frame)) for frame in frames],
        "local_source_row_count": int(len(local_frame)),
        "exact_batch_payloads_equal": bool(exact_batch_match),
        "exact_local_payload_matches_batch": local_matches,
        "local_matches_any_batch": bool(any(local_matches)),
        "unique_payload_rows_across_batches": int(len(union_payloads)),
        "batch_row_sum": int(combined_rows),
        "exact_duplicate_rows_within_each_batch": [int(v) for v in duplicate_rows_within],
        "catalog_count_906_matches_batch_row_sum": bool(combined_rows == 906),
        "interpretation": "Exact row-payload comparison only. It cannot establish that listed shelters are currently open, that capacity is current, or that same-address distinct records are one facility.",
    }
    out_json = ROOT / "data/processed/shelter_batch_reconciliation.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    status = "EXACT_DUPLICATE_BATCHES" if exact_batch_match else "BATCHES_DIFFER"
    lines = [
        "# Official shelter attachment batch reconciliation", "",
        f"- Audited UTC: `{result['audited_at_utc']}`",
        f"- Portal catalog URL: {PORTAL_URL}",
        f"- Batch IDs: `{BATCH_IDS[0]}` and `{BATCH_IDS[1]}`",
        f"- Rows per batch: `{result['batch_row_counts']}`; total payload rows: `{combined_rows}`",
        f"- Local workbook rows: `{len(local_frame)}`; exact match to batch 1/2: `{local_matches}`",
        f"- Exact full-row payloads equal between batches: **{exact_batch_match}**",
        f"- Unique row payloads across batches: `{len(union_payloads)}`",
        f"- Exact duplicate rows within each batch: `{duplicate_rows_within}`",
        f"- Catalog count 906 equals sum of batch rows: **{combined_rows == 906}**",
        f"- Result: **{status}**", "",
        "This comparison uses normalized values for the eight published fields and exact SHA256 row fingerprints. It establishes payload equality or difference only; it does not validate the 2024 status/capacity or prove facility identity for near-duplicate records.",
    ]
    report = ROOT / "docs/shelter_batch_reconciliation.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"JSON: {out_json}")
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
