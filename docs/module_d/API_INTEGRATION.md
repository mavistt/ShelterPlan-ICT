# Module D API Integration

## C service

Configure the frontend dev/preview proxy with `C_API_TARGET` (default `http://127.0.0.1:8080`). The browser calls:

| Purpose | C route | Frontend handling |
|---|---|---|
| Health | `GET /healthz` | Connection state and local service check |
| Capabilities | `GET /v1/capabilities` | Five statuses, task types, limits, disclosure |
| Region list | `GET /v1/regions` | Regions available to C; intersected with local A metadata |
| Layer catalog | `GET /v1/regions/{region_id}/layers` | C descriptors; geometry served by the local manifest adapter |
| Scenarios | `GET /v1/regions/{region_id}/scenarios` | Available scenario catalog (UI does not fabricate scenario IDs) |
| Create session | `POST /v1/sessions` | Body `{ "region_id": "shiqi" }` |
| Session snapshot | `GET /v1/sessions/{session_id}` | Validates region/version before browser storage reuse |
| Submit turn | `POST /v1/sessions/{session_id}/turns` | Body `{ "text": "…", "client_turn_id": "…" }`; C returns 202/RUNNING |
| Poll turn | `GET /v1/turns/{turn_id}` | Poll every 500 ms until one of C's terminal statuses |
| Cancel | `POST /v1/turns/{turn_id}/cancel` | Shows `cancel_accepted`; started B solver may not be interruptible |
| Result layers | `GET /v1/results/{result_id}/layers?session_id=…&variant=auto` | Read only after SUCCEEDED; session-scoped |
| Evidence page | `GET /v1/results/{result_id}/evidence/{section}?session_id=…&offset=…&limit=20` | Bounded, paginated evidence |

The UI preserves `NEEDS_CLARIFICATION`, `RUNNING`, `SUCCEEDED`, `FAILED`, and `CANCELLED`. Only a successful envelope with a `result_id` opens result layers. A 403/404 from C remains an error and is not worked around in the browser.

## A v2 local read-only data adapter

Vite middleware provides:

- `GET /__a/v2/regions`: selected safe fields from region manifests and quality summaries.
- `GET /__a/v2/layers/{region_id}/{layer_id}`: manifest-listed GeoJSON only, with `x-shelterplan-manifest-sha256` response header.

Only IDs in `configs/regions.yaml` are accepted. Layer IDs map to paths server-side, and each source path must be within the corresponding `data/processed/regions/{region_id}` directory and present in that region's A manifest. Data is normalized from CRLF to LF before SHA-256 validation because the checked-out files use Windows line endings while the A manifest records canonical LF content. TIFF, parquet, and arbitrary file access are not served.

The map loads layers only when selected. The initial overview requests the boundary, shelter capacity, and modeled population heat layer. The multi-megabyte 10/15/20/30-minute vector layers and large road-edge files are opt-in. B result rows have no geometry and are associated with A shelter features only through `shelter_id`.

## Error and disclosure behavior

- If C is offline, the frontend still shows manifest-backed A metadata and map layers, while disabling task submission and showing the connection state.
- If a layer is missing or its hash differs, the adapter returns an error and the layer is not silently substituted.
- If a task times out in the browser after 190 seconds, the UI says the submitted turn may still be running; it does not fabricate a terminal C status.
- `DEMO_ONLY` is shown in the header, data banner, and turn metadata. Model population, candidate reachability, and solver assignment remain distinct quantities.
- The UI displays `LLM_FALLBACK` warning fields when C reports a rule fallback. RuleLLM tests are integration checks, not real LLM performance evidence.
