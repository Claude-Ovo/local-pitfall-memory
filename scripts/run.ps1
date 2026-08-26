$ErrorActionPreference = 'Stop'
# run.ps1 - fixed entry point (never rename). Contract: platform gate -> env -> client. Exit codes: 0 ok, 1 unsupported/error, 3 model download pending (rerun with --continue).
# Host contract (codex #3): every failure this script can detect is ONE JSON line on stdout + a non-zero exit code.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot

function Fail($code, $message, $exitCode = 1) {
    $safe = "$message" -replace [regex]::Escape($env:USERPROFILE), '~'
    if ($env:USERNAME -and $env:USERNAME.Length -ge 3) { $safe = $safe -replace ('(?<=[\\/])' + [regex]::Escape($env:USERNAME) + '(?=[\\/])'), '<USER>' }
    Write-Output (@{ ok = $false; error = $code; message = $safe } | ConvertTo-Json -Compress)
    exit $exitCode
}

# --- 1. Platform gate (no WMI/CIM: those can be access-denied in restricted hosts) ------------
# Requirements: 64-bit Windows on x86-64, >= 6 GB RAM. ISA-level checks (AVX2 etc.) are delegated to the
# OpenVINO CPU plugin at load time; the client reports a structured error if the model cannot load.
# The gate is fail-CLOSED: if memory cannot be probed it reports platform_probe_failed (override: PITFALL_SKIP_GATE=1).
if ($env:PITFALL_SKIP_GATE -ne '1') {
    if (-not [Environment]::Is64BitOperatingSystem) { Fail 'platform_unsupported' 'This skill requires 64-bit Windows.' }
    $arch = "$env:PROCESSOR_ARCHITEW6432$env:PROCESSOR_ARCHITECTURE"
    if ($arch -notmatch 'AMD64') { Fail 'platform_unsupported' "This skill requires an x86-64 CPU (found: $arch)." }
    $memGb = $null
    try {
        Add-Type -AssemblyName Microsoft.VisualBasic
        $memGb = [math]::Round((New-Object Microsoft.VisualBasic.Devices.ComputerInfo).TotalPhysicalMemory / 1GB, 1)
    } catch {
        try { $memGb = [math]::Round((Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1GB, 1) } catch { $memGb = $null }
    }
    if ($null -eq $memGb) { Fail 'platform_probe_failed' 'Could not determine physical memory (both ComputerInfo and CIM failed); set PITFALL_SKIP_GATE=1 to bypass the 6 GB gate on this host.' }
    if ($memGb -lt 6) { Fail 'platform_unsupported' "This skill needs at least 6 GB RAM (found $memGb GB)." }
}

# --- 2. Ensure Python environment (first run: venv + pinned deps from PyPI) ---------------------
$envOut = @()
try {
    $envOut = & (Join-Path $PSScriptRoot 'install-env.ps1') 2>&1
    $envRc = $LASTEXITCODE
} catch {
    $envOut += "$($_.Exception.Message)"; $envRc = 1
}
if ($envRc -ne 0) {
    $last = ($envOut | Where-Object { "$_" -ne '' } | Select-Object -Last 3) -join ' | '
    Fail 'env_install_failed' "Python environment setup failed: $last"
}

# --- 3. Resolve venv python --------------------------------------------------
$infoPath = Join-Path $Root 'info.json'
$info     = Get-Content $infoPath -Raw -Encoding UTF8 | ConvertFrom-Json
$venv     = Join-Path $env:USERPROFILE ".openvino\venv\$($info.venv_name)"
$python   = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) { Fail 'env_install_failed' "venv python missing: $python" }

# --- 4. --continue: resume a pending model download, nothing else ------------
if ($args.Count -ge 1 -and $args[0] -eq '--continue') {
    & $python (Join-Path $PSScriptRoot 'download_model.py') --continue
    exit $LASTEXITCODE     # 0 ready, 3 still incomplete, 1 error (one JSON line either way)
}

# --- 5. Launch client (forward all args) -------------------------------------
& $python (Join-Path $PSScriptRoot 'client.py') @args
exit $LASTEXITCODE
