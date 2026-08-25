# E2E test: full pitfall chain through the official entry point (scripts\run.ps1)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Split-Path -Parent $PSScriptRoot
$Run  = Join-Path $Root 'scripts\run.ps1'
$env:PITFALL_DB = Join-Path $env:TEMP "pitfall-test-$(Get-Random).db"
$env:PITFALL_FAKE_MODEL = '1'   # tests never load OpenVINO; real-model check is scripts\smoke_model.py

Write-Output '== unit tests'
python (Join-Path $PSScriptRoot 'test_unit.py')
if ($LASTEXITCODE -ne 0) { Write-Output 'UNIT FAIL'; exit 1 }
Write-Output '== server protocol tests (fake model)'
python (Join-Path $PSScriptRoot 'test_server.py')
if ($LASTEXITCODE -ne 0) { Write-Output 'SERVER FAIL'; exit 1 }
Write-Output '== codex review #1 regression tests'
python (Join-Path $PSScriptRoot 'test_review1.py')
if ($LASTEXITCODE -ne 0) { Write-Output 'REVIEW1 FAIL'; exit 1 }
Write-Output '== e2e through run.ps1'
python (Join-Path $PSScriptRoot 'make_fixtures.py') $PSScriptRoot | Out-Null

function Step($name, $script) {
    $out = & $script
    Write-Output ("[{0}] {1}" -f $name, ($out -join ' '))
    return ($out -join ' ')
}

$s = Step 'status'  { & $Run status --json };                  if ($s -notmatch '"ok": true')        { exit 1 }
$s = Step 'lookup0' { & $Run lookup --request-file (Join-Path $PSScriptRoot 'req.json') --json }; if ($s -notmatch '"hit": "none"') { exit 1 }
$s = Step 'propose' { & $Run propose --request-file (Join-Path $PSScriptRoot 'fix.json') --json }; if ($s -notmatch '"ok": true')  { exit 1 }
$s = Step 'lookup1' { & $Run lookup --request-file (Join-Path $PSScriptRoot 'req.json') --json };  if ($s -notmatch '需谨慎')       { exit 1 }
$s = Step 'commit'  { & $Run commit --id 1 --verify-exit-code 0 --json };                          if ($s -notmatch 'verified')     { exit 1 }
$s = Step 'lookup2' { & $Run lookup --request-file (Join-Path $PSScriptRoot 'req.json') --json };  if ($s -notmatch '可引用')       { exit 1 }
$s = Step 'cross'   { & $Run lookup --request-file (Join-Path $PSScriptRoot 'req2.json') --json }; if ($s -notmatch '"hit": "exact"') { exit 1 }
$s = Step 'negative'{ & $Run lookup --request-file (Join-Path $PSScriptRoot 'req3.json') --json }; if ($s -match '"hit": "exact"')  { exit 1 }
$s = Step 'digest'  { & $Run digest };                                                             if ($s -notmatch 'ERR_REQUIRE_ESM') { exit 1 }
# entry contract: --continue is handled by run.ps1 itself (exit 0 when model complete, 3 when pending)
& $Run --continue | Out-Null
$rc = $LASTEXITCODE; Write-Output "[continue] exit=$rc"; if ($rc -ne 0 -and $rc -ne 3) { exit 1 }
# entry contract: structured error + exit 1 on bad input
& $Run lookup --request-file (Join-Path $PSScriptRoot 'nope.json') --json | Out-Null
if ($LASTEXITCODE -ne 1) { Write-Output 'expected exit 1 on missing request file'; exit 1 }

& $Run server stop --json | Out-Null
Remove-Item $env:PITFALL_DB -ErrorAction SilentlyContinue
Write-Output 'ALL PASS'
exit 0
