from pathlib import Path
import json
import os
import subprocess
import sys
from PySide6.QtCore import Qt, QTimer, QLockFile
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTextBrowser, QLineEdit, QPushButton, QLabel, QListWidget, QSystemTrayIcon,
    QMenu, QMessageBox, QFileDialog, QInputDialog, QDialog, QFormLayout, QCheckBox,
    QComboBox, QSpinBox, QDialogButtonBox, QPlainTextEdit, QListWidgetItem)
from .actions import ActionRegistry, Executor, Cancellation
from .brain import RuleBasedProvider, OllamaProvider, CloudProvider, DEFAULT_OLLAMA_MODEL
from .character import Character, CharacterPack
from .editor import SkillEditor, LABELS, readable_plan
from .infrastructure import Task, CaptureTask, EmergencyHotkey, data_dir, autostart, install_russian_ui
from .learning import Recorder
from .speech import SpeechController
from .models import Plan, ValidationError
from .security import Policy, SecretVault, Denied
from .storage import Store


STATUS_LABELS = {'success': 'Успешно', 'unverified': 'Не подтверждено', 'failed': 'Ошибка',
                 'error': 'Ошибка', 'cancelled': 'Отменено', 'unvalidated': 'Не проверено',
                 'pending': 'Ожидает проверки', 'running': 'Выполняется', 'skipped': 'Пропущено', 'interrupted': 'Прервано'}


def readable_result(value):
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return 'Да' if value else 'Нет'
    if value is None:
        return 'Нет данных'
    if isinstance(value, list):
        return '\n'.join(readable_result(item) for item in value) or 'Список пуст'
    if isinstance(value, dict):
        if isinstance(value.get('message'), str):
            message = value['message']
            if value.get('executable'):
                message += '\nФайл приложения: ' + json.dumps(value['executable'], ensure_ascii=False)
            return message
        labels = {'process': 'Процесс', 'title': 'Окно', 'elements': 'Элементы',
                  'automation_id': 'ID элемента', 'control_type': 'Тип элемента',
                  'name': 'Название', 'id': 'Идентификатор', 'created_at': 'Создано',
                  'current_version': 'Текущая версия', 'status': 'Состояние',
                  'executable': 'Файл приложения', 'already_open': 'Было открыто ранее',
                  'exists': 'Существует', 'enabled': 'Доступен', 'visible': 'Виден'}
        return '\n'.join(labels.get(key, key) + ': ' + readable_result(item) for key, item in value.items())
    return str(value)


