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
$reportPath = Join-Path (Get-Location) 'build/packaged-smoke.json'
if (Test-Path $reportPath) { Remove-Item $reportPath }
$process = Start-Process -FilePath dist/BenderAI/BenderAI.exe -ArgumentList '--smoke-test','--native-smoke','--smoke-report',"`"$reportPath`"" -PassThru
if (!$process.WaitForExit(60000)) {
    & taskkill.exe /PID $process.Id /T /F
    throw 'Packaged smoke test timed out after 60 seconds'
}
if (!(Test-Path $reportPath)) { throw 'Packaged executable did not produce a current smoke report' }
$smokeJson = Get-Content $reportPath -Raw -Encoding UTF8
$smoke = $smokeJson | ConvertFrom-Json
if ($env:GITHUB_STEP_SUMMARY) {
    Add-Content $env:GITHUB_STEP_SUMMARY -Encoding UTF8 -Value ("`n## Packaged startup`n`n~~~json`n" + $smokeJson + "`n~~~`n")
}
if ($null -eq $process.ExitCode -or $process.ExitCode -ne 0 -or $smoke.status -ne 'passed' -or $smoke.exit_code -ne 0) {
    $diagnostic = $smokeJson.Replace('%','%25').Replace("`r",'%0D').Replace("`n",'%0A')
    Write-Output ("::error title=Packaged startup failed::" + $diagnostic)
    throw "Packaged smoke test failed: $($process.ExitCode)"
}
Copy-Item $reportPath dist/BenderAI/packaged-smoke.json
@{
    commit = (& git rev-parse HEAD)
    workflow_run = $env:GITHUB_RUN_ID
    platform = 'windows-x64'
    packaged_smoke = 'passed'
    interactive_desktop_verified = $false
} | ConvertTo-Json | Set-Content dist/BenderAI/BUILD-INFO.json -Encoding UTF8
Compress-Archive -Path dist/BenderAI/* -DestinationPath dist/BenderAI-windows-x64.zip -Force
