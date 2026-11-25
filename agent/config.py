"""Harness configuration with explicit, bounded defaults."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_A_V2_CONFIG = PROJECT_ROOT / "configs" / "module_a_v2_b" / "shiqi.yaml"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def defaults_from_pilot_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read scenario defaults from an A config, with a safe bounded fallback."""
    candidate = Path(path) if path else DEFAULT_A_V2_CONFIG
    fallback = {
        "data_version": "shelterplan_module_a_v2.0-demo__shiqi",
        "region_id": "shiqi",
        "population_demand_ratio": 0.2,
        "max_walk_time_min": 15.0,
    }
    try:
        document = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        project = document["project"]
        defaults = document.get("scenario_defaults") or fallback
        declared_region = project.get("region_id")
        return {
            "data_version": str(project["data_version"]),
            # The explicit legacy pilot adapter has no region_id; preserve
            # that fact so legacy callers do not accidentally send Shiqi's
            # region binding with a v1 data_version.
            "region_id": (
                str(declared_region)
                if declared_region is not None
                else (fallback["region_id"] if path is None else None)
            ),
            "population_demand_ratio": float(defaults.get("population_demand_ratio", 0.2)),
            "max_walk_time_min": float(defaults.get("max_walk_time_min", 15.0)),
        }
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        return fallback


@dataclass(frozen=True)
class HarnessConfig:
    """All timeouts are bounded so no tool call can hang a conversation."""

    b_base_url: str = "http://127.0.0.1:8000"
    data_version: str = "zhongshan_pilot_v0.1"
    default_region_id: str | None = None
    expected_data_status: str = "DEMO_ONLY"
    default_population_demand_ratio: float = 0.2
    default_max_walk_time_min: float = 15.0

    connect_timeout_s: float = 5.0
    read_timeout_s: float = 30.0
    submit_timeout_s: float = 20.0
    job_timeout_s: float = 180.0
    poll_initial_s: float = 0.25
    poll_max_s: float = 2.0

    max_transport_retries: int = 2
    max_job_retries: int = 1
    retry_backoff_s: float = 0.5

    summary_max_bytes: int = 20 * 1024
    evidence_page_default: int = 50
    evidence_page_max: int = 200

    trace_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "outputs" / "module_c" / "trace")
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)
    send_region_id_to_b: bool = False

    @classmethod
    def from_env(cls) -> "HarnessConfig":
        configured_path = os.environ.get("SHELTERPLAN_PILOT_CONFIG") or str(DEFAULT_A_V2_CONFIG)
        pilot = defaults_from_pilot_config(configured_path)
        send_region = os.environ.get("SHELTERPLAN_B_SEND_REGION_ID", "1").strip().lower() not in {
            "0", "false", "no", "off",
        }
        return cls(
            b_base_url=os.environ.get("SHELTERPLAN_B_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            data_version=os.environ.get("SHELTERPLAN_DATA_VERSION", pilot["data_version"]),
            default_region_id=os.environ.get("SHELTERPLAN_REGION_ID", pilot.get("region_id")),
            expected_data_status=os.environ.get("SHELTERPLAN_DATA_STATUS", "DEMO_ONLY"),
            default_population_demand_ratio=_env_float(
                "SHELTERPLAN_DEFAULT_RATIO", pilot["population_demand_ratio"]),
            default_max_walk_time_min=_env_float(
                "SHELTERPLAN_DEFAULT_WALK_MIN", pilot["max_walk_time_min"]),
            connect_timeout_s=_env_float("SHELTERPLAN_CONNECT_TIMEOUT_S", 5.0),
            read_timeout_s=_env_float("SHELTERPLAN_READ_TIMEOUT_S", 30.0),
            submit_timeout_s=_env_float("SHELTERPLAN_SUBMIT_TIMEOUT_S", 20.0),
            job_timeout_s=_env_float("SHELTERPLAN_JOB_TIMEOUT_S", 180.0),
            poll_initial_s=_env_float("SHELTERPLAN_POLL_INITIAL_S", 0.25),
            poll_max_s=_env_float("SHELTERPLAN_POLL_MAX_S", 2.0),
            max_transport_retries=_env_int("SHELTERPLAN_MAX_TRANSPORT_RETRIES", 2),
            max_job_retries=_env_int("SHELTERPLAN_MAX_JOB_RETRIES", 1),
            retry_backoff_s=_env_float("SHELTERPLAN_RETRY_BACKOFF_S", 0.5),
            summary_max_bytes=_env_int("SHELTERPLAN_SUMMARY_MAX_BYTES", 20 * 1024),
            evidence_page_default=_env_int("SHELTERPLAN_EVIDENCE_PAGE_DEFAULT", 50),
            evidence_page_max=_env_int("SHELTERPLAN_EVIDENCE_PAGE_MAX", 200),
            trace_dir=Path(os.environ.get(
                "SHELTERPLAN_TRACE_DIR", PROJECT_ROOT / "outputs" / "module_c" / "trace")),
            project_root=Path(os.environ.get("SHELTERPLAN_PROJECT_ROOT", PROJECT_ROOT)),
            send_region_id_to_b=send_region,
        )
