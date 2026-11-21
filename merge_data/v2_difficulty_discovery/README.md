# V2 difficulty discovery and controlled stress benchmark

V2 searches for Greedy-suboptimal planning cases on the retained Module A-derived pilot graph and separately measures fully synthetic stress cases. It does not change Module B's public 12-candidate request limit. All GIS-anchored outputs are `DEMO_ONLY`; investment costs and added capacity are `HYPOTHETICAL`; synthetic fixtures are labeled `FULLY_SYNTHETIC_STRESS`.

## Retained evidence

- `REAL-000-TARGETED-TRAP` and `REAL-081` complete result JSONs, including Greedy, MIP, independent enumerated max-flow Oracle and verifier rows.
- Complete synthetic stress Oracle aggregate, generated fixtures, screening records, seed/config, ablations and original V2 reports.
- Compact phase-B search summary with all 100 screening records, all nine completed-case metrics and Oracle fields, plus SHA-256/count evidence for omitted non-key assignment/check arrays. The original 53.5 MB aggregate and seven redundant per-case dumps are listed in `../EXCLUDED_ASSETS.csv`.

The independent Oracle enumerates feasible candidate subsets and runs integer-scaled maximum flow with NetworkX Dinitz; it does not use the Module B MIP output as ground truth. Greedy and MIP use the same source graph, budget and eligibility rules.

## Reproduce

From a clean disposable checkout of the Module B benchmark branch, install `requirements-b.txt`, verify the processed A files against `data/manifests/pilot_manifest.json`, then run:

```powershell
python merge_data/v2_difficulty_discovery/tools/analyze_real_structure.py
python merge_data/v2_difficulty_discovery/tools/run_grounded_search.py
python merge_data/v2_difficulty_discovery/tools/run_synthetic_stress.py
python merge_data/v2_difficulty_discovery/tools/generate_reports.py
python -m pytest merge_data/v2_difficulty_discovery/tests -q -p no:cacheprovider
```

The generator scripts write fixed output paths and can replace reports/fixtures, so use a disposable worktree when regenerating. `phase_b_grounded_search.json` is an ignored generated output; the curated input is `phase_b_grounded_search_summary.json`. The generated output contains the full run payload again.

The retained processed A data are required for GIS-grounded reruns. No A data copy is stored here. Rebuilding Module A processing from its original raster additionally requires the authorized Git LFS object. See the parent `merge_data/README.md` for source paths, status labels and V1/V2/V3 reproducibility boundaries.
