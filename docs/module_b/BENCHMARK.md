# ShelterPlan-Bench

The deterministic case catalog now contains 40 synthetic cases over ten template families with family-separated dev/validation/test splits (16/12/12). The latest all-run report is `3dda8a61-8355-4dbf-9eee-59063af8d991`.

Its independent Dinic maximum-flow oracle matches the static solver's integer primary objective. All 40 graph-plus-verifier runs passed, injected-fault recall is 1.0 with zero clean-case false positives, safe rejection passed all applicable test cases, and mean GreedyExpansion gap against the enumerated budget oracle is 0.0481%.

These cases are generated from internal synthetic templates. No external field dataset or authority was provided (GT-C `NOT_VALIDATED`), and no AgentArts traces were supplied (`FlatToolAgent` `NOT_RUN`). Full case families, metrics, commands, and report paths are in [BENCHMARK_EVALUATION.md](BENCHMARK_EVALUATION.md).
