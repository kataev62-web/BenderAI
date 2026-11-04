from dataclasses import dataclass, field, asdict
from pathlib import Path
import sys
import ntpath
import re


class Denied(RuntimeError):
    pass


def check_private_title(title):
    if any(token in title.casefold() for token in ('inprivate', 'incognito', 'инкогнито', 'приватн', 'private browsing')):
        raise Denied('Приватное окно исключено')


def ensure_uia_app(app):
    if not isinstance(app, str) or len(app) > 512 or not re.fullmatch(r'[^\\/:*?"<>|\x00]+\.exe', app, re.IGNORECASE):
        raise Denied('Нужно точное имя процесса .exe')
    # Browser privacy/control semantics and command consoles need dedicated reviewed adapters.
    if app.casefold() in {'chrome.exe','msedge.exe','firefox.exe','brave.exe','opera.exe',
            'iexplore.exe','cmd.exe','powershell.exe','pwsh.exe','windowsterminal.exe',
            'regedit.exe','credentialuibroker.exe','consent.exe'}:
        raise Denied('Для браузера, консоли или защищённого приложения нужен отдельный адаптер')


def executable_key(path):
    return ntpath.normcase(ntpath.normpath(str(path)))


@dataclass
class Policy:
    read_roots: list[str] = field(default_factory=list)
    write_roots: list[str] = field(default_factory=list)
    ui_apps: list[str] = field(default_factory=list)
    read_apps: list[str] = field(default_factory=list)
    gradle_projects: dict[str, dict] = field(default_factory=dict)
    window_scopes: dict[str, list[str]] = field(default_factory=dict)
    app_paths: dict[str, str] = field(default_factory=dict)
    ui_window_scopes: dict[str, list[str]] = field(default_factory=dict)
    network: bool = False
    screenshot: bool = False
    observer: bool = False

    def path(self, raw, mode):
        path = Path(raw).expanduser().resolve()
        roots = self.write_roots if mode == 'write' else self.read_roots
        if not any(path.is_relative_to(Path(root).expanduser().resolve()) for root in roots):
            raise Denied('Каталог не разрешён для ' + mode)
        if self.observer and mode == 'write':
            raise Denied('Активен режим наблюдателя')
        return path

    def app(self, name, control=False):
        allowed = self.ui_apps if control else self.read_apps
        if name.casefold() not in {s.casefold() for s in allowed}:
            raise Denied('Нет разрешения на приложение: ' + name)
        if self.observer and control:
            raise Denied('Активен режим наблюдателя')

    def window(self, app, title, control=False):
        check_private_title(title)
        scopes = self.window_scopes.get(app.casefold())
        if scopes is not None and title not in scopes:
            raise Denied('Это окно не разрешено')
        if control:
            scopes = self.ui_window_scopes.get(app.casefold())
            if scopes is not None and title not in scopes:
                raise Denied('Нет разрешения управления этим окном')

    def identity(self, app, executable):
        expected = self.app_paths.get(app.casefold())
        if expected and executable_key(expected) != executable_key(executable):
            raise Denied('Исполняемый файл не совпадает с разрешённым приложением')

    def grant_app(self, app, title, executable='', control=True):
        ensure_uia_app(app)
        if not isinstance(title, str) or not title.strip() or len(title)>512 or '\x00' in title:
            raise Denied('Нужен точный непустой заголовок окна')
        check_private_title(title)
        app = app.casefold()
        if executable:
            if not ntpath.isabs(executable) or ntpath.basename(executable).casefold() != app:
                raise Denied('Нужен абсолютный путь к выбранному .exe')
            self.identity(app, executable)
        # First explicit grant narrows any legacy process-wide scopes to selected windows.
        scopes = self.window_scopes.setdefault(app, [])
        if title not in scopes:
            scopes.append(title)
        control_scopes = self.ui_window_scopes.setdefault(app, [])
        if app not in self.read_apps:
            self.read_apps.append(app)
        if control:
            if app not in self.ui_apps:
                self.ui_apps.append(app)
            if title not in control_scopes:
                control_scopes.append(title)
        if executable:
            self.app_paths[app] = executable

    def revoke_app(self, app):
        app = app.casefold()
        self.read_apps[:] = [x for x in self.read_apps if x.casefold()!=app]
        self.ui_apps[:] = [x for x in self.ui_apps if x.casefold()!=app]
        self.window_scopes.pop(app, None)
        self.ui_window_scopes.pop(app, None)
        self.app_paths.pop(app, None)

    def to_dict(self):
        return asdict(self)


class SecretVault:
    """Explicit Windows Credential Manager backend, never a plaintext fallback."""
    def _backend(self):
        if sys.platform != 'win32':
            raise Denied('Хранилище ключей доступно только в Windows')
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()

    def save(self, key):
        if not key or len(key) > 4096:
            raise Denied('Некорректный ключ')
        self._backend().set_password('BenderAI', 'cloud', key)

    def load(self):
        key = self._backend().get_password('BenderAI', 'cloud')
        if not key:
            raise Denied('Добавьте ключ в настройках Windows Credential Manager')
        return key
