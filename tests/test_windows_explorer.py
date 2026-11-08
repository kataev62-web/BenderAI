"""Opt-in semantic operations on one newly opened, owned Explorer window."""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from bender_ai.security import Denied, Policy
from bender_ai.models import Selector

pytestmark = [pytest.mark.windows, pytest.mark.skipif(
    sys.platform != 'win32' or os.environ.get('BENDER_WINDOWS_INTEGRATION') != '1',
    reason='requires opt-in interactive Windows desktop')]


def eventually(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Owned Explorer condition did not become true')


@pytest.fixture
def owned_explorer(tmp_path):
    import win32con
    import win32gui
    import win32process
    from bender_ai.isolation import WindowsBridge
    from bender_ai.windows import process_path
    from windows_fixture_process import spawn_fixture

    root = tmp_path / ('BenderExplorer_' + uuid.uuid4().hex)
    root.mkdir()
    destination = root / 'Destination'
    destination.mkdir()
    executable = Path(os.environ['WINDIR']) / 'explorer.exe'
    before = []
    win32gui.EnumWindows(lambda hwnd, _: before.append(hwnd), None)
    process = spawn_fixture([str(executable), '/separate,', str(root)])
    owned = None
    bridge = None
    try:
        def find_owned():
            matches = []
            def consider(hwnd, _):
                if hwnd in before or win32gui.GetClassName(hwnd) not in ('CabinetWClass', 'ExploreWClass'):
                    return
                title = win32gui.GetWindowText(hwnd)
                if title in (root.name, str(root)):
                    matches.append((hwnd, title))
            win32gui.EnumWindows(consider, None)
            assert len(matches) <= 1, 'Owned Explorer window is ambiguous'
            return matches[0] if matches else None
        hwnd, title = eventually(find_owned)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        assert Path(process_path(pid)).resolve() == executable.resolve()
        owned = hwnd, pid
        policy = Policy(read_roots=[str(root)], write_roots=[str(root)])
        policy.grant_app('explorer.exe', title, executable=str(executable), control=True)
        selector = Selector('explorer.exe', title, '', 'Window', name=title)
        bridge = WindowsBridge(policy)
        def bind_ready():
            try:
                return bridge.explorer.bind(selector.to_dict())
            except Denied:
                # Shell registration can follow HWND creation; rejection still fails at the bound.
                return None
        state = eventually(bind_ready)
        assert state['hwnd'] == hwnd
        assert Path(state['path']).resolve() == root.resolve()
        yield bridge, policy, selector, root, destination
    finally:
        if bridge:
            bridge.close()
        try:
            if owned:
                hwnd, pid = owned
                if win32gui.IsWindow(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    eventually(lambda: not win32gui.IsWindow(hwnd), timeout=5)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_native_explorer_navigation_create_capture_save_reopen_replay(owned_explorer, tmp_path):
    from bender_ai.actions import ActionRegistry, Executor
    from bender_ai.explorer import ExplorerCapture
    from bender_ai.learning import Recorder
    from bender_ai.storage import Store

    bridge, policy, selector, root, destination = owned_explorer
    recorder = Recorder(selector.app, selector.window)
    recorder.start()
    capture = ExplorerCapture(bridge.explorer, recorder, selector)
    store = Store(tmp_path / 'explorer-skills.db')
    created = destination / 'SyntheticCreated'
    try:
        capture.poll()
        state = capture.navigate(str(destination))
        assert Path(state['path']).resolve() == destination.resolve()
        with pytest.raises(Denied):
            capture.create_subfolder(created.name, confirmed=False)
        assert not created.exists()
        state = capture.create_subfolder(created.name, confirmed=True)
        assert created.is_dir()
        assert Path(state['created']).resolve() == created.resolve()
        with pytest.raises(Denied):
            bridge.explorer.navigate(selector.to_dict(), str(tmp_path))
        assert Path(bridge.explorer.snapshot(selector.to_dict())['path']).resolve() == destination.resolve()
        plan = recorder.finish('Owned Explorer navigation and create')
        assert any(step.action == 'explorer_navigate' for step in plan.steps)
        assert any(step.action == 'create_folder' for step in plan.steps)
        skill = store.save_skill(plan)
        store.close()
        store = Store(tmp_path / 'explorer-skills.db')
        # Remove only the empty synthetic demonstration result so replay has an observable effect.
        created.rmdir()
        bridge.explorer.navigate(selector.to_dict(), str(root))
        registry = ActionRegistry(policy, store, ui=bridge.ui, desktop=bridge.desktop,
                                  explorer=bridge.explorer)
        result = Executor(registry, store).run(store.load_skill(skill), confirmed=True)
        assert result.status == 'success', result.error
        assert created.is_dir()
        assert Path(bridge.explorer.snapshot(selector.to_dict())['path']).resolve() == destination.resolve()
    finally:
        capture.close()
        store.close()
