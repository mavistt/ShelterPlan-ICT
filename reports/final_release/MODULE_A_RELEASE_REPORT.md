# Module A / ShelterPlan-Eval Release

This is the A-only handoff package for the current iteration. It freezes the existing Stage 2.1 pool, adds measured A-side structural evidence, and supplies pure GT/evaluation contracts. It does not run or claim B, C, LLM, Agent, or frontend integration.

## Release contents

- Task dataset: `24` frozen tasks, grouped by scenario family.
- Evaluation set: `24` tasks; the eight historical Stage 3 cases are marked `pilot_replay=true`.
- Regions: `{'east_district': 13, 'shiqi': 11}`.
- Expected behaviors: `{'CLARIFY': 1, 'EXECUTE': 13, 'REJECT': 9, 'SAFE_STOP': 1}`.
- Difficulty evidence: `a2-difficulty-discovery-1.0`; all evidence is A-side structural/reachability evidence.
- Split policy: the available pool is too small for a training split; all 24 tasks are a compact frozen evaluation set. No task is silently reused as training data.

## Evidence boundary

- A v2 content identity is `dataset-content-v1` with stable dataset hash `609a14546cd10baa28c455a59de7503d2010ee980b502cad27451435f97d730f`.
- All admitted regions remain `DEMO_ONLY`; the release does not upgrade them to `VERIFIED`.
- Raw Manifest comparison remains blocked by 84 CRLF-only transport differences while canonical content matches. The raw files and manifests were not rewritten.
- Real GIS cases have `oracle.status=NOT_PROVEN`: the A-side flow engine is independently tested on small fixtures, but no full GIS portfolio optimality proof is claimed here.
- `ST21-shiqi-004` has a multi-stage structure but no damaged/disabled shelter input; its recovery difficulty is structural-only until a runtime lineage trace exists.

## Reproduction

```powershell
python -B -m benchmark.shelterplan_eval.release
```
The command regenerates only A-owned release artifacts under `reports/final_release/` and never invokes B/C/LLM.

## Artifact hashes

- `reports/final_release/difficulty_discovery.json`: `5dc42a0c09b51bf6cca2027593bc6349282fc5cce153b5d10c38e2e338ffe84a`
- `reports/final_release/difficulty_discovery.md`: `0a0b700a40c81dc14eb866eed3f24a7beef06aca9cff1c46cf025c0ba22a177e`
- `reports/final_release/evaluation_set.json`: `b67fa0994b2663ab207e230375410705f66cce5acef1a511e2c212fc0fdc0a38`
- `reports/final_release/task_dataset.json`: `a2e2a846b903e738e6bdece6e9835ea8bd2b847eb515870dc2e3dfee9ef9ac4b`
