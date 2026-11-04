import os
from pathlib import Path
import subprocess
import sys


def test_ci_summary_reports_failure_and_skip_without_secrets(tmp_path):
    report = tmp_path / 'junit.xml'
    report.write_text('''<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1"><testcase classname="test_windows" name="passing"/><testcase classname="test_windows" name="broken"><failure message="native failed">AssertionError: expected success — отказ доступа\nAPI value: PLACEHOLDER</failure></testcase><testcase classname="test_windows" name="interactive"><skipped message="needs desktop"/></testcase></testsuite></testsuites>'''.replace('PLACEHOLDER', 'ghp_' + 'a'*36),encoding='utf-8')
    summary = tmp_path / 'summary.md'
    result = subprocess.run([sys.executable,'scripts/ci_summary.py',str(report)],env={**os.environ,'GITHUB_STEP_SUMMARY':str(summary),'GITHUB_ACTIONS':'true','PYTHONIOENCODING':'ascii'},capture_output=True,text=True,encoding='utf-8')
    assert result.returncode == 0, result.stderr
    text = summary.read_text('utf-8')
    assert '1 passed' in text and '1 failed' in text and '1 skipped' in text
    assert 'test_windows.broken' in text
    assert 'AssertionError: expected success' in text
    assert 'ghp_' not in text

    assert '::error title=Windows test diagnostics::' in result.stdout
    assert 'ghp_' not in result.stdout
