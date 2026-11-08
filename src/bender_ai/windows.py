"""Windows adapters. Importable on Linux; native dependencies load only on Windows."""
from contextlib import contextmanager
from pathlib import Path
import ctypes
from ctypes import wintypes
import os
import ntpath
import subprocess
import sys
import time
from .models import Selector, OperationUnverified
from .security import Denied, ensure_uia_app, executable_key


class AppLaunchError(RuntimeError):
    """Safe, predefined error for an application dispatch that failed."""


class AppLaunchUnverified(OperationUnverified):
    """Dispatch succeeded, but the resulting application window is unverified."""


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
    def __init__(self, policy=None):
        self.policy = policy

    def open_app(self, executable):
        require_windows()
        if executable not in ('notepad.exe', 'calc.exe', 'explorer.exe'):
            raise Denied('Приложение не разрешено')
        system = Path(os.environ['WINDIR']) / 'System32'
        path = (Path(os.environ['WINDIR']) / executable if executable == 'explorer.exe'
                else system / executable)
        expected = {'notepad.exe': ('notepad.exe',),
                    'calc.exe': ('calculatorapp.exe', 'calculator.exe', 'calc.exe'),
                    'explorer.exe': ('explorer.exe',)}[executable]
        def verified_window():
            from pywinauto import Desktop
            with com_thread():
                return any(window_owned_by_app(window, expected)
                           for window in Desktop(backend='uia').windows())
        return self._launch_verified(path, verified_window)

    def open_approved_app(self, app_id):
        require_windows()
        if self.policy is None:
            raise Denied('Нет разрешения запуска приложения')
        path, titles = self.policy.approved_launch(app_id)
        def verified_window():
            from pywinauto import Desktop
            with com_thread():
                for window in Desktop(backend='uia').windows():
                    try:
                        title = window.window_text()
                        check_private(title)
                        if title not in titles:
                            continue
                        if executable_key(process_path(window.process_id())) == executable_key(path):
                            return True
                    except (Denied, OSError):
                        continue
                return False
        return self._launch_verified(path, verified_window)

    @staticmethod
    def _launch_verified(path, verified_window):
        def result(already_open):
            return {'status': 'verified', 'message': 'Приложение уже открыто' if already_open
                    else 'Приложение открыто', 'executable': str(path),
                    'already_open': already_open}
        try:
            if verified_window():
                return result(True)
        except Exception:
            # A metadata lookup failure cannot establish that an app is open.
            pass
        try:
            subprocess.Popen([str(path)], shell=False)
        except Exception:
            raise AppLaunchError('Windows не смогла запустить разрешённое приложение') from None
        # Modern apps may hand off to another process. Never dispatch a second time.
        try:
            end = time.monotonic() + 8
            while time.monotonic() < end:
                if verified_window():
                    return result(False)
                time.sleep(.1)
        except Exception:
            pass
        raise AppLaunchUnverified('Приложение могло открыться, но Windows не подтвердила его окно. '
                                  'Не повторяйте запуск автоматически.') from None

    def open_folder(self, path):
        require_windows()
        if not Path(path).is_dir():
            raise Denied('Папка отсутствует')
        os.startfile(str(path), 'open')
        return 'Папка передана Проводнику'

    def active(self, policy):
        require_windows()
        if not policy.read_apps:
            raise Denied('Нет разрешения на чтение приложений')
        try:
            import win32gui
            import win32process
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd or not win32gui.IsWindow(hwnd):
                raise Denied('Активное окно недоступно')
            thread, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not thread or not isinstance(pid, int) or not 0 < pid <= 0xFFFFFFFF:
                raise Denied('Активное окно недоступно')
            name = process_name(pid)
            ensure_uia_app(name)
            policy.identity(name, process_path(pid))
            check_process_access(pid)
            policy.app(name)
            title = win32gui.GetWindowText(hwnd)
            policy.window(name, title)
            check_private(title)
            with com_thread():
                from pywinauto import Desktop
                window = Desktop(backend='uia').window(handle=hwnd).wrapper_object()
                if (not win32gui.IsWindow(hwnd) or
                        win32process.GetWindowThreadProcessId(hwnd) != (thread, pid) or
                        window.process_id() != pid or window.window_text() != title):
                    raise Denied('Активное окно изменилось')
                elements = []
                for e in window.descendants()[:300]:
                    if safe_element(e):
                        info = e.element_info
                        elements.append({'automation_id': info.automation_id, 'control_type': info.control_type})
            return {'process': name, 'title': title, 'elements': elements}
        except Denied:
            raise
        except Exception:
            raise Denied('Не удалось безопасно прочитать активное окно') from None


