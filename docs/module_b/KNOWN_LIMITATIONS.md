# Known limitations

- A7 is `DEMO_ONLY` and lists seven real-pilot blockers. All 24 shelters are unverified, use approximate coordinates without approved source evidence, and have unverified current capacity/open status. The analysis area is not an official boundary.
- The A manifest declares a 922,251,437-byte population raster, while the local raw file is a 134-byte Git LFS pointer. Processed outputs match their manifest hashes, but a complete source rebuild cannot be reproduced.
- Real-data results are mathematically verified for the loaded inputs only. They do not establish real emergency coverage, current capacity, official geography, or engineering suitability.
- Hypothetical expansion costs are abstract cost units and 25% capacity deltas, not estimates or recommendations.
- The benchmark has 40 deterministic synthetic cases and family-separated internal splits. This is not external field validation; GT-C is `NOT_VALIDATED`. FlatToolAgent is `NOT_RUN` without AgentArts traces or model configuration.
- One deterministic execution per case is recorded. Confidence intervals, token cost, public-channel publication escape, and external replication are unavailable.
- SciPy HiGHS LP uses a 10-second per-solve limit. Budget MIP is capped at 64 options and 10 seconds total. A feasible time-limited incumbent is labeled `FEASIBLE` without an optimality claim; a timeout without an incumbent is not reported as optimal.
- The API queue/cache are in-process and results are local JSON. No authentication, HTTPS, rate limiting, distributed queue, or public deployment is configured.
