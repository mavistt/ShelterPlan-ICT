# Snap distance and walk-time sensitivity

- Run UTC: `2026-11-11T01:32:00.280666+00:00`
- Base snap limit: `200 m`
- Base time scenario: `15 min`
- Candidate routable pairs evaluated: `190296`
- Base snap route keys reconciled with matrix: `172704`; max distance/time deltas `0.000000 m` / `0.00000000 min`
- Demand population estimate in analysis area: `551028.703`

| Snap limit (m) | Time limit (min) | Routable pairs | Demand nodes with a candidate | Population with a candidate | Population share |
|---:|---:|---:|---:|---:|---:|
| 200 | 10 | 1289 | 1176 | 114780.2 | 20.8% |
| 200 | 15 | 3272 | 2366 | 215542.3 | 39.1% |
| 200 | 20 | 6427 | 3350 | 286091.7 | 51.9% |
| 200 | 30 | 16211 | 4869 | 384409.1 | 69.8% |
| 300 | 10 | 1306 | 1193 | 115612.9 | 21.0% |
| 300 | 15 | 3319 | 2405 | 217024.9 | 39.4% |
| 300 | 20 | 6530 | 3431 | 288085.8 | 52.3% |
| 300 | 30 | 16509 | 5035 | 387806.9 | 70.4% |
| 400 | 10 | 1306 | 1193 | 115612.9 | 21.0% |
| 400 | 15 | 3320 | 2406 | 217026.6 | 39.4% |
| 400 | 20 | 6533 | 3434 | 288090.3 | 52.3% |
| 400 | 30 | 16524 | 5049 | 387826.6 | 70.4% |
| 500 | 10 | 1306 | 1193 | 115612.9 | 21.0% |
| 500 | 15 | 3320 | 2406 | 217026.6 | 39.4% |
| 500 | 20 | 6534 | 3435 | 288091.7 | 52.3% |
| 500 | 30 | 16527 | 5052 | 387830.4 | 70.4% |
| 700 | 10 | 1306 | 1193 | 115612.9 | 21.0% |
| 700 | 15 | 3320 | 2406 | 217026.6 | 39.4% |
| 700 | 20 | 6534 | 3435 | 288091.7 | 52.3% |
| 700 | 30 | 16531 | 5056 | 387836.1 | 70.4% |

## Interpretation

- Keep the configured `200.0 m` snap limit: expanding to `300 m` at the default time threshold adds only `39` demand cells and `0.27` percentage points of candidate population. A longer connector may cross a barrier, so this small modeled gain does not justify changing the base limit.
- Time threshold has a much larger effect: at `200.0 m`, changing from the default `15 min` to `30 min` raises population with at least one candidate from `39.1%` to `69.8%`.
- These are candidate reachability figures. They do not model capacity competition or confirm that people can safely access a road or shelter.

These are sensitivity scenarios, not evidence that a longer off-network connector is safe or walkable. A candidate means at least one network path exists and meets the selected time limit; it does not account for facility capacity, allocation competition, barriers between a population-cell centroid and the nearest road, or shelter verification. The 200 m configured base result remains unchanged.