def process_path(pid):
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
        return buffer.value
    finally:
        kernel.CloseHandle(handle)


def process_name(pid):
    return process_path(pid).replace('\\', '/').rsplit('/', 1)[-1]


def safe_raw_element(element):
    try:
        status = element.CurrentIsPassword
        return type(status) in (bool, int) and status == 0
    except Exception:
        return False


def _belongs_to_root(api, sender, root):
    """PID is insufficient: another window of that process is out of scope."""
    for _ in range(64):
        if not sender:
            return False
        if api.iuia.CompareElements(sender, root):
            return True
        sender = api.iuia.ControlViewWalker.GetParentElement(sender)
    return False


def check_process_access(pid):
    """Do not automate elevated/protected targets or bypass their security boundary."""
    require_windows()
    import win32api
    import win32security
    process = token = None
    try:
        process = win32api.OpenProcess(0x1000, False, pid)
        token = win32security.OpenProcessToken(process, 0x0008)
        if win32security.GetTokenInformation(token, win32security.TokenElevation):
            raise Denied('Повышенные приложения исключены')
    except Denied:
        raise
    except Exception:
        raise Denied('Не удалось проверить защиту приложения') from None
    finally:
        if token is not None:
            token.Close()
        if process is not None:
            process.Close()


def deny_dangerous_name(name):
    if any(token in name.casefold() for token in ('delete', 'remove', 'send', 'pay', 'purchase', 'buy', 'удал', 'отправ', 'оплат', 'купить')):
        raise Denied('Удаление, отправка и финансовые действия запрещены')


def process_package_family(pid):
    """Read Windows package identity, never infer it from a directory name."""
    require_windows()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    query = kernel.GetPackageFamilyName
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
    query.restype = wintypes.LONG
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        length = wintypes.UINT(0)
        result = query(handle, ctypes.byref(length), None)
        if result == 15700:  # APPMODEL_ERROR_NO_PACKAGE
            return ''
        if result != 122 or not 0 < length.value <= 512:  # ERROR_INSUFFICIENT_BUFFER
            raise OSError('Не удалось проверить пакет приложения')
        value = ctypes.create_unicode_buffer(length.value)
        if query(handle, ctypes.byref(length), value) != 0:
            raise OSError('Не удалось проверить пакет приложения')
        return value.value
    finally:
        kernel.CloseHandle(handle)


def trusted_app_process(pid, expected):
    """Built-in identity requires an OS installation path or OS package family."""
    path = process_path(pid)
    owner = ntpath.basename(path).casefold()
    if owner not in expected:
        return False
    root = os.environ.get('WINDIR', '')
    if not root:
        return False
    system_paths = {
        'calc.exe': (ntpath.join(root,'System32','calc.exe'),),
        'notepad.exe': (ntpath.join(root, 'System32', 'notepad.exe'), ntpath.join(root, 'notepad.exe')),
        'explorer.exe': (ntpath.join(root, 'explorer.exe'),),
        'applicationframehost.exe': (ntpath.join(root, 'System32', 'ApplicationFrameHost.exe'),),
    }
    if any(executable_key(path) == executable_key(candidate)
           for candidate in system_paths.get(owner, ())):
        return True
    families = {'notepad.exe': 'Microsoft.WindowsNotepad_8wekyb3d8bbwe',
                'calculatorapp.exe': 'Microsoft.WindowsCalculator_8wekyb3d8bbwe',
                'calculator.exe': 'Microsoft.WindowsCalculator_8wekyb3d8bbwe'}
    expected_family = families.get(owner)
    return bool(expected_family and process_package_family(pid).casefold() == expected_family.casefold())


