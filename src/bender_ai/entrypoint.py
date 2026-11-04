"""Bootstrap diagnostics for unattended frozen smoke tests, without GUI error dialogs."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import traceback


def run():
    smoke = '--smoke-test' in sys.argv
    report_path = None
    if smoke and '--smoke-report' in sys.argv:
        index = sys.argv.index('--smoke-report')
        if index + 1 >= len(sys.argv):
            return 2
        report_path = Path(sys.argv[index+1])
    report = {'status':'running','native_probe_requested':'--native-smoke' in sys.argv,
              'started_at':datetime.now(timezone.utc).isoformat()}
    def write_report():
        if report_path:
            report_path.parent.mkdir(parents=True,exist_ok=True)
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    write_report()
    try:
        # Import after freeze_support: spawned COM children must not initialize the GUI.
        from .app import main
        code = main()
        report.update(status='passed' if code == 0 else 'failed',exit_code=code)
        write_report()
        return code
    except Exception as exc:
        if not smoke:
            raise
        # Smoke uses a temporary database and empty policy; no user secrets are loaded.
        report.update(status='failed',exit_code=1,exception_type=type(exc).__name__,traceback=traceback.format_exc())
        write_report()
        return 1
