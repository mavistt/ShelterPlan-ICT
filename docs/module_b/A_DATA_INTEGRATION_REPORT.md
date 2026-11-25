# Module A data integration audit

Audit run: `202dcf95-5fe2-452d-a659-a466e04edab1` (2026-11-11 UTC). The audit reads A files and records evidence under Module B outputs; it does not modify Module A data, scripts, configuration, or documentation.

## Readiness

All five configured handoff files are present. The loaded version is `zhongshan_pilot_v0.1`, and the manifest, `configs/pilot.yaml`, processed files, and A7 report agree on that version. A7 remains `DEMO_ONLY`, with `real_pilot_accepted=false` and seven blockers. The adapter therefore keeps every result in demo mode.

| Input | Bytes | SHA-256 | Parsed rows |
|---|---:|---|---:|
| `data/processed/shelters_geocoded.geojson` | 28,304 | `879babf9a3f5c59336809f06ee7ee16d527b767e6a8fdc35a3132055fd572318` | 24 shelters |
| `data/processed/demand_nodes.geojson` | 2,633,782 | `b313e0376ca41664c16b4a01baf323c29fbdd54a19380b49036eb025816b6c46` | 7,965 demand nodes |
| `data/processed/travel_matrix.parquet` | 6,077,300 | `e651edf6eb3a17e7ed556b100add03d7d44bf5367b2e6f2d639f8d514a163f9c` | 191,160 OD rows |
| `data/manifests/pilot_manifest.json` | 13,722 | `4b4b58e246c7b98ac9a76069420557500dd325df422f8e6a43285d17b57c02e4` | 42 declared files |
| `data/processed/a7_quality_summary.json` | 4,457 | `50e2aa158c9e91cef1d6b42c8317141b81410c2cd088c3312c0d0451a773086c` | A7 audit |

Module B's configured-source load also validated the SHA-256 and byte count of all three consumed processed artifacts and `configs/pilot.yaml`. GeoJSON coordinates are WGS84 (`CRS84` longitude/latitude); the adapter accepts this equivalent representation. IDs are unique, OD pairs are unique and form the full 7,965 × 24 relation, and all OD foreign keys match. Reachable rows have valid times; unreachable rows have null times. The loader streams Parquet batches rather than materializing a pandas copy.

The full manifest audit matched 41 of 42 declared files. The sole mismatch is `data/raw/chn_pop_2025_CN_100m_R2025A_v1.tif`: the manifest expects 922,251,437 bytes and SHA-256 `99b2bcebaf173d0d9f9fc43b45a122e896bcd0ac2190d1882a1b6f2ab0941913`; the local 134-byte file is a Git LFS pointer with SHA-256 `9403c4f51ab98c2b22262b6ded208fac2155d64808ae0172db796ed62cb35be5`. The processed outputs are hash-valid, but the full raw-data build cannot be reproduced from this checkout.

## A7 findings affecting interpretation

- Estimated population: 551,028.703 across 7,965 nodes. The configured demand ratio of 0.2 implies 110,205.741 scenario demand.
- All 24 shelters are unverified; their coordinates are approximate and lack approved coordinate evidence. Their nominal total capacity of 11,332 is unverified.
- The analysis polygon is not an official administrative boundary, and current shelter capacity/open status is not established.
- A7 reports 172,704 reachable OD pairs and 18,456 unreachable pairs. Of the latter, 17,712 are `demand_snap_too_far` and 744 are `no_path`.
- At a 15-minute threshold, 3,272 OD rows connect to 2,366 demand nodes; 5,599 nodes have no candidate. The nodes with candidates account for about 39.12% of estimated population.
- A7 lists seven pilot blockers, including the unresolved 453-versus-906 shelter-register reconciliation and all 24 shelter verification/coordinate/capacity gaps.

The machine-readable audit run ID is `202dcf95-5fe2-452d-a659-a466e04edab1`; detailed local run JSON is intentionally excluded from the handoff package. No `.git` metadata exists in either local project root, so an origin URL and source revision could not be established.
