"""Human-readable step editor; advanced JSON is optional, never executed as code."""
import json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QDialogButtonBox, QFormLayout,
    QComboBox, QPlainTextEdit, QMessageBox, QInputDialog)
from .models import Plan, Step, SCHEMAS
from .infrastructure import install_russian_ui
from PySide6.QtWidgets import QApplication

LABELS = {'read_text_file':'Прочитать текстовый файл','write_text_file':'Создать текстовый файл',
          'copy_file':'Скопировать файл','uia_select': 'Выбрать элемент', 'uia_toggle': 'Переключить (on/off)',
          'uia_expand': 'Раскрыть / свернуть', 'wait_for_window': 'Ждать окно', 'uia_click': 'Нажать кнопку', 'uia_set_text': 'Ввести текст', 'wait_for_element': 'Ждать элемент',
          'assert_element': 'Проверить элемент', 'open_app': 'Открыть приложение', 'open_folder': 'Открыть папку',
          'create_folder': 'Создать папку', 'get_active_window': 'Активное окно', 'list_skills': 'Список навыков',
          'focus_window': 'Выбрать окно', 'list_folder': 'Содержимое папки', 'stop': 'Стоп',
          'launch_approved_gradle_task': 'Утверждённая задача Gradle'}


ARG_LABELS = {'app_id': 'Приложение', 'path': 'Путь', 'parent': 'Родительская папка',
              'name': 'Имя', 'text': 'Текст', 'timeout': 'Ожидание, сек.',
              'state': 'Состояние', 'expected_state': 'Ожидаемый результат',
              'project_path': 'Проект', 'task': 'Задача', 'source': 'Исходный файл',
              'target': 'Целевой файл', 'app': 'Процесс', 'window': 'Окно',
              'automation_id': 'ID элемента', 'control_type': 'Тип элемента', 'parents': 'Родители'}


def readable_plan(plan):
    # Values remain exact and JSON-quoted: embedded newlines cannot imitate another step.
    quoted = lambda value: json.dumps(value, ensure_ascii=False)
    lines = ['План: ' + quoted(plan.name)]
    if plan.parameters:
        lines.append('Параметры при запуске: ' + quoted(plan.parameters))
    for number, step in enumerate(plan.steps, 1):
        lines.append(f'{number}. {LABELS.get(step.action, step.action)}')
        for key, value in step.args.items():
            if key == 'selector':
                lines.append('   Целевой элемент:')
                for field, field_value in value.items():
                    lines.append('      ' + ARG_LABELS.get(field, field) + ': ' + quoted(field_value))
            else:
                lines.append('   ' + ARG_LABELS.get(key, key) + ': ' + quoted(value))
    return '\n'.join(lines)


class StepDialog(QDialog):
    def __init__(self, step, parent=None):
        super().__init__(parent)
        install_russian_ui(QApplication.instance())
        self.setWindowTitle('Изменить шаг')
        self.step = step
        form = QFormLayout(self)
        form.addRow(QLabel(LABELS.get(step.action, step.action)))
        self.fields, self.selector_fields = {}, {}
        for key, value in step.args.items():
            if key == 'selector':
                for k, v in value.items():
                    field = QLineEdit(json.dumps(v) if k == 'parents' else str(v))
                    self.selector_fields[k] = field
                    form.addRow({'app': 'Процесс', 'window': 'Заголовок окна', 'automation_id': 'AutomationId',
                                 'control_type': 'Тип контрола', 'name': 'Имя', 'parents': 'ID родителей (JSON)'}.get(k,k), field)
            else:
                field = QLineEdit(str(value))
                self.fields[key] = field
                form.addRow({'text': 'Текст / ${параметр}', 'timeout': 'Таймаут, сек.', 'expected_state': 'exists / enabled / visible / text:значение',
                             'path':'Путь к файлу / папке', 'source':'Исходный файл', 'target':'Новый файл',
                             'state': 'on / off; expanded / collapsed', 'app_id': 'ID разрешённого приложения (notepad или writer.exe)', 'parent': 'Родительская папка', 'name': 'Имя папки'}.get(key,key), field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def validate(self):
        try:
            args = {k: float(v.text()) if k == 'timeout' else v.text() for k,v in self.fields.items()}
            if self.selector_fields:
                args['selector'] = {k: json.loads(v.text()) if k == 'parents' else v.text() for k,v in self.selector_fields.items()}
            self.step = Step(self.step.action, args)
            self.accept()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, 'Неверный шаг', str(exc))


