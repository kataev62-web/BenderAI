"""Windows adapters. Importable on Linux; native dependencies load only on Windows."""
from contextlib import contextmanager
from pathlib import Path
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import time
from .models import Selector
from .security import Denied


def require_windows():
    if sys.platform != 'win32':
        raise RuntimeError('Эта функция требует Windows 10/11')


@contextmanager
def com_thread():
    require_windows()
    import pythoncom
    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def downloads_folder():
    require_windows()
    import uuid
    guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID('374de290-123f-4565-9164-39c4925e467b').bytes_le)
    value = ctypes.c_wchar_p()
    shell = ctypes.windll.shell32
    result = shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(value))
    if result:
        raise OSError('Не удалось найти папку Загрузки')
    try:
        return Path(value.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(value)


class WindowsDesktop:
    def open_app(self, executable):
        require_windows()
        system = Path(os.environ['WINDIR']) / 'System32'
        path = system / executable
        if executable not in ('notepad.exe', 'calc.exe', 'explorer.exe'):
            raise Denied('Приложение не разрешено')
        if executable == 'explorer.exe':
            path = Path(os.environ['WINDIR']) / executable
        process = subprocess.Popen([str(path)], shell=False)
        # Modern Windows apps may hand over to an existing process; verify a matching UI window.
        from pywinauto import Desktop
        end = time.monotonic() + 8
        expected = {'notepad.exe': ('notepad.exe',), 'calc.exe': ('calculatorapp.exe', 'calculator.exe'), 'explorer.exe': ('explorer.exe',)}[executable]
        while time.monotonic() < end:
            with com_thread():
                for window in Desktop(backend='uia').windows():
                    try:
                        if process_name(window.process_id()).casefold() in expected:
                            return 'Приложение открыто'
                    except (OSError, RuntimeError):
                        continue
            time.sleep(.1)
        raise RuntimeError('Не удалось подтвердить открытие приложения')

    def open_folder(self, path):
        require_windows()
        if not Path(path).is_dir():
            raise Denied('Папка отсутствует')
        os.startfile(str(path), 'open')
        return 'Папка передана Проводнику'

    def active(self, policy):
        require_windows()
        import win32gui
        import win32process
        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        name = process_name(pid)
        policy.app(name)
        title = win32gui.GetWindowText(hwnd)
        policy.window(name, title)
        check_private(title)
        with com_thread():
            from pywinauto import Desktop
            window = Desktop(backend='uia').window(handle=hwnd).wrapper_object()
            elements = []
            for e in window.descendants()[:300]:
                if safe_element(e):
                    info = e.element_info
                    elements.append({'automation_id': info.automation_id, 'control_type': info.control_type})
        return {'process': name, 'title': title, 'elements': elements}


def process_name(pid):
    require_windows()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(buffer.value).name
    finally:
        kernel.CloseHandle(handle)


def check_private(title):
    if any(token in title.casefold() for token in ('inprivate', 'incognito', 'инкогнито', 'приватн', 'private browsing')):
        raise Denied('Приватное окно исключено')


def safe_element(element):
    try:
        return not bool(element.element_info.element.CurrentIsPassword)
    except Exception:
        # Unknown password state must fail closed.
        return False


class WindowsUIA:
    """Exact matches only. No fuzzy lookup and no coordinate replay."""
    def __init__(self, policy):
        self.policy = policy

    def _window(self, selector):
        from pywinauto import Desktop
        self.policy.app(selector.app)
        check_private(selector.window)
        self.policy.window(selector.app, selector.window)
        matches = []
        for w in Desktop(backend='uia').windows(title=selector.window):
            try:
                if process_name(w.process_id()).casefold() == selector.app.casefold():
                    matches.append(w)
            except OSError:
                continue
        if len(matches) != 1:
            raise Denied('Окно отсутствует или найдено несколько совпадений')
        return matches[0]

    def _find(self, selector):
        window = self._window(selector)
        found = []
        for element in window.descendants()[:1000]:
            info = element.element_info
            if info.control_type != selector.control_type:
                continue
            if selector.automation_id and info.automation_id != selector.automation_id:
                continue
            if not selector.automation_id and info.name != selector.name:
                continue
            if selector.parents:
                parent, actual = element.parent(), []
                for _ in selector.parents:
                    actual.append(parent.element_info.automation_id)
                    parent = parent.parent()
                if tuple(actual) != selector.parents:
                    continue
            if not safe_element(element):
                raise Denied('Password-поля и неизвестные поля исключены')
            found.append(element)
        if len(found) != 1:
            raise Denied('Элемент отсутствует или неоднозначен; исправьте селектор')
        return found[0]

    def click(self, selector):
        self.policy.app(selector.app, control=True)
        with com_thread():
            element = self._find(selector)
            if selector.control_type != 'Button' or not element.is_enabled():
                raise Denied('Поддерживаются только доступные кнопки Invoke')
            name = element.element_info.name.casefold()
            if any(token in name for token in ('delete', 'remove', 'send', 'pay', 'удал', 'отправ', 'оплат', 'купить')):
                raise Denied('Удаление, отправка и финансовые действия запрещены')
            element.iface_invoke.Invoke()

    def set_text(self, selector, text):
        self.policy.app(selector.app, control=True)
        with com_thread():
            element = self._find(selector)
            if selector.control_type != 'Edit' or not element.is_enabled():
                raise Denied('Поддерживаются только доступные Edit/ValuePattern')
            element.iface_value.SetValue(text)
            if element.iface_value.CurrentValue != text:
                raise RuntimeError('UIA не подтвердила введённое значение')

    def exists(self, selector):
        with com_thread():
            try:
                self._find(selector)
                return True
            except Denied:
                return False

    def assert_state(self, selector, state):
        with com_thread():
            element = self._find(selector)
            if state.startswith('text:'):
                value = element.iface_value.CurrentValue if selector.control_type == 'Edit' else element.element_info.name
                return value == state[5:]
            return {'exists': True, 'enabled': element.is_enabled(), 'visible': element.is_visible()}[state]

    def focus(self, selector):
        self.policy.app(selector.app, control=True)
        with com_thread():
            self._window(selector).set_focus()

    def windows(self):
        with com_thread():
            from pywinauto import Desktop
            result = []
            for window in Desktop(backend='uia').windows():
                try:
                    app = process_name(window.process_id())
                    self.policy.app(app)
                    title = window.window_text()
                    self.policy.window(app, title)
                    check_private(title)
                    if title:
                        result.append((app, title))
                except (Denied, OSError):
                    continue
            return result

    def snapshot(self, app, title, include_text=False):
        with com_thread():
            window = self._window(Selector(app, title, '__window__', 'Window'))
            result = []
            for element in window.descendants()[:300]:
                info = element.element_info
                if not safe_element(element) or info.control_type not in ('Edit', 'Button', 'Text'):
                    continue
                if not info.automation_id:
                    continue  # recording requires a stable ID, never an unstable coordinate fallback
                selector = Selector(app, title, info.automation_id, info.control_type)
                value = ''
                if include_text and info.control_type == 'Edit':
                    try:
                        value = element.iface_value.CurrentValue
                    except Exception:
                        continue
                result.append((selector, value))
            return result

    def clicked_selector(self, app, title):
        with com_thread():
            import win32gui
            from pywinauto import Desktop
            point = win32gui.GetCursorPos()
            element = Desktop(backend='uia').from_point(*point)
            info = element.element_info
            if info.control_type != 'Button' or not info.automation_id or not safe_element(element):
                return None
            root = element.top_level_parent()
            if root.window_text() != title or process_name(root.process_id()).casefold() != app.casefold():
                return None
            return Selector(app, title, info.automation_id, info.control_type)


def _record_events(connection, app, title, policy_data, literal_text):
    """Dedicated UIA event process. No keyboard hooks and no cursor sampling."""
    import threading
    # pythoncom initializes the importing main thread; choose MTA before that import.
    sys.coinit_flags = 0
    initialized = False
    try:
        import pythoncom
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        initialized = True
        import comtypes
        from pywinauto.uia_defines import IUIA
        from .security import Policy
        policy = Policy(**policy_data)
        adapter = WindowsUIA(policy)
        root = adapter._window(Selector(app, title, '__window__', 'Window'))
        api = IUIA()
        write_lock = threading.Lock()
        def emit(kind, sender, new_value=''):
            try:
                if sender.CurrentIsPassword or sender.CurrentProcessId != root.process_id():
                    return
                if root.window_text() != title:
                    raise Denied('Заголовок записываемого окна изменился')
                control_type = {50000: 'Button', 50004: 'Edit'}.get(sender.CurrentControlType)
                if control_type != ('Button' if kind == 'click' else 'Edit'):
                    return
                automation_id = sender.CurrentAutomationId
                if not automation_id:
                    return
                selector = Selector(app, title, automation_id, control_type)
                value = str(new_value) if literal_text and kind == 'set_text' else ''
                with write_lock:
                    connection.send(('event', kind, selector.to_dict(), value, time.perf_counter()))
            except Exception:
                # Fail visibly instead of silently claiming a complete demonstration.
                with write_lock:
                    connection.send(('error', 'UIA не смогла прочитать событие. Проверьте черновик.'))
        class InvokeHandler(comtypes.COMObject):
            _com_interfaces_ = [api.UIA_dll.IUIAutomationEventHandler]
            def HandleAutomationEvent(self, sender, event_id):
                emit('click', sender)
                return 0
        class PropertyHandler(comtypes.COMObject):
            _com_interfaces_ = [api.UIA_dll.IUIAutomationPropertyChangedEventHandler]
            def HandlePropertyChangedEvent(self, sender, property_id, new_value):
                if property_id == 30045:
                    emit('set_text', sender, new_value)
                return 0
        invoke, value = InvokeHandler(), PropertyHandler()
        properties = (ctypes.c_int * 1)(30045)
        element = root.element_info.element
        api.iuia.AddAutomationEventHandler(20009, element, api.tree_scope['subtree'], None, invoke)
        api.iuia.AddPropertyChangedEventHandlerNativeArray(element, api.tree_scope['subtree'], None, value, properties, 1)
        connection.send(('ready',))
        while not connection.poll(.05):
            pass  # callbacks are delivered on COM's MTA thread pool
        connection.recv()
        api.iuia.RemoveAutomationEventHandler(20009, element, invoke)
        api.iuia.RemovePropertyChangedEventHandler(element, value)
    except Exception as exc:
        connection.send(('error', 'Запись UIA недоступна: ' + type(exc).__name__))
    finally:
        if initialized:
            pythoncom.CoUninitialize()
        connection.close()


class WindowsCapture:
    """Scoped Invoke/ValuePattern event subscription; no global input capture."""
    def __init__(self, ui, recorder):
        self.ui, self.recorder = ui, recorder
        self.process, self.connection = None, None
        self.started = time.monotonic()
        self.ready = False

    def _start(self):
        import multiprocessing
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        self.connection = parent
        policy = self.ui.bridge.policy
        self.process = context.Process(target=_record_events, args=(child, self.recorder.app,
            self.recorder.window, policy.to_dict(), self.recorder.literal_text), daemon=True)
        self.process.start()
        child.close()

    def poll(self):
        if self.process is None:
            self._start()
        if time.monotonic() - self.started > 300:
            raise TimeoutError('Лимит записи: 5 минут')
        self._drain()
        if not self.process.is_alive():
            raise RuntimeError('Процесс записи завершился')
        if not self.ready and time.monotonic() - self.started > 12:
            raise TimeoutError('UIA не подтвердила подписку на события')

    def _drain(self):
        while self.connection.poll():
            try:
                event = self.connection.recv()
            except EOFError:
                break
            if event[0] == 'ready':
                self.ready = True
            elif event[0] == 'error':
                raise RuntimeError(event[1])
            elif event[0] == 'event':
                self.recorder.capture(event[1], Selector.from_dict(event[2]), event[3], timestamp=event[4])
    def close(self):
        if self.process:
            if self.process.is_alive():
                try:
                    self.connection.send(('stop',))
                except (OSError, EOFError):
                    pass
                self.process.join(.3)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(1)
            try:
                self._drain()
            except (OSError, RuntimeError):
                pass
            self.process.close()
            self.process = None
        if self.connection:
            self.connection.close()
            self.connection = None
