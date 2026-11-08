"""Opt-in real built-in app launches; no UI control and no user-window shutdown."""
import json
import os
from pathlib import Path
import sys

import pytest

pytestmark = [pytest.mark.windows, pytest.mark.skipif(
    sys.platform != 'win32' or os.environ.get('BENDER_WINDOWS_INTEGRATION') != '1',
    reason='requires opt-in interactive Windows desktop')]


def test_real_system_app_launch_outcomes_are_reported():
    from bender_ai.windows import WindowsDesktop, AppLaunchError, AppLaunchUnverified
    report = {'platform': 'Windows', 'apps': []}
    desktop = WindowsDesktop()
    for executable in ('notepad.exe', 'calc.exe', 'explorer.exe'):
        try:
            result = desktop.open_app(executable)
            assert result['status'] == 'verified'
            assert type(result['already_open']) is bool
            report['apps'].append({'app': executable, **result})
        except AppLaunchUnverified as exc:
            report['apps'].append({'app': executable, 'status': 'unverified', 'message': str(exc)})
        except AppLaunchError as exc:
            report['apps'].append({'app': executable, 'status': 'failed', 'message': str(exc)})
        except Exception as exc:
            # Provider messages can contain private UI text: retain only the class.
            report['apps'].append({'app': executable, 'status': 'unexpected_error',
                                   'error_type': type(exc).__name__})
    target = Path(os.environ.get('BENDER_LAUNCH_REPORT', 'build/launch-report.json'))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert len(report['apps']) == 3
    assert all(row['status'] in ('verified', 'unverified') for row in report['apps']), report
