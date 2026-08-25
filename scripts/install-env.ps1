$ErrorActionPreference = 'Stop'
# install-env.ps1 - create venv on first run; reinstall when requirements.txt or the python version changes.
$Root = Split-Path -Parent $PSScriptRoot
$info = Get-Content (Join-Path $Root 'info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$venv = Join-Path $env:USERPROFILE ".openvino\venv\$($info.venv_name)"
$py   = Join-Path $venv 'Scripts\python.exe'
$req  = Join-Path $Root 'requirements.txt'
$marker = Join-Path $venv '.deps-installed'

# marker = sha256(requirements.txt) + requested python version → any change forces a reinstall
$reqHash = (Get-FileHash -Path $req -Algorithm SHA256).Hash
$want = "$reqHash|py$($info.python_version)"
$have = ''
if (Test-Path $marker) { $have = "$(Get-Content $marker -Raw -ErrorAction SilentlyContinue)" }
if ((Test-Path $py) -and ("$have".Trim() -eq $want)) { exit 0 }

if (-not (Test-Path $py)) {
    # prefer the requested interpreter via the py launcher; fall back to whatever `python` is
    $launcher = "-$($info.python_version)"
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py $launcher -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Output "[install-env] creating venv with Python $($info.python_version): $venv"
            & py $launcher -m venv $venv
            if ($LASTEXITCODE -eq 0) { $created = $true }
        }
    }
    if (-not $created) {
        Write-Output "[install-env] Python $($info.python_version) not found via py launcher; trying default python"
        python -m venv $venv
        if ($LASTEXITCODE -ne 0) { Write-Output '[install-env] venv creation failed'; exit 1 }
    }
}
# the venv must really be the requested minor version — never let a fallback interpreter masquerade as it
$actual = (& $py -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')").Trim()
if ($actual -ne "$($info.python_version)") {
    Write-Output "[install-env] venv python is $actual but info.json requires $($info.python_version); install Python $($info.python_version) (py launcher) and delete $venv"
    exit 1
}
Write-Output '[install-env] installing pinned requirements'
& $py -m pip install --quiet --disable-pip-version-check -r $req
if ($LASTEXITCODE -ne 0) { Write-Output '[install-env] pip install failed'; exit 1 }
Set-Content -Path $marker -Value $want -Encoding ASCII
exit 0
