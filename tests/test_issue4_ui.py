from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication
from bender_ai.app import MainWindow
from bender_ai.storage import Store

@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'ui.db')
    win = MainWindow(store, test_mode=True)
    yield win
    win.shutdown()
    store.close()

def test_default_send_routes_question_to_conversation_without_plan(window, monkeypatch):
    import bender_ai.app as module
    window.store.set_setting('ai', {'provider': 'ollama'})
    monkeypatch.setattr(module.OllamaProvider, 'converse_cancelable', lambda self, text, cancellation=None: 'Ответ')
    invoked = []
    window.start_task = lambda function, callback: invoked.append((function, callback)) or True
    window.input.setText('Почему небо голубое?')
    window.send()
    assert invoked and invoked[0][0]() == 'Ответ'
    invoked[0][1]('Ответ')
    assert window.pending is None and window.store.runs() == []

def test_ambiguous_auto_input_requires_explicit_mode(window):
    errors = []
    window.on_error = errors.append
    window.input.setText('Работа с отчётом')
    window.send()
    assert window.task is None and window.pending is None
    assert errors and 'режим' in errors[0]

def test_talking_state_begins_only_after_speech_started_event(window):
    events = iter([('started', ''), ('ok', '')])
    window.speech = SimpleNamespace(speak=lambda *a, **k: None, poll=lambda: next(events), stop=lambda: None)
    window.store.set_setting('speech_enabled', True)
    window.on_talk_reply('Привет')
    assert window.character.state != 'talk'
    window.poll_speech()
    assert window.character.state == 'talk'
    window.poll_speech()
    assert window.character.state == 'idle'

def test_microphone_requires_consent_and_fills_input_without_sending(window, monkeypatch):
    import sys
    starts = []
    fake = SimpleNamespace(active=False, start=lambda *a, **kw: starts.append(kw),
                           poll=lambda: ('text', 'Открой калькулятор'), stop=lambda: None)
    monkeypatch.setitem(sys.modules, 'bender_ai.microphone', SimpleNamespace(
        MicrophoneController=lambda: fake,
        microphone_status=lambda path: {'available': True, 'reason': 'Готово'}))
    window.confirm = lambda *args: False
    window.start_microphone()
    assert not starts
    window.confirm = lambda *args: True
    window.start_microphone()
    assert starts == [{'consent': True, 'timeout': 15}]
    window.poll_microphone()
    assert window.input.text() == 'Открой калькулятор'
    assert window.pending is None and window.store.runs() == []


def test_glb_import_requires_license_confirmation_and_only_persists_valid_pack(window, monkeypatch, tmp_path):
    import bender_ai.app as module
    import bender_ai.avatar_import as avatar
    from pathlib import Path
    paths = iter([('/local/model.glb', ''), ('/usr/bin/blender', '')])
    monkeypatch.setattr(module.QFileDialog, 'getOpenFileName', lambda *args: next(paths))
    window.confirm = lambda *args: False
    window.import_avatar()
    assert window.task is None and window.store.setting('character_pack') is None
    paths = iter([('/local/model.glb', ''), ('/usr/bin/blender', '')])
    window.confirm = lambda *args: True
    monkeypatch.setattr(module, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(avatar, 'import_glb', lambda *args, **kwargs: tmp_path / 'pack')
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import Qt
    from bender_ai.character import STATES
    image = QPixmap(10, 10)
    image.fill(Qt.GlobalColor.white)
    pack = module.CharacterPack({state: [image] for state in STATES})
    monkeypatch.setattr(module.CharacterPack, 'load', lambda path: pack)
    tasks = []
    window.start_task = lambda fn, cb: tasks.append((fn, cb)) or True
    window.import_avatar()
    assert window.store.setting('character_pack') is None
    tasks[0][1](tasks[0][0]())
    assert window.store.setting('character_pack') == str(tmp_path / 'pack')


def test_voice_preferences_are_saved_and_used_for_playback(window):
    calls = []
    window.speech = SimpleNamespace(speak=lambda *args, **kwargs: calls.append(kwargs), stop=lambda: None)
    window.store.set_setting('speech_enabled', True)
    window.store.set_setting('speech_voice', 'local-ru-voice')
    window.store.set_setting('speech_rate', -3)
    window.on_talk_reply('Привет')
    assert calls == [{'voice_id': 'local-ru-voice', 'rate': -3}]


def test_settings_save_russian_voice_rate_and_microphone_model(window, monkeypatch):
    from PySide6.QtWidgets import QDialog, QComboBox, QSpinBox, QLineEdit
    def save(dialog):
        voices = next(c for c in dialog.findChildren(QComboBox) if 'русский голос' in c.itemText(0))
        voices.addItem('Русский установленный голос', 'local-ru')
        voices.setCurrentIndex(1)
        rate = next(c for c in dialog.findChildren(QSpinBox) if c.minimum() == -3)
        rate.setValue(-2)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', save)
    window.settings()
    assert window.store.setting('speech_voice') == 'local-ru'
    assert window.store.setting('speech_rate') == -2


def test_stop_cancels_microphone_and_speech(window):
    stopped = []
    window.microphone = SimpleNamespace(stop=lambda: stopped.append('mic'))
    window.speech = SimpleNamespace(stop=lambda: stopped.append('speech'))
    window.stop()
    assert stopped == ['mic', 'speech']


def test_conversation_reply_finishes_busy_status_without_claiming_os_execution(window):
    window.statusBar().showMessage('Выполняется задача…')
    window.on_talk_reply('Обычный текстовый ответ')
    assert 'Ответ получен' in window.statusBar().currentMessage()
    assert window.pending is None and window.store.runs() == []


def test_explorer_jit_permission_denial_starts_no_capture_and_grants_nothing(window, tmp_path):
    from bender_ai.explorer import ExplorerPermissionRequired
    selected = [('explorer.exe', 'Work')]
    window.bridge = SimpleNamespace(explorer=SimpleNamespace(), close=lambda: None)
    window.bridge.explorer.bind = lambda selector: (_ for _ in ()).throw(ExplorerPermissionRequired('read', tmp_path))
    tasks = []
    window.start_task = lambda fn, cb: tasks.append((fn, cb)) or True
    window.confirm = lambda *args: False
    before = window.policy.to_dict()
    window.prepare_explorer_recording(selected, False)
    tasks[0][1](tasks[0][0]())
    assert window.recorder is None and window.policy.to_dict() == before


def test_observed_explorer_creation_needs_confirmation_before_recording(window, tmp_path, monkeypatch):
    from bender_ai.learning import Recorder
    from bender_ai.explorer import ExplorerCapture
    from PySide6.QtWidgets import QInputDialog
    window.recorder = Recorder(windows=[('explorer.exe', 'Work')])
    window.recorder.start()
    capture = ExplorerCapture(None, window.recorder, dict(app='explorer.exe', window='Work', automation_id='__window__', control_type='Window'))
    capture.pending_creations.append({'parent': str(tmp_path), 'name': 'Example'})
    window.explorer_captures = [capture]
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: (args[3][0], True))
    window.confirm = lambda *args: False
    window.review_explorer_creations()
    assert window.recorder.steps == [] and window.policy.write_roots == []
    window.confirm = lambda *args: True
    window.review_explorer_creations()
    assert window.recorder.steps[0].action == 'create_folder'
    assert window.recorder.steps[0].args == {'parent': str(tmp_path), 'name': 'Example'}
    window.cancel_recording()


