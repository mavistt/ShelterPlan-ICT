# ShelterPlan Module D

Module D is the local React + TypeScript planning workspace for the integrated A/B/C branch. It reads A v2 manifest-backed GeoJSON through a read-only Vite middleware and sends planning requests only through the Module C session API.

## Start locally

Use Node 20+ and Python 3.11/3.12. From the repository root, install the isolated B/C runtime once:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-b.txt -r requirements-c.txt
```

Start Module B in one PowerShell window:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Start Module C in a second window:

```powershell
$env:SHELTERPLAN_PROJECT_ROOT = (Get-Location).Path
$env:SHELTERPLAN_PILOT_CONFIG = 'configs/module_a_v2_b/shiqi.yaml'
$env:SHELTERPLAN_REGION_ID = 'shiqi'
$env:SHELTERPLAN_DATA_VERSION = 'shelterplan_module_a_v2.0-demo__shiqi'
.\.venv\Scripts\python.exe -m uvicorn agent.api:app --host 127.0.0.1 --port 8080
```

Start the frontend in a third window:

```powershell
cd frontend
npm install
$env:C_API_TARGET = 'http://127.0.0.1:8080'
npm run dev
```

Open `http://127.0.0.1:5173`. The Vite development and preview servers bind to loopback. Set `C_API_TARGET` if C uses another local port. No external LLM API key is needed; without a local model endpoint, C can report its rule fallback. This fallback must not be described as real LLM performance.

## Commands

```powershell
npm run test
npm run typecheck
npm run build
npm run e2e
```

`npm run e2e` expects the app at `http://127.0.0.1:5173` and B/C already running. Playwright Chromium may need `npx playwright install chromium` on a new machine.

## Data adapter and deployment boundary

`/__a/v2/regions` returns a limited metadata projection from the checked out A v2 manifests and quality summaries. `__a/v2/layers/{region_id}/{layer_id}` only serves GeoJSON IDs declared in the region layer manifest or explicitly allowlisted supplementary layers present in the region manifest. The adapter rejects arbitrary file paths and validates the exact raw byte hash, or an exact in-memory CRLF reconstruction when the checkout uses LF. It never serves Parquet or raw A inputs. GeoTIFF is listed but not rendered.

The adapter is attached to Vite dev/preview middleware for a local demo. A separately deployed static build needs an equivalent allowlisted data endpoint and a same-origin reverse proxy for `/api/c`; do not expose the Vite dev server to an untrusted network. Module C has no authentication in this local-demo contract.

## Data status

Every region is `DEMO_ONLY` / `PASS_DEMO`. Shelter coordinates, capacities, and current open status are not field-verified. WorldPop values are modeled population estimates, not observed evacuees. Coverage and access-gap layers express candidate network reachability, not capacity-aware assignment. The front end does not load the full OD Parquet into the browser.
