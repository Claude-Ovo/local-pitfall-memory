$ErrorActionPreference = 'Stop'
# run.ps1 - fixed entry point (never rename). Contract: platform gate -> env -> client. Exit codes: 0 ok, 1 unsupported/error, 3 model download pending (rerun with --continue).
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot

# --- 1. Platform gate (no WMI/CIM: those can be access-denied in restricted hosts) ------------
# Requirements: 64-bit Windows on x86-64, >= 6 GB RAM. ISA-level checks (AVX2 etc.) are delegated to the
# OpenVINO CPU plugin at load time; the client reports a structured error if the model cannot load.
if (-not [Environment]::Is64BitOperatingSystem) { Write-Output 'This skill requires 64-bit Windows.'; exit 1 }
$arch = "$env:PROCESSOR_ARCHITEW6432$env:PROCESSOR_ARCHITECTURE"
if ($arch -notmatch 'AMD64') { Write-Output "This skill requires an x86-64 CPU (found: $arch)."; exit 1 }
$memGb = $null
try {
    Add-Type -AssemblyName Microsoft.VisualBasic
    $memGb = [math]::Round((New-Object Microsoft.VisualBasic.Devices.ComputerInfo).TotalPhysicalMemory / 1GB, 1)
} catch {
    try { $memGb = [math]::Round((Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1GB, 1) } catch { $memGb = $null }
}
if ($null -ne $memGb -and $memGb -lt 6) { Write-Output "This skill needs at least 6 GB RAM (found $memGb GB)."; exit 1 }
# (if memory cannot be determined at all, continue: the model loader will surface a structured error)

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
