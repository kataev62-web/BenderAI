"""Selected Windows Explorer Shell adapter; no input capture or arbitrary Shell verbs."""
from dataclasses import dataclass
from pathlib import Path
import time
from .models import Selector, Step, OperationUnverified, ValidationError
from .security import Denied, check_private_title, executable_key


class ExplorerPermissionRequired(Denied):
    def __init__(self, mode, path):
        if mode not in ('read', 'write'):
            raise ValueError('Invalid permission mode')
        self.mode, self.path = mode, str(Path(path).expanduser().resolve())
        super().__init__('Для выбранной папки нужно отдельное разрешение: ' + mode)


def _native_windows():
    from .windows import require_windows
    require_windows()
    import win32gui
    result = []
    win32gui.EnumWindows(lambda hwnd, _: result.append(hwnd) if win32gui.IsWindowVisible(hwnd) else None, None)
    return result


def _native_metadata(hwnd):
    from .windows import require_windows, process_path, trusted_app_process, check_process_access
    require_windows()
    import win32gui
    import win32process
    if not isinstance(hwnd, int) or not hwnd or not win32gui.IsWindow(hwnd):
        raise Denied('Выбранное окно Проводника недоступно')
    title = win32gui.GetWindowText(hwnd)
    check_private_title(title)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    if win32gui.GetClassName(hwnd) not in ('CabinetWClass', 'ExploreWClass') or not trusted_app_process(pid, ('explorer.exe',)):
        raise Denied('Нужно настоящее окно папки Проводника')
    check_process_access(pid)
    return {'pid': pid, 'executable': process_path(pid), 'title': title,
            'class_name': win32gui.GetClassName(hwnd), 'active': win32gui.GetForegroundWindow() == hwnd}


@dataclass
class _Binding:
    selector: Selector
    hwnd: int
    pid: int
    executable: str
    browser: object


