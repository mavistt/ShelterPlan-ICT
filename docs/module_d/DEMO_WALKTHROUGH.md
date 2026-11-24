# Module D Demo Walkthrough

## Local launch

Follow `frontend/README.md` to start B on `127.0.0.1:8000`, C on `127.0.0.1:8080`, then Vite on `127.0.0.1:5173`. The default region is Shiqi. The local UI uses the checked-out A v2 data and C API; it does not require a paid LLM API. If a local LLM is not available, inspect the C envelope warning for fallback status.

## Suggested walkthrough

1. **Overview:** point out Shiqi's 7,336 demand nodes, 25 shelters, 183,400 OD pairs, and `DEMO_ONLY` disclaimer. Explain that 231,619 modeled population with a 15-minute candidate route is reachability evidence only; it is not a capacity allocation result.
2. **Map:** show population heat, shelter capacity points, and analysis boundary. Click a shelter to see its stable `shelter_id`. Toggle coverage or access-gap layers and note that they are candidate network layers. All map files are loaded locally from the manifest allowlist.
3. **Service assessment:** use the form to set walk time and demand ratio. The generated request text shows the numbers attached to their intended fields; submit and follow the C turn through RUNNING to a terminal state.
4. **Clarification:** ask for an optimization without budget/options. C may return `NEEDS_CLARIFICATION`; answer in the same Agent panel and show that the session continues without inventing missing values.
5. **Scenario simulation:** select an actual shelter ID from the region GeoJSON and submit a temporary closure request. State clearly that closure is a hypothetical scenario and does not modify A source data.
6. **Budget optimization:** enter a budget and one or more explicit candidate rows. The candidate IDs are drawn from actual A v2 shelters; capacities and cost units are assumptions supplied by the user. Keep at or below 12 options. Read solver status exactly as returned.
7. **Runs and evidence:** inspect the session timeline, `result_id`, assignment table, and paginated verifier checks. Explain that assignment rows are joined to map points by shelter ID and are session-scoped.

## Limits to state aloud

- `PASS_DEMO` is a data-release verdict for demonstration readiness, not field validation.
- Population is modeled WorldPop data, not known evacuation demand.
- Shelter capacity, coordinates, and open state contain explicit demo assumptions.
- A coverage/access-gap layer does not include capacity allocation.
- C's local contract does not provide user authentication or an all-session history listing.
