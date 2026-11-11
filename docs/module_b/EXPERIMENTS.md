# Experiments and baselines

The latest experiments use the configured A source and keep its `DEMO_ONLY` status throughout. Run `285516ee-1fd7-4fa0-82cf-334938becaf9` contains the baseline assessment, 12 ratio/threshold combinations, the highest-loaded shelter closure, a capacity-only reachability upper bound, and Greedy/MIP hypothetical budget curves. See [REAL_DATA_EXPERIMENTS.md](REAL_DATA_EXPERIMENTS.md) for values and limits.

The local API's assess, stress-test, and comparison operations were also smoke-tested against those A files. All results were `PASS_DEMO`.

Synthetic baseline evaluation is expanded to 40 cases. It compares `StaticWorkflow`, `GreedyExpansion`, `SkillGraphOnly` (shadow-only and quarantined), and `SkillGraphVerifier`; an independent Dinic oracle checks integer primary objectives, with exhaustive option-subset enumeration for budget cases. Fault injection and family-separated synthetic validation are recorded in the benchmark report. No LLM calls were made, so AgentArts token cost is N/A. External field validation remains `NOT_VALIDATED`.
