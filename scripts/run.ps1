$ErrorActionPreference = 'Stop'
# run.ps1 - fixed entry point (never rename). Contract: hardware gate -> env -> client. Exit codes: 0 ok, 1 unsupported/error, 3 model download pending (rerun with --continue).
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot

# --- 1. Hardware / platform gate (explicit, executable) ----------------------
# This skill runs the model on CPU via OpenVINO; requirements: 64-bit Windows, >= 6 GB RAM, x86-64 with AVX2.
if (-not [Environment]::Is64BitOperatingSystem) {
    Write-Output 'This skill requires 64-bit Windows.'; exit 1
}
$memGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
if ($memGb -lt 6) {
    Write-Output "This skill needs at least 6 GB RAM (found $memGb GB)."; exit 1
}
$arch = (Get-CimInstance Win32_Processor | Select-Object -First 1).Architecture   # 9 = x64
if ($arch -ne 9) {
    Write-Output 'This skill requires an x86-64 CPU (OpenVINO CPU plugin).'; exit 1
}

# --- 2. Ensure Python environment -------------------------------------------
& (Join-Path $PSScriptRoot 'install-env.ps1')
if ($LASTEXITCODE -ne 0) { exit 1 }

# --- 3. Resolve venv python --------------------------------------------------
$infoPath = Join-Path $Root 'info.json'
$info     = Get-Content $infoPath -Raw -Encoding UTF8 | ConvertFrom-Json
$venv     = Join-Path $env:USERPROFILE ".openvino\venv\$($info.venv_name)"
$python   = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) { Write-Output "venv python missing: $python"; exit 1 }

# --- 4. --continue: resume a pending model download, nothing else ------------
if ($args.Count -ge 1 -and $args[0] -eq '--continue') {
    & $python (Join-Path $PSScriptRoot 'download_model.py') --continue
    exit $LASTEXITCODE     # 0 ready, 3 still incomplete, 1 error
}

# --- 5. Launch client (forward all args) -------------------------------------
& $python (Join-Path $PSScriptRoot 'client.py') @args
exit $LASTEXITCODE
