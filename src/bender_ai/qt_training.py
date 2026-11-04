"""In-process adapter for portable integration checks, never used as Windows UIA."""
from PySide6.QtWidgets import QLineEdit, QPushButton
from .security import Denied


class QtFormAdapter:
    def __init__(self, form, process):
        self.form, self.process = form, process

    def find(self, selector):
        if selector.app != self.process or selector.window != self.form.windowTitle():
            raise Denied('Окно не совпадает')
        candidates = [w for w in self.form.findChildren(object) if hasattr(w, 'objectName') and w.objectName() == selector.automation_id]
        if len(candidates) != 1:
            raise Denied('Элемент отсутствует или неоднозначен')
        widget = candidates[0]
        if isinstance(widget, QLineEdit) and widget.echoMode() != QLineEdit.EchoMode.Normal:
            raise Denied('Password-поле запрещено')
        return widget

    def click(self, selector):
        widget = self.find(selector)
        if not isinstance(widget, QPushButton):
            raise Denied('Поддерживается только кнопка')
        widget.click()

    def set_text(self, selector, text):
        widget = self.find(selector)
        if not isinstance(widget, QLineEdit):
            raise Denied('Элемент не является полем ввода')
        widget.setText(text)
        if widget.text() != text:
            raise RuntimeError('Текст не установлен')

    def exists(self, selector):
        try:
            self.find(selector)
            return True
        except Denied:
            return False

    def assert_state(self, selector, state):
        widget = self.find(selector)
        if state.startswith('text:'):
            return widget.text() == state[5:]
        return {'exists': True, 'enabled': widget.isEnabled(), 'visible': widget.isVisible()}[state]

    def focus(self, selector):
        self.find(selector).setFocus()
