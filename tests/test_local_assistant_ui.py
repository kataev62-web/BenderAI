import time
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from bender_ai.app import MainWindow
from bender_ai.editor import StepDialog
from bender_ai.models import Plan, Step
from bender_ai.storage import Store

@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication([])

@pytest.fixture
def window(qt, tmp_path, monkeypatch):
    store = Store(tmp_path / 'ui.db')
    win = MainWindow(store, test_mode=True)
    win.autostart_calls = []
    monkeypatch.setattr('bender_ai.app.autostart',win.autostart_calls.append)
    yield win
    win.shutdown()
    store.close()

def drain(qt, window):
    end = time.monotonic() + 3
    while window.task is not None and time.monotonic() < end:
        qt.processEvents()
        time.sleep(.005)
    assert window.task is None

def test_saved_skill_command_previews_without_model_or_execution(window, monkeypatch):
    plan = Plan([Step('open_app', {'app_id': 'calculator'})], 'Мои расчёты')
    window.store.save_skill(plan)
    monkeypatch.setattr(window, 'provider', lambda: pytest.fail('saved skills must not call model'))
    window.input.setText('Выполни навык МОИ РАСЧЁТЫ')
    window.send()
    assert window.pending.name == 'Мои расчёты'
    assert window.store.runs() == []

def test_planning_passes_token_and_stop_cancels(window, qt, monkeypatch):
    tokens = []
    def plan(text, cancellation=None):
        tokens.append(cancellation)
        cancellation.event.wait(1)
        return Plan([Step('list_skills')])
    monkeypatch.setattr(window, 'provider', lambda: SimpleNamespace(plan=plan))
    window.input.setText('Неизвестная задача')
    window.send()
    end = time.monotonic() + 1
    while not tokens and time.monotonic() < end:
        qt.processEvents()
        time.sleep(.005)
    window.stop()
    drain(qt, window)
    assert tokens and tokens[0] is not None and tokens[0].event.is_set()
    assert window.pending is None

def test_status_label_shows_saved_provider_and_model(window):
    window.store.set_setting('ai', {'provider': 'ollama', 'model': 'qwen2.5-coder:3b'})
    window.refresh_provider_status()
    assert 'Ollama' in window.provider_status.text()
    assert 'qwen2.5-coder:3b' in window.provider_status.text()

def test_unverified_result_is_russian_and_not_success(window):
    window.on_run(SimpleNamespace(status='unverified', error='Окно пока не найдено', outputs=[]))
    assert 'не подтверждено' in window.statusBar().currentMessage().casefold()
    assert window.character.state != 'success'

def test_standard_editor_buttons_are_russian(window):
    dialog = StepDialog(Step('list_skills'), window)
    box = dialog.findChild(QDialogButtonBox)
    assert box.button(QDialogButtonBox.StandardButton.Save).text().replace('&', '') == 'Сохранить'
    assert box.button(QDialogButtonBox.StandardButton.Cancel).text().replace('&', '') == 'Отмена'

def test_ollama_check_is_async_and_displays_missing_model(window, qt, monkeypatch):
    import bender_ai.app as module
    calls = []
    def status(self, cancellation=None):
        calls.append(cancellation)
        return {'connected': True, 'installed': False, 'model': 'qwen2.5-coder:3b', 'message': 'Модель не установлена'}
    monkeypatch.setattr(module.OllamaProvider, 'status', status, raising=False)
    window.check_ollama()
    assert window.task is not None
    drain(qt, window)
    assert calls[0] is not None
    assert 'Модель не установлена' in window.provider_status.text()

def test_duplicate_skill_names_require_manual_selection(window, monkeypatch):
    for _ in range(2):
        window.store.save_skill(Plan([Step('list_skills')], 'Дубликат'))
    monkeypatch.setattr(window, 'provider', lambda: pytest.fail('ambiguous skill must not reach AI'))
    errors = []
    window.on_error = errors.append
    window.input.setText('Дубликат')
    window.send()
    assert window.pending is None
    assert errors and 'Несколько навыков' in errors[0]

def test_russian_settings_persist_provider_model(window, monkeypatch):
    from PySide6.QtWidgets import QDialog, QComboBox, QLineEdit
    def save(dialog):
        combo = next(c for c in dialog.findChildren(QComboBox) if c.findData('ollama') >= 0)
        assert combo.itemText(combo.findData('offline')).startswith('Офлайн')
        combo.setCurrentIndex(combo.findData('ollama'))
        model = next(c for c in dialog.findChildren(QLineEdit) if 'qwen' in c.placeholderText())
        model.setText('qwen2.5-coder:3b')
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', save)
    window.settings()
    assert window.store.setting('ai')['provider'] == 'ollama'
    assert window.autostart_calls == [False]
    assert 'qwen2.5-coder:3b' in window.provider_status.text()

def test_ollama_failure_clears_checking_label(window, qt, monkeypatch):
    import bender_ai.app as module
    from bender_ai.brain import ProviderConnectionError
    def status(self, cancellation=None):
        raise ProviderConnectionError('Ollama недоступна; офлайн-команды доступны.')
    monkeypatch.setattr(module.OllamaProvider, 'status', status, raising=False)
    window.check_ollama()
    drain(qt, window)
    assert 'недоступна' in window.provider_status.text()

def test_ready_plan_status_waits_for_user_execution(window):
    window.statusBar().showMessage('Выполняется задача…')
    window.accept_plan(Plan([Step('list_skills')]))
    assert 'План готов' in window.statusBar().currentMessage()
    assert window.store.runs() == []

