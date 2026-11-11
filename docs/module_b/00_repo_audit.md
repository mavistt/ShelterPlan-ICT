# Module B Gate 0 repository audit

Audit date: 2026-11-11 (Asia/Shanghai). The supplied checkout has no `.git` metadata, so no Git status, origin, or revision can be established. Module A data, scripts, configuration, and documentation were kept read-only.

## Initial baseline snapshot

The first Gate 0 inventory was performed before the current A handoff files arrived. At that point, the configured processed GeoJSON, Parquet, and manifest paths were missing, and the baseline A GIS tests could not be collected because the active Python environment lacked pytest. That historical state is retained in the original project notes, not presented as current status.

## Current follow-up

The configured A handoff now includes shelters, demand nodes, travel matrix, manifest, and A7 summary. The read-only adapter loaded 7,965 demand nodes, 24 shelters, and 191,160 OD rows for `zhongshan_pilot_v0.1`. All consumed processed files and `configs/pilot.yaml` match their declared hashes. A7 is still `DEMO_ONLY`, and one 922,251,437-byte raw raster is a 134-byte Git LFS pointer. See [A_DATA_INTEGRATION_REPORT.md](A_DATA_INTEGRATION_REPORT.md) for checksums and detailed findings.

The A GIS tests and Module B tests passed after installing the needed packages in the ignored project-local `.venv`. See [PROGRESS.md](PROGRESS.md) for the latest test and experiment records. This repository folder has no `.git` directory, so filesystem review is the available change-review method.

## Scope and safeguards

The A handoff contract uses `zhongshan_pilot_v0.1`, WGS84 vector exchange, network distances derived in `EPSG:32649`, 1.1 m/s walking speed, and scenario defaults of demand ratio 0.2 / walk threshold 15 minutes. These scenario defaults are not legal standards. The A7 audit still reports 24 unverified shelters and prohibits claims of verified coverage or current capacity. All A-backed Module B outputs therefore carry `DEMO_ONLY` and `PASS_DEMO` at most.

Module B code, reports, fixtures, dependencies, and runtime outputs are under the B-owned paths. No A-owned files were edited.