def window_owned_by_app(window, expected):
    """Verify process identity, including packaged Calculator inside ApplicationFrameHost.

    Do not infer app identity from localized/window-supplied titles.
    """
    try:
        pid = window.process_id()
        if trusted_app_process(pid, expected):
            if 'explorer.exe' in expected:
                # Explorer also owns the desktop/taskbar, which are not file browsers.
                try:
                    window_class = window.class_name()
                except AttributeError:
                    window_class = window.element_info.class_name
                return window_class in ('CabinetWClass', 'ExploreWClass')
            if ntpath.basename(process_path(pid)).casefold()=='calc.exe':
                try:
                    window_class=window.class_name()
                except AttributeError:
                    window_class=window.element_info.class_name
                return window_class in ('CalcFrame','SciCalc')
            return True
        if 'calculatorapp.exe' not in expected or not trusted_app_process(pid, ('applicationframehost.exe',)):
            return False
        # Windows Calculator may render UI in another process under the UWP frame.
        # Check unique child process IDs rather than trusting the outer frame name.
        pids = set()
        for element in window.descendants()[:100]:
            pid = getattr(element.element_info, 'process_id', None)
            if isinstance(pid, int) and pid > 0:
                pids.add(pid)
        for pid in pids:
            try:
                if trusted_app_process(pid, expected):
                    return True
            except (OSError, RuntimeError):
                pass
    except (OSError, RuntimeError, AttributeError):
        return False
    return False


def check_private(title):
    if any(token in title.casefold() for token in ('inprivate', 'incognito', 'инкогнито', 'приватн', 'private browsing')):
        raise Denied('Приватное окно исключено')


def safe_element(element):
    try:
        return safe_raw_element(element.element_info.element)
    except Exception:
        # Unknown password state must fail closed.
        return False


def _wait_uia_state(predicate, end):
    """Observe asynchronous provider state without redispatching an action."""
    while True:
        try:
            if predicate():
                return True
        except Exception:
            pass
        if time.monotonic() >= end:
            return False
        time.sleep(min(.025, max(0, end - time.monotonic())))