class WindowsExplorer:
    """Transient binding to one exact approved ShellBrowser, never persisted HWNDs."""
    def __init__(self, policy, *, shell_windows=None):
        self.policy = policy
        self._bindings = {}
        self._shell_source = shell_windows
        self._com_initialized = False
        self._shell = None

    def _shell_windows(self):
        if self._shell_source is not None:
            return self._shell_source()
        if not self._com_initialized:
            import pythoncom
            pythoncom.CoInitialize()
            self._com_initialized = True
        if self._shell is None:
            import win32com.client
            self._shell = win32com.client.Dispatch('Shell.Application')
        return self._shell.Windows()

    @staticmethod
    def _selector(value):
        selector = Selector.from_dict(value) if isinstance(value, dict) else value
        if not isinstance(selector, Selector) or selector.app.casefold() != 'explorer.exe' or selector.control_type != 'Window':
            raise Denied('Нужен точный селектор окна Проводника')
        return selector

    def _check(self, binding, control=False):
        selector = binding.selector
        self.policy.app('explorer.exe', control=control)
        self.policy.window('explorer.exe', selector.window, control=control)
        info = _native_metadata(binding.hwnd)
        check_private_title(info['title'])
        self.policy.identity('explorer.exe', info['executable'])
        if info['pid'] != binding.pid or executable_key(info['executable']) != executable_key(binding.executable):
            raise Denied('Выбранное окно Проводника изменилось')
        if int(binding.browser.HWND) != binding.hwnd:
            raise Denied('Выбранное Shell-окно изменилось')
        return info

    def _binding(self, value, control=False):
        selector = self._selector(value)
        key = (selector.app.casefold(), selector.window)
        if key not in self._bindings:
            self.policy.app('explorer.exe', control=control)
            self.policy.window('explorer.exe', selector.window, control=control)
            matches = []
            for hwnd in _native_windows():
                try:
                    info = _native_metadata(hwnd)
                    if info['title'] == selector.window:
                        self.policy.identity('explorer.exe', info['executable'])
                        matches.append((hwnd, info))
                except Denied:
                    continue
            if len(matches) != 1:
                raise Denied('Окно Проводника отсутствует или неоднозначно')
            hwnd, info = matches[0]
            browsers = [browser for browser in self._shell_windows() if int(browser.HWND) == hwnd]
            if len(browsers) != 1:
                raise Denied('Shell не подтвердил выбранное окно Проводника')
            self._bindings[key] = _Binding(selector, hwnd, info['pid'], info['executable'], browsers[0])
        binding = self._bindings[key]
        self._check(binding, control)
        return binding

    def _path(self, raw, mode):
        if mode == 'write' and self.policy.observer:
            raise Denied('Режим наблюдателя запрещает создание папок')
        try:
            return self.policy.path(raw, mode)
        except Denied:
            # This is a request for explicit user review, never an automatic grant.
            raise ExplorerPermissionRequired(mode, raw) from None

    def _current_path(self, binding):
        item = binding.browser.Document.Folder.Self
        if not item.IsFileSystem:
            raise Denied('Виртуальные папки Shell не поддерживаются')
        raw = item.Path
        if not isinstance(raw, str) or not raw or '\x00' in raw:
            raise Denied('Shell не передал путь выбранной папки')
        return self._path(raw, 'read')

    def _state(self, binding):
        observation_started = time.perf_counter()
        info = self._check(binding)
        path = self._current_path(binding)
        if not path.is_dir():
            raise Denied('Выбранная папка недоступна')
        folders = []
        for index, child in enumerate(path.iterdir()):
            if index >= 1000:
                break
            try:
                approved = self.policy.path(str(child), 'read')
                if approved.is_dir() and approved.parent == path:
                    folders.append(child.name)
            except (Denied, OSError):
                continue
        final_info = self._check(binding)
        return {'hwnd': binding.hwnd, 'title': final_info['title'], 'path': str(path),
                'active': info.get('active') is True and final_info.get('active') is True,
                'folders': sorted(folders), 'observation_started': observation_started,
                'timestamp': time.perf_counter()}

    def bind(self, selector):
        return self._state(self._binding(selector))

    snapshot = bind

    def navigate(self, selector, path):
        binding = self._binding(selector, control=True)
        target = self._path(path, 'read')
        if not target.is_dir():
            raise Denied('Нужна существующая разрешённая папка')
        try:
            binding.browser.Navigate2(str(target))
        except Exception:
            raise OperationUnverified('Shell не подтвердил переход; окно могло измениться, автоматический повтор не выполнен') from None
        end = time.monotonic() + 8
        while time.monotonic() < end:
            self._check(binding, control=True)
            try:
                if self._current_path(binding) == target:
                    return self._state(binding)
            except ExplorerPermissionRequired:
                pass  # The asynchronous navigation can briefly expose the previous folder.
            except Denied:
                raise
            except Exception:
                pass  # Shell.Document can be unavailable while Navigate2 is in flight.
            time.sleep(.05)
        raise OperationUnverified('Переход в Проводнике отправлен, но путь не подтверждён; повтор не выполнен')

    def create_subfolder(self, selector, name, confirmed=False):
        if confirmed is not True:
            raise Denied('Создание подпапки требует отдельного подтверждения')
        # Reuse the closed create_folder name schema, including Windows device names.
        Step('create_folder', {'parent': '.', 'name': name})
        binding = self._binding(selector, control=True)
        parent = self._current_path(binding)
        self._path(str(parent), 'write')
        target = self._path(str(parent / name), 'write')
        if target.parent != parent:
            raise Denied('Подпапка должна находиться непосредственно в выбранной папке')
        target.mkdir(exist_ok=False)
        if not target.is_dir():
            raise OperationUnverified('Создание подпапки не подтверждено; повтор не выполнен')
        return {**self._state(binding), 'created': str(target)}

    def close(self):
        self._bindings.clear()
        self._shell = None
        if self._com_initialized:
            import pythoncom
            pythoncom.CoUninitialize()
            self._com_initialized = False


def _record_semantic(recorder, selector, step, timestamp, observation_start=None):
    with recorder.lock:
        if not recorder.active or recorder.paused or timestamp < recorder.started_at:
            return False
        if recorder.pause_started is not None and timestamp >= recorder.pause_started:
            return False
        if any(start <= timestamp < end for start, end in recorder.pause_intervals):
            return False
        if observation_start is not None and (observation_start < recorder.started_at or
                any(start <= timestamp and end > observation_start for start, end in recorder.pause_intervals)):
            recorder.report('explorer_review')
            return False
        if (selector.app.casefold(), selector.window) not in recorder.windows:
            recorder.report('unselected_window')
            return False
        if len(recorder.steps) >= 100:
            recorder.pause()
            raise ValidationError('Лимит записи: 100 шагов')
        recorder.steps.append(step)
        recorder.current_window = (selector.app.casefold(), selector.window)
        return True


