import json
import os
from pathlib import Path
import subprocess
import sys
import pytest


def test_packaged_entrypoint_writes_machine_readable_smoke_result(tmp_path):
    output = tmp_path / 'smoke.json'
    result = subprocess.run([sys.executable,'scripts/desktop_entry.py','--smoke-test','--smoke-report',str(output)],
                            env={**os.environ,'QT_QPA_PLATFORM':'offscreen'},capture_output=True,timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    report = json.loads(output.read_text('utf-8'))
    assert report['status'] == 'passed' and report['exit_code'] == 0
    assert report['native_probe_requested'] is False


@pytest.mark.skipif(sys.platform == 'win32', reason='exercises the unsupported-platform error path')
def test_native_smoke_failure_returns_error_without_unhandled_gui_dialog(tmp_path):
    output = tmp_path / 'smoke-failure.json'
    result = subprocess.run([sys.executable,'scripts/desktop_entry.py','--smoke-test','--native-smoke','--smoke-report',str(output)],
                            env={**os.environ,'QT_QPA_PLATFORM':'offscreen'},capture_output=True,timeout=20)
    assert result.returncode != 0
    report = json.loads(output.read_text('utf-8'))
    assert report['status'] == 'failed'
    assert report['exception_type'] == 'Denied'


def test_feature_probe_requires_output_path():
    result = subprocess.run([sys.executable, 'scripts/desktop_entry.py', '--smoke-test',
                             '--feature-smoke-report'],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, timeout=20)
    assert result.returncode == 2


@pytest.mark.skipif(sys.platform == 'win32', reason='requires unsupported platform')
def test_feature_probe_reports_unsupported_platform_without_starting_gui(tmp_path):
    output = tmp_path / 'feature-smoke.json'
    result = subprocess.run([sys.executable, 'scripts/desktop_entry.py',
                             '--feature-smoke-report', str(output)],
                            capture_output=True, timeout=20)
    assert result.returncode == 1
    report = json.loads(output.read_text('utf-8'))
    assert report['status'] == 'failed'
    assert report['microphone_capture_started'] is False
    assert report['speaker_playback_verified'] is False
    assert report['microphone_hardware_verified'] is False
    assert report['speech_model_verified'] is False
