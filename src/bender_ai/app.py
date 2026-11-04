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
from .brain import RuleBasedProvider, OllamaProvider, CloudProvider
from .character import Character, CharacterPack
from .editor import SkillEditor
from .infrastructure import Task, CaptureTask, EmergencyHotkey, data_dir, autostart
from .learning import Recorder
from .models import Plan, ValidationError
from .security import Policy, SecretVault, Denied
from .storage import Store


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
        self.store, self.test_mode = store, test_mode
        self.task, self.capture_task, self.recorder = None, None, None
        self.pending, self.validation_target = None, None
        self.active_cancel = None
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
        self.character.activated.connect(self.show_chat)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        chat = QWidget()
        layout = QVBoxLayout(chat)
        heading = QLabel('BENDER AI   /   Исполнитель · офлайн по умолчанию')
        heading.setStyleSheet('font-size: 19px; font-weight: bold; padding: 12px;')
        layout.addWidget(heading)
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
        self.send_button = self.button('Предложить план', self.send, row)
        layout.addLayout(row)
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
        task.result.connect(lambda result: callback(result) if generation == self.generation else None)
        task.failure.connect(lambda error: self.on_error(error) if generation == self.generation else None)
        task.finished.connect(self.task_finished)
        task.start()
        return True

    def task_finished(self):
        old = self.task
        self.task = None
        self.send_button.setEnabled(True)
        self.execute_button.setEnabled(self.pending is not None)
        if self.character.state not in ('error', 'success', 'sleep'):
            self.character.state = 'idle'
        if old:
            old.deleteLater()
        self.refresh_journal()
        if self.restore_chat_after_run:
            self.restore_chat_after_run = False
            self.show_chat()
        if self.shutting_down:
            self.quit_app()

    def on_error(self, message):
        self.character.state = 'error'
        self.message('Ошибка', message)
        self.statusBar().showMessage('Ошибка • выполнение остановлено')

    def provider(self):
        config = self.store.setting('ai', {})
        kind = config.get('provider', 'offline')
        if kind == 'ollama':
            return OllamaProvider(config.get('model', 'qwen2.5:7b'))
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
        if self.task is not None:
            return
        self.input.clear()
        self.pending = None
        self.validation_target = None
        self.message('Вы', text)
        try:
            provider = self.provider()
            self.start_task(lambda: provider.plan(text), self.accept_plan)
        except Exception as exc:
            self.on_error(str(exc))

    def accept_plan(self, plan):
        self.pending = plan
        self.plan_view.setPlainText(plan.to_json())
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
                self.task.progress.connect(lambda i,a: self.statusBar().showMessage(f'Шаг {i}: {a}'))
                self.character.state = 'working'
        except Exception as exc:
            self.on_error(str(exc))

    def on_run(self, result, target=None):
        self.character.state = 'success' if result.status == 'success' else 'error'
        self.message('Результат', f'{result.status}: ' + (result.error or json.dumps(result.outputs, ensure_ascii=False)))
        self.statusBar().showMessage(result.status)
        QTimer.singleShot(2500, lambda: setattr(self.character, 'state', 'sleep' if self.character.dnd else 'idle') if self.task is None else None)
        if target:
            self.store.mark_validation(*target, result.status)
            self.refresh_skills()

    def stop(self):
        self.generation += 1
        self.executor.stop()
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
            state = versions[0]['validation']
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
            labels = [f"v{v['version']} • {v['created_at']} • {v['validation']}" for v in versions]
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
                                       ['Только чтение', 'Чтение и управление'], 0, False)
        if not ok:
            return
        control = mode == 'Чтение и управление'
        preview = (candidate['app'] + '\nОкно: ' + candidate['title'] + '\nФайл: ' + candidate['executable']
                   + '\nПрава: ' + ('чтение и управление' if control else 'только чтение')
                   + '\nНе выбирайте окна с чувствительными данными. Поддержка UIA зависит от приложения.')
        if not self.confirm('Выдать выбранные права?', preview):
            return
        try:
            self.policy.grant_app(candidate['app'], candidate['title'], candidate['executable'], control=control)
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
        self.start_task(self.ui.windows, self.choose_recording_window)

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
        self.recorder = Recorder(windows=selected, literal_text=dialog.literal_text.isChecked())
        self.recorder.start()
        self.record_diagnostics.clear()
        from .windows import WindowsCapture
        self.capture_task = CaptureTask(WindowsCapture(self.ui, self.recorder), self)
        self.capture_task.count.connect(self.recording_count)
        self.capture_task.ready.connect(lambda: self.recording_count(0))
        self.capture_task.diagnostic.connect(self.recording_diagnostic)
        self.capture_task.failure.connect(self.recording_failed)
        self.capture_task.finished.connect(lambda: self.quit_app() if self.shutting_down else None)
        self.capture_task.start()
        self.tabs.setCurrentIndex(2)
        self.record_label.setStyleSheet('color:#c93046;font-weight:bold;font-size:16px')
        self.record_label.setText('Подключение UIA… дождитесь красного индикатора записи.')

    def recording_count(self, count):
        if self.recorder:
            state = 'ПАУЗА' if self.recorder.paused else '● ЗАПИСЬ ДЕЙСТВИЙ ВКЛЮЧЕНА'
            windows = getattr(self.recorder, 'windows', ())
            self.record_label.setText(f'{state} • окон: {len(windows)} • шагов: {count}')

    def recording_diagnostic(self, reason):
        # Recorder supplies a closed set of safe reason codes, never field text.
        self.record_diagnostics.appendPlainText(str(reason))

    def recording_failed(self, message):
        self.record_label.setText('ЗАПИСЬ НА ПАУЗЕ • ошибка UIA. Проверьте черновик; запись может быть неполной.')
        self.on_error(message)

    def pause_recording(self):
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
            self.record_label.setText('Запись отменена; черновик удалён')

    def refresh_journal(self):
        runs = self.store.runs()
        lines = []
        for run in runs:
            lines.append(f"{run['created_at']} • {run['name']} • {run['status']}")
            for step in self.store.run_steps(run['id']):
                lines.append(f"  {step['position']+1}. {step['action']}: {step['status']} {step['detail']}")
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
            'speed': self.character.speed, 'animate': self.character.animate})

    def load_state(self):
        state = self.store.setting('character', {})
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
        for box, value in [(dnd,self.character.dnd),(pinned,self.character.pinned),(animate,self.character.animate),
                           (startup,self.store.setting('autostart',False)),(network,self.policy.network),(observer,self.policy.observer)]:
            box.setChecked(value)
        speed, size = QSpinBox(), QSpinBox()
        speed.setRange(0,120)
        speed.setValue(self.character.speed)
        size.setRange(80,240)
        size.setValue(self.character.width())
        provider = QComboBox()
        provider.addItems(['offline','ollama','cloud'])
        ai = self.store.setting('ai', {})
        provider.setCurrentText(ai.get('provider','offline'))
        model = QLineEdit(ai.get('model','qwen2.5:7b'))
        endpoint = QLineEdit(ai.get('endpoint','https://api.openai.com/v1/chat/completions'))
        key = QLineEdit()
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText('Только для сохранения в Windows Credential Manager')
        hotkey = QComboBox()
        hotkey.addItems(['Ctrl+Alt+F12','Ctrl+Shift+F12'])
        hotkey.setCurrentIndex(int(self.store.setting('hotkey_shift',False)))
        for label, field in [('Характер',personality),('Не мешать / пропуск кликов',dnd),('Закрепить позицию',pinned),
            ('Анимации',animate),('Скорость',speed),('Размер',size),('Автозапуск Windows',startup),
            ('Наблюдатель (без изменений)',observer),('Разрешить облачную сеть',network),('ИИ',provider),
            ('Модель',model),('Cloud HTTPS endpoint',endpoint),('Новый API-ключ',key),('Экстренный стоп',hotkey)]:
            form.addRow(label,field)
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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return
        try:
            if provider.currentText() == 'cloud' and not self.confirm('Включить облачный ИИ?', 'Текст новых команд будет отправляться выбранному HTTPS API. Скриншоты и история автоматически не отправляются. Продолжить?'):
                return
            if key.text():
                SecretVault().save(key.text())
                key.clear()
            autostart(startup.isChecked())
            self.store.set_setting('autostart',startup.isChecked())
            self.store.set_setting('personality',personality.currentText())
            self.store.set_setting('ai',{'provider':provider.currentText(),'model':model.text(),'endpoint':endpoint.text()})
            self.store.set_setting('hotkey_shift',hotkey.currentIndex() == 1)
            self.policy.network, self.policy.observer = network.isChecked(), observer.isChecked()
            self.persist_policy()
            self.character.set_dnd(dnd.isChecked())
            self.character.pinned, self.character.animate = pinned.isChecked(), animate.isChecked()
            self.character.speed = speed.value()
            self.character.resize(size.value(),round(size.value()*146/116))
            self.character.ensure_visible()
            self.save_state()
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
