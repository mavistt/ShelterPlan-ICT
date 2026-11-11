# Module B benchmark evaluation

All-run ID: `3dda8a61-8355-4dbf-9eee-59063af8d991`. The benchmark contains 40 deterministic synthetic cases across ten template families. Families are assigned to exactly one split:

| Split | Cases | Families |
|---|---:|---|
| dev | 16 | capacity competition, network disconnection, walk-threshold cutoff, demand ratio |
| validation | 12 | budget expansion, exclusive group, disabled shelter |
| test | 12 | scenario-version mismatch, invalid input, verifier fault |

The family separation prevents a template family from appearing in more than one split. The validation and test cases are internal synthetic checks, not external or field validation.

## All-run results

| Metric | Result |
|---|---:|
| SkillGraphVerifier verified task success | 100% (40/40) |
| Safe input rejection | 100% on applicable cases (8/8) |
| Injected fault detection recall | 100% |
| False-positive clean cases | 0 |
| Invalid published outputs | 0 |
| Feasibility rate | 100% |
| Mean GreedyExpansion gap vs independent budget oracle | 0.0481% |
| Mean weighted walk time | 7.051 minutes |

Each ordinary case compares `StaticWorkflow`, `GreedyExpansion`, `SkillGraphOnly` (shadow and quarantined), and `SkillGraphVerifier`. GT-A uses a separate Dinic maximum-flow implementation for the integer primary objective; budget cases enumerate valid option subsets and enforce exclusive groups. GT-B is the registered graph execution and independent result certificate. GT-C remains `NOT_VALIDATED` because no external field authority was supplied. `FlatToolAgent` remains `NOT_RUN`; no AgentArts trace or model configuration was provided. Token cost is N/A because no LLM calls were made.

The ablation `SkillGraphOnly` deliberately does not run the verifier, so its zero verifier-pass count is expected and its output is not publishable. The benchmark's publication metric only checks local quarantine flags; a public-channel escape rate cannot be measured in this local system.

## Artifacts and commands

- All 40 cases: run `3dda8a61-8355-4dbf-9eee-59063af8d991`; machine-readable JSON, CSV, and Markdown records remain under local `outputs/module_b/bench/all/` and are excluded from the GitHub handoff package. The summary above is the handoff record.
- dev 16: `2b40e5a8-912e-4e33-ad54-dd5e46381a61`.
- validation 12: `af48ffaa-2314-43ea-be2c-f0a08c9dc609`.
- test 12: `951a09d2-8b9d-434c-8d8c-fa2c8b2b6722`.
- Refinement run `4bab8457-cbce-49c4-9c2d-2046f460f3e8`: its machine-readable record remains under local `outputs/module_b/refinement/` and is excluded from the GitHub handoff package. The registered-template fallback passed all 12 family-separated synthetic validation cases. It remains `KEEP_REVIEW_REQUIRED`; synthetic validation is not external validation and does not authorize production promotion.

Reproduce all splits with:

```powershell
.venv\Scripts\python.exe -m backend.benchmark --split all --output outputs/module_b/bench/all
.venv\Scripts\python.exe -m backend.benchmark --split dev --output outputs/module_b/bench/dev
.venv\Scripts\python.exe -m backend.benchmark --split validation --output outputs/module_b/bench/validation
.venv\Scripts\python.exe -m backend.benchmark --split test --output outputs/module_b/bench/test
```

The benchmark is one deterministic run per case; it does not report confidence intervals or independent external replication.
