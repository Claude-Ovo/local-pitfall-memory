# run.ps1 - fixed entry point (never rename)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot

# --- 1. Hardware gate: none. CPU path works everywhere; OpenVINO picks best device.

# --- 2. Ensure Python environment ------------------------------------------
& (Join-Path $PSScriptRoot 'install-env.ps1')
if ($LASTEXITCODE -ne 0) { exit 1 }

# --- 3. Resolve venv python -------------------------------------------------
$infoPath = Join-Path $Root 'info.json'
$info     = Get-Content $infoPath -Raw -Encoding UTF8 | ConvertFrom-Json
$venv     = Join-Path $env:USERPROFILE ".openvino\venv\$($info.venv_name)"
$python   = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) {
    # fallback: system python (status/lookup path has zero non-stdlib deps)
    $python = 'python'
}

# --- 4. Launch client (forward all args) -----------------------------------
& $python (Join-Path $PSScriptRoot 'client.py') @args
exit $LASTEXITCODE
