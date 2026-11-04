from types import SimpleNamespace
import pytest
from bender_ai import windows
from bender_ai.models import Selector
from bender_ai.security import Denied

class Raw:
    CurrentIsPassword = False
    CurrentProcessId = 7
    CurrentControlType = 50004
    CurrentAutomationId = 'edit'
    def __init__(self, parent=None): self.parent = parent

class Walker:
    def GetParentElement(self, element): return element.parent

class API:
    def __init__(self): self.iuia = SimpleNamespace(ControlViewWalker=Walker(), CompareElements=lambda a,b:a is b)

def test_event_requires_exact_selected_root_not_just_process():
    root, foreign = Raw(), Raw()
    assert windows._belongs_to_root(API(), Raw(root), root)
    assert not windows._belongs_to_root(API(), Raw(foreign), root)

def test_unknown_password_state_is_denied_before_values():
    class Unknown:
        @property
        def CurrentIsPassword(self): raise RuntimeError('unknown')
    assert not windows.safe_raw_element(Unknown())

def test_window_exists_does_not_swallow_permission_denial(monkeypatch):
    ui = windows.WindowsUIA(SimpleNamespace())
    monkeypatch.setattr(windows, 'com_thread', __import__('contextlib').nullcontext)
    monkeypatch.setattr(ui, '_window', lambda selector: (_ for _ in ()).throw(Denied('policy')))
    with pytest.raises(Denied): ui.window_exists(Selector('a.exe','A','root','Window'))

def test_toggle_and_expand_are_bounded_and_verify(monkeypatch):
    monkeypatch.setattr(windows, 'com_thread', __import__('contextlib').nullcontext)
    class Toggle:
        CurrentToggleState = 0
        def Toggle(self): self.CurrentToggleState = 1
    toggle = Toggle()
    element = SimpleNamespace(is_enabled=lambda:True, iface_toggle=toggle)
    ui = windows.WindowsUIA(SimpleNamespace(app=lambda *a,**k:None))
    monkeypatch.setattr(ui,'_find',lambda s:element)
    ui.toggle(Selector('a.exe','A','check','CheckBox'), 'on')
    assert toggle.CurrentToggleState == 1
    with pytest.raises(Denied): ui.toggle(Selector('a.exe','A','check','CheckBox'),'invalid')
