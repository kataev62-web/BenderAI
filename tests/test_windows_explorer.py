"""Opt-in semantic operations on one newly opened, owned Explorer window."""
import os
import json
from pathlib import Path
import sys
import time
import uuid

import pytest

from bender_ai.security import Denied, Policy
from bender_ai.models import Selector

pytestmark = [pytest.mark.windows, pytest.mark.skipif(
    sys.platform != 'win32' or os.environ.get('BENDER_WINDOWS_INTEGRATION') != '1',
    reason='requires opt-in interactive Windows desktop')]


def eventually(predicate, timeout=20, context=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    detail = '; ' + json.dumps(context(), sort_keys=True) if context else ''
    raise AssertionError('Owned Explorer condition did not become true' + detail)


@pytest.fixture
def owned_explorer(tmp_path):
    import win32con
    import win32gui
    import win32process
    from bender_ai.isolation import WindowsBridge
    from bender_ai.windows import process_path

    root = tmp_path / ('BenderExplorer_' + uuid.uuid4().hex)
    root.mkdir()
    destination = root / 'Destination'
    destination.mkdir()
    executable = Path(os.environ['WINDIR']) / 'explorer.exe'
    before = []
    win32gui.EnumWindows(lambda hwnd, _: before.append(hwnd), None)
    owned = None
    policy = Policy(read_roots=[str(root)], write_roots=[str(root)])
    bridge = WindowsBridge(policy)
    def startup_diagnostics():
        import win32api
        import win32security
        context = {'launch': 'trusted-shell-open-folder', 'new_cabinet_windows': 0,
                   'synthetic_title_matches': 0, 'existing_synthetic_title_matches': 0,
                   'trusted_processes': []}
        try:
            token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
            try:
                context['runner_elevated'] = bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
                integrity = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
                sid = integrity[0] if isinstance(integrity, tuple) else integrity
                context['runner_integrity_rid'] = sid.GetSubAuthority(sid.GetSubAuthorityCount() - 1)
            finally:
                token.Close()
        except Exception as exc:
            context['runner_metadata_error'] = type(exc).__name__
        seen_pids = set()
        def inspect(hwnd, _):
            if win32gui.GetClassName(hwnd) not in ('CabinetWClass', 'ExploreWClass'):
                return
            matches_target = win32gui.GetWindowText(hwnd) in (root.name, str(root))
            if hwnd in before:
                context['existing_synthetic_title_matches'] += int(matches_target)
                return
            context['new_cabinet_windows'] += 1
            context['synthetic_title_matches'] += int(matches_target)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in seen_pids:
                return
            seen_pids.add(pid)
            try:
                if Path(process_path(pid)).resolve() != executable.resolve():
                    return
                info = {'pid': pid}
                handle = win32api.OpenProcess(0x1000, False, pid)
                try:
                    info['exit_code'] = win32process.GetExitCodeProcess(handle)
                    token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
                    try:
                        info['elevated'] = bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
                        integrity = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
                        sid = integrity[0] if isinstance(integrity, tuple) else integrity
                        info['integrity_rid'] = sid.GetSubAuthority(sid.GetSubAuthorityCount() - 1)
                    finally:
                        token.Close()
                finally:
                    handle.Close()
                context['trusted_processes'].append(info)
            except Exception as exc:
                context['trusted_processes'].append({'pid': pid, 'metadata_error': type(exc).__name__})
        win32gui.EnumWindows(inspect, None)
        return context
    try:
        # Use the same trusted Shell association as the application and frozen probe.
        bridge.desktop.open_folder(str(root))
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
        hwnd, title = eventually(find_owned, context=startup_diagnostics)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        assert Path(process_path(pid)).resolve() == executable.resolve()
        owned = hwnd, pid
        policy.grant_app('explorer.exe', title, executable=str(executable), control=True)
        selector = Selector('explorer.exe', title, '', 'Window', name=title)
        def bind_ready():
            try:
                return bridge.explorer.bind(selector.to_dict())
            except Denied:
                # Shell registration can follow HWND creation; rejection still fails at the bound.
                return None
        state = eventually(bind_ready, context=startup_diagnostics)
        assert state['hwnd'] == hwnd
        assert Path(state['path']).resolve() == root.resolve()
        yield bridge, policy, selector, root, destination
    finally:
        if bridge:
            bridge.close()
        if owned:
            hwnd, pid = owned
            if win32gui.IsWindow(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                eventually(lambda: not win32gui.IsWindow(hwnd), timeout=5)


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
