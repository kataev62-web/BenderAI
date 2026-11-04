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
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: ('writer.exe • Draft', True))
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
