$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path .venv\Scripts\python.exe)) { py -3.12 -m venv .venv; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
& .venv\Scripts\python.exe -m pip install -c constraints.txt -e '.[dev]'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .venv\Scripts\python.exe -m bender_ai
exit $LASTEXITCODE
