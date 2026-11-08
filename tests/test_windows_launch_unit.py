"""Portable launch dispatch/verification and IPC outcome regressions."""
from contextlib import nullcontext
from types import SimpleNamespace
import sys

import pytest
from bender_ai import windows, isolation
from bender_ai.security import Denied, Policy


@pytest.fixture
def launch(monkeypatch):
    calls = []
    rows = [[]]
    monkeypatch.setattr(windows,'require_windows',lambda:None)
    monkeypatch.setattr(windows,'com_thread',nullcontext)
    monkeypatch.setenv('WINDIR','/Windows')
    monkeypatch.setattr(windows.subprocess,'Popen',lambda *a,**k:calls.append((a,k)))
    clock = [0.0]
    monkeypatch.setattr(windows.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(windows.time,'sleep',lambda delta:clock.__setitem__(0,clock[0]+delta))
    def enumerate_windows():
        item = rows.pop(0) if len(rows)>1 else rows[0]
        if isinstance(item,Exception): raise item
        return item
    monkeypatch.setitem(sys.modules,'pywinauto',SimpleNamespace(Desktop=lambda **kw:SimpleNamespace(windows=enumerate_windows)))
    monkeypatch.setattr(windows,'window_owned_by_app',lambda window,expected:window.owner in expected)
    return calls, rows


def test_existing_uwp_owned_window_is_reused_without_dispatch(launch):
    calls, rows = launch
    rows[:] = [[SimpleNamespace(owner='calculatorapp.exe')]]
    result = windows.WindowsDesktop().open_app('calc.exe')
    assert calls == []
    assert result['status'] == 'verified' and result['already_open'] is True


def test_single_dispatch_then_window_verifies(launch):
    calls, rows = launch
    rows[:] = [[],[SimpleNamespace(owner='notepad.exe')]]
    result = windows.WindowsDesktop().open_app('notepad.exe')
    assert len(calls) == 1
    assert calls[0][1] == {'shell':False}
    assert result['status'] == 'verified' and result['already_open'] is False


@pytest.mark.parametrize('rows_after', [[], RuntimeError('private provider message')])
def test_after_dispatch_failed_verification_is_unverified_never_retried(launch, rows_after):
    calls, rows = launch
    rows[:] = [[],rows_after]
    with pytest.raises(windows.AppLaunchUnverified) as exc:
        windows.WindowsDesktop().open_app('calc.exe')
    assert len(calls) == 1
    assert 'private' not in str(exc.value)


def test_actual_dispatch_failure_is_launch_error(launch, monkeypatch):
    calls, rows = launch
    def fail(*a,**kw):
        calls.append(1)
        raise OSError('private file path')
    monkeypatch.setattr(windows.subprocess,'Popen',fail)
    with pytest.raises(windows.AppLaunchError) as exc:
        windows.WindowsDesktop().open_app('calc.exe')
    assert len(calls) == 1 and 'private' not in str(exc.value)


def test_unknown_executable_never_dispatches(launch):
    calls,_ = launch
    with pytest.raises(Denied): windows.WindowsDesktop().open_app('powershell.exe')
    assert calls == []


def test_rpc_preserves_unverified_outcome(monkeypatch):
    from bender_ai.models import OperationUnverified
    response = []
    class Connection:
        count = 0
        def recv(self):
            self.count += 1
            if self.count > 1: raise EOFError
            return ('desktop','open_app',('calc.exe',),Policy().to_dict())
        def send(self,value): response.append(value)
    def unverified(*args): raise windows.AppLaunchUnverified('Приложение могло открыться; не повторяйте запуск.')
    monkeypatch.setattr(windows.WindowsDesktop,'open_app',unverified)
    isolation.windows_service(Connection())
    assert response[0][0] == 'unverified'
    bridge = SimpleNamespace(policy=Policy(),channel=SimpleNamespace(call=lambda *args:response[0]))
    with pytest.raises(OperationUnverified): isolation._RemoteAdapter(bridge,'desktop').open_app('calc.exe')


def test_custom_launch_without_policy_is_denied(launch):
    calls,_ = launch
    with pytest.raises(Denied): windows.WindowsDesktop().open_approved_app('tool.exe')
    assert calls == []


def test_custom_launch_uses_only_policy_path_and_no_arguments(launch, monkeypatch):
    calls,rows = launch
    path = r'C:\Approved\tool.exe'
    policy = SimpleNamespace(approved_launch=lambda app:(path,('Approved',)))
    rows[:] = [[],[SimpleNamespace(window_text=lambda:'Approved',process_id=lambda:7)]]
    monkeypatch.setattr(windows,'process_path',lambda pid:path)
    result = windows.WindowsDesktop(policy).open_approved_app('tool.exe')
    assert calls == [(([path],),{'shell':False})]
    assert result['status'] == 'verified' and result['executable'] == path


@pytest.mark.parametrize('title,actual_path', [
    ('Wrong title',r'C:\Approved\tool.exe'),
    ('Approved',r'C:\Different\tool.exe'),
    ('Approved incognito',r'C:\Approved\tool.exe'),
])
def test_custom_launch_requires_exact_nonprivate_title_and_executable(launch, monkeypatch, title, actual_path):
    calls,rows = launch
    path = r'C:\Approved\tool.exe'
    policy = SimpleNamespace(approved_launch=lambda app:(path,('Approved','Approved incognito')))
    rows[:] = [[],[SimpleNamespace(window_text=lambda:title,process_id=lambda:7)]]
    monkeypatch.setattr(windows,'process_path',lambda pid:actual_path)
    with pytest.raises(windows.AppLaunchUnverified):
        windows.WindowsDesktop(policy).open_approved_app('tool.exe')
    assert len(calls) == 1


def test_custom_existing_approved_window_is_reused(launch, monkeypatch):
    calls,rows = launch
    path = r'C:\Approved\tool.exe'
    policy = SimpleNamespace(approved_launch=lambda app:(path,('Approved',)))
    rows[:] = [[SimpleNamespace(window_text=lambda:'Approved',process_id=lambda:7)]]
    monkeypatch.setattr(windows,'process_path',lambda pid:path)
    assert windows.WindowsDesktop(policy).open_approved_app('tool.exe')['already_open'] is True
    assert calls == []


def test_custom_denied_policy_never_dispatches(launch):
    calls,_ = launch
    def deny(app): raise Denied('Нет разрешения запуска')
    with pytest.raises(Denied): windows.WindowsDesktop(SimpleNamespace(approved_launch=deny)).open_approved_app('tool.exe')
    assert calls == []


def test_rpc_custom_launch_receives_reconstructed_policy(monkeypatch):
    approved_policy = object()
    seen, responses = [], []
    monkeypatch.setattr(isolation,'Policy',lambda **data:approved_policy)
    def launch(self, app_id):
        seen.append((self.policy,app_id))
        return {'status':'verified'}
    monkeypatch.setattr(windows.WindowsDesktop,'open_approved_app',launch)
    class Connection:
        count = 0
        def recv(self):
            self.count += 1
            if self.count > 1: raise EOFError
            return ('desktop','open_approved_app',('tool.exe',),{})
        def send(self,value): responses.append(value)
    isolation.windows_service(Connection())
    assert seen == [(approved_policy,'tool.exe')]
    assert responses == [('ok',{'status':'verified'})]
