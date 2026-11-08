"""Bounded package probe: dependency loading and inventory, never audio capture."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def _probe_explorer():
    """Operate only on a fresh synthetic folder and a newly opened exact window."""
    import win32con
    import win32gui
    import win32process
    from .actions import ActionRegistry, Executor
    from .explorer import ExplorerCapture
    from .isolation import WindowsBridge
    from .learning import Recorder
    from .models import Selector
    from .security import Denied, Policy
    from .storage import Store
    from .windows import process_path

    with tempfile.TemporaryDirectory(prefix='BenderExplorerFeature_') as folder:
        root = Path(folder)
        destination = root / 'Destination'
        destination.mkdir()
        executable = Path(os.environ['WINDIR']) / 'explorer.exe'
        before = []
        win32gui.EnumWindows(lambda hwnd, _: before.append(hwnd), None)
        policy = Policy(read_roots=[str(root)], write_roots=[str(root)])
        bridge = WindowsBridge(policy)
        owned = None
        store = None
        try:
            bridge.desktop.open_folder(str(root))
            deadline = time.monotonic() + 20
            while owned is None and time.monotonic() < deadline:
                matches = []
                def consider(hwnd, _):
                    if hwnd not in before and win32gui.GetClassName(hwnd) in ('CabinetWClass', 'ExploreWClass'):
                        title = win32gui.GetWindowText(hwnd)
                        if title in (root.name, str(root)):
                            matches.append((hwnd, title))
                win32gui.EnumWindows(consider, None)
                if len(matches) > 1:
                    raise RuntimeError('Synthetic Explorer window is ambiguous')
                if matches:
                    hwnd, title = matches[0]
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if Path(process_path(pid)).resolve() != executable.resolve():
                        raise RuntimeError('Synthetic Explorer identity mismatch')
                    owned = hwnd, pid
                else:
                    time.sleep(.05)
            if owned is None:
                raise RuntimeError('Synthetic Explorer window was not created')
            policy.grant_app('explorer.exe', title, executable=str(executable), control=True)
            selector = Selector('explorer.exe', title, '', 'Window', name=title)
            # Wait only for registration of the exact new HWND; protected targets remain denied.
            deadline = time.monotonic() + 12
            while True:
                try:
                    state = bridge.explorer.bind(selector.to_dict())
                    break
                except Denied:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.05)
            if state['hwnd'] != owned[0] or Path(state['path']).resolve() != root.resolve():
                raise RuntimeError('Synthetic Explorer binding mismatch')
            recorder = Recorder(selector.app, selector.window)
            recorder.start()
            capture = ExplorerCapture(bridge.explorer, recorder, selector)
            capture.poll()
            capture.navigate(str(destination))
            created = destination / 'SyntheticCreated'
            capture.create_subfolder(created.name, confirmed=True)
            if not created.is_dir():
                raise RuntimeError('Synthetic Explorer creation was not verified')
            plan = recorder.finish('Packaged synthetic Explorer probe')
            capture.close()
            store = Store(root / 'probe.db')
            skill = store.save_skill(plan)
            store.close()
            store = Store(root / 'probe.db')
            created.rmdir()  # Only the empty synthetic demonstration result.
            bridge.explorer.navigate(selector.to_dict(), str(root))
            runner = Executor(ActionRegistry(policy, store, ui=bridge.ui, desktop=bridge.desktop,
                                             explorer=bridge.explorer), store)
            result = runner.run(store.load_skill(skill), confirmed=True)
            state = bridge.explorer.snapshot(selector.to_dict())
            if result.status != 'success' or not created.is_dir() or Path(state['path']).resolve() != destination.resolve():
                raise RuntimeError('Synthetic Explorer replay was not verified')
        finally:
            if store is not None:
                store.close()
            bridge.close()
            if owned:
                hwnd, pid = owned
                if win32gui.IsWindow(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    deadline = time.monotonic() + 5
                    while win32gui.IsWindow(hwnd) and time.monotonic() < deadline:
                        time.sleep(.05)
                    if win32gui.IsWindow(hwnd):
                        raise RuntimeError('Synthetic Explorer window did not close')


def run(report_path):
    from . import __version__
    report = {'status': 'running', 'exit_code': 1,
              'app_version': __version__,
              'started_at': datetime.now(timezone.utc).isoformat(),
              'frozen': bool(getattr(sys, 'frozen', False)),
              'microphone_capture_started': False, 'speaker_playback_verified': False,
              'microphone_hardware_verified': False, 'speech_model_verified': False}
    code = 1
    try:
        if sys.platform != 'win32':
            raise RuntimeError('Windows feature probe requires Windows')
        import sounddevice
        import vosk
        from .microphone import MicrophoneController, microphone_status
        from .speech import SpeechController, list_voices

        if not callable(vosk.Model) or sounddevice.get_portaudio_version()[0] <= 0:
            raise RuntimeError('Voice runtime did not load')
        report['voice_dependencies_loaded'] = True
        voices = list_voices()
        report['sapi_voice_count'] = len(voices)
        report['sapi_russian_voice_count'] = sum(bool(voice['russian']) for voice in voices)
        speaker = SpeechController()
        try:
            speaker.speak('Проверка остановки синтеза речи.', voice_id=voices[0]['id'] if voices else '')
            if speaker.process is None or speaker.process.pid is None:
                raise RuntimeError('Speech worker did not start')
            started = time.monotonic()
            speaker.stop()
            if speaker.process is not None or time.monotonic() - started > 3:
                raise RuntimeError('Speech worker did not stop within the bound')
            report['speech_spawn_stop_verified'] = True
        finally:
            speaker.stop()
        with tempfile.TemporaryDirectory(prefix='bender-voice-probe-') as folder:
            missing = Path(folder) / 'model-not-installed'
            status = microphone_status(missing)
            if status['available'] is not False or not status['reason']:
                raise RuntimeError('Missing microphone model was not reported')
            report['missing_model_reported'] = True
        microphone = MicrophoneController()
        try:
            if microphone.active:
                raise RuntimeError('Microphone started without consent')
            microphone.stop()
            if microphone.active:
                raise RuntimeError('Microphone did not remain stopped')
            report['microphone_inactive_by_default'] = True
        finally:
            microphone.stop()
        report['phase'] = 'explorer'
        _probe_explorer()
        report['explorer_navigate_create_replay_verified'] = True
        code = 0
        report.update(status='passed', exit_code=0)
    except Exception as exc:
        # Native exception messages may contain device/user paths; retain only type.
        report.update(status='failed', exception_type=type(exc).__name__)
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return code
