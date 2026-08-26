$ErrorActionPreference = 'Stop'
# install-env.ps1 - create venv on first run; reinstall when requirements.txt or the python version changes.
# Progress lines go to the information stream (Write-Host) so run.ps1's stdout stays JSON-only; failures exit 1
# with the reason as the last output line (run.ps1 wraps it into a JSON envelope).
$Root = Split-Path -Parent $PSScriptRoot
$info = Get-Content (Join-Path $Root 'info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$venv = Join-Path $env:USERPROFILE ".openvino\venv\$($info.venv_name)"
$py   = Join-Path $venv 'Scripts\python.exe'
$req  = Join-Path $Root 'requirements.txt'
$marker = Join-Path $venv '.deps-installed'
$want_py = "$($info.python_version)"

function Actual-Version {
    try { return (& $py -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null).Trim() } catch { return '' }
}

# marker = sha256(requirements.txt) + requested python version → any change forces a reinstall
$reqHash = (Get-FileHash -Path $req -Algorithm SHA256).Hash
$want = "$reqHash|py$want_py"
$have = ''
if (Test-Path $marker) { $have = "$(Get-Content $marker -Raw -ErrorAction SilentlyContinue)" }

# fast path: only when the venv exists AND really is the requested minor version (codex #3: verify before trusting the marker)
if ((Test-Path $py) -and ("$have".Trim() -eq $want) -and ((Actual-Version) -eq $want_py)) { exit 0 }

if ((Test-Path $py) -and ((Actual-Version) -ne $want_py)) {
    Write-Host "[install-env] existing venv is Python $(Actual-Version), not $want_py; recreating $venv"
    Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue
}

if (-not (Test-Path $py)) {
    # prefer the requested interpreter via the py launcher; fall back to whatever `python` is
    $launcher = "-$want_py"
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py $launcher -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[install-env] creating venv with Python ${want_py}: $venv"
            & py $launcher -m venv $venv
            if ($LASTEXITCODE -eq 0) { $created = $true }
        }
    }
    if (-not $created) {
        Write-Host "[install-env] Python $want_py not found via py launcher; trying default python"
        $pyCmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pyCmd) { Write-Output "Python $want_py is not installed (no 'py' launcher and no 'python' on PATH); install it from python.org, then rerun"; exit 1 }
        python -m venv $venv
        if ($LASTEXITCODE -ne 0) { Write-Output 'venv creation failed'; exit 1 }
    }
}
# the venv must really be the requested minor version — never let a fallback interpreter masquerade as it
$actual = Actual-Version
if ($actual -ne $want_py) {
    Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue      # do not leave a wrong-version venv behind
    Write-Output "venv python is '$actual' but info.json requires $want_py; install Python $want_py (py launcher) and rerun"
    exit 1
}
Write-Host '[install-env] installing pinned requirements from PyPI (first run only; may take several minutes)'
& $py -m pip install --quiet --disable-pip-version-check -r $req
if ($LASTEXITCODE -ne 0) { Write-Output 'pip install failed (network/proxy/disk?)'; exit 1 }
Set-Content -Path $marker -Value $want -Encoding ASCII
exit 0
