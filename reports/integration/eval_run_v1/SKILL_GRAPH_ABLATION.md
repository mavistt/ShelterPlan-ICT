# Skill Graph ablation

**Status: NOT_RUN.** The required matched real-LLM arms (Flat Tools, Fixed Workflow, Composite Skills + Harness) were unavailable because no real LLM endpoint was configured. The RuleLLM integration smoke verifies a route through C Session/Turn to B, but is not a controlled ablation and is excluded from scores.

No causal claim about Skill Graph success rate, task duration, cache savings, tool-call count, or failure recovery can be made from this run. The B solver's internal skill graph is observable in B traces but does not substitute for comparing Agent architectures under matched tasks and execution budgets.
