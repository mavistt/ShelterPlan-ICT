from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required to run the admission gate") from exc


ROOT = Path(__file__).resolve().parents[1]
REGION_IDS = ("shiqi", "east_district", "xiaolan")
DEFAULT_MAX_SNAP_DISTANCE_M = 200.0


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def truth(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是"}


def number(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def first_reason(reasons: Iterable[str]) -> str:
    return "; ".join(dict.fromkeys(reason for reason in reasons if reason))


def evaluate_row(
    row: dict[str, str],
    *,
    region_meta: dict[str, Any],
    snap: dict[str, str] | None,
    geocode: dict[str, str] | None,
    source_disposition: dict[str, str] | None,
    duplicate_rows: list[dict[str, str]],
    max_snap_distance_m: float = DEFAULT_MAX_SNAP_DISTANCE_M,
) -> dict[str, Any]:
    demo_blockers: list[str] = []
    verified_blockers: list[str] = []
    fatal_blockers: list[str] = []

    if not row.get("shelter_id"):
        fatal_blockers.append("missing_shelter_id")
    if not row.get("source_row_excel"):
        fatal_blockers.append("missing_source_row")
    if row.get("region_id") != region_meta["region_id"]:
        fatal_blockers.append("region_id_mismatch")
    if not row.get("source_file") or not row.get("source_sheet"):
        fatal_blockers.append("missing_source_provenance")

    lon = number(row.get("lon_wgs84"))
    lat = number(row.get("lat_wgs84"))
    if lon is None or lat is None or not 105 <= lon <= 125 or not 18 <= lat <= 30:
        fatal_blockers.append("coordinates_missing_or_out_of_bounds")

    snap_distance = number((snap or {}).get("snap_distance_m"))
    snap_ok = truth((snap or {}).get("snap_ok"))
    if snap_distance is None:
        fatal_blockers.append("missing_shelter_snap_evidence")
    elif snap_distance > max_snap_distance_m or not snap_ok:
        fatal_blockers.append(f"shelter_snap_too_far:{snap_distance:.3f}m")

    if not region_meta["enabled"]:
        demo_blockers.append("region_disabled")
    if not truth(row.get("active")):
        demo_blockers.append("source_record_inactive")
        verified_blockers.append("source_record_inactive")
    if row.get("coordinate_status") != "verified":
        demo_blockers.append("coordinate_not_verified")
        verified_blockers.append("coordinate_status_not_verified")
    if row.get("identity_status") != "verified":
        demo_blockers.append("identity_not_verified")
        verified_blockers.append("identity_status_not_verified")
    if row.get("field_mapping_status") != "verified":
        verified_blockers.append("field_mapping_not_verified")
    if row.get("current_open_status") != "confirmed":
        demo_blockers.append("current_open_status_assumed")
        verified_blockers.append("current_open_status_not_confirmed")
    if row.get("capacity_verification_status") != "verified":
        verified_blockers.append("capacity_not_verified")
    if row.get("capacity_source_status") == "demo_imputed":
        demo_blockers.append("capacity_demo_imputed")
        verified_blockers.append("capacity_is_demo_imputed")
    if row.get("data_status") != "VERIFIED":
        verified_blockers.append("data_status_not_verified")
    if region_meta.get("boundary_status") != "official":
        demo_blockers.append("boundary_not_official")
        verified_blockers.append("boundary_not_official")
    duplicate_candidate = (
        truth(row.get("duplicate_source_name_candidate"))
        or truth(row.get("duplicate_source_specific_address_candidate"))
        or bool(duplicate_rows)
    )
    if duplicate_candidate:
        demo_blockers.append("duplicate_candidate_requires_review")
        verified_blockers.append("duplicate_candidate_unresolved")
    if (source_disposition or {}).get("review_decision") == "needs_review":
        demo_blockers.append("source_disposition_needs_review")
        verified_blockers.append("source_disposition_not_verified")
    if (geocode or {}).get("status") in {"candidate", "unresolved"}:
        demo_blockers.append("geocode_candidate_or_unresolved")
        verified_blockers.append("geocode_not_verified")

    structural_ok = not fatal_blockers
    eligible_for_demo = bool(
        region_meta["enabled"] and truth(row.get("active")) and structural_ok
    )
    eligible_for_verified = bool(
        eligible_for_demo
        and not demo_blockers
        and not verified_blockers
        and row.get("verified_status") == "verified"
    )
    if fatal_blockers:
        admission_class = "excluded"
    elif eligible_for_demo:
        admission_class = "demo_eligible_with_warnings"
    else:
        admission_class = "needs_review"

    return {
        "region_id": row.get("region_id", ""),
        "shelter_id": row.get("shelter_id", ""),
        "source_row_excel": row.get("source_row_excel", ""),
        "admission_class": admission_class,
        "eligible_for_demo": eligible_for_demo,
        "eligible_for_verified": eligible_for_verified,
        "eligible_for_normal_planning": eligible_for_demo,
        "region_enabled": bool(region_meta["enabled"]),
        "active_in_source": truth(row.get("active")),
        "source_review_decision": row.get("review_decision", ""),
        "identity_status": row.get("identity_status", ""),
        "field_mapping_status": row.get("field_mapping_status", ""),
        "coordinate_status": row.get("coordinate_status", ""),
        "geocode_status": (geocode or {}).get("status", row.get("geocode_status", "")),
        "capacity_source_status": row.get("capacity_source_status", ""),
        "capacity_verification_status": row.get("capacity_verification_status", ""),
        "current_open_status": row.get("current_open_status", ""),
        "snap_distance_m": snap_distance,
        "snap_ok": snap_ok,
        "demo_blockers": first_reason(demo_blockers),
        "verified_blockers": first_reason(verified_blockers),
        "fatal_blockers": first_reason(fatal_blockers),
        "reviewer_notes": row.get("reviewer_notes", ""),
        "source_disposition_notes": (source_disposition or {}).get("reviewer_notes", ""),
        "data_version": region_meta["data_version"],
    }


def load_region_meta(root: Path) -> dict[str, dict[str, Any]]:
    cfg = yaml.safe_load((root / "configs/regions.yaml").read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for region_id in REGION_IDS:
        adapter = yaml.safe_load(
            (root / "configs/module_a_v2_b" / f"{region_id}.yaml").read_text(encoding="utf-8")
        )
        boundary_path = root / "data/processed/regions" / region_id / "boundary_metadata.json"
        boundary = json.loads(boundary_path.read_text(encoding="utf-8"))
        result[region_id] = {
            "region_id": region_id,
            "enabled": bool(cfg.get("regions", {}).get(region_id, {}).get("enabled", True)),
            "data_version": adapter["project"]["data_version"],
            "boundary_status": boundary.get("boundary_status"),
            "max_snap_distance_m": float(
                cfg.get("project", {}).get("max_snap_distance_m", DEFAULT_MAX_SNAP_DISTANCE_M)
            ),
        }
    return result


def run_gate(root: Path = ROOT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    region_meta = load_region_meta(root)
    dispositions = {
        (row.get("region_id", ""), row.get("shelter_id", "")): row
        for row in read_csv(root / "data/interim/regions/source_disposition.csv")
    }
    duplicate_map: defaultdict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(root / "data/interim/regions/duplicate_candidates_all.csv"):
        if row.get("region_id") and row.get("shelter_id"):
            duplicate_map[(row["region_id"], row["shelter_id"])].append(row)

    records: list[dict[str, Any]] = []
    region_summaries: list[dict[str, Any]] = []
    for region_id in REGION_IDS:
        region_root = root / "data/processed/regions" / region_id
        shelters = read_csv(region_root / "shelters_all.csv")
        snaps = {
            row.get("shelter_id", ""): row
            for row in read_csv(region_root / "shelter_snap_audit.csv")
        }
        geocodes = {
            row.get("shelter_id", ""): row
            for row in read_csv(root / "data/interim/regions" / region_id / "shelter_geocode_audit.csv")
        }
        for row in shelters:
            records.append(
                evaluate_row(
                    row,
                    region_meta=region_meta[region_id],
                    snap=snaps.get(row.get("shelter_id", "")),
                    geocode=geocodes.get(row.get("shelter_id", "")),
                    source_disposition=dispositions.get((region_id, row.get("shelter_id", ""))),
                    duplicate_rows=duplicate_map[(region_id, row.get("shelter_id", ""))],
                    max_snap_distance_m=region_meta[region_id]["max_snap_distance_m"],
                )
            )
        quality = json.loads((region_root / "quality_summary.json").read_text(encoding="utf-8"))
        region_records = [record for record in records if record["region_id"] == region_id]
        counts = Counter(record["admission_class"] for record in region_records)
        region_summaries.append(
            {
                "region_id": region_id,
                "enabled": region_meta[region_id]["enabled"],
                "data_version": region_meta[region_id]["data_version"],
                "release_status": quality.get("release_status"),
                "data_status": quality.get("data_status"),
                "source_rows": len(shelters),
                "demo_eligible": sum(record["eligible_for_demo"] for record in region_records),
                "normal_planning_candidates": sum(
                    record["eligible_for_normal_planning"] for record in region_records
                ),
                "verified_eligible": sum(record["eligible_for_verified"] for record in region_records),
                "admission_counts": dict(counts),
                "quality_summary": {
                    "demand_nodes": quality.get("travel_matrix", {}).get("demand_nodes"),
                    "od_rows": quality.get("travel_matrix", {}).get("rows"),
                    "reachable_pairs": quality.get("travel_matrix", {}).get("reachable_pairs"),
                    "reason_counts": quality.get("travel_matrix", {}).get("reason_counts", {}),
                    "blockers": quality.get("blockers", []),
                },
            }
        )
    summary = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "source_data_modified": False,
            "manifest_modified": False,
            "verified_upgrade_automatic": False,
            "disabled_regions_used_for_normal_admission": False,
            "normal_candidate_definition": (
                "region_enabled AND active_in_source AND structural checks pass; "
                "DEMO_ONLY warnings remain attached"
            ),
        },
        "totals": {
            "source_rows": len(records),
            "demo_eligible": sum(record["eligible_for_demo"] for record in records),
            "normal_planning_candidates": sum(
                record["eligible_for_normal_planning"] for record in records
            ),
            "verified_eligible": sum(record["eligible_for_verified"] for record in records),
            "needs_review": sum(record["admission_class"] == "needs_review" for record in records),
            "excluded": sum(record["admission_class"] == "excluded" for record in records),
        },
        "regions": region_summaries,
        "records": records,
    }
    return records, summary


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "region_id",
        "shelter_id",
        "source_row_excel",
        "admission_class",
        "eligible_for_demo",
        "eligible_for_verified",
        "eligible_for_normal_planning",
        "region_enabled",
        "active_in_source",
        "source_review_decision",
        "identity_status",
        "field_mapping_status",
        "coordinate_status",
        "geocode_status",
        "capacity_source_status",
        "capacity_verification_status",
        "current_open_status",
        "snap_distance_m",
        "snap_ok",
        "demo_blockers",
        "verified_blockers",
        "fatal_blockers",
        "reviewer_notes",
        "source_disposition_notes",
        "data_version",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def quality_report(summary: dict[str, Any]) -> str:
    lines = [
        "# ShelterPlan-Eval Stage 1 数据质量审计与准入",
        "",
        f"- 生成时间（UTC）：{summary['generated_at_utc']}",
        "- 输入快照：A v2 已有处理结果，只读",
        "- 准入策略：demo 与 verified 分离；不自动升级 verified；不静默删除源记录",
        "- active 只表示源记录的 active 字段；不等同于当前开放状态。正常候选必须同时满足 region enabled、active 和结构校验；DEMO_ONLY 警告继续保留。",
        "",
        "## 总体计数",
        "",
        "| 指标 | 数量 |",
        "| --- | ---: |",
        f"| 源记录 | {summary['totals']['source_rows']} |",
        f"| 可用于受控 DEMO_ONLY 的记录 | {summary['totals']['demo_eligible']} |",
        f"| 正常求解候选（受控 DEMO_ONLY） | {summary['totals']['normal_planning_candidates']} |",
        f"| 可用于 VERIFIED 的记录 | {summary['totals']['verified_eligible']} |",
        f"| needs_review | {summary['totals']['needs_review']} |",
        f"| excluded | {summary['totals']['excluded']} |",
        "",
        "## 分区域准入结果",
        "",
        "| 区域 | 启用 | 源记录 | Demo 可用 | Verified 可用 | needs_review | excluded |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for region in summary["regions"]:
        counts = region["admission_counts"]
        lines.append(
            f"| {region['region_id']} | {region['enabled']} | {region['source_rows']} | "
            f"{region['demo_eligible']} | {region['verified_eligible']} | "
            f"{counts.get('needs_review', 0)} | {counts.get('excluded', 0)} |"
        )
    lines.extend(
        [
            "",
            "## 证据与解释",
            "",
            "- 石岐和东区可以继续用于明确标注 DEMO_ONLY 的受控软件演示；这不代表身份、精确坐标、容量或开放状态已现场核验。",
            "- 小榄区域保持 disabled；所有小榄记录不能进入普通 Demo admission，只能用于禁用区域安全拒绝或后续人工核验。",
            "- 小榄 shelter_id zs_61811ada3c 的 Snap 距离超过 200m，按记录级 excluded 处理；源记录仍保留。",
            "- 三地区当前没有任何记录获得 verified admission。原因包括 needs_review 身份、approximate 坐标、assumed_open、demo boundary、缺少独立容量证据或 DEMO_ONLY 数据状态。",
            "- 东区重复地址候选和石岐容量中位数插补只作为审计证据，不自动删除、不自动去重、不自动升级。",
            "",
            "## 后续处置建议",
            "",
            "1. 对 needs_review 记录建立人工核验表，补充身份、字段映射、坐标、开放状态和容量证据。",
            "2. 只有在独立证据补齐后，生成新的 A 数据快照、重新计算受影响 OD/图层并更新 manifest；不得原地改旧 manifest。",
            "3. 小榄先修正异常坐标并重新生成该区域全部受影响派生物；在此之前不用于正常规划题。",
            "4. 官方边界和门户批次数据补齐后，再单独评估是否能进入 verified。",
            "5. A v2 原始 TIFF/PBF 若仍是 LFS pointer，先完成对象可读性和存储迁移，再进行需要原始数据重建的任务。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the read-only A v2 admission gate")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--eligibility", default="sources/eligibility.csv")
    parser.add_argument("--summary", default="reports/admission_summary.json")
    parser.add_argument("--report", default="reports/quality_audit.md")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    records, summary = run_gate(root)
    write_csv(root / args.eligibility, records)
    (root / args.summary).parent.mkdir(parents=True, exist_ok=True)
    (root / args.summary).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / args.report).parent.mkdir(parents=True, exist_ok=True)
    (root / args.report).write_text(quality_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