class ExplorerCapture:
    """Selected Shell-folder transitions, plus explicitly confirmed assisted creation.

    External folder appearances are proposals only: filesystem polling cannot prove
    which process/user created them. No keyboard, mouse or filesystem-wide hooks.
    """
    def __init__(self, explorer, recorder, selector):
        self.explorer, self.recorder = explorer, recorder
        self.selector = WindowsExplorer._selector(selector)
        self.ready = False
        self.last = None
        self.pending_creations = []
        self._permission_target = None
        self.started = time.monotonic()

    def poll(self):
        if time.monotonic() - self.started > 300:
            raise TimeoutError('Лимит записи: 5 минут')
        try:
            current = self.explorer.snapshot(self.selector.to_dict())
        except ExplorerPermissionRequired as exc:
            if exc.mode == 'read':
                self._permission_target = exc.path
            raise
        if self.last is not None and current['hwnd'] != self.last['hwnd']:
            raise Denied('Выбранное окно Проводника изменилось')
        if self._permission_target is not None:
            # A successful snapshot proves the GUI has granted the required root.
            # Record only a fresh observation of the exact folder the user approved,
            # not a transition inferred across the permission dialog's pause.
            if Path(current['path']).resolve() != Path(self._permission_target).resolve():
                self._permission_target = None
                self.recorder.report('explorer_review')
            elif not self.recorder.paused and self.recorder.active:
                accepted = _record_semantic(self.recorder, self.selector, Step('explorer_navigate', {
                    'selector': self.selector.to_dict(), 'path': current['path']}),
                    current['timestamp'], current['observation_started'])
                if accepted:
                    self._permission_target = None
            self.last, self.ready = current, True
            return
        if not current.get('active') or (self.last is not None and not self.last.get('active')):
            self.last, self.ready = current, True
            return
        if self.last is not None:
            if current['hwnd'] != self.last['hwnd']:
                raise Denied('Выбранное окно Проводника изменилось')
            if current['path'] != self.last['path']:
                _record_semantic(self.recorder, self.selector, Step('explorer_navigate', {
                    'selector': self.selector.to_dict(), 'path': current['path']}),
                    current['timestamp'], self.last['observation_started'])
            elif self.recorder.active and not self.recorder.paused:
                lower = self.last['observation_started']
                if (lower < self.recorder.started_at or any(start <= current['timestamp'] and end > lower
                        for start,end in self.recorder.pause_intervals)):
                    self.last, self.ready = current, True
                    self.recorder.report('explorer_review')
                    return
                for name in sorted(set(current['folders']) - set(self.last['folders'])):
                    proposal = {'parent': current['path'], 'name': name}
                    if proposal not in self.pending_creations and len(self.pending_creations) < 100:
                        self.pending_creations.append(proposal)
                        self.recorder.report('explorer_review')
        self.last, self.ready = current, True

    def _active(self):
        if not self.recorder.active or self.recorder.paused:
            raise Denied('Для учебного действия нужна активная запись')

    def navigate(self, path):
        self._active()
        start = time.perf_counter()
        current = self.explorer.navigate(self.selector.to_dict(), path)
        _record_semantic(self.recorder, self.selector, Step('explorer_navigate', {
            'selector': self.selector.to_dict(), 'path': current['path']}), current['timestamp'], start)
        self.last, self.ready = current, True
        return current

    def create_subfolder(self, name, confirmed=False):
        self._active()
        start = time.perf_counter()
        current = self.explorer.create_subfolder(self.selector.to_dict(), name, confirmed)
        target = Path(current['created'])
        _record_semantic(self.recorder, self.selector, Step('create_folder', {
            'parent': str(target.parent), 'name': target.name}), current['timestamp'], start)
        self.last, self.ready = current, True
        return current

    def approve_creation(self, index, confirmed=False):
        self._active()
        if confirmed is not True:
            raise Denied('Добавление наблюдаемого создания папки требует подтверждения')
        proposal = self.pending_creations[index]
        accepted = _record_semantic(self.recorder, self.selector, Step('create_folder', proposal), time.perf_counter())
        if accepted:
            self.pending_creations.pop(index)
        return accepted

    def close(self):
        self.ready = False


class ScopedRecorder:
    """Limit native subscriptions, while the central recorder retains every scope."""
    def __init__(self, recorder, windows):
        self.recorder, self.windows = recorder, tuple(windows)
        if not self.windows or any(pair not in recorder.windows for pair in self.windows):
            raise ValidationError('Неверные области записи')
        self.app, self.window = self.windows[0]

    def __getattr__(self, name):
        return getattr(self.recorder, name)


class CompositeCapture:
    def __init__(self, captures, recorder=None):
        self.captures = tuple(captures)
        if not self.captures:
            raise ValidationError('Нет выбранных источников записи')
        self.recorder = recorder or self.captures[0].recorder

    @property
    def ready(self):
        return all(capture.ready for capture in self.captures)

    def poll(self):
        for capture in self.captures:
            capture.poll()

    def close(self):
        for capture in self.captures:
            try:
                capture.close()
            except Exception:
                pass
