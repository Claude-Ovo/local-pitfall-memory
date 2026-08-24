# install-env.ps1 - create venv on first run; cheap no-op afterwards
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$info = Get-Content (Join-Path $Root 'info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$venv = Join-Path $env:USERPROFILE ".openvino\venv\$($info.venv_name)"
$py   = Join-Path $venv 'Scripts\python.exe'
$marker = Join-Path $venv '.deps-installed'

if ((Test-Path $py) -and (Test-Path $marker)) { exit 0 }

if (-not (Test-Path $py)) {
    Write-Output "[install-env] creating venv $venv"
    python -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Output "[install-env] venv creation failed"; exit 1 }
}
Write-Output "[install-env] installing requirements (first run only)"
& $py -m pip install --quiet --disable-pip-version-check -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Write-Output "[install-env] pip install failed"; exit 1 }
New-Item -ItemType File -Path $marker -Force | Out-Null
exit 0
