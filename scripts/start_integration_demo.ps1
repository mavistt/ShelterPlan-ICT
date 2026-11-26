param(
  [string]$Python = $env:SHELTERPLAN_PYTHON
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Frontend = Join-Path $Root 'frontend'
$RunDir = Join-Path $Root 'outputs/integration/local_ui'
$LogDir = Join-Path $RunDir 'logs'
$PidFile = Join-Path $RunDir 'pids.json'

if (-not $Python) {
  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if (-not $pythonCommand) { throw 'Python was not found. Set SHELTERPLAN_PYTHON to the project Python executable.' }
  $Python = $pythonCommand.Source
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python executable not found: $Python" }
if (-not (Test-Path -LiteralPath (Join-Path $Frontend 'node_modules/.bin/vite.cmd'))) {
  throw 'Frontend packages are missing. Run npm ci from frontend/ first.'
}

$pythonCheck = & $Python -c "import fastapi, httpx, mcp, numpy, scipy, uvicorn" 2>&1
if ($LASTEXITCODE -ne 0) { throw "Python B/C dependencies are incomplete: $pythonCheck" }

function Test-LocalPort([int]$Port) {
  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $pending = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
    return $pending.AsyncWaitHandle.WaitOne(200) -and $client.Connected
  } catch { return $false } finally { $client.Dispose() }
}

foreach ($port in @(8000, 8080, 5173)) {
  if (Test-LocalPort $port) { throw "Port $port is already occupied. No process was stopped; inspect it and choose a free port." }
}

New-Item -ItemType Directory -Force -Path $LogDir, (Join-Path $RunDir 'state'), (Join-Path $RunDir 'trace') | Out-Null
$processes = @()

function Start-HiddenProcess([string]$Name, [string]$FilePath, [string[]]$ArgumentList, [string]$WorkingDirectory) {
  $outPath = Join-Path $LogDir "$Name.out.log"
  $errPath = Join-Path $LogDir "$Name.err.log"
  $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput $outPath -RedirectStandardError $errPath
  $script:processes += [pscustomobject]@{ name = $Name; pid = $proc.Id; executable = $FilePath; arguments = ($ArgumentList -join ' ') }
  return $proc
}

$env:SHELTERPLAN_PROJECT_ROOT = $Root
$env:SHELTERPLAN_DATA_VERSION = 'shelterplan_module_a_v2.0-demo__shiqi'
$env:SHELTERPLAN_B_SEND_REGION_ID = 'true'
$env:SHELTERPLAN_REGION_ID = 'shiqi'
$env:SHELTERPLAN_PILOT_CONFIG = 'configs/module_a_v2_b/shiqi.yaml'
$b = Start-HiddenProcess 'module-b' $Python @('-m','uvicorn','backend.app:app','--host','127.0.0.1','--port','8000','--log-level','warning') $Root

$env:PORT = '8080'
$env:STORE_DIR = Join-Path $RunDir 'state'
$env:TRACE_DIR = Join-Path $RunDir 'trace'
$env:SHELTERPLAN_TRACE_DIR = Join-Path $RunDir 'trace'
$env:SHELTERPLAN_B_BASE_URL = 'http://127.0.0.1:8000'
$env:LLM_MODE = 'rule'
$c = Start-HiddenProcess 'module-c-rule-smoke' $Python @('-m','tests.module_c.api_http_launcher') $Root

$env:C_API_TARGET = 'http://127.0.0.1:8080'
$node = (Get-Command node.exe -ErrorAction Stop).Source
$ui = Start-HiddenProcess 'module-d-vite' $node @('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173') $Frontend

$services = @(
  @{ name = 'Module B'; url = 'http://127.0.0.1:8000/healthz'; pid = $b.Id },
  @{ name = 'Module C RuleLLM smoke'; url = 'http://127.0.0.1:8080/healthz'; pid = $c.Id },
  @{ name = 'Module D'; url = 'http://127.0.0.1:5173/'; pid = $ui.Id }
)
foreach ($service in $services) {
  $ready = $false
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    try {
      Invoke-WebRequest -Uri $service.url -TimeoutSec 2 -UseBasicParsing | Out-Null
      $ready = $true
      break
    } catch {
      if (-not (Get-Process -Id $service.pid -ErrorAction SilentlyContinue)) { break }
      Start-Sleep -Milliseconds 500
    }
  }
  if (-not $ready) { throw "$($service.name) did not become ready at $($service.url). See $LogDir" }
}

$registry = [pscustomobject]@{
  started_at = (Get-Date).ToUniversalTime().ToString('o')
  workspace = $Root
  mode = 'RULE_BASED_SMOKE; not an Agent benchmark run'
  services = $processes
}
$registry | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $PidFile -Encoding UTF8
Write-Output 'Module B: http://127.0.0.1:8000/healthz'
Write-Output 'Module C: http://127.0.0.1:8080/healthz'
Write-Output 'Module D: http://127.0.0.1:5173'
Write-Output "Process registry: $PidFile"
Write-Output "Logs: $LogDir"
Write-Output 'Module C uses deterministic RuleLLM for local smoke only; no external model API is called.'