class WindowsUIA:
    """Exact matches only. No fuzzy lookup and no coordinate replay."""
    def __init__(self, policy):
        self.policy = policy

    def _window(self, selector):
        ensure_uia_app(selector.app)
        from pywinauto import Desktop
        self.policy.app(selector.app)
        check_private(selector.window)
        self.policy.window(selector.app, selector.window)
        matches = []
        for w in Desktop(backend='uia').windows(title=selector.window):
            try:
                if process_name(w.process_id()).casefold() == selector.app.casefold() and w.window_text() == selector.window:
                    self.policy.identity(selector.app, process_path(w.process_id()))
                    check_process_access(w.process_id())
                    matches.append(w)
            except OSError:
                continue
        if not matches:
            raise Denied('Окно отсутствует')
        if len(matches) != 1:
            raise Denied('Найдено несколько совпадений окна')
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
        self.policy.window(selector.app, selector.window, control=True)
        with com_thread():
            element = self._find(selector)
            if selector.control_type != 'Button' or not element.is_enabled():
                raise Denied('Поддерживаются только доступные кнопки Invoke')
            deny_dangerous_name(element.element_info.name)
            element.iface_invoke.Invoke()

    def set_text(self, selector, text):
        self.policy.app(selector.app, control=True)
        self.policy.window(selector.app, selector.window, control=True)
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
        self.policy.window(selector.app, selector.window, control=True)
        with com_thread():
            import win32gui
            import win32api
            import win32process
            window = self._window(selector)
            hwnd = window.handle
            attached = []
            try:
                end = time.monotonic() + 2
                if not win32gui.IsWindow(hwnd):
                    raise Denied('Окно недоступно')
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE, no input injection
                def activate():
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                    except Exception:
                        pass  # Only actual foreground state can establish success.
                activate()
                initial_end = min(end, time.monotonic() + .25)
                while time.monotonic() < initial_end:
                    if win32gui.GetForegroundWindow() == hwnd:
                        return
                    time.sleep(.025)
                # UIA SetFocus alone does not activate a top-level native window.
                # Link only this authorized activation's current/foreground/target queues.
                win32gui.PeekMessage(None, 0, 0, 0)  # Ensure the current thread has a queue.
                current = win32api.GetCurrentThreadId()
                foreground = win32gui.GetForegroundWindow()
                target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
                threads = []
                if foreground and win32gui.IsWindow(foreground):
                    foreground_thread, _ = win32process.GetWindowThreadProcessId(foreground)
                    threads.append(foreground_thread)
                threads.append(target_thread)
                for thread in dict.fromkeys(threads):
                    if thread and thread != current:
                        result = win32process.AttachThreadInput(current, thread, True)
                        if result is False:
                            raise Denied('Windows отказала в переключении окна')
                        attached.append((current, thread))
                while time.monotonic() < end:
                    if not win32gui.IsWindow(hwnd):
                        raise Denied('Окно недоступно')
                    activate()
                    if win32gui.GetForegroundWindow() == hwnd:
                        return
                    time.sleep(.025)
                raise Denied('Windows не подтвердила переключение окна')
            except Denied:
                raise
            except Exception:
                raise Denied('Windows не подтвердила переключение окна') from None
            finally:
                detach_failed = False
                for first, second in reversed(attached):
                    try:
                        if win32process.AttachThreadInput(first, second, False) is False:
                            detach_failed = True
                    except Exception:
                        detach_failed = True
                if detach_failed:
                    raise Denied('Windows не завершила переключение окна')

    def window_exists(self, selector):
        # Policy failures must remain failures; only absence can be waited for.
        self.policy.app(selector.app)
        self.policy.window(selector.app, selector.window)
        check_private(selector.window)
        with com_thread():
            try:
                self._window(selector)
                return True
            except Denied as exc:
                if str(exc) == 'Окно отсутствует':
                    return False
                raise

    def select(self, selector):
        self.policy.app(selector.app, control=True)
        self.policy.window(selector.app, selector.window, control=True)
        with com_thread():
            element = self._find(selector)
            if not element.is_enabled():
                raise Denied('Элемент недоступен')
            deny_dangerous_name(element.element_info.name)
            pattern = element.iface_selection_item
            end = time.monotonic() + 2
            pattern.Select()
            def selected():
                try:
                    value = pattern.CurrentIsSelected
                    if type(value) in (bool, int) and value == 1:
                        return True
                except Exception:
                    pass
                if selector.control_type == 'RadioButton':
                    # Legacy Win32 radios can expose checked state through TogglePattern.
                    value = element.iface_toggle.CurrentToggleState
                    return type(value) is int and value == 1
                return False
            if not _wait_uia_state(selected, end):
                raise Denied('UIA не подтвердила выбор элемента')

    def toggle(self, selector, state):
        if state not in ('on', 'off'):
            raise Denied('Недопустимое состояние переключателя')
        self.policy.app(selector.app, control=True)
        self.policy.window(selector.app, selector.window, control=True)
        with com_thread():
            element = self._find(selector)
            if not element.is_enabled():
                raise Denied('Элемент недоступен')
            deny_dangerous_name(element.element_info.name)
            pattern = element.iface_toggle
            expected = int(state == 'on')
            end = time.monotonic() + 2
            def current():
                value = pattern.CurrentToggleState
                if type(value) is not int or value not in (0, 1, 2):
                    raise Denied('UIA не подтвердила состояние переключателя')
                return value
            # A further tri-state transition is allowed only after observed progress.
            for _ in range(3):
                previous = current()
                if previous == expected:
                    return
                pattern.Toggle()
                if not _wait_uia_state(lambda: current() != previous, end):
                    raise Denied('UIA не подтвердила переключатель')
                if current() == expected:
                    return
                if time.monotonic() >= end:
                    break
            raise Denied('UIA не подтвердила переключатель')

    def expand(self, selector, state):
        if state not in ('expanded', 'collapsed'):
            raise Denied('Недопустимое состояние раскрытия')
        self.policy.app(selector.app, control=True)
        self.policy.window(selector.app, selector.window, control=True)
        with com_thread():
            element = self._find(selector)
            if not element.is_enabled():
                raise Denied('Элемент недоступен')
            deny_dangerous_name(element.element_info.name)
            pattern = element.iface_expand_collapse
            expected = int(state == 'expanded')
            end = time.monotonic() + 2
            if pattern.CurrentExpandCollapseState != expected:
                (pattern.Expand if expected else pattern.Collapse)()
            def matches():
                value = pattern.CurrentExpandCollapseState
                return type(value) is int and value == expected
            if not _wait_uia_state(matches, end):
                raise Denied('UIA не подтвердила раскрытие элемента')

    def candidates(self):
        # Native top-level metadata only: no UIA tree access before approval.
        require_windows()
        import win32gui
        import win32process
        result = []
        def visit(hwnd, unused):
            try:
                title = win32gui.GetWindowText(hwnd)
                if not title or not win32gui.IsWindowVisible(hwnd):
                    return
                check_private(title)
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                executable = process_path(pid)
                app = process_name(pid)
                ensure_uia_app(app)
                check_process_access(pid)
                result.append({'app': app, 'title': title, 'executable': executable})
            except (Denied, OSError):
                return
        win32gui.EnumWindows(visit, None)
        return result[:300]

    def windows(self):
        with com_thread():
            from pywinauto import Desktop
            result = []
            for window in Desktop(backend='uia').windows():
                try:
                    app = process_name(window.process_id())
                    ensure_uia_app(app)
                    self.policy.app(app)
                    self.policy.identity(app, process_path(window.process_id()))
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


