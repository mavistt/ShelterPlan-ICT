# Module B request examples

These JSON payloads follow the current `TaskRequest` and `ImprovementOption` schemas. `*_current_a.json` requests use the configured A source and `zhongshan_pilot_v0.1`; their planning status is `DEMO_ONLY`. Do not run them against a different source version. The A-backed budget option is an abstract hypothetical example, not a cost estimate.

| File | Purpose |
|---|---|
| `assess_current_a.json` | Current configured-source assessment |
| `budget_hypothetical_current_a.json` | One-option hypothetical budget optimization |
| `stress_close_shelter_a.json` | Disable a current A shelter for a stress test |
| `compare_current_a.json` | Compare two demand-ratio scenarios |
| `invalid_scenario_toy.json` | Out-of-range ratio that should fail scenario validation |
| `valid_skill_plan_toy.json` | Canonical registered assessment DAG |
| `invalid_skill_plan_toy.json` | Unregistered skill ID that should be rejected |

The A-backed files require the approved A release to be installed at the paths in `configs/pilot.yaml`. Toy-case examples are self-contained in the repository and are suitable for checking request/schema handling without A data.

For HTTP calls, POST a JSON file, then poll the job and retrieve the result:

```powershell
$base = 'http://127.0.0.1:8000'
$payload = Get-Content examples/module_b/assess_current_a.json -Raw
$submission = Invoke-RestMethod -Method Post -Uri "$base/planning/tasks" -ContentType 'application/json' -Body $payload
$job = Invoke-RestMethod -Uri "$base/planning/jobs/$($submission.job_id)"
while ($job.status -in @('QUEUED','RUNNING')) {
    Start-Sleep -Milliseconds 250
    $job = Invoke-RestMethod -Uri "$base/planning/jobs/$($submission.job_id)"
}
if ($job.status -eq 'SUCCEEDED') { Invoke-RestMethod -Uri "$base/planning/results/$($job.result_id)" }
```

Use a fresh `idempotency_key` for each new request. Reusing the key is for retrying the exact same logical task.
