# Agent baseline comparison

| Arm | Status | Scored tasks | Result |
|---|---|---:|---|
| Direct LLM | NOT_RUN | 0 | No real LLM endpoint configured |
| Flat Tools | NOT_RUN | 0 | No real LLM endpoint configured |
| Fixed Workflow | NOT_RUN | 0 | No real LLM endpoint configured |
| Composite Skills + Harness | NOT_RUN | 0 | No real LLM endpoint configured |
| B direct structured solver | PARTIAL, separate baseline | 8 attempted | See `b_structured/results.json`; not an Agent arm |
| RuleLLM smoke | Unscored smoke only | Not applicable | Excluded from Agent metrics |

No true Agent success rate, failure-recovery rate, token/call comparison, or latency comparison is available. No equal-budget four-arm ablation was performed. No paid API was called. Do not interpret solver PASS as Agent success.