class RecordingWindowsDialog(QDialog):
    def __init__(self, windows, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Бендер, учись — выберите окна')
        self.resize(620, 430)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Выберите от 1 до 12 разрешённых окон. Переключайтесь между ними вручную.'))
        self.windows = QListWidget()
        for app, title in windows:
            item = QListWidgetItem(app + ' • ' + title)
            item.setData(Qt.ItemDataRole.UserRole, (app, title))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.windows.addItem(item)
        layout.addWidget(self.windows)
        self.literal_text = QCheckBox('Сохранять несекретные значения текста (по умолчанию только параметры)')
        layout.addWidget(self.literal_text)
        layout.addWidget(QLabel('Пароли исключены. UIA-паттерны зависят от приложения. Неподдерживаемые действия показаны в диагностике.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_windows(self):
        return [self.windows.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.windows.count())
                if self.windows.item(i).checkState() == Qt.CheckState.Checked]


class MainWindow(QMainWindow):
    def __init__(self, store, test_mode=False):
        super().__init__()
        install_russian_ui(QApplication.instance())
        self.store, self.test_mode = store, test_mode
        self.task, self.capture_task, self.recorder = None, None, None
        self.capture_failed = False
        self.explorer_captures = []
        self.pending, self.validation_target = None, None
        self.after_task_callback = None
        self.active_cancel = None
        self.checking_ollama = False
        self.generation = 0
        self.shutting_down = False
        self.hotkey = None
        self.restore_chat_after_run = False
        self.setWindowTitle('BENDER AI • локальный помощник')
        self.resize(820, 680)
        saved = store.setting('policy', {})
        if not saved:
            downloads = Path.home() / 'Downloads'
            if sys.platform == 'win32':
                from .windows import downloads_folder
                downloads = downloads_folder()
            saved = {'read_roots': [str(downloads)]}
        self.policy = Policy(**saved)
        self.ui, desktop = None, None
        self.bridge = None
        if sys.platform == 'win32' and not test_mode:
            from .isolation import WindowsBridge
            self.bridge = WindowsBridge(self.policy)
            self.ui, desktop = self.bridge.ui, self.bridge.desktop
        self.registry = ActionRegistry(self.policy, store, self.ui, desktop)
        self.executor = Executor(self.registry, store)
        self.character = Character()
        self.microphone = None
        self.microphone_model = store.setting('microphone_model', '')
        self.speech = SpeechController() if sys.platform == 'win32' and not test_mode else None
        self.character.activated.connect(self.show_chat)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        chat = QWidget()
        layout = QVBoxLayout(chat)
        heading = QLabel('BENDER AI   /   Исполнитель · офлайн по умолчанию')
        heading.setStyleSheet('font-size: 19px; font-weight: bold; padding: 12px;')
        layout.addWidget(heading)
        self.provider_status = QLabel()
        self.provider_status.setWordWrap(True)
        layout.addWidget(self.provider_status)
        provider_row = QHBoxLayout()
        self.ollama_check_button = self.button('Проверить Ollama', self.check_ollama, provider_row)
        layout.addLayout(provider_row)
        self.refresh_provider_status()
        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(False)
        layout.addWidget(self.chat)
        self.plan_view = QPlainTextEdit()
        self.plan_view.setReadOnly(True)
        self.plan_view.setMaximumHeight(160)
        self.plan_view.setPlaceholderText('Здесь появится предварительный план. Никаких действий до нажатия «Выполнить».')
        layout.addWidget(self.plan_view)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText('Например: Открой Блокнот')
        self.input.returnPressed.connect(self.send)
        row.addWidget(self.input)
        self.message_mode = QComboBox()
        for title, value in [('Авто', 'auto'), ('Разговор', 'chat'), ('План действий', 'command')]:
            self.message_mode.addItem(title, value)
        row.addWidget(self.message_mode)
        self.send_button = self.button('Отправить', self.send, row)
        self.microphone_button = self.button('Говорить', self.start_microphone, row)
        layout.addLayout(row)
        self.microphone_status_label = QLabel('Микрофон выключен. Распознанный текст появится в строке без отправки.')
        self.microphone_status_label.setWordWrap(True)
        layout.addWidget(self.microphone_status_label)
        row = QHBoxLayout()
        self.execute_button = self.button('Выполнить показанный план', self.execute_pending, row)
        self.execute_button.setEnabled(False)
        self.button('Отмена / СТОП', self.stop, row).setStyleSheet('background:#8b2635;color:white;font-weight:bold')
        self.button('Бендер, учись', self.learn, row)
        layout.addLayout(row)
        self.tabs.addTab(chat, 'Чат')
        skills = QWidget()
        layout = QVBoxLayout(skills)
        self.skill_list = QListWidget()
        layout.addWidget(self.skill_list)
        row = QHBoxLayout()
        for title, fn in [('Показать план', self.preview_skill), ('Проверить', self.validate_skill), ('Выполнить', self.preview_skill), ('Изменить', self.edit_skill), ('Удалить', self.delete_skill)]:
            self.button(title, fn, row)
        layout.addLayout(row)
        row = QHBoxLayout()
        for title, fn in [('Импорт JSON', self.import_skill), ('Экспорт JSON', self.export_skill), ('Версии / откат', self.restore_skill), ('Открыть тестовую форму', self.launch_training)]:
            self.button(title, fn, row)
        layout.addLayout(row)
        self.tabs.addTab(skills, 'Навыки')
        recording = QWidget()
        layout = QVBoxLayout(recording)
        self.record_label = QLabel('Запись выключена. Нет фонового наблюдения.')
        self.record_label.setWordWrap(True)
        layout.addWidget(self.record_label)
        row = QHBoxLayout()
        for title, fn in [('Начать', self.learn), ('Пауза / продолжить', self.pause_recording), ('Стоп и редактировать', self.finish_recording), ('Отменить запись', self.cancel_recording)]:
            self.button(title, fn, row)
        layout.addLayout(row)
        layout.addWidget(QLabel('Только выбранные окна: Invoke, Value, SelectionItem, Toggle, ExpandCollapse при поддержке UIA.\nПароли исключены. Текст по умолчанию становится параметром.\nЛимиты: 100 шагов, 5 минут. Неподдерживаемые события видны ниже. Проверьте результат в редакторе.'))
        self.record_diagnostics = QPlainTextEdit()
        self.record_diagnostics.setReadOnly(True)
        self.record_diagnostics.setPlaceholderText('Диагностика записи: только безопасные причины, без значений полей.')
        layout.addWidget(self.record_diagnostics)
        layout.addStretch()
        self.explorer_review_button = QPushButton('Подтвердить найденные папки Проводника…')
        self.explorer_review_button.setEnabled(False)
        self.explorer_review_button.clicked.connect(self.review_explorer_creations)
        layout.addWidget(self.explorer_review_button)
        self.tabs.addTab(recording, 'Обучение')
        permissions = QWidget()
        layout = QVBoxLayout(permissions)
        layout.addWidget(QLabel('Разрешения задаются вручную. ИИ и импорт навыков не могут их изменять.'))
        self.permission_view = QPlainTextEdit()
        self.permission_view.setReadOnly(True)
        layout.addWidget(self.permission_view)
        row = QHBoxLayout()
        self.button('Добавить папку для чтения', lambda: self.add_folder(False), row)
        self.button('Добавить папку для записи', lambda: self.add_folder(True), row)
        self.button('Разрешить процесс', self.add_app, row)
        self.button('Отозвать приложение', self.revoke_app, row)
        self.button('Сбросить разрешения', self.reset_permissions, row)
        gradle_row = QHBoxLayout()
        self.button('Разрешить проект и задачу Gradle', self.approve_gradle, gradle_row)
        self.button('Скриншот по запросу', self.take_screenshot, gradle_row)
        layout.addLayout(gradle_row)
        layout.addLayout(row)
        self.tabs.addTab(permissions, 'Разрешения')
        journal = QWidget()
        layout = QVBoxLayout(journal)
        self.journal = QPlainTextEdit()
        self.journal.setReadOnly(True)
        layout.addWidget(self.journal)
        row = QHBoxLayout()
        self.button('Обновить', self.refresh_journal, row)
        self.button('Очистить историю', self.clear_history, row)
        layout.addLayout(row)
        self.tabs.addTab(journal, 'Журнал')
        self.statusBar().showMessage('Готов • никаких действий в фоне')
        menu = self.menuBar().addMenu('Управление')
        for title, fn in [('Настройки', self.settings), ('Показать / скрыть персонажа', self.toggle_character), ('Выход', self.quit_app)]:
            action = menu.addAction(title)
            action.triggered.connect(fn)
        self.tray = None
        if not test_mode and QSystemTrayIcon.isSystemTrayAvailable():
            pix = QPixmap(32,32)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setBrush(QColor('#7de2d1'))
            painter.drawRoundedRect(4,4,24,24,8,8)
            painter.end()
            self.tray = QSystemTrayIcon(QIcon(pix), self)
            tray_menu = QMenu()
            for title, fn in [('Открыть чат', self.show_chat), ('Начать обучение', self.learn), ('Пауза/возобновить', self.pause_recording), ('СТОП', self.stop), ('Показать / скрыть', self.toggle_character), ('Настройки', self.settings), ('Выход', self.quit_app)]:
                action = tray_menu.addAction(title)
                action.triggered.connect(fn)
            self.tray.setContextMenu(tray_menu)
            self.tray.activated.connect(lambda reason: self.show_chat() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.show()
        self.load_state()
        self.refresh_skills()
        self.refresh_permissions()
        self.refresh_journal()
        self.message('Бендер', 'Готов. Офлайн-команды работают без модели. Сначала показываю план, затем жду запуска. Для обучения откройте тестовую форму во вкладке «Навыки».')
        if sys.platform != 'win32':
            self.message('Среда', 'Windows-автоматизация недоступна в этой ОС. GUI, память, редактор и операции с разрешёнными папками доступны.')
        if not test_mode:
            self.register_hotkey()
            self.character.show()
        self.state_timer = QTimer(self)
        self.state_timer.timeout.connect(self.save_state)
        self.state_timer.start(10000)
        self.voice_timer = QTimer(self)
        self.voice_timer.timeout.connect(self.poll_speech)
        self.voice_timer.timeout.connect(self.poll_microphone)
        self.voice_timer.start(250)

    @staticmethod
    def button(title, fn, layout):
        button = QPushButton(title)
        button.clicked.connect(fn)
        layout.addWidget(button)
        return button

    def message(self, role, text):
        # QTextBrowser insertion is plain text; UI/model output cannot inject HTML links.
        self.chat.append('')
        cursor = self.chat.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        tone = self.store.setting('personality', 'Обычный')
        if role == 'Бендер' and tone == 'Молчаливый':
            return
        prefix = 'К делу. ' if tone == 'Деловой' else 'Ладно, кожаный коллега. ' if tone == 'Саркастичный' else ''
        cursor.insertText(role + ': ' + (prefix if role == 'Бендер' else '') + str(text) + '\n')
        self.chat.setTextCursor(cursor)

    def show_chat(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def start_task(self, function, callback):
        if self.task is not None or self.recorder is not None:
            self.message('Бендер', 'Завершите текущую задачу или запись.')
            return False
        task = Task(function, self)
        generation = self.generation
        self.task = task
        self.send_button.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.character.state = 'thinking'
        self.statusBar().showMessage('Выполняется задача… СТОП доступен.')
        task.result.connect(lambda result: callback(result) if generation == self.generation else None)
        task.failure.connect(lambda error: self.on_error(error) if generation == self.generation else None)
        task.finished.connect(self.task_finished)
        task.start()
        return True

    def task_finished(self):
        old = self.task
        self.task = None
        self.checking_ollama = False
        self.send_button.setEnabled(True)
        self.execute_button.setEnabled(self.pending is not None)
        if self.character.state not in ('error', 'success', 'sleep', 'talk'):
            self.character.state = 'idle'
        if old:
            old.deleteLater()
        self.refresh_journal()
        if self.restore_chat_after_run:
            self.restore_chat_after_run = False
            self.show_chat()
        continuation, self.after_task_callback = self.after_task_callback, None
        if continuation and continuation[0] == self.generation and not self.shutting_down:
            continuation[1]()
        if self.shutting_down:
            self.quit_app()

    def on_error(self, message):
        if self.checking_ollama:
            self.refresh_provider_status()
            self.provider_status.setText(self.provider_status.text() + '\nOllama: ' + message)
        self.character.state = 'error'
        self.message('Ошибка', message)
        self.speak_response('Не удалось выполнить запрос. Подробности показаны в чате.')
        self.statusBar().showMessage('Ошибка • выполнение остановлено')

    def refresh_provider_status(self, connection=None):
        config = self.store.setting('ai', {})
        kind = config.get('provider', 'offline')
        if kind == 'ollama':
            self.provider_status.setText('Локальный ИИ: Ollama • модель: ' + (config.get('model') or DEFAULT_OLLAMA_MODEL)
                                         + ' • ' + (connection or 'подключение не проверено') + '. Простые команды работают офлайн.')
        elif kind == 'cloud':
            self.provider_status.setText('Облачный ИИ • модель: ' + config.get('model', '') + ' • требуется разрешение сети.')
        else:
            self.provider_status.setText('Офлайн • простые команды без модели и подключения к сети.')

    def check_ollama(self):
        if self.task is not None or self.recorder is not None:
            self.on_error('Завершите текущую задачу или запись перед проверкой Ollama.')
            return
        config = self.store.setting('ai', {})
        try:
            provider = OllamaProvider(config.get('model') or DEFAULT_OLLAMA_MODEL)
        except (ValidationError,Denied) as exc:
            self.on_error(str(exc))
            return
        token = Cancellation(12)
        self.active_cancel = token
        self.checking_ollama = True
        self.provider_status.setText('Проверка локального подключения Ollama…')
        def checked(result):
            self.refresh_provider_status(connection=result['message'])
            if self.store.setting('ai', {}).get('provider', 'offline') != 'ollama':
                self.provider_status.setText(self.provider_status.text() + '\nOllama • модель: ' + result['model'] + ' • ' + result['message'])
            self.statusBar().showMessage('Проверка Ollama завершена')
        self.start_task(lambda: provider.status(cancellation=token), checked)

    def provider(self):
        config = self.store.setting('ai', {})
        kind = config.get('provider', 'offline')
        if kind == 'ollama':
            from .brain import HybridProvider
            return HybridProvider(OllamaProvider(config.get('model') or DEFAULT_OLLAMA_MODEL))
        if kind == 'cloud':
            return CloudProvider(self.policy, SecretVault(), config.get('endpoint', ''), config.get('model', ''), enabled=True)
        return RuleBasedProvider()

    def send(self):
        text = self.input.text().strip()
        if not text:
            return
        if text.casefold().rstrip('.!') in ('стоп', 'остановись'):
            self.stop()
            return
        if self.task is not None or self.recorder is not None:
            return
        self.input.clear()
        self.pending = None
        self.validation_target = None
        self.message('Вы', text)
        try:
            if self.message_mode.currentData() == 'chat':
                self.start_conversation(text)
                return
            requested = text.casefold()
            prefix = 'выполни навык '
            explicit_skill = requested.startswith(prefix)
            if explicit_skill:
                requested = requested[len(prefix):].strip()
            matches = [skill for skill in self.store.skills() if skill['name'].casefold() == requested]
            if len(matches) > 1:
                raise ValidationError('Несколько навыков с этим названием. Выберите нужный во вкладке «Навыки».')
            if matches:
                self.accept_plan(self.store.load_skill(matches[0]['id']))
                return
            if explicit_skill:
                raise ValidationError('Навык не найден. Проверьте название во вкладке «Навыки».')
            try:
                shortcut = RuleBasedProvider().plan(text)
            except ValidationError:
                pass
            else:
                if self.store.setting('ai', {}).get('provider', 'offline') == 'offline':
                    self.start_task(lambda: shortcut, self.accept_plan)
                else:
                    self.accept_plan(shortcut)
                return
            from .brain import classify_message
            mode = self.message_mode.currentData()
            route = classify_message(text) if mode == 'auto' else mode
            if route == 'ambiguous':
                self.input.setText(text)
                self.on_error('Уточните режим: «Разговор» для ответа или «План действий» для управления компьютером.')
                return
            if route == 'chat':
                self.start_conversation(text)
                return
            provider = self.provider()
            token = Cancellation(95)
            self.active_cancel = token
            self.start_task(lambda: provider.plan(text, cancellation=token), self.accept_plan)
        except Exception as exc:
            self.on_error(str(exc))

    def talk(self):
        """Explicit conversation mode: never proposes or executes PC actions."""
        if self.task is not None or self.recorder is not None:
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.pending = None
        self.execute_button.setEnabled(False)
        self.plan_view.clear()
        self.message('Вы', text)
        self.start_conversation(text)

    def start_conversation(self, text):
        config = self.store.setting('ai', {})
        if config.get('provider') != 'ollama':
            self.on_error('Для разговора выберите локальный ИИ Ollama в настройках. Офлайн-команды остаются доступны.')
            return
        self.pending = None
        self.validation_target = None
        self.execute_button.setEnabled(False)
        self.plan_view.clear()
        token = Cancellation(95)
        self.active_cancel = token
        provider = OllamaProvider(config.get('model') or DEFAULT_OLLAMA_MODEL)
        self.start_task(lambda: provider.converse_cancelable(text, cancellation=token), self.on_talk_reply)

    def start_microphone(self):
        if self.microphone is not None and self.microphone.active:
            self.microphone.stop()
            self.microphone_status_label.setText('Микрофон выключен.')
            return
        if self.task is not None or self.recorder is not None:
            self.on_error('Завершите задачу или запись перед включением микрофона.')
            return
        from .microphone import MicrophoneController, microphone_status
        status = microphone_status(self.microphone_model)
        if not status['available']:
            self.microphone_status_label.setText(status['reason'])
            return
        if not self.confirm('Включить микрофон?', 'Разрешить однократное локальное распознавание речи (до 15 секунд)? Текст появится в строке; отправьте его сами. Аудио не сохраняется и не отправляется в облако.'):
            return
        try:
            if self.microphone is None:
                self.microphone = MicrophoneController()
            self.microphone.start(self.microphone_model, consent=True, timeout=15)
            self.microphone_status_label.setText('Подключение микрофона…')
        except (RuntimeError, ValueError, OSError):
            self.microphone_status_label.setText('Не удалось включить микрофон. Проверьте модель и локальные зависимости.')

    def poll_microphone(self):
        if self.microphone is None:
            return
        result = self.microphone.poll()
        if result is None:
            return
        kind, value = result
        if kind == 'listening':
            self.microphone_status_label.setText('● Микрофон включён • говорите; СТОП выключает запись.')
        elif kind == 'text':
            self.input.setText(value)
            self.microphone_status_label.setText('Речь распознана. Проверьте текст и нажмите «Отправить». Микрофон выключен.')
        else:
            self.microphone_status_label.setText('Микрофон выключен. ' + value)

    def on_talk_reply(self, answer):
        self.statusBar().showMessage('Ответ получен • разговор не запускает действия на компьютере.')
        self.message('Бендер', answer)
        if not self.speak_response(answer):
            self.character.state = 'idle'

    def speak_response(self, text):
        if self.speech is None or not self.store.setting('speech_enabled', False):
            return False
        try:
            self.speech.speak(text, voice_id=self.store.setting('speech_voice', ''), rate=self.store.setting('speech_rate', -1))
            return True
        except (OSError, RuntimeError, ValueError) as exc:
            self.message('Голос', 'Не удалось запустить озвучивание: ' + type(exc).__name__)
            return False

    def poll_speech(self):
        if self.speech is None:
            return
        result = self.speech.poll()
        if result is None:
            return
        status, detail = result
        if status == 'started':
            self.character.state = 'talk'
            return
        if status != 'ok':
            self.message('Голос', detail)
        if self.character.state == 'talk':
            self.character.state = 'thinking' if self.task is not None else 'idle'

    def accept_plan(self, plan):
        self.statusBar().showMessage('План готов • проверьте шаги и нажмите «Выполнить». Действия ещё не запущены.')
        self.pending = plan
        self.plan_view.setPlainText(readable_plan(plan))
        self.execute_button.setEnabled(True)
        self.message('План', 'Проверьте шаги. Права будут проверены перед выполнением; план не выдаёт разрешений.')

    def execute_pending(self):
        if not self.pending or self.task is not None:
            return
        plan = self.pending
        try:
            if plan.parameters:
                values = {}
                for param in plan.parameters:
                    value, ok = QInputDialog.getText(self, 'Параметры навыка', param + ' (несекретное значение):')
                    if not ok:
                        return
                    values[param] = value
                plan = plan.bind(values)
            self.plan_view.setPlainText(readable_plan(plan))
            if any(step.action in ('open_folder', 'create_folder') for step in plan.steps):
                self.review_plan_folder_permissions(plan)
                return
            self.execute_prepared_plan(plan)
        except Exception as exc:
            self.on_error(str(exc))

    def review_plan_folder_permissions(self, plan):
        token = Cancellation(30)
        self.active_cancel = token
        def inspect():
            requests = []
            for step in plan.steps:
                token.check()
                if step.action not in ('open_folder', 'create_folder'):
                    continue
                if self.policy.observer:
                    raise Denied('Режим наблюдателя запрещает управление папками.')
                mode = 'write' if step.action == 'create_folder' else 'read'
                raw = step.args['parent'] if mode == 'write' else self.registry.folder(step.args['path'])
                try:
                    self.policy.path(raw, mode)
                except Denied:
                    request = (mode, str(Path(raw).expanduser().resolve()))
                    if request not in requests:
                        requests.append(request)
            return requests
        def inspected(requests):
            if not requests:
                self.after_current_task(lambda: self.execute_prepared_plan(plan))
                return
            grants = []
            for mode, path in requests:
                rights = 'создание подпапок' if mode == 'write' else 'чтение папки и её подпапок'
                if not self.confirm('Разрешение папке из плана', 'Разрешить ' + rights + '?\n' + json.dumps(path, ensure_ascii=False)
                                    + '\nРазрешение сохраняется только для указанной области. План сейчас не запускается.'):
                    self.statusBar().showMessage('Разрешение не выдано • план не запущен.')
                    return
                grants.append((mode, path))
            for mode, path in grants:
                if path not in self.policy.read_roots:
                    self.policy.read_roots.append(path)
                if mode == 'write' and path not in self.policy.write_roots:
                    self.policy.write_roots.append(path)
            self.persist_policy()
            self.statusBar().showMessage('Разрешения сохранены • проверьте план и снова нажмите «Выполнить».')
        self.start_task(inspect, inspected)

    def execute_prepared_plan(self, plan):
        try:
            # Permission/path validation runs in Executor's worker, never on the GUI thread.
            if plan.needs_confirmation and not self.confirm('Выполнить показанный план?', 'Разрешаете эти изменения в указанных приложениях и папках? Разрешение действует только на этот запуск.'):
                return
            target = self.validation_target
            self.pending, self.validation_target = None, None
            self.execute_button.setEnabled(False)
            if not self.test_mode and any(s.action == 'get_active_window' for s in plan.steps):
                self.restore_chat_after_run = True
                self.hide()
                QApplication.processEvents()
            token = Cancellation()
            self.active_cancel = token
            def run():
                return self.executor.run(plan, confirmed=True, progress=lambda i,a: self.task.progress.emit(i,a), cancellation=token)
            if self.start_task(run, lambda result: self.on_run(result, target)):
                self.task.progress.connect(lambda i,a: self.statusBar().showMessage(f'Шаг {i}: {LABELS.get(a, a)}'))
                self.character.state = 'working'
        except Exception as exc:
            self.on_error(str(exc))

    def on_run(self, result, target=None):
        self.character.state = 'success' if result.status == 'success' else 'error'
        status = STATUS_LABELS.get(result.status, 'Неизвестный результат')
        detail = result.error or (readable_result(result.outputs) if result.outputs else 'План выполнен.' if result.status == 'success' else 'Проверьте журнал выполнения.')
        for action, label in LABELS.items():
            detail = detail.replace('(' + action + ')', '(' + label + ')')
        self.message('Результат', status + ': ' + detail)
        self.speak_response(status + '. Подробности результата показаны в чате.')
        self.statusBar().showMessage(status)
        QTimer.singleShot(2500, lambda: setattr(self.character, 'state', 'sleep' if self.character.dnd else 'idle') if self.task is None else None)
        if target:
            self.store.mark_validation(*target, result.status)
            self.refresh_skills()

    def stop(self):
        self.generation += 1
        if self.checking_ollama:
            self.refresh_provider_status()
            self.provider_status.setText(self.provider_status.text() + '\nПроверка Ollama отменена.')
        self.executor.stop()
        if self.microphone is not None:
            self.microphone.stop()
            self.microphone_status_label.setText('Микрофон выключен.')
        if self.speech is not None:
            self.speech.stop()
        if self.active_cancel:
            self.active_cancel.event.set()
        self.pending, self.validation_target = None, None
        self.execute_button.setEnabled(False)
        self.plan_view.clear()
        if self.recorder:
            self.recorder.pause()
            self.record_label.setText('ЗАПИСЬ НА ПАУЗЕ • СТОП. Можно завершить или отменить.')
        self.statusBar().showMessage('СТОП • следующие шаги запрещены')
        self.message('Бендер', 'Стоп. План сброшен; активная задача получает сигнал отмены. Уже переданное ОС действие нельзя отозвать.')

    def confirm(self, title, message):
        return QMessageBox.question(self, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def refresh_skills(self):
        self.skill_list.clear()
        self.skill_rows = self.store.skills()
        for skill in self.skill_rows:
            versions = self.store.versions(skill['id'])
            state = STATUS_LABELS.get(versions[0]['validation'], 'Не проверено')
            self.skill_list.addItem(f"{skill['name']} • v{skill['current_version']} • {state}")

    def selected_skill(self):
        i = self.skill_list.currentRow()
        if i < 0:
            raise ValidationError('Выберите навык')
        return self.skill_rows[i]

    def preview_skill(self):
        try:
            skill = self.selected_skill()
            self.validation_target = None
            self.accept_plan(self.store.load_skill(skill['id']))
            self.tabs.setCurrentIndex(0)
        except ValueError as exc:
            self.on_error(str(exc))

    def validate_skill(self):
        try:
            skill = self.selected_skill()
            plan = self.store.load_skill(skill['id'])
            if not any(s.action == 'assert_element' for s in plan.steps):
                raise ValidationError('Добавьте явную проверку результата (assert_element) в редакторе перед проверкой навыка.')
            self.accept_plan(plan)
            self.validation_target = (skill['id'], skill['current_version'])
            self.tabs.setCurrentIndex(0)
            self.message('Проверка', 'Это реальный запуск с проверками, а не dry-run. Нажмите «Выполнить», чтобы разрешить его.')
        except ValueError as exc:
            self.on_error(str(exc))

    def edit_skill(self):
        try:
            skill = self.selected_skill()
            self.edit_plan(self.store.load_skill(skill['id']), skill['id'])
        except ValueError as exc:
            self.on_error(str(exc))

    def edit_plan(self, plan, skill_id=None):
        dialog = SkillEditor(plan, self)
        if dialog.exec() and self.confirm('Сохранить навык?', 'Сохранить показанную версию? Изменения не запускаются автоматически.'):
            self.store.save_skill(dialog.plan, skill_id)
            self.refresh_skills()

    def delete_skill(self):
        try:
            skill = self.selected_skill()
            if self.confirm('Удалить навык?', 'Будут удалены навык и его версии из локальной базы.'):
                self.store.delete_skill(skill['id'])
                self.refresh_skills()
        except ValueError as exc:
            self.on_error(str(exc))

    def export_skill(self):
        try:
            skill = self.selected_skill()
            path, _ = QFileDialog.getSaveFileName(self, 'Экспорт навыка', 'skill.json', 'JSON (*.json)')
            if path:
                Path(path).write_text(self.store.export_skill(skill['id']), 'utf-8')
        except (ValueError, OSError) as exc:
            self.on_error(str(exc))

    def import_skill(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Импорт навыка', '', 'JSON (*.json)')
        if not path:
            return
        try:
            if Path(path).stat().st_size > 1_000_000:
                raise ValidationError('Файл слишком большой')
            self.store.import_skill(Path(path).read_text('utf-8'))
            self.refresh_skills()
            self.message('Импорт', 'Навык импортирован без разрешений и без запуска. Проверьте его в редакторе.')
        except (ValueError, OSError) as exc:
            self.on_error(str(exc))

    def restore_skill(self):
        try:
            skill = self.selected_skill()
            versions = self.store.versions(skill['id'])
            labels = [f"v{v['version']} • {v['created_at']} • {STATUS_LABELS.get(v['validation'],'Не проверено')}" for v in versions]
            label, ok = QInputDialog.getItem(self, 'Откат версии', 'Выберите сохранённую версию', labels, 0, False)
            if ok and self.confirm('Восстановить?', 'Будет создана новая версия с выбранными шагами.'):
                self.store.restore_version(skill['id'], versions[labels.index(label)]['version'])
                self.refresh_skills()
        except ValueError as exc:
            self.on_error(str(exc))

    def persist_policy(self):
        self.store.set_setting('policy', self.policy.to_dict())
        self.refresh_permissions()

    def refresh_permissions(self):
        self.permission_view.setPlainText(json.dumps(self.policy.to_dict(), ensure_ascii=False, indent=2))

    def add_folder(self, write):
        if self.task is not None or self.recorder:
            return
        path = QFileDialog.getExistingDirectory(self, 'Разрешённая рабочая папка')
        if path and self.confirm('Выдать разрешение?', f"Разрешить {'создание папок' if write else 'чтение'} в этом каталоге и его подпапках?\n{path}"):
            root = str(Path(path).resolve())
            if root not in self.policy.read_roots:
                self.policy.read_roots.append(root)
            if write and root not in self.policy.write_roots:
                self.policy.write_roots.append(root)
            self.persist_policy()

    def add_app(self):
        if self.task is not None or self.recorder:
            return
        if self.ui is None:
            self.on_error('Выбор окон требует Windows UI Automation')
            return
        self.start_task(self.ui.candidates, self.choose_app_candidate)

    def choose_app_candidate(self, candidates):
        candidates = [c for c in candidates if c.get('app') and c.get('title') and c.get('executable')
                      and c['title'] != self.windowTitle()]
        if not candidates:
            self.on_error('Нет доступных окон с проверяемым исполняемым файлом.')
            return
        labels = [c['app'] + ' • ' + c['title'] for c in candidates]
        label, ok = QInputDialog.getItem(self, 'Разрешить окно', 'Выберите точное окно:', labels, 0, False)
        if not ok:
            return
        candidate = candidates[labels.index(label)]
        mode, ok = QInputDialog.getItem(self, 'Вид разрешения', 'Разрешить:',
                                       ['Только чтение', 'Чтение и управление', 'Чтение, управление и запуск'], 0, False)
        if not ok:
            return
        launch = mode == 'Чтение, управление и запуск'
        control = launch or mode == 'Чтение и управление'
        preview = (candidate['app'] + '\nОкно: ' + candidate['title'] + '\nФайл: ' + candidate['executable']
                   + '\nПрава: ' + ('чтение, управление и запуск выбранного файла без аргументов' if launch else 'чтение и управление' if control else 'только чтение')
                   + '\nНе выбирайте окна с чувствительными данными. Поддержка UIA зависит от приложения.')
        if not self.confirm('Выдать выбранные права?', preview):
            return
        try:
            rights = {'control': control}
            if launch:
                rights['launch'] = True
            self.policy.grant_app(candidate['app'], candidate['title'], candidate['executable'], **rights)
            self.persist_policy()
        except (ValueError, Denied) as exc:
            self.on_error(str(exc))

    def revoke_app(self):
        if self.task is not None or self.recorder:
            return
        apps = sorted(set(self.policy.read_apps + self.policy.ui_apps))
        if not apps:
            return
        app, ok = QInputDialog.getItem(self, 'Отозвать приложение', 'Процесс и все его разрешённые окна:', apps, 0, False)
        if ok and self.confirm('Отозвать?', app):
            self.policy.revoke_app(app)
            self.persist_policy()

    def reset_permissions(self):
        if self.task is not None or self.recorder:
            return
        if self.confirm('Сбросить?', 'Отозвать все разрешения приложений, каталогов, сети и команд?'):
            fresh = Policy()
            self.policy.__dict__.update(fresh.__dict__)
            self.persist_policy()

    def approve_gradle(self):
        if self.task is not None or self.recorder:
            return
        import re
        project = QFileDialog.getExistingDirectory(self, 'Явно доверенный проект Gradle (build-скрипты исполняют код)')
        if not project:
            return
        java, _ = QFileDialog.getOpenFileName(self, 'Нативный java.exe из доверенного JDK', '', 'Executable (*.exe)' if sys.platform == 'win32' else 'All (*)')
        if not java:
            return
        gradle_home = QFileDialog.getExistingDirectory(self, 'Каталог доверенной установленной Gradle (с lib/)')
        if not gradle_home:
            return
        lib = Path(gradle_home) / 'lib'
        if not list(lib.glob('gradle-launcher-*.jar')):
            self.on_error('В lib/ нет gradle-launcher-*.jar')
            return
        tasks, ok = QInputDialog.getText(self, 'Разрешённые задачи', 'Имена задач через запятую (например test,build):')
        allowed = [x.strip() for x in tasks.split(',') if x.strip()]
        if not ok or not allowed or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_:.-]{0,99}',x) for x in allowed):
            self.on_error('Недопустимые имена задач')
            return
        if self.confirm('Разрешить исполнение проекта?', 'Gradle исполняет build-скрипты проекта с вашими правами. Только доверенный проект! Подтвердить указанные задачи: ' + ', '.join(allowed)):
            self.policy.gradle_projects[str(Path(project).resolve())] = {'executable':str(Path(java).resolve()),
                'tasks':allowed,'prefix_args':['-classpath',str(lib.resolve() / '*'),'org.gradle.launcher.GradleMain','--no-daemon']}
            self.persist_policy()

    def take_screenshot(self):
        if self.task is not None or self.recorder:
            return
        if not self.confirm('Скриншот выбранного экрана?', 'На изображение попадут видимые окна, включая личные данные. Сохранить локально один скриншот? В облако он не отправляется.'):
            return
        screens = QApplication.screens()
        labels = [f'{i+1}: {screen.name()}' for i,screen in enumerate(screens)]
        label, ok = QInputDialog.getItem(self, 'Экран', 'Выберите экран', labels, 0, False)
        if not ok:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Сохранить скриншот', 'screenshot.png', 'PNG (*.png)')
        if path:
            self.policy.screenshot = True
            try:
                image = screens[labels.index(label)].grabWindow(0)
                if image.isNull() or not image.save(path,'PNG'):
                    raise RuntimeError('Экран не поддерживает захват')
                self.store.log('success','screenshot')
                self.message('Бендер','Скриншот сохранён локально.')
            except Exception:
                self.on_error('Не удалось сохранить скриншот')
            finally:
                self.policy.screenshot = False  # single-use permission, never background capture

    def launch_training(self):
        if sys.platform != 'win32':
            self.on_error('Нативная тестовая форма доступна в Windows. Переносимый тест Qt запускается через pytest.')
            return
        if not self.confirm('Запустить тестовую форму?', 'Открыть локальную форму и разрешить чтение/UIA её процесса для обучения?'):
            return
        if getattr(sys, 'frozen', False):
            executable = Path(sys.executable).with_name('BenderTraining.exe')
            command = [str(executable)]
            process = executable.name.casefold()
        else:
            command = [sys.executable, '-m', 'bender_ai.training_form']
            process = Path(sys.executable).name.casefold()
        subprocess.Popen(command, shell=False)
        self.policy.grant_app(process, 'BENDER AI Training', command[0], control=True)
        self.persist_policy()
        self.message('Обучение', 'Форма открывается. Нажмите «Бендер, учись» и выберите BENDER AI Training.')

    def learn(self):
        if self.task is not None or self.recorder:
            self.on_error('Завершите текущую задачу/запись')
            return
        if self.ui is None:
            self.on_error('Запись демонстрации требует Windows UI Automation')
            return
        self.start_task(self.ui.candidates, self.choose_recording_candidates)

    def choose_recording_candidates(self, candidates):
        candidates = [item for item in candidates if item.get('app') and item.get('title') and item.get('executable')
                      and item['title'] != self.windowTitle()]
        if not candidates:
            self.on_error('Нет доступных окон для обучения.')
            return
        dialog = RecordingWindowsDialog([(item['app'], item['title']) for item in candidates], self)
        if not dialog.exec():
            return
        selected = dialog.selected_windows()
        if not 1 <= len(selected) <= 12:
            self.on_error('Выберите от 1 до 12 окон.')
            return
        chosen = []
        for app, title in selected:
            matches = [item for item in candidates if item['app'] == app and item['title'] == title]
            if len(matches) != 1:
                self.on_error('Окно неоднозначно. Выберите окно с уникальным заголовком.')
                return
            chosen.append(matches[0])
        missing = []
        for item in chosen:
            try:
                self.policy.app(item['app'], control=True)
                self.policy.window(item['app'], item['title'], control=True)
                self.policy.identity(item['app'], item['executable'])
            except Denied:
                missing.append(item)
        preview = '\n'.join(json.dumps(item['app'] + ' • ' + item['title'], ensure_ascii=False)
                              + '\nФайл: ' + json.dumps(item['executable'], ensure_ascii=False) for item in chosen)
        if missing:
            preview += '\nДля выбранных окон без прав будут выданы чтение и управление. Запуск приложений не разрешается.'
        if not self.confirm('Начать обучение выбранных окон?', preview + '\nРазрешить запись поддерживаемых действий только этих окон?'):
            return
        try:
            reviewed = Policy(**self.policy.to_dict())
            for item in missing:
                reviewed.grant_app(item['app'], item['title'], item['executable'], control=True)
            if missing:
                self.policy.__dict__.update(reviewed.__dict__)
                self.persist_policy()
        except (ValueError, Denied) as exc:
            self.on_error(str(exc))
            return
        self.begin_recording_selection(selected, dialog.literal_text.isChecked())

    def choose_recording_window(self, windows):
        approved = []
        for app, title in windows:
            if title == self.windowTitle():
                continue
            try:
                self.policy.app(app, control=True)
                self.policy.window(app, title, control=True)
            except Denied:
                continue
            approved.append((app, title))
        windows = approved
        if not windows:
            self.on_error('Нет разрешённых окон. Откройте тестовую форму или выдайте разрешение процессу.')
            return
        dialog = RecordingWindowsDialog(windows, self)
        if not dialog.exec():
            return
        selected = dialog.selected_windows()
        if not 1 <= len(selected) <= 12:
            self.on_error('Выберите от 1 до 12 окон.')
            return
        if not self.confirm('Начать запись выбранных окон?', '\n'.join(a + ' • ' + w for a, w in selected)):
            return
        self.begin_recording_selection(selected, dialog.literal_text.isChecked())

    def begin_recording_selection(self, selected, literal_text):
        if any(app.casefold() == 'explorer.exe' for app, title in selected):
            self.after_current_task(lambda: self.prepare_explorer_recording(selected, literal_text))
        else:
            self.start_recording(selected, literal_text)

    def after_current_task(self, function):
        if self.task is None:
            function()
        else:
            self.after_task_callback = (self.generation, function)

    def prepare_explorer_recording(self, selected, literal_text):
        if self.bridge is None or not hasattr(self.bridge, 'explorer'):
            self.on_error('Адаптер выбранного окна Проводника недоступен.')
            return
        from .explorer import ExplorerPermissionRequired
        selectors = [dict(app=app, window=title, automation_id='__window__', control_type='Window')
                     for app, title in selected if app.casefold() == 'explorer.exe']
        token = Cancellation(30)
        self.active_cancel = token
        def inspect():
            self.bridge.explorer.cancellation = token
            try:
                for selector in selectors:
                    self.bridge.explorer.bind(selector)
                return None
            except ExplorerPermissionRequired as exc:
                return {'mode': exc.mode, 'path': exc.path}
            finally:
                self.bridge.explorer.cancellation = None
        def inspected(required):
            if required is None:
                self.start_recording(selected, literal_text)
                return
            path, mode = required['path'], required['mode']
            description = 'чтение выбранной папки и её подпапок' if mode == 'read' else 'создание подпапок в выбранной папке'
            if not self.confirm('Разрешение выбранной папке Проводника', 'Разрешить ' + description + '?\n' + json.dumps(path, ensure_ascii=False) + '\nДругие папки и диски не добавляются. Отказ не запускает запись.'):
                return
            root = str(Path(path).resolve())
            if root not in self.policy.read_roots:
                self.policy.read_roots.append(root)
            if mode == 'write' and root not in self.policy.write_roots:
                self.policy.write_roots.append(root)
            self.persist_policy()
            self.after_current_task(lambda: self.prepare_explorer_recording(selected, literal_text))
        self.start_task(inspect, inspected)

    def start_recording(self, selected, literal_text):
        self.recorder = Recorder(windows=selected, literal_text=literal_text)
        self.recorder.start()
        self.capture_failed = False
        self.record_diagnostics.clear()
        from .windows import WindowsCapture
        self.explorer_captures = []
        if any(app.casefold() == 'explorer.exe' for app, title in selected):
            from .explorer import ExplorerCapture, CompositeCapture, ScopedRecorder
            captures = []
            ordinary = [(app, title) for app, title in selected if app.casefold() != 'explorer.exe']
            if ordinary:
                captures.append(WindowsCapture(self.ui, ScopedRecorder(self.recorder, ordinary)))
            for app, title in selected:
                if app.casefold() == 'explorer.exe':
                    selector = dict(app=app, window=title, automation_id='__window__', control_type='Window')
                    explorer_capture = ExplorerCapture(self.bridge.explorer, self.recorder, selector)
                    self.explorer_captures.append(explorer_capture)
                    captures.append(explorer_capture)
            capture = CompositeCapture(captures, self.recorder)
        else:
            capture = WindowsCapture(self.ui, self.recorder)
        self.capture_task = CaptureTask(capture, self)
        self.capture_task.count.connect(self.recording_count)
        self.capture_task.ready.connect(lambda: self.recording_count(0))
        self.capture_task.diagnostic.connect(self.recording_diagnostic)
        self.capture_task.failure.connect(self.recording_failed)
        self.capture_task.finished.connect(lambda: self.quit_app() if self.shutting_down else None)
        self.capture_task.start()
        self.tabs.setCurrentIndex(2)
        self.record_label.setStyleSheet('color:#c93046;font-weight:bold;font-size:16px')
        self.record_label.setText('Подключение UIA… дождитесь красного индикатора записи.')

    def review_explorer_creations(self):
        if self.recorder is None or self.capture_failed or self.recorder.paused:
            self.on_error('Для подтверждения папок нужна активная запись Проводника.')
            return
        choices = [(capture, dict(proposal)) for capture in self.explorer_captures
                   for proposal in list(capture.pending_creations)]
        if not choices:
            return
        labels = [json.dumps(str(Path(item['parent']) / item['name']), ensure_ascii=False) for _, item in choices]
        label, ok = QInputDialog.getItem(self, 'Подтвердить учебный шаг', 'Папка обнаружена, но её создатель не установлен. Выберите ваше действие:', labels, 0, False)
        if not ok:
            return
        capture, proposal = choices[labels.index(label)]
        if not self.confirm('Добавить создание папки в навык?', 'Подтверждаю, что это моё учебное действие: создать подпапку ' + json.dumps(proposal, ensure_ascii=False) + '\nСейчас папка повторно не создаётся. При воспроизведении потребуется отдельный запуск плана.'):
            return
        try:
            self.policy.path(proposal['parent'], 'write')
        except Denied:
            if not self.confirm('Разрешить создание подпапок?', 'Для воспроизведения нужно разрешение на создание подпапок в выбранной папке:\n' + json.dumps(proposal['parent'], ensure_ascii=False)):
                return
            root = str(Path(proposal['parent']).resolve())
            if root not in self.policy.read_roots:
                self.policy.read_roots.append(root)
            if root not in self.policy.write_roots:
                self.policy.write_roots.append(root)
            self.persist_policy()
        try:
            index = capture.pending_creations.index(proposal)
            capture.approve_creation(index, confirmed=True)
            self.recording_count(len(self.recorder.steps))
        except (ValueError, Denied):
            self.on_error('Предложение изменилось или запись приостановлена. Проверьте черновик.')

    def recording_count(self, count):
        self.explorer_review_button.setEnabled(bool(self.recorder and not self.capture_failed and any(c.pending_creations for c in self.explorer_captures)))
        if self.recorder and self.capture_failed:
            self.show_capture_failed()
            return
        if self.recorder:
            state = 'ПАУЗА' if self.recorder.paused else '● ЗАПИСЬ ДЕЙСТВИЙ ВКЛЮЧЕНА'
            windows = getattr(self.recorder, 'windows', ())
            self.record_label.setText(f'{state} • окон: {len(windows)} • шагов: {count}')

    def recording_diagnostic(self, reason):
        # Recorder supplies a closed set of safe reason codes, never field text.
        self.record_diagnostics.appendPlainText(str(reason))

    def show_capture_failed(self):
        self.record_label.setText('ЗАПИСЬ ОСТАНОВЛЕНА • ошибка UIA. Завершите и сохраните черновик или отмените его, затем начните новую запись.')

    def recording_failed(self, message):
        if self.recorder is None:
            return
        self.capture_failed = True
        self.recorder.pause()
        self.show_capture_failed()
        self.on_error(message)

    def pause_recording(self):
        if self.recorder and self.capture_failed:
            self.recorder.pause()
            self.show_capture_failed()
            return
        if self.recorder:
            self.recorder.resume() if self.recorder.paused else self.recorder.pause()
            self.record_label.setText('ПАУЗА' if self.recorder.paused else '● ЗАПИСЬ ДЕЙСТВИЙ ВКЛЮЧЕНА')
        else:
            self.character.pinned = not self.character.pinned

    def end_capture(self):
        if self.capture_task:
            self.capture_task.stop_event.set()
            if not self.capture_task.wait(1500):
                self.on_error('UIA ещё завершает чтение. Запись приостановлена; повторите Стоп после завершения.')
                self.recorder.pause()
                return False
            self.capture_task.deleteLater()
            self.capture_task = None
        return True

    def finish_recording(self):
        if not self.recorder or not self.end_capture():
            return
        recorder, self.recorder = self.recorder, None
        self.explorer_captures = []
        self.explorer_review_button.setEnabled(False)
        self.capture_failed = False
        self.record_label.setText('Запись выключена')
        try:
            plan = recorder.finish('Записанный навык')
            self.edit_plan(plan)
        except ValueError as exc:
            self.on_error(str(exc))

    def cancel_recording(self):
        if self.recorder and self.end_capture():
            self.recorder.cancel()
            self.recorder = None
            self.explorer_captures = []
            self.explorer_review_button.setEnabled(False)
            self.capture_failed = False
            self.record_label.setText('Запись отменена; черновик удалён')

    def refresh_journal(self):
        runs = self.store.runs()
        lines = []
        for run in runs:
            lines.append(f"{run['created_at']} • {run['name']} • {STATUS_LABELS.get(run['status'], 'Неизвестно')}")
            for step in self.store.run_steps(run['id']):
                lines.append(f"  {step['position']+1}. {LABELS.get(step['action'], step['action'])}: {STATUS_LABELS.get(step['status'], 'Неизвестно')} {step['detail']}")
        self.journal.setPlainText('\n'.join(lines) or 'Запусков пока нет.')

    def clear_history(self):
        if self.task is None and self.confirm('Очистить историю?', 'Удалить локальную историю запусков и аудит? Навыки останутся.'):
            self.store.clear_history()
            self.refresh_journal()
            self.chat.clear()

    def toggle_character(self):
        self.character.setVisible(not self.character.isVisible())

    def register_hotkey(self):
        if self.hotkey:
            self.hotkey.close()
        try:
            self.hotkey = EmergencyHotkey(QApplication.instance(), self.stop, self.store.setting('hotkey_shift', False))
        except RuntimeError as exc:
            self.on_error(str(exc))

    def save_state(self):
        self.store.set_setting('character', {'x': self.character.x(), 'y': self.character.y(),
            'size': self.character.width(), 'dnd': self.character.dnd, 'pinned': self.character.pinned,
            'speed': self.character.speed, 'animate': self.character.animate, 'topmost': self.character.topmost})

    def load_state(self):
        state = self.store.setting('character', {})
        self.character.set_topmost(state.get('topmost', True))
        size = max(80, min(240, state.get('size', 116)))
        self.character.resize(size, round(size*146/116))
        screen = QApplication.primaryScreen().availableGeometry()
        self.character.move(state.get('x', screen.right()-size-40), state.get('y', screen.bottom()-self.character.height()))
        self.character.pinned = state.get('pinned', False)
        self.character.speed = state.get('speed', 30)
        self.character.animate = state.get('animate', True)
        if state.get('dnd', False):
            self.character.set_dnd(True)
        self.character.ensure_visible()
        pack = self.store.setting('character_pack')
        if pack:
            try:
                self.character.pack = CharacterPack.load(pack)
            except (ValueError, OSError):
                self.message('Персонаж', 'Пакет недоступен; используется оригинальный робот.')

    def import_avatar(self):
        if self.task is not None or self.recorder is not None:
            self.on_error('Завершите задачу или запись перед импортом персонажа.')
            return
        source, _ = QFileDialog.getOpenFileName(self, 'Локальная модель персонажа', '', 'Модель GLB (*.glb)')
        if not source:
            return
        blender, _ = QFileDialog.getOpenFileName(self, 'Установленный Blender', self.store.setting('blender_path', ''),
                                               'Blender (*.exe)' if sys.platform == 'win32' else 'Blender (*)')
        if not blender:
            return
        if not self.confirm('Импортировать модель?', 'Подтверждаю право использовать выбранную модель и её материалы.\nМодель: ' + source + '\nBlender: ' + blender + '\nЗапустить локальный рендер в PNG-пакет? СТОП отменяет импорт. Исходная модель не изменяется.'):
            return
        from .avatar_import import import_glb
        token = Cancellation(190)
        self.active_cancel = token
        def imported(path):
            try:
                pack = CharacterPack.load(path)
            except (ValueError, OSError):
                self.on_error('Полученный пакет персонажа не прошёл проверку.')
                return
            self.character.pack = pack
            self.store.set_setting('character_pack', str(path))
            self.store.set_setting('blender_path', blender)
            self.statusBar().showMessage('Персонаж импортирован из GLB в локальный PNG-пакет.')
        self.start_task(lambda: import_glb(source, blender, data_dir() / 'avatars', cancel=token.event), imported)

    def settings(self):
        if self.task is not None or self.recorder:
            self.on_error('Настройки доступны после завершения задачи или записи')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Настройки • всё локально по умолчанию')
        form = QFormLayout(dialog)
        personality = QComboBox()
        personality.addItems(['Обычный', 'Саркастичный', 'Деловой', 'Молчаливый'])
        personality.setCurrentText(self.store.setting('personality', 'Обычный'))
        dnd, pinned, animate, startup, network, observer = [QCheckBox() for _ in range(6)]
        topmost = QCheckBox()
        topmost.setObjectName('character_topmost')
        topmost.setChecked(self.character.topmost)
        speech_enabled = QCheckBox()
        speech_enabled.setChecked(self.store.setting('speech_enabled', False))
        voice = QComboBox()
        voice.addItem('Автоматически — установленный русский голос', '')
        saved_voice = self.store.setting('speech_voice', '')
        if saved_voice:
            voice.addItem('Сохранённый голос: ' + saved_voice, saved_voice)
            voice.setCurrentIndex(1)
        speech_rate = QSpinBox()
        speech_rate.setRange(-3, 3)
        speech_rate.setValue(self.store.setting('speech_rate', -1))
        voice_status = QLabel('Только установленные русские SAPI-голоса. Новые голоса не скачиваются.')
        voice_status.setWordWrap(True)
        voice_row = QHBoxLayout()
        refresh_voice = QPushButton('Найти русские голоса')
        test_voice = QPushButton('Проверить голос')
        voice_row.addWidget(refresh_voice)
        voice_row.addWidget(test_voice)
        def load_voices():
            from .speech import list_voices
            def loaded(voices):
                current = voice.currentData()
                voice.clear()
                voice.addItem('Автоматически — установленный русский голос', '')
                for item in voices:
                    if item['russian']:
                        voice.addItem(item['name'], item['id'])
                selected = voice.findData(current)
                voice.setCurrentIndex(max(0, selected))
                voice_status.setText('Русских голосов: ' + str(voice.count() - 1) if voice.count() > 1 else 'Русский SAPI-голос не установлен. Ответы останутся текстовыми.')
            self.start_task(list_voices, loaded)
        def preview_voice():
            if self.speech is None:
                voice_status.setText('Проверка голоса доступна только в Windows.')
                return
            try:
                self.speech.speak('Привет. Это проверка русского голоса Бендера.', voice_id=voice.currentData(), rate=speech_rate.value())
            except (RuntimeError, ValueError, OSError):
                voice_status.setText('Голос недоступен. Проверьте установленные русские SAPI-голоса.')
        refresh_voice.clicked.connect(load_voices)
        test_voice.clicked.connect(preview_voice)
        microphone_model = QLineEdit(self.microphone_model)
        microphone_model.setReadOnly(True)
        choose_microphone_model = QPushButton('Выбрать локальную модель Vosk…')
        mic_status = QLabel('Микрофон включается только кнопкой «Говорить» после согласия.')
        mic_status.setWordWrap(True)
        def choose_model():
            directory = QFileDialog.getExistingDirectory(dialog, 'Распакованная локальная русская модель Vosk')
            if directory:
                from .microphone import microphone_status
                microphone_model.setText(directory)
                mic_status.setText(microphone_status(directory)['reason'])
        choose_microphone_model.clicked.connect(choose_model)
        for box, value in [(dnd,self.character.dnd),(pinned,self.character.pinned),(animate,self.character.animate),
                           (startup,self.store.setting('autostart',False)),(network,self.policy.network),(observer,self.policy.observer)]:
            box.setChecked(value)
        speed, size = QSpinBox(), QSpinBox()
        speed.setRange(0,120)
        speed.setValue(self.character.speed)
        size.setRange(80,240)
        size.setValue(self.character.width())
        provider = QComboBox()
        for title, value in [('Офлайн — простые команды без модели', 'offline'), ('Локальный ИИ — Ollama', 'ollama'), ('Облачный ИИ — отдельно разрешить', 'cloud')]:
            provider.addItem(title, value)
        ai = self.store.setting('ai', {})
        provider.setCurrentIndex(max(0, provider.findData(ai.get('provider', 'offline'))))
        model = QLineEdit(ai.get('model') or DEFAULT_OLLAMA_MODEL)
        model.setPlaceholderText('Ollama: qwen2.5-coder:3b')
        model.setToolTip('Для локальной модели: ollama pull qwen2.5-coder:3b')
        endpoint = QLineEdit(ai.get('endpoint','https://api.openai.com/v1/chat/completions'))
        key = QLineEdit()
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText('Только для сохранения в Windows Credential Manager')
        hotkey = QComboBox()
        hotkey.addItems(['Ctrl+Alt+F12','Ctrl+Shift+F12'])
        hotkey.setCurrentIndex(int(self.store.setting('hotkey_shift',False)))
        for label, field in [('Характер',personality),('Не мешать / пропуск кликов',dnd),('Закрепить позицию',pinned),('Персонаж поверх остальных окон',topmost),
            ('Анимации',animate),('Скорость',speed),('Размер',size),('Озвучивать ответы (локально)',speech_enabled),('Автозапуск Windows',startup),
            ('Наблюдатель (без изменений)',observer),('Разрешить облачную сеть',network),('ИИ',provider),
            ('Модель',model),('Адрес облачного API (HTTPS)',endpoint),('Новый API-ключ',key),('Экстренный стоп',hotkey)]:
            form.addRow(label,field)
        form.addRow('Русский голос', voice)
        form.addRow('Темп речи (−3…3)', speech_rate)
        form.addRow(voice_status)
        form.addRow(voice_row)
        form.addRow('Модель распознавания речи', microphone_model)
        form.addRow(choose_microphone_model)
        form.addRow(mic_status)
        def cloud_fields():
            visible = provider.currentData() == 'cloud'
            for field in (network, endpoint, key):
                field.setVisible(visible)
                form.labelForField(field).setVisible(visible)
        provider.currentIndexChanged.connect(cloud_fields)
        cloud_fields()
        pack_button = QPushButton('Загрузить локальный пакет персонажа…')
        def load_pack():
            folder = QFileDialog.getExistingDirectory(dialog, 'Папка с manifest.json и PNG')
            if folder:
                try:
                    self.character.pack = CharacterPack.load(folder)
                    self.store.set_setting('character_pack',folder)
                except (ValueError,OSError) as exc:
                    QMessageBox.warning(dialog,'Пакет отклонён',str(exc))
        pack_button.clicked.connect(load_pack)
        form.addRow(pack_button)
        glb_button = QPushButton('Импортировать локальную GLB-модель через Blender…')
        glb_button.clicked.connect(self.import_avatar)
        form.addRow(glb_button)
        stop_button = QPushButton('СТОП — голос, микрофон и импорт')
        stop_button.clicked.connect(self.stop)
        form.addRow(stop_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return
        try:
            if provider.currentData() == 'cloud' and not self.confirm('Включить облачный ИИ?', 'Текст новых команд будет отправляться выбранному HTTPS API. Скриншоты и история автоматически не отправляются. Продолжить?'):
                return
            if key.text():
                SecretVault().save(key.text())
                key.clear()
            autostart(startup.isChecked())
            self.store.set_setting('autostart',startup.isChecked())
            self.store.set_setting('personality',personality.currentText())
            self.store.set_setting('speech_enabled',speech_enabled.isChecked())
            self.store.set_setting('speech_voice', voice.currentData())
            self.store.set_setting('speech_rate', speech_rate.value())
            self.microphone_model = microphone_model.text()
            self.store.set_setting('microphone_model', self.microphone_model)
            if not speech_enabled.isChecked() and self.speech is not None:
                self.speech.stop()
            self.store.set_setting('ai',{'provider':provider.currentData(),'model':model.text(),'endpoint':endpoint.text()})
            self.store.set_setting('hotkey_shift',hotkey.currentIndex() == 1)
            self.policy.network, self.policy.observer = network.isChecked(), observer.isChecked()
            self.persist_policy()
            self.character.set_dnd(dnd.isChecked())
            self.character.set_topmost(topmost.isChecked())
            self.character.pinned, self.character.animate = pinned.isChecked(), animate.isChecked()
            self.character.speed = speed.value()
            self.character.resize(size.value(),round(size.value()*146/116))
            self.character.ensure_visible()
            self.save_state()
            self.refresh_provider_status()
            if not self.test_mode:
                self.register_hotkey()
        except Exception as exc:
            self.on_error('Настройки сохранены не полностью: ' + type(exc).__name__)

    def closeEvent(self, event):
        if self.tray and not self.shutting_down:
            self.hide()
            event.ignore()
        else:
            event.ignore()
            self.quit_app()

    def shutdown(self):
        self.state_timer.stop()
        self.voice_timer.stop()
        if self.microphone is not None:
            self.microphone.stop()
            self.microphone_status_label.setText('Микрофон выключен.')
        if self.speech is not None:
            self.speech.stop()
        if self.bridge:
            self.bridge.close()
        self.save_state()
        self.executor.stop()
        if self.hotkey:
            self.hotkey.close()
        if self.capture_task:
            self.end_capture()
        if self.tray:
            self.tray.hide()
        self.character.timer.stop()
        self.character.hide()
        self.hide()

    def quit_app(self):
        self.shutting_down = True
        self.stop()
        self.cancel_recording()
        if self.task is not None or self.capture_task is not None:
            self.statusBar().showMessage('Завершаются операции; выход после их остановки…')
            return
        self.shutdown()
        QApplication.instance().quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('BenderAI')
    app.setStyle('Fusion')
    app.setStyleSheet('''
        QWidget { font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #e0e8f2; }
        QMainWindow, QDialog, QMenu { background: #172132; }
        QMenuBar { background: #22364e; color: #ffffff; padding: 5px; }
        QMenuBar::item { background: transparent; padding: 7px 12px; }
        QMenuBar::item:selected, QMenu::item:selected { background: #326a78; color: #ffffff; }
        QMenu::item { padding: 8px 20px; }
        QTabWidget::pane { border: 1px solid #344156; border-radius: 8px; background: #1c293c; }
        QTabBar::tab { background: #172132; padding: 12px 18px; }
        QTabBar::tab:selected { color: #7de2d1; border-bottom: 2px solid #7de2d1; }
        QTextBrowser, QPlainTextEdit, QLineEdit, QListWidget, QTableWidget, QComboBox, QSpinBox {
            background: #101a29; border: 1px solid #344156; border-radius: 6px; padding: 8px; selection-background-color: #326a78; }
        QPushButton { background: #2a4058; border: 1px solid #3c5570; border-radius: 6px; padding: 9px 12px; }
        QPushButton:hover { background: #355671; }
        QPushButton:disabled { color: #78879b; background: #202d40; }
        QLabel { background: transparent; }
        QHeaderView::section { background: #2a4058; padding: 6px; }
    ''')
    app.setQuitOnLastWindowClosed(False)
    import tempfile
    smoke = '--smoke-test' in sys.argv
    temporary = tempfile.TemporaryDirectory(prefix='bender-smoke-') if smoke else None
    root = Path(temporary.name) if temporary else data_dir()
    if '--native-smoke' in sys.argv:
        from .isolation import WindowsBridge
        bridge = WindowsBridge(Policy())
        try:
            assert isinstance(bridge.ui.windows(), list)
        finally:
            bridge.close()
    lock = QLockFile(str(root / 'instance.lock'))
    if not lock.tryLock(100):
        QMessageBox.information(None,'BENDER AI','Приложение уже запущено. Откройте чат через системный трей.')
        return 0
    store = Store(root / 'memory.db')
    window = MainWindow(store, test_mode=smoke)
    window.show()
    if smoke:
        QTimer.singleShot(400, window.quit_app)
    code = app.exec()
    window.shutdown()
    store.close()
    lock.unlock()
    if temporary:
        temporary.cleanup()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
