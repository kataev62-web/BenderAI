"""Real UIA capture/replay across isolated native fixture processes.

Set both BENDER_TRAINING_EXE and BENDER_TRAINING_PEER_EXE to exercise distinct
packaged executables. Source runs use separate Python fixture processes.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from bender_ai.actions import ActionRegistry, Cancellation, Executor
from bender_ai.learning import Recorder
from bender_ai.models import Plan, Selector, Step
from bender_ai.security import Denied, Policy
from bender_ai.storage import Store

pytestmark = [pytest.mark.windows, pytest.mark.skipif(
    sys.platform != 'win32' or os.environ.get('BENDER_WINDOWS_INTEGRATION') != '1',
    reason='requires opt-in interactive Windows desktop')]


def eventually(predicate, timeout=15, context=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('Native multi-window condition timed out' + (': '+str(context()) if context else ''))


@pytest.fixture
def native_windows():
    from bender_ai.isolation import WindowsBridge
    from bender_ai.windows import process_path

    user = ctypes.WinDLL('user32', use_last_error=True)
    user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user.FindWindowW.restype = wintypes.HWND
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    titles = ['BENDER Multi Training', 'BENDER Multi Peer', 'BENDER Multi Unselected',
              'BENDER Multi InPrivate']
    configured = [os.environ.get('BENDER_TRAINING_EXE'), os.environ.get('BENDER_TRAINING_PEER_EXE')]
    assert all(configured) or not any(configured), 'Configure both fixture executables'
    commands = [[str(Path(path).resolve())] if path else [sys.executable, '-m', 'bender_ai.training_form']
                for path in configured]
    processes, handles = [], []
    bridge = None
    try:
        for command, title in zip([*commands, commands[0], commands[0]], titles):
            from windows_fixture_process import spawn_fixture
            processes.append(spawn_fixture([*command, '--title', title]))
            eventually(lambda: user.FindWindowW(None, title))
            handles.append(user.FindWindowW(None, title))
        pids = []
        for handle in handles:
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            pids.append(pid.value)
        assert len(set(pids)) == 4
        paths = [process_path(pid) for pid in pids]
        if all(configured):
            assert Path(paths[0]).name.casefold() != Path(paths[1]).name.casefold()
        apps = [Path(path).name.casefold() for path in paths]
        policy = Policy()
        for app, title, path in zip(apps[:2], titles[:2], paths[:2]):
            policy.grant_app(app, title, executable=path, control=True)
        with pytest.raises(Denied):
            policy.grant_app(apps[3], titles[3], executable=paths[3], control=True)
        bridge = WindowsBridge(policy)
        yield bridge, policy, apps, titles, handles, user
    finally:
        if bridge:
            bridge.close()
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_multi_process_capture_save_reopen_parameterize_replay(native_windows, tmp_path):
    from bender_ai.windows import WindowsCapture

    bridge, policy, apps, titles, handles, user = native_windows
    def selector(index, ident='1001', kind='Edit'):
        return Selector(apps[index], titles[index], ident, kind)
    recorder = Recorder(windows=list(zip(apps[:2], titles[:2])))
    recorder.start()
    capture = WindowsCapture(bridge.ui, recorder)
    def recording_context():
        return {'actions':[step.action for step in recorder.steps], 'diagnostics':list(recorder.diagnostics)}
    store = Store(tmp_path / 'skills.db')
    try:
        def ready():
            capture.poll()
            return capture.ready
        eventually(ready)
        for index, text in enumerate(['Synthetic Alpha', 'Synthetic Beta']):
            bridge.ui.focus(selector(index))
            eventually(lambda: user.GetForegroundWindow() == handles[index])
            bridge.ui.set_text(selector(index), text)
            def text_seen():
                capture.poll()
                return any(s.action == 'uia_set_text' and s.args['selector']['window'] == titles[index]
                           for s in recorder.steps)
            eventually(text_seen, context=recording_context)
            bridge.ui.click(selector(index, '1002', 'Button'))
            def click_seen():
                capture.poll()
                return any(s.action == 'uia_click' and s.args['selector']['window'] == titles[index]
                           for s in recorder.steps)
            eventually(click_seen, context=recording_context)
        # Standard Win32 checkbox exposes TogglePattern; the event must become a typed step.
        bridge.ui.toggle(selector(1, '1007', 'CheckBox'), 'on')
        def toggle_seen():
            capture.poll()
            return any(s.action == 'uia_toggle' for s in recorder.steps)
        eventually(toggle_seen, context=recording_context)
        bridge.ui.select(selector(1, '1008', 'RadioButton'))
        def selection_seen():
            capture.poll()
            return any(s.action == 'uia_select' and s.args['selector']['automation_id'] == '1008'
                       for s in recorder.steps)
        eventually(selection_seen, context=recording_context)
        for state in ('expanded', 'collapsed'):
            bridge.ui.expand(selector(1, '1006', 'ComboBox'), state)
            def expansion_seen():
                capture.poll()
                return any(s.action == 'uia_expand' and s.args['selector']['automation_id'] == '1006'
                           and s.args['state'] == state for s in recorder.steps)
            eventually(expansion_seen, context=recording_context)
        with pytest.raises(Denied):
            bridge.ui.set_text(selector(0, '1004'), 'synthetic-password-never-persist')
        with pytest.raises(Denied):
            bridge.ui.set_text(selector(2), 'private-window-never-persist')
        with pytest.raises(Denied):
            bridge.ui.set_text(selector(3), 'private-window-never-persist')
        # Test-driver Win32 input proves unselected windows/password values are not captured.
        user.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetDlgItem.restype = wintypes.HWND
        user.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user.SetWindowTextW(user.GetDlgItem(handles[2], 1001), 'private-window-never-persist')
        user.SetWindowTextW(user.GetDlgItem(handles[3], 1001), 'private-window-never-persist')
        user.SetWindowTextW(user.GetDlgItem(handles[0], 1004), 'synthetic-password-never-persist')
        capture.close()
        capture = None
        plan = recorder.finish('Multi-process greeting')
        serialized = plan.to_json()
        for private in ['Synthetic Alpha', 'Synthetic Beta', 'synthetic-password-never-persist',
                        'private-window-never-persist', titles[2], titles[3]]:
            assert private not in serialized
        assert len(plan.parameters) == 2
        assert sum(s.action == 'focus_window' for s in plan.steps) >= 2
        assert sum(s.action == 'wait_for_window' for s in plan.steps) >= 2
        for index, text in enumerate(['Hello Replay Alpha', 'Hello Replay Beta']):
            plan.steps.append(Step('assert_element', {'selector': selector(index, '1003', 'Text').to_dict(),
                                                     'expected_state': 'text:' + text}))
        skill = store.save_skill(plan)
        store.close()
        store = Store(tmp_path / 'skills.db')
        bridge.ui.toggle(selector(1, '1007', 'CheckBox'), 'off')
        bridge.ui.select(selector(1, '1009', 'RadioButton'))
        for index in range(2):
            bridge.ui.set_text(selector(index), '')
            bridge.ui.click(selector(index, '1005', 'Button'))
        runner = Executor(ActionRegistry(policy, store, ui=bridge.ui, desktop=bridge.desktop), store)
        result = runner.run(store.load_skill(skill).bind(dict(zip(plan.parameters, ['Replay Alpha', 'Replay Beta']))),
                            confirmed=True)
        assert result.status == 'success', result.error
        for index, text in enumerate(['Hello Replay Alpha', 'Hello Replay Beta']):
            assert bridge.ui.assert_state(selector(index, '1003', 'Text'), 'text:' + text)
        user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.SendMessageW.restype = ctypes.c_ssize_t
        assert user.SendMessageW(user.GetDlgItem(handles[1], 1007), 0x00F0, 0, 0) == 1  # BM_GETCHECK
        assert user.SendMessageW(user.GetDlgItem(handles[1], 1008), 0x00F0, 0, 0) == 1
        assert user.SendMessageW(user.GetDlgItem(handles[1], 1009), 0x00F0, 0, 0) == 0
        assert user.SendMessageW(user.GetDlgItem(handles[1], 1006), 0x0157, 0, 0) == 0  # CB_GETDROPPEDSTATE
        eventually(lambda: user.GetForegroundWindow() == handles[1])
        # Cancellation and timeout must prevent the subsequent real UI mutation.
        bridge.ui.set_text(selector(0), 'unchanged')
        cancelled = Plan([Step('stop', {}), Step('uia_set_text', {'selector': selector(0).to_dict(), 'text': 'forbidden'})])
        assert runner.run(cancelled, confirmed=True).status == 'cancelled'
        missing = selector(0, 'missing-id')
        timeout = Plan([Step('wait_for_element', {'selector': missing.to_dict(), 'timeout': .1}),
                        Step('uia_set_text', {'selector': selector(0).to_dict(), 'text': 'forbidden'})])
        assert runner.run(timeout, confirmed=True).status == 'failed'
        assert bridge.ui.assert_state(selector(0), 'text:unchanged')
        assert runner.run(Plan([Step('uia_set_text', {'selector': selector(0).to_dict(), 'text': 'forbidden'})]),
                          confirmed=True, cancellation=Cancellation(timeout=0)).status == 'failed'
        assert bridge.ui.assert_state(selector(0), 'text:unchanged')
    finally:
        if capture:
            capture.close()
        store.close()