@pytest.mark.parametrize('consent', [False, True])
def test_app_launch_permission_requires_explicit_mode_and_positive_consent(window, monkeypatch, consent):
    from PySide6.QtWidgets import QInputDialog
    choices = iter([('writer.exe • Draft', True), ('Чтение, управление и запуск', True)])
    offered = []
    def choose(*args):
        offered.append(args[3])
        return next(choices)
    monkeypatch.setattr(QInputDialog, 'getItem', choose)
    previews = []
    window.confirm = lambda title, text: previews.append(text) or consent
    window.choose_app_candidate([{'app': 'writer.exe', 'title': 'Draft', 'executable': '/trusted/writer.exe'}])
    assert 'Чтение, управление и запуск' in offered[1]
    assert 'запуск' in previews[0]
    if consent:
        assert window.store.setting('policy')['launch_apps'] == ['writer.exe']
        assert window.policy.ui_apps == ['writer.exe']
    else:
        assert window.policy.read_apps == []
        assert window.store.setting('policy', {}) == {}


def test_invalid_saved_model_reports_error_without_starting_a_task(window):
    window.store.set_setting('ai',{'provider':'ollama','model':'invalid model name'})
    errors=[]
    window.on_error=errors.append
    window.check_ollama()
    assert errors and window.task is None


def test_ollama_available_label_does_not_claim_unchecked(window, qt, monkeypatch):
    import bender_ai.app as module
    window.store.set_setting('ai', {'provider': 'ollama', 'model': 'qwen2.5-coder:3b'})
    monkeypatch.setattr(module.OllamaProvider, 'status', lambda self, cancellation=None: {
        'connected': True, 'installed': True, 'model': self.model, 'message': 'Ollama доступна, модель установлена'})
    window.check_ollama()
    drain(qt, window)
    assert 'доступна' in window.provider_status.text()
    assert 'не проверено' not in window.provider_status.text()


def test_launch_result_uses_russian_message_instead_of_internal_json(window):
    window.on_run(SimpleNamespace(status='success', error='', outputs=[{
        'status': 'verified', 'message': 'Приложение уже открыто, окно найдено.',
        'executable': 'writer.exe', 'already_open': True}]))
    displayed = window.chat.toPlainText()
    assert 'Приложение уже открыто, окно найдено.' in displayed
    assert 'already_open' not in displayed and 'verified' not in displayed


def test_plan_preview_is_russian_complete_and_quotes_untrusted_values(window):
    selector = {'app': 'writer.exe', 'window': 'Draft', 'automation_id': 'body', 'control_type': 'Edit',
                'name': 'Имя', 'parents': ['panel']}
    plan = Plan([Step('open_app', {'app_id': 'writer.exe'}),
                 Step('uia_set_text', {'selector': selector, 'text': '${value_1}\n9. Ложный шаг'}),
                 Step('wait_for_element', {'selector': selector, 'timeout': 7})], 'Мой план', ['value_1'])
    before = plan.to_json()
    window.accept_plan(plan)
    displayed = window.plan_view.toPlainText()
    assert 'Открыть приложение' in displayed and 'Ввести текст' in displayed
    for value in ['writer.exe', 'Draft', 'body', 'Edit', 'Имя', 'panel', '${value_1}', '7']:
        assert value in displayed
    assert '\n9. Ложный шаг' not in displayed
    assert '\\n9. Ложный шаг' in displayed
    assert 'uia_set_text' not in displayed
    assert plan.to_json() == before and window.store.runs() == []


def test_bound_parameter_preview_precedes_confirmation_and_rejection_executes_nothing(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    selector = {'app': 'writer.exe', 'window': 'Draft', 'automation_id': 'body', 'control_type': 'Edit'}
    plan = Plan([Step('uia_set_text', {'selector': selector, 'text': '${value_1}'}),
                 Step('write_text_file', {'path': '/allowed/result.txt', 'text': 'Результат'})],
                'Текст', ['value_1'])
    window.accept_plan(plan)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args: ('Проверить\nзначение', True))
    seen = []
    window.confirm = lambda *args: seen.append(window.plan_view.toPlainText()) or False
    window.execute_pending()
    assert seen and 'Проверить\\nзначение' in seen[0]
    assert '${value_1}' not in seen[0]
    assert '/allowed/result.txt' in seen[0]
    assert window.pending is plan and window.pending.parameters == ['value_1']
    assert window.task is None and window.store.runs() == []


def test_failed_capture_cannot_resume_or_display_active_recording(window):
    from bender_ai.learning import Recorder
    window.recorder = Recorder('writer.exe', 'Draft')
    window.recorder.start()
    window.recorder.pause()  # CaptureTask pauses the recorder before emitting its failure.
    window.recording_failed('Запись приостановлена: ошибка UIA.')
    window.pause_recording()
    assert window.recorder.paused
    assert 'ВКЛЮЧЕНА' not in window.record_label.text()
    assert 'черновик' in window.record_label.text()
    window.recording_count(4)  # A queued count/ready event cannot restore a live indicator.
    assert 'ВКЛЮЧЕНА' not in window.record_label.text()
    assert 'черновик' in window.record_label.text()
    window.cancel_recording()


def test_manual_capture_pause_resume_remains_available(window):
    from bender_ai.learning import Recorder
    window.recorder = Recorder('writer.exe', 'Draft')
    window.recorder.start()
    window.pause_recording()
    assert window.recorder.paused
    window.pause_recording()
    assert not window.recorder.paused
    assert 'ВКЛЮЧЕНА' in window.record_label.text()
    window.cancel_recording()


def test_stale_capture_failure_after_recording_is_gone_is_ignored(window):
    before = window.record_label.text()
    errors = []
    window.on_error = errors.append
    window.recording_failed('Запоздалое событие UIA')
    assert window.record_label.text() == before
    assert errors == []