def _inventory_reason(element):
    if not safe_element(element):
        return 'password_or_unknown'
    info = element.element_info
    if not info.automation_id:
        return 'unstable_selector'
    patterns = {'Button': ('iface_invoke',), 'Edit': ('iface_value',),
                'CheckBox': ('iface_toggle',), 'RadioButton': ('iface_selection_item',),
                'ListItem': ('iface_selection_item',),
                'TreeItem': ('iface_selection_item', 'iface_expand_collapse'),
                'ComboBox': ('iface_expand_collapse',), 'Text': ()}
    if info.control_type not in patterns:
        return 'unsupported_control'
    for name in patterns[info.control_type]:
        try:
            if getattr(element, name) is None:
                return 'unsupported_pattern'
        except Exception:
            return 'unsupported_pattern'
    return None


def _record_value(kind, new_value, literal_text):
    """COM callbacks can carry VARIANT wrappers; default text stays unread."""
    if kind == 'set_text' and not literal_text:
        return ''  # Must precede even accessing a VARIANT's .value property.
    if kind not in ('set_text', 'toggle', 'expand'):
        return ''
    if type(new_value) not in (str, int):
        try:
            new_value = new_value.value
        except Exception:
            raise Denied('unsupported_state') from None
    if kind == 'set_text':
        if not isinstance(new_value, str):
            raise Denied('unsupported_state')
        return new_value
    if type(new_value) is not int or new_value not in (0, 1):
        raise Denied('unsupported_state')
    return ({0: 'off', 1: 'on'} if kind == 'toggle' else
            {0: 'collapsed', 1: 'expanded'})[new_value]


def _record_state(kind, raw, resolved):
    """Use an exact element's pattern state only when event data lacks a valid enum.

    Snapshot fallback can merge rapid transitions; a fixed diagnostic requires review.
    Explicit unsupported enum states remain unsupported, even if the current state changed.
    """
    try:
        return _record_value(kind, raw, False), False
    except Denied:
        pass
    scalar = raw
    if type(scalar) not in (int, bool, float, str, type(None)):
        try:
            scalar = scalar.value
        except Exception:
            scalar = None
    if type(scalar) in (int, float) and scalar not in (0, 1):
        raise Denied('unsupported_state')
    try:
        if kind == 'toggle':
            state = resolved.iface_toggle.CurrentToggleState
        elif kind == 'expand':
            state = resolved.iface_expand_collapse.CurrentExpandCollapseState
        else:
            raise Denied('unsupported_state')
        return _record_value(kind, state, False), True
    except Exception:
        raise Denied('unsupported_state') from None