def test_topmost_choice_persists_and_preserves_position(window, monkeypatch):
    from PySide6.QtWidgets import QDialog, QCheckBox
    from PySide6.QtCore import Qt
    position = window.character.pos()
    def save(dialog):
        checkbox = dialog.findChild(QCheckBox, 'character_topmost')
        assert checkbox is not None
        checkbox.setChecked(False)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', save)
    window.settings()
    assert not window.character.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.character.pos() == position
    restarted = MainWindow(window.store, test_mode=True)
    assert not restarted.character.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    restarted.shutdown()


@pytest.mark.parametrize('consent', [False, True])
def test_missing_folder_permission_is_explicit_and_never_executes_after_grant(window, tmp_path, consent):
    import time
    from bender_ai.models import Plan, Step
    target = tmp_path / 'selected'
    target.mkdir()
    window.policy.read_roots = []
    window.policy.write_roots = []
    plan = Plan([Step('create_folder', {'parent': str(target), 'name': 'Example'})])
    window.accept_plan(plan)
    prompts = []
    window.confirm = lambda title, body: prompts.append(body) or consent
    window.execute_pending()
    app = QApplication.instance()
    end = time.monotonic() + 3
    while window.task is not None and time.monotonic() < end:
        app.processEvents()
        time.sleep(.005)
    assert prompts and str(target) in prompts[0]
    assert window.policy.write_roots == ([str(target)] if consent else [])
    assert window.pending is plan and window.store.runs() == []
    assert not (target / 'Example').exists()


@pytest.mark.parametrize('consent', [False, True])
def test_learning_candidate_picker_grants_only_selected_window_after_consent(window, monkeypatch, consent):
    import bender_ai.app as module
    candidates = [{'app': 'writer.exe', 'title': 'Draft', 'executable': '/trusted/writer.exe'},
                  {'app': 'calc.exe', 'title': 'Private', 'executable': '/trusted/calc.exe'}]
    monkeypatch.setattr(module.RecordingWindowsDialog, 'exec', lambda self: 1)
    monkeypatch.setattr(module.RecordingWindowsDialog, 'selected_windows', lambda self: [('writer.exe', 'Draft')])
    window.confirm = lambda *args: consent
    started = []
    window.begin_recording_selection = lambda windows, literal: started.append(windows)
    window.choose_recording_candidates(candidates)
    assert window.policy.ui_apps == (['writer.exe'] if consent else [])
    assert 'calc.exe' not in window.policy.read_apps
    assert window.policy.launch_apps == []
    assert started == ([[('writer.exe', 'Draft')]] if consent else [])
