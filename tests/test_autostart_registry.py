"""Registry behavior with fake per-user keys; never alter the test user's startup."""
import sys
from types import SimpleNamespace
import pytest
from bender_ai import infrastructure


def registry(monkeypatch, **methods):
    monkeypatch.setattr(infrastructure.sys,'platform','win32')
    monkeypatch.setitem(sys.modules,'winreg',SimpleNamespace(HKEY_CURRENT_USER=1,KEY_SET_VALUE=2,REG_SZ=1,**methods))


def test_disabling_autostart_when_run_key_is_absent_is_a_noop(monkeypatch):
    def absent(*a):raise FileNotFoundError()
    registry(monkeypatch,OpenKey=absent)
    infrastructure.autostart(False)


def test_enabling_autostart_creates_only_the_current_user_key(monkeypatch):
    calls=[]
    class Key:
        def __enter__(self):return self
        def __exit__(self,*a):pass
    registry(monkeypatch,CreateKeyEx=lambda *args:(calls.append(('create',args)) or Key()),
             SetValueEx=lambda *args:calls.append(('set',args)))
    infrastructure.autostart(True)
    assert calls[0][0]=='create' and calls[0][1][0]==1
    assert calls[1][1][1]=='BenderAI'


def test_disabling_does_not_hide_registry_access_denial(monkeypatch):
    def deny(*a):raise PermissionError()
    registry(monkeypatch,OpenKey=deny)
    with pytest.raises(PermissionError):infrastructure.autostart(False)