def _record_operation(kind, raw, resolved, control_type, literal_text):
    """Canonicalize already-verified UIA notifications to replayable operations."""
    if kind in ('toggle', 'expand'):
        value, snapshot = _record_state(kind, raw, resolved)
        if kind != 'toggle' or control_type != 'RadioButton':
            return kind, value, snapshot
        if value == 'off':
            return None  # An implicit radio deselection cannot be replayed directly.
        try:
            if resolved.iface_selection_item is None:
                raise AttributeError
        except Exception:
            raise Denied('unsupported_pattern') from None
        return 'select', '', snapshot
    if kind == 'selection_state':
        value = raw
        if type(value) not in (bool, int, type(None)):
            try:
                value = value.value
            except Exception:
                raise Denied('unsupported_state') from None
        snapshot = value is None
        try:
            pattern = resolved.iface_selection_item
            if pattern is None:
                raise AttributeError
        except Exception:
            raise Denied('unsupported_pattern') from None
        if snapshot:
            try:
                value = pattern.CurrentIsSelected
            except Exception:
                raise Denied('unsupported_state') from None
        # UIA BOOL getters can marshal as exact int or bool, never general truthiness.
        if type(value) not in (bool, int) or value not in (0, 1):
            raise Denied('unsupported_state')
        return ('select', '', snapshot) if value == 1 else None
    return kind, _record_value(kind, raw, literal_text), False


def _state_metadata(kind, raw):
    types = {int: 'int', bool: 'bool', float: 'float', str: 'str', type(None): 'none'}
    scalar = raw
    try:
        if type(raw) not in types:
            scalar = raw.value
        scalar_type = types.get(type(scalar), 'wrapped')
    except Exception:
        scalar_type = 'unavailable'
    return {'kind': kind, 'payload_type': types.get(type(raw), 'wrapped'),
            'scalar_type': scalar_type}


class _ScopedStateSampler:
    """Observe only non-text pattern transitions in inventoried approved roots.

    Some native providers change state without raising UIA notifications. Sampling
    cannot preserve transitions faster than the interval; the editor warns of this.
    Foreground re-entry resets the baseline rather than recording background work.
    Every emitted transition still passes the normal live selector/policy checks.
    """
    def __init__(self, entries, validate=None):
        self.validate = validate
        self.observation_start = {}
        self.observed_at = {}
        self.entries = list(entries)[:1000]
        self.previous = {}
        self.foreground = None

    @staticmethod
    def state(item):
        try:
            info = item.element_info
            if not safe_raw_element(info.element) or not info.automation_id:
                return None
            kind = info.control_type
            if kind == 'CheckBox':
                operation, value = 'toggle', item.iface_toggle.CurrentToggleState
            elif kind == 'RadioButton':
                # Win32's SelectionItem state can lag behind its checked state.
                pattern = item.iface_selection_item
                value = pattern.CurrentIsSelected
                try:
                    checked = item.iface_toggle.CurrentToggleState
                    if type(checked) is int and checked in (0, 1):
                        value = checked
                except Exception:
                    pass
                operation = 'selection_state'
            elif kind in ('ComboBox', 'TreeItem'):
                operation, value = 'expand', item.iface_expand_collapse.CurrentExpandCollapseState
            elif kind == 'ListItem':
                operation, value = 'selection_state', item.iface_selection_item.CurrentIsSelected
            else:
                return None  # Never read text, password values, or invoke controls.
            if type(value) not in (int, bool) or value not in (0, 1):
                return None
            return operation, int(value)
        except Exception:
            return None

    def changes(self, foreground):
        changed = []
        same_window = foreground == self.foreground
        timestamp = time.perf_counter()
        self.observation_start = {}
        for index, (handle, item) in enumerate(self.entries):
            if handle != foreground:
                self.previous.pop(index, None)
                continue
            try:
                if self.validate:
                    self.validate(handle, item)
                state = self.state(item)
            except Exception:
                state = None
            old = self.previous.get(index)
            if state is not None and old is not None and same_window and state != old:
                self.observation_start[id(item)] = self.observed_at[index]
                changed.append((item, *state))
            self.observed_at[index] = timestamp
            self.previous[index] = state
        self.foreground = foreground
        return changed


