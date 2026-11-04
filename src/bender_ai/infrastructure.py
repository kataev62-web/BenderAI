from pathlib import Path
import ctypes
import os
import sys
import threading
from PySide6.QtCore import QThread, Signal, QAbstractNativeEventFilter


def data_dir():
    root = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'BenderAI'
    root.mkdir(parents=True, exist_ok=True)
    return root


class Task(QThread):
    result = Signal(object)
    failure = Signal(str)
    progress = Signal(int, str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.result.emit(self.function())
        except Exception as exc:
            from .models import ValidationError
            from .security import Denied
            message = str(exc) if isinstance(exc, (Denied, ValidationError, TimeoutError)) else 'Операция не выполнена: ' + type(exc).__name__ + '. Проверьте настройки или выберите офлайн-режим.'
            self.failure.emit(message)


class CaptureTask(QThread):
    count = Signal(int)
    diagnostic = Signal(str)
    ready = Signal()
    failure = Signal(str)

    def __init__(self, capture, parent=None):
        super().__init__(parent)
        self.capture = capture
        self.stop_event = threading.Event()

    def run(self):
        ready_sent = False
        diagnostics_sent = 0
        try:
            while not self.stop_event.wait(.04):
                self.capture.poll()
                diagnostics = getattr(self.capture.recorder, "diagnostics", [])
                for reason in diagnostics[diagnostics_sent:]:
                    self.diagnostic.emit(reason)
                diagnostics_sent = len(diagnostics)
                if self.capture.ready:
                    if not ready_sent:
                        self.ready.emit()
                        ready_sent = True
                    self.count.emit(len(self.capture.recorder.steps))
        except Exception:
            self.capture.recorder.pause()
            self.failure.emit('Запись приостановлена: ошибка доступа UIA или лимит. Проверьте окно и шаги.')
        finally:
            self.capture.close()


class EmergencyHotkey(QAbstractNativeEventFilter):
    def __init__(self, app, callback, shift=False):
        super().__init__()
        self.app, self.callback, self.registered = app, callback, False
        if sys.platform == 'win32':
            # Ctrl+Alt+F12 by default, optionally Ctrl+Shift+F12. Fail visibly on conflict.
            self.registered = bool(ctypes.windll.user32.RegisterHotKey(None, 0xB3AD, 0x4000 | 2 | (4 if shift else 1), 0x7B))
            if not self.registered:
                raise RuntimeError('Горячая клавиша занята; выберите другое сочетание в настройках')
            app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if sys.platform == 'win32':
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x312 and msg.wParam == 0xB3AD:
                self.callback()
                return True, 0
        return False, 0

    def close(self):
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, 0xB3AD)
            self.app.removeNativeEventFilter(self)
            self.registered = False


def autostart(enable):
    if sys.platform != 'win32':
        if enable:
            raise RuntimeError('Автозапуск доступен только в Windows')
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run', 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            # Frozen GUI executable or pythonw + module, always quoted.
            if getattr(sys, 'frozen', False):
                command = '"' + sys.executable + '"'
            else:
                pythonw = Path(sys.executable).with_name('pythonw.exe')
                command = '"' + str(pythonw) + '" -m bender_ai'
            winreg.SetValueEx(key, 'BenderAI', 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, 'BenderAI')
            except FileNotFoundError:
                pass
