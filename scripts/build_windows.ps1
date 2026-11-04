param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& $Python -c "import sys; assert sys.platform == 'win32' and sys.maxsize > 2**32, 'Build requires Windows x64'"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m pip install -c constraints.txt -e '.[dev]'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m pytest -m 'not windows' -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -c "from pywinauto.uia_defines import IUIA; IUIA()"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name BenderAI --paths src --collect-submodules pywinauto --collect-submodules comtypes --hidden-import keyring.backends.Windows --hidden-import comtypes.gen.UIAutomationClient scripts/desktop_entry.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m PyInstaller --noconfirm --clean --windowed --onefile --name BenderTraining --distpath build/training-dist scripts/training_entry.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# Self-contained fixture avoids overwriting the application runtime libraries.
Copy-Item build/training-dist/BenderTraining.exe dist/BenderAI/
Copy-Item LICENSE-THIRD-PARTY.md dist/BenderAI/
& $Python scripts/collect_licenses.py dist/BenderAI/licenses
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$env:QT_QPA_PLATFORM = 'offscreen'
$process = Start-Process -FilePath dist/BenderAI/BenderAI.exe -ArgumentList '--smoke-test','--native-smoke' -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Packaged smoke test failed: $($process.ExitCode)" }
Compress-Archive -Path dist/BenderAI/* -DestinationPath dist/BenderAI-windows-x64.zip -Force