def _record_events(connection, windows, policy_data, literal_text):
    """One bounded child, selected-root subscriptions, no global input hooks."""
    import threading
    sys.coinit_flags = 0
    initialized = False
    subscriptions = []
    lock = threading.RLock()
    failed = False
    active = None
    try:
        import pythoncom
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        initialized = True
        import comtypes
        import win32gui
        from pywinauto.uia_defines import IUIA
        from .security import Policy
        policy = Policy(**policy_data)
        adapter, api = WindowsUIA(policy), IUIA()
        roots = []
        for app, title in windows:
            ensure_uia_app(app)
            policy.app(app, control=True)
            policy.window(app, title, control=True)
            root = adapter._window(Selector(app, title, '__window__', 'Window'))
            roots.append((app, title, root))
        def validate(app, title, root):
            policy.app(app, control=True)
            policy.window(app, title, control=True)
            check_private(root.window_text())
            if not win32gui.IsWindow(root.handle) or root.window_text() != title:
                raise Denied('Записываемое окно изменилось')
            policy.identity(app, process_path(root.process_id()))
            check_process_access(root.process_id())
        def send_focus(app, title, root, timestamp, observation_start=None):
            nonlocal active
            if active != root.handle:
                selector = Selector(app, title, '__window__', 'Window')
                connection.send(('event', 'focus', selector.to_dict(), '', timestamp, observation_start))
                active = root.handle
        def diagnostic(reason, metadata=None):
            connection.send(('diagnostic', reason) if metadata is None else ('diagnostic', reason, metadata))
        def emit(kind, sender, new_value='', timestamp=None, observation_start=None):
            nonlocal failed
            # Preserve callback arrival time across lock contention and slow COM reads.
            if timestamp is None:
                timestamp = time.perf_counter()
            with lock:
                if failed:
                    return
                try:
                    selected = next(((a,t,r) for a,t,r in roots if _belongs_to_root(api, sender, r.element_info.element)), None)
                    if selected is None:
                        return
                    app, title, root = selected
                    validate(app, title, root)
                    if not safe_raw_element(sender):
                        diagnostic('password_or_unknown')
                        return
                    if win32gui.GetForegroundWindow() != root.handle:
                        return
                    control_type = {50000:'Button',50004:'Edit',50002:'CheckBox',50003:'ComboBox',50007:'ListItem',50013:'RadioButton',50024:'TreeItem'}.get(sender.CurrentControlType)
                    automation_id = sender.CurrentAutomationId
                    if not automation_id or control_type is None:
                        diagnostic('unsupported_control')
                        return
                    if kind in ('click', 'select', 'selection_state', 'toggle', 'expand'):
                        try:
                            deny_dangerous_name(sender.CurrentName)
                        except Denied:
                            diagnostic('dangerous_action')
                            return
                    if kind == 'click':
                        if control_type != 'Button':
                            diagnostic('unsupported_control')
                            return
                    if kind == 'set_text' and control_type != 'Edit':
                        diagnostic('unsupported_control')
                        return
                    selector = Selector(app, title, automation_id, control_type)
                    # Replay must identify precisely the same element, including duplicate IDs.
                    resolved = adapter._find(selector)
                    if not api.iuia.CompareElements(sender, resolved.element_info.element):
                        diagnostic('ambiguous_selector')
                        return
                    try:
                        operation = _record_operation(kind, new_value, resolved, control_type, literal_text)
                        if operation is None:
                            return
                        output_kind, value, snapshot = operation
                        if snapshot:
                            diagnostic('state_snapshot', _state_metadata(kind, new_value))
                    except Denied as exc:
                        reason = 'unsupported_pattern' if str(exc) == 'unsupported_pattern' else 'unsupported_state'
                        diagnostic(reason, _state_metadata(kind, new_value))
                        return
                    send_focus(app, title, root, timestamp, observation_start)
                    connection.send(('event',output_kind,selector.to_dict(),value,timestamp,observation_start))
                except Exception:
                    failed = True
                    connection.send(('error','UIA не смогла проверить событие. Запись остановлена.'))
        class EventHandler(comtypes.COMObject):
            _com_interfaces_ = [api.UIA_dll.IUIAutomationEventHandler]
            def HandleAutomationEvent(self, sender, event_id):
                timestamp = time.perf_counter()
                emit('click' if event_id == 20009 else 'select', sender, timestamp=timestamp)
                return 0
        class PropertyHandler(comtypes.COMObject):
            _com_interfaces_ = [api.UIA_dll.IUIAutomationPropertyChangedEventHandler]
            def HandlePropertyChangedEvent(self, sender, property_id, new_value):
                timestamp = time.perf_counter()
                kind = {30045:'set_text',30086:'toggle',30070:'expand',30079:'selection_state'}.get(property_id)
                if kind:
                    emit(kind,sender,new_value,timestamp=timestamp)
                return 0
        events, properties_handler = EventHandler(), PropertyHandler()
        properties = (ctypes.c_int * 4)(30045,30086,30070,30079)
        sampled_controls = []
        for app,title,root in roots:
            validate(app,title,root)
            # Inventory only selected approved trees, with fixed diagnostics and no text.
            for item in root.descendants()[:1000]:
                if item.element_info.control_type in ('CheckBox', 'RadioButton', 'ComboBox', 'TreeItem', 'ListItem'):
                    sampled_controls.append((root.handle, item))
                reason = _inventory_reason(item)
                if reason:
                    diagnostic(reason)
            element = root.element_info.element
            for event_id in (20009,20012):
                api.iuia.AddAutomationEventHandler(event_id,element,api.tree_scope['subtree'],None,events)
                subscriptions.append(('event',event_id,element))
            api.iuia.AddPropertyChangedEventHandlerNativeArray(element,api.tree_scope['subtree'],None,properties_handler,properties,4)
            subscriptions.append(('property',None,element))
        def validate_sample(handle, item):
            app, title, root = next((a,t,r) for a,t,r in roots if r.handle == handle)
            validate(app, title, root)
            raw = item.element_info.element
            if (win32gui.GetForegroundWindow() != handle or not safe_raw_element(raw)
                    or not _belongs_to_root(api, raw, root.element_info.element)):
                raise Denied('Контрол вне активного разрешённого окна')
            info = item.element_info
            resolved = adapter._find(Selector(app, title, info.automation_id, info.control_type))
            if not api.iuia.CompareElements(raw, resolved.element_info.element):
                raise Denied('Элемент изменился')
        sampler = _ScopedStateSampler(sampled_controls, validate=validate_sample)
        sampler.changes(win32gui.GetForegroundWindow())
        connection.send(('ready',))
        while not connection.poll(.05):
            timestamp = time.perf_counter()
            with lock:
                if failed:
                    break
                foreground = win32gui.GetForegroundWindow()
                for app,title,root in roots:
                    validate(app,title,root)
                    if foreground == root.handle:
                        send_focus(app,title,root,timestamp)
                if not any(root.handle == foreground for _,_,root in roots):
                    active = None
                for item, kind, value in sampler.changes(foreground):
                    diagnostic('state_snapshot')
                    emit(kind, item.element_info.element, value, timestamp=timestamp,
                         observation_start=sampler.observation_start[id(item)])
    except Exception as exc:
        try:
            connection.send(('error','Запись UIA недоступна: '+type(exc).__name__
                             + (': '+str(exc) if isinstance(exc, Denied) else '')))
        except (OSError,EOFError):
            pass
    finally:
        for kind,event_id,element in subscriptions:
            try:
                if kind == 'event':
                    api.iuia.RemoveAutomationEventHandler(event_id,element,events)
                else:
                    api.iuia.RemovePropertyChangedEventHandler(element,properties_handler)
            except Exception:
                pass
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
        self.state_diagnostics = []

    def _start(self):
        import multiprocessing
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        self.connection = parent
        policy = self.ui.bridge.policy
        self.process = context.Process(target=_record_events, args=(child, self.recorder.windows,
            policy.to_dict(), self.recorder.literal_text), daemon=True)
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
            elif event[0] == 'diagnostic':
                self.recorder.report(event[1])
                if len(event) > 2 and isinstance(event[2], dict) and len(self.state_diagnostics) < 100:
                    metadata = event[2]
                    if (metadata.get('kind') in ('toggle', 'expand', 'selection_state') and
                            metadata.get('payload_type') in ('int', 'bool', 'float', 'str', 'none', 'wrapped')):
                        self.state_diagnostics.append({'reason': event[1] if event[1] in ('state_snapshot', 'unsupported_state', 'unsupported_pattern')
                                                       else 'unsupported_state',
                                                       'kind': metadata['kind'], 'payload_type': metadata['payload_type'],
                                                       'scalar_type': metadata.get('scalar_type') if metadata.get('scalar_type') in
                                                       ('int', 'bool', 'float', 'str', 'none', 'wrapped', 'unavailable') else 'unavailable'})
            elif event[0] == 'event':
                self.recorder.capture(event[1], Selector.from_dict(event[2]), event[3], timestamp=event[4],
                                      observation_start=event[5] if len(event)>5 else None)
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
