# A7 independent GIS quality audit

- Audited UTC: `2026-11-11T03:29:52.619680+00:00`
- Verdict: **DEMO_ONLY**
- Demo ready: **True**
- Real pilot accepted: **False**
- Shelter source rows / portal catalog count: `453` / `906`; update values: `['2024年度']`
- Portal count interpretation: `Catalog metadata; two 2024 attachment batches are shown and their first 20 preview rows match; full unique-record equivalence is not yet verified.`
- Full batch payload reconciliation current and matches local inputs: `False`

## Population

- CRS / NoData: `EPSG:4326` / `-99999.0`
- Non-negative valid raster cells: `8151`
- Raster sum / demand-node sum: `551028.703` / `551028.703`
- Demand points within 100 m of analysis boundary: `321`

## Pilot source-to-geocode reconciliation

- East District source / pilot input rows: `31` / `30`
- Held before geocoding: `[{'shelter_id': 'zs_63f1d8e4ef', 'source_row_excel': 75}]`
- Geocode candidates / documented exclusions: `24` / `6`; dispositions reconcile: `True`

## Shelters

- Included: `24`; unverified: `24`; approximate/unknown coordinates: `24`
- Registered capacity sum (unverified): `11332` persons
- Coordinate evidence missing: `24`; current capacity evidence missing: `24`

## Network and matching

- Graph nodes/edges: `9040` / `26822`
- Connected components: `19`; largest component share: `0.9912`
- Demand snap within configured threshold: `7227/7965`; over threshold: `738`
- Shelter snap within configured threshold: `24/24`

## Travel matrix

- Rows / expected: `191160` / `191160`
- Reachable pairs: `172704`; unreachable pairs: `18456`
- Pairs within configured 15 min scenario threshold: `3272`
- Demand nodes with no pair within threshold: `5599`
- Unreachable reason counts:
  - `ok`: 172704
  - `demand_snap_too_far`: 17712
  - `no_path`: 744

## Real-pilot blockers
- Downloaded shelter workbook has 453 rows while portal catalog metadata reports 906; two 2024 batches share the same first 20 preview rows, but full-batch reconciliation is missing or inconclusive.
- 24 included shelters lack verified status.
- 24 shelter coordinates use approximate/demo methods.
- 24 shelter points lack an approved WGS84 method and coordinate source URL.
- 24 shelter capacities lack current verification evidence.
- Analysis polygon is not an authoritative administrative boundary.
- Shelter register records are from 2024; current capacity/open status is not established.

## Errors
- None

## Warnings
- 24/24 shelters are not verified; results are DEMO_ONLY.
- 1 East District source records are held out before geocoding: [{'shelter_id': 'zs_63f1d8e4ef', 'source_row_excel': 75}].
- Analysis area is not documented as an official administrative boundary.

This audit checks file structure, arithmetic consistency, matching distances and declared provenance. It cannot prove a shelter is currently open, validate its capacity, or turn an approximate coordinate into a surveyed location.
