$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PidFile = Join-Path $Root 'outputs/integration/local_ui/pids.json'
if (-not (Test-Path -LiteralPath $PidFile)) { Write-Output 'No integration-demo process registry found.'; exit 0 }

$registry = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
$stopped = @()
$skipped = @()
foreach ($entry in $registry.services) {
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($entry.pid)" -ErrorAction SilentlyContinue
  if (-not $process) { $skipped += "$($entry.name): already stopped"; continue }
  $commandLine = [string]$process.CommandLine
  $expected = switch -Regex ($entry.name) {
    '^module-b$' { $commandLine.Contains('backend.app:app') -and $commandLine.Contains('--port 8000'); break }
    '^module-c-rule-smoke$' { $commandLine.Contains('tests.module_c.api_http_launcher'); break }
    '^module-d-vite$' { $commandLine.Contains('vite') -and $commandLine.Contains('5173'); break }
    default { $false }
  }
  if (-not $expected) { $skipped += "$($entry.name): PID identity did not match; left untouched"; continue }
  Stop-Process -Id ([int]$entry.pid) -ErrorAction Stop
  $stopped += $entry.name
}
Remove-Item -LiteralPath $PidFile -Force
Write-Output "Stopped: $($stopped -join ', ')"
if ($skipped.Count) { Write-Output "Skipped: $($skipped -join '; ')" }
