# Module D Frontend Architecture

## Runtime layout

```text
Browser (React / TypeScript)
  ├─ /api/c/* ── Vite same-origin proxy ── Module C :8080
  │                                      └─ BClient ── Module B :8000
  └─ /__a/v2/* ── read-only Vite adapter ── A v2 manifests + GeoJSON
```

The web client creates one C session per selected region and uses C turn submission, polling, cancellation, result layers, and evidence endpoints. It never calls the B solver directly. C retains ownership of intent parsing, task routing, verification, and result publication.

The local A adapter resolves a region from `configs/regions.yaml`; resolves the requested layer ID from that region's `layers_manifest.json` or a fixed supplementary-layer allowlist; verifies that the canonicalized file hash matches `manifest.json`; and returns GeoJSON only. Client requests never contain filesystem paths. Only the small quality summary and layer descriptors are projected into metadata. Raster layers are disclosed but not rendered. OD Parquet and raw GIS files are not sent to the browser.

## Workspace views

- **Planning overview:** region and dataset KPIs, a layer-backed local MapLibre view, reachability context chart, and quick C tasks.
- **Spatial data:** manifest layer selector, data catalog, source/status facts, and local GIS view.
- **Service assessment:** explicit walk-time and demand-ratio controls generate a C natural-language request.
- **Scenario lab:** shelter IDs are loaded from the selected region's actual A v2 GeoJSON before creating a closure comparison request.
- **Budget optimization:** budget plus up to 12 individually edited candidate options. Each candidate uses an A shelter ID; capacity change and cost are user-supplied HYPOTHETICAL values.
- **Runs and evidence:** current browser session timeline, shelter assignments joined by `shelter_id`, and bounded C evidence pages.

## State and trust boundaries

- React Query handles C and local manifest metadata; Zustand keeps selected region, current session, and in-memory message timeline.
- A browser `sessionStorage` key is scoped per region. On reload, the client asks C to validate the stored session before reusing it. C result calls include `session_id` to preserve server-side ownership checks.
- The UI knows only the active browser session. C does not expose a session-list endpoint, so the history screen does not claim to be a user-wide or server-wide archive.
- All five C statuses are represented. `NEEDS_CLARIFICATION` remains distinct from failure, and `FEASIBLE` is never relabeled as `OPTIMAL`.
- C warnings, fallback flags, data version/status, and solver text remain visible. No result is inferred from map coverage layers.
- The local Vite adapter is intended for loopback demo usage. Module C has no authentication, HTTPS, or multi-user deployment guarantee in this integration contract.

## Primary source boundaries

The A v2 quality summary and manifest remain the authority for displayed region counts, version, source status, and coverage. Module C remains the authority for planning task state and result data. Module B remains the authority for mathematical assignments and solver status. A map overlay of B results uses a keyed join on `shelter_id`; result layers do not provide geometry.