class SkillEditor(QDialog):
    def __init__(self, plan, parent=None):
        super().__init__(parent)
        install_russian_ui(QApplication.instance())
        self.setWindowTitle('Редактор навыка — сохранение после проверки')
        self.resize(900, 560)
        self.plan = Plan.from_dict(plan.to_dict())
        layout = QVBoxLayout(self)
        self.name = QLineEdit(plan.name)
        self.params = QLineEdit(', '.join(plan.parameters))
        layout.addWidget(QLabel('Название навыка'))
        layout.addWidget(self.name)
        layout.addWidget(QLabel('Параметры (латиницей через запятую); значения вводятся при запуске'))
        layout.addWidget(self.params)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Действие', 'Приложение', 'Окно', 'Элемент / цель', 'Значение / проверка'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit())
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for index, (title, fn) in enumerate([('Изменить', self.edit), ('Удалить шаг', self.remove), ('Вверх', self.up), ('Вниз', self.down), ('Добавить проверку', self.add_assert), ('Ждать окно', self.add_wait_window), ('Ждать элемент', self.add_wait_element), ('Выбрать окно', self.add_focus_window), ('JSON…', self.advanced)]):
            if index == 4:
                layout.addLayout(row)
                row = QHBoxLayout()
            b = QPushButton(title)
            b.clicked.connect(fn)
            row.addWidget(b)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.table.setRowCount(len(self.plan.steps))
        for i, step in enumerate(self.plan.steps):
            selector = step.args.get('selector', {})
            target = selector.get('automation_id', '') or selector.get('name', '') or str(step.args.get('path', step.args.get('app_id', '')))
            value = step.args.get('text', step.args.get('expected_state', step.args.get('state', step.args.get('timeout', ''))))
            for j, text in enumerate((LABELS.get(step.action, step.action), selector.get('app', ''), selector.get('window', ''), target, str(value))):
                self.table.setItem(i, j, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def edit(self):
        i = self.table.currentRow()
        if i >= 0:
            dialog = StepDialog(self.plan.steps[i], self)
            if dialog.exec():
                self.plan.steps[i] = dialog.step
                self.refresh()

    def remove(self):
        i = self.table.currentRow()
        if i >= 0:
            self.plan.steps.pop(i)
            self.refresh()

    def up(self):
        i = self.table.currentRow()
        if i > 0:
            self.plan.steps[i-1], self.plan.steps[i] = self.plan.steps[i], self.plan.steps[i-1]
            self.refresh()
            self.table.selectRow(i-1)

    def down(self):
        i = self.table.currentRow()
        if 0 <= i < len(self.plan.steps) - 1:
            self.plan.steps[i], self.plan.steps[i+1] = self.plan.steps[i+1], self.plan.steps[i]
            self.refresh()
            self.table.selectRow(i+1)

    def add_window_step(self, action):
        i = self.table.currentRow()
        if i >= 0 and 'selector' in self.plan.steps[i].args:
            args = {'selector': dict(self.plan.steps[i].args['selector'])}
            if action in ('wait_for_window', 'wait_for_element'):
                args['timeout'] = 10
            self.plan.steps.insert(i+1, Step(action, args))
            self.refresh()
            self.table.selectRow(i+1)
        else:
            QMessageBox.information(self, 'Окно', 'Выберите шаг с селектором окна или элемента.')

    def add_wait_window(self):
        self.add_window_step('wait_for_window')

    def add_wait_element(self):
        self.add_window_step('wait_for_element')

    def add_focus_window(self):
        self.add_window_step('focus_window')

    def add_assert(self):
        i = self.table.currentRow()
        if i >= 0 and 'selector' in self.plan.steps[i].args:
            step = Step('assert_element', {'selector': self.plan.steps[i].args['selector'], 'expected_state': 'exists'})
            dialog = StepDialog(step, self)
            if dialog.exec():
                self.plan.steps.insert(i+1, dialog.step)
                self.refresh()
        else:
            QMessageBox.information(self, 'Проверка', 'Выберите шаг с UIA-элементом. В проверке можно изменить ID и ожидаемый текст.')

    def advanced(self):
        raw, ok = QInputDialog.getMultiLineText(self, 'JSON сценария', 'Строго валидируется; неизвестные инструменты отклоняются', self.plan.to_json())
        if ok:
            try:
                self.plan = Plan.from_json(raw)
                self.params.setText(', '.join(self.plan.parameters))
                self.refresh()
            except ValueError as exc:
                QMessageBox.warning(self, 'Ошибка', str(exc))

    def save(self):
        try:
            self.plan = Plan(self.plan.steps, self.name.text(), [x.strip() for x in self.params.text().split(',') if x.strip()])
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, 'Ошибка', str(exc))
