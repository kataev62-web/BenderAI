param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& $Python -c "import sys; assert sys.platform == 'win32' and sys.maxsize > 2**32, 'Build requires Windows x64'"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m pip install -c constraints.txt -e '.[dev,voice]'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m pytest -m 'not windows' -q --junitxml=build/portable-tests.xml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -c "from pywinauto.uia_defines import IUIA; IUIA()"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name BenderAI --paths src --collect-submodules pywinauto --collect-submodules comtypes --collect-submodules win32com --collect-all vosk --collect-all sounddevice --collect-all _sounddevice_data --add-data 'src/bender_ai/avatar_blender.py;bender_ai' --hidden-import pythoncom --hidden-import keyring.backends.Windows --hidden-import comtypes.gen.UIAutomationClient scripts/desktop_entry.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m PyInstaller --noconfirm --clean --windowed --onefile --name BenderTraining --distpath build/training-dist scripts/training_entry.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# Self-contained fixture avoids overwriting the application runtime libraries.
Copy-Item build/training-dist/BenderTraining.exe dist/BenderAI/
& $Python -m PyInstaller --noconfirm --clean --windowed --onefile --name BenderTrainingPeer --distpath build/training-dist scripts/training_entry.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item build/training-dist/BenderTrainingPeer.exe dist/BenderAI/
New-Item -ItemType Directory -Path dist/BenderAI/docs -Force | Out-Null
Copy-Item docs/USER_GUIDE_RU.md, docs/MULTIWINDOW_LEARNING_RU.md, docs/SECURITY.md dist/BenderAI/docs/
Copy-Item LICENSE-THIRD-PARTY.md dist/BenderAI/
& $Python scripts/collect_licenses.py dist/BenderAI/licenses
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$env:QT_QPA_PLATFORM = 'offscreen'
$reportPath = Join-Path (Get-Location) 'build/packaged-smoke.json'
if (Test-Path $reportPath) { Remove-Item $reportPath }
$process = Start-Process -FilePath dist/BenderAI/BenderAI.exe -ArgumentList '--smoke-test','--native-smoke','--smoke-report',"`"$reportPath`"" -PassThru
$retainedProcessHandle = $process.Handle
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
$featureReport = Join-Path (Get-Location) 'build/packaged-features.json'
if (Test-Path $featureReport) { Remove-Item $featureReport }
$featureProcess = Start-Process -FilePath dist/BenderAI/BenderAI.exe -ArgumentList '--feature-smoke-report',"`"$featureReport`"" -PassThru
$retainedFeatureHandle = $featureProcess.Handle
if (!$featureProcess.WaitForExit(90000)) {
    & taskkill.exe /PID $featureProcess.Id /T /F
    throw 'Packaged feature probe timed out after 90 seconds'
}
if (!(Test-Path $featureReport)) { throw 'Packaged feature probe did not produce a current report' }
$features = Get-Content $featureReport -Raw -Encoding UTF8 | ConvertFrom-Json
if ($null -eq $featureProcess.ExitCode -or $featureProcess.ExitCode -ne 0 -or $features.status -ne 'passed' -or !$features.frozen -or !$features.voice_dependencies_loaded -or !$features.speech_spawn_stop_verified -or !$features.missing_model_reported -or !$features.microphone_inactive_by_default -or !$features.explorer_navigate_create_replay_verified -or $features.microphone_capture_started) {
    throw "Packaged feature probe failed: $($featureProcess.ExitCode), $($features.exception_type)"
}
Copy-Item $featureReport dist/BenderAI/packaged-features.json
Copy-Item build/portable-tests.xml dist/BenderAI/portable-tests.xml
[xml]$testReport = Get-Content build/portable-tests.xml -Raw -Encoding UTF8
$suite = $testReport.testsuites.testsuite
@{
    app_version = (& $Python -c "from bender_ai import __version__; print(__version__)")
    commit = (& git rev-parse HEAD)
    workflow_run = $env:GITHUB_RUN_ID
    platform = 'windows-x64'
    packaged_smoke = 'passed'
    packaged_voice_probe = 'passed'
    tests = [int]$suite.tests
    skipped = [int]$suite.skipped
    failures = [int]$suite.failures
    errors = [int]$suite.errors
    interactive_desktop_verified = $false
} | ConvertTo-Json | Set-Content dist/BenderAI/BUILD-INFO.json -Encoding UTF8
Compress-Archive -Path dist/BenderAI/* -DestinationPath dist/BenderAI-windows-x64.zip -Force
