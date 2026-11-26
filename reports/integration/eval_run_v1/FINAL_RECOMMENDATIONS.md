# Final recommendations

1. Keep the frozen 24 tasks and GT immutable. Preserve NOT_RUN and unscorable states.
2. Fix or explicitly quarantine the legacy v1 fixture/manifest mismatch before treating the complete Module B/C regression suites as green; do not alter data hashes to silence the test.
3. Keep the East C-to-B memory regression in the release regression set; the final browser suite passed after the streaming-storage and source-cache changes. Preserve the opaque pre-fix `B_INTERNAL_ERROR` traces for future diagnosis.
4. Keep A v2 data and planning marked DEMO_ONLY; keep Xiaolan disabled until its 30 review rows and one exclusion are resolved with evidence.
5. Configure a local, no-cost real LLM before attempting the four matched Agent arms. Use the same frozen tasks, tools, execution limits, and independent GT; do not score RuleLLM smoke.
6. Run the Skill Graph and Verifier ablations only after real Agent traces and matched controls are available. Current evidence does not establish incremental benefit over B's solver constraints.
7. Avoid further B solver mathematics or C Harness architecture changes until new traces show a reproducible defect. Current follow-up work is the legacy fixture integrity repair and a real-LLM matched evaluation, not a measured optimizer limit.
