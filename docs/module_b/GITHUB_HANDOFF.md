# Module B GitHub handoff scope

Prepared 2026-11-11 for `mavistt/ShelterPlan-ICT`, target branch `feature/module-b`. This local package has not been committed or pushed. The supplied checkout has no `.git` metadata, the connected GitHub account could not read the target repository, and `git ls-remote` was reset. Recheck the repository and branch before creating a separate clone/worktree; compare its existing files before staging anything.

## Allowlisted paths

Stage only these Module B paths after reviewing the target branch:

```text
backend/
configs/module_b.yaml
configs/module_b_hypothetical.yaml
tests/module_b/
docs/module_b/
examples/module_b/
requirements-b.txt
outputs/module_b/.gitignore
```

`backend/adapters/a_reader.py` reads Module A's configured source but does not own or package that source. The current implementation expects the approved A files to exist in a checkout according to `configs/pilot.yaml`; obtain them through Module A's release process.

## Explicit exclusions

Do not stage Module A files such as `configs/pilot.yaml`, `configs/pilot_analysis_area.geojson`, `data/`, or `scripts/`; root-level A requirements, lockfiles, or README; other modules; `.venv/`; Python/test caches; `.env` files or credentials; or any run output. `outputs/module_b/.gitignore` is the only allowed file under `outputs/module_b/`; it excludes local assignments, diagnostics, traces, and experiment records.

## Review before upload

In the separate clone/worktree, confirm the checked-out branch and clean starting status, compare `feature/module-b` with its base, then stage only the allowlisted paths above. Review `git diff --cached --name-status` and the full staged diff for A-owned changes, data, generated outputs, credentials, and unrelated files. Run the checks in [README.md](README.md) in that checkout. Push normally and open a PR only after confirming the remote URL and target branch; never force-push or overwrite files that already belong to the target branch.

The handoff documentation records run IDs and summarized measurements. Detailed JSON/CSV/Markdown experiment artifacts stay local and are intentionally excluded.
