from dataclasses import dataclass, field, asdict
from pathlib import Path
import sys


class Denied(RuntimeError):
    pass


@dataclass
class Policy:
    read_roots: list[str] = field(default_factory=list)
    write_roots: list[str] = field(default_factory=list)
    ui_apps: list[str] = field(default_factory=list)
    read_apps: list[str] = field(default_factory=list)
    gradle_projects: dict[str, dict] = field(default_factory=dict)
    window_scopes: dict[str, list[str]] = field(default_factory=dict)
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

    def window(self, app, title):
        scopes = self.window_scopes.get(app.casefold())
        if scopes is not None and title not in scopes:
            raise Denied('Это окно не разрешено')

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
