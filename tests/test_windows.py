"""Opt-in tests: run in an unlocked interactive Windows session, never a service."""
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from bender_ai.models import Selector, Step
from bender_ai.learning import Recorder
from bender_ai.security import Policy, Denied
from bender_ai.storage import Store
from bender_ai.actions import ActionRegistry, Executor

pytestmark = [pytest.mark.windows, pytest.mark.skipif(sys.platform != 'win32' or os.environ.get('BENDER_WINDOWS_INTEGRATION') != '1', reason='requires opt-in interactive Windows desktop')]


def eventually(predicate, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('UIA condition did not become true')


def test_native_form_record_save_restart_replay_and_moved_button(tmp_path):
    from bender_ai.isolation import WindowsBridge
    from bender_ai.windows import WindowsCapture
    process = subprocess.Popen([sys.executable,'-m','bender_ai.training_form'])
    app = Path(sys.executable).name.casefold()
    title = 'BENDER AI Training'
    policy = Policy(read_apps=[app],ui_apps=[app],window_scopes={app:[title]})
    bridge = WindowsBridge(policy)
    store = Store(tmp_path / 'db')
    def selector(ident, kind):
        return Selector(app,title,str(ident),kind)
    capture = None
    try:
        eventually(lambda: bridge.ui.exists(selector(1001,'Edit')))
        recorder = Recorder(app,title)
        recorder.start()
        capture = WindowsCapture(bridge.ui,recorder)
        def ready():
            capture.poll()
            return capture.ready
        eventually(ready)
        bridge.ui.set_text(selector(1001,'Edit'),'Alice')
        def text_recorded():
            capture.poll()
            return any(s.action == 'uia_set_text' for s in recorder.steps)
        eventually(text_recorded)
        bridge.ui.click(selector(1002,'Button'))
        def click_recorded():
            capture.poll()
            return any(s.action == 'uia_click' for s in recorder.steps)
        eventually(click_recorded)
        with pytest.raises(Denied):
            bridge.ui.set_text(selector(1004,'Edit'),'never-store-this')
        capture.close()
        capture = None
        plan = recorder.finish('Native greeting')
        assert 'Alice' not in plan.to_json()
        assert 'never-store-this' not in plan.to_json()
        plan.steps.append(Step('assert_element',{'selector':selector(1003,'Text').to_dict(),'expected_state':'text:Hello Bob'}))
        skill = store.save_skill(plan)
        store.close()
        store = Store(tmp_path / 'db')
        bridge.ui.click(selector(1005,'Button'))  # reposition Greet without changing AutomationId
        runner = Executor(ActionRegistry(policy,store,ui=bridge.ui,desktop=bridge.desktop),store)
        result = runner.run(store.load_skill(skill).bind({'value_1':'Bob'}),confirmed=True)
        assert result.status == 'success', result.error
        assert bridge.ui.assert_state(selector(1003,'Text'),'text:Hello Bob')
        bad = store.load_skill(skill)
        bad.steps[1].args['selector']['automation_id'] = 'missing-id'
        assert runner.run(bad.bind({'value_1':'Charlie'}),confirmed=True).status == 'failed'
    finally:
        if capture:
            capture.close()
        bridge.close()
        store.close()
        process.terminate()
        process.wait(timeout=5)


def test_windows_active_window_permission_boundary():
    from bender_ai.windows import WindowsDesktop
    with pytest.raises(Denied):
        WindowsDesktop().active(Policy())
