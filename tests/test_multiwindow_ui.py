import pytest
from PySide6.QtWidgets import QApplication, QDialog
from bender_ai.app import MainWindow
from bender_ai.editor import SkillEditor
from bender_ai.models import Plan, Step
from bender_ai.storage import Store


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt, tmp_path):
    store = Store(tmp_path / 'ui.db')
    window = MainWindow(store, test_mode=True)
    yield window
    window.shutdown()
    store.close()


def test_rejected_candidate_grants_nothing(window, monkeypatch):
    from bender_ai.app import QInputDialog
    answers = iter([('writer.exe • Draft', True), ('Чтение и управление', True)])
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: next(answers))
    window.confirm = lambda *a: False
    window.choose_app_candidate([{'app': 'writer.exe', 'title': 'Draft', 'executable': '/trusted/writer.exe'}])
    assert window.policy.read_apps == []
    assert window.policy.ui_apps == []


def test_recording_window_picker_keeps_multiple_exact_choices(qt):
    from bender_ai.app import RecordingWindowsDialog
    dialog = RecordingWindowsDialog([('writer.exe', 'Draft'), ('calc.exe', 'Totals')])
    for i in range(dialog.windows.count()):
        dialog.windows.item(i).setCheckState(__import__('PySide6.QtCore', fromlist=['Qt']).Qt.CheckState.Checked)
    assert dialog.selected_windows() == [('writer.exe', 'Draft'), ('calc.exe', 'Totals')]
    assert not dialog.literal_text.isChecked()


def test_editor_displays_app_window_element_and_adds_wait(qt):
    selector = dict(app='writer.exe', window='Draft', automation_id='save', control_type='Button')
    editor = SkillEditor(Plan([Step('uia_click', {'selector': selector})]))
    assert editor.table.columnCount() == 5
    assert [editor.table.item(0, i).text() for i in (1, 2, 3)] == ['writer.exe', 'Draft', 'save']
    editor.table.selectRow(0)
    editor.add_wait_window()
    assert editor.plan.steps[1].action == 'wait_for_window'
    editor.table.selectRow(0)
    editor.down()
    assert editor.plan.steps[0].action == 'wait_for_window'


def test_diagnostics_are_visible_readonly(window):
    assert window.record_diagnostics.isReadOnly()
    window.recording_diagnostic('unsupported_pattern')
    assert 'unsupported_pattern' in window.record_diagnostics.toPlainText()


def test_candidate_consent_persists_exact_read_only_scope(window, monkeypatch):
    from bender_ai.app import QInputDialog
    answers = iter([('writer.exe • Draft', True), ('Только чтение', True)])
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: next(answers))
    confirmations = []
    window.confirm = lambda title, body: confirmations.append(body) or True
    window.choose_app_candidate([{'app': 'writer.exe', 'title': 'Draft', 'executable': '/trusted/writer.exe'}])
    assert window.policy.read_apps == ['writer.exe']
    assert window.policy.ui_apps == []
    assert window.policy.window_scopes['writer.exe'] == ['Draft']
    assert '/trusted/writer.exe' in confirmations[0]
    assert window.store.setting('policy')['window_scopes']['writer.exe'] == ['Draft']


def test_capture_diagnostics_signal_contains_recorder_reasons(qt):
    from bender_ai.infrastructure import CaptureTask
    from types import SimpleNamespace
    recorder = SimpleNamespace(steps=[], diagnostics=[], pause=lambda: None)
    capture = SimpleNamespace(recorder=recorder, ready=True)
    task = CaptureTask(capture)
    def poll():
        recorder.diagnostics.append('unsupported_pattern')
        task.stop_event.set()
    capture.poll = poll
    capture.close = lambda: None
    received = []
    task.diagnostic.connect(received.append)
    task.run()
    assert received == ['unsupported_pattern']


def test_multiwindow_selection_starts_recorder_and_excludes_read_only(window, monkeypatch):
    import bender_ai.app as app_module
    import bender_ai.windows as native
    from types import SimpleNamespace
    from PySide6.QtCore import Qt
    window.policy.grant_app('writer.exe', 'Draft', '/trusted/writer.exe', control=True)
    window.policy.grant_app('writer.exe', 'Private', '/trusted/writer.exe', control=False)
    window.policy.grant_app('calc.exe', 'Totals', '/trusted/calc.exe', control=True)
    offered = []
    def choose(dialog):
        offered.extend(dialog.windows.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dialog.windows.count()))
        for i in range(dialog.windows.count()):
            dialog.windows.item(i).setCheckState(Qt.CheckState.Checked)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(app_module.RecordingWindowsDialog, 'exec', choose)
    monkeypatch.setattr(native, 'WindowsCapture', lambda ui, recorder: SimpleNamespace(recorder=recorder))
    monkeypatch.setattr(app_module.CaptureTask, 'start', lambda self: None)
    window.confirm = lambda *args: True
    window.choose_recording_window([('writer.exe', 'Draft'), ('writer.exe', 'Private'), ('calc.exe', 'Totals')])
    assert offered == [('writer.exe', 'Draft'), ('calc.exe', 'Totals')]
    assert window.recorder.windows == (('writer.exe', 'Draft'), ('calc.exe', 'Totals'))
    assert not window.recorder.literal_text
    window.recording_count(3)
    assert '2' in window.record_label.text() and '3' in window.record_label.text()
    window.stop()
    assert window.recorder.paused
    window.cancel_recording()


def test_recording_window_limit_is_reported_before_capture(window, monkeypatch):
    import bender_ai.app as app_module
    choices = [('writer.exe', f'Draft {i}') for i in range(13)]
    for app, title in choices:
        window.policy.grant_app(app, title, '/trusted/writer.exe', control=True)
    monkeypatch.setattr(app_module.RecordingWindowsDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(app_module.RecordingWindowsDialog, 'selected_windows', lambda self: choices)
    window.confirm = lambda *args: True
    errors = []
    window.on_error = errors.append
    window.choose_recording_window(choices)
    assert window.recorder is None
    assert errors and '12' in errors[0]
