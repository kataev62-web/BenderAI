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
    ui = windows.WindowsUIA(SimpleNamespace(app=lambda *a,**k:None, window=lambda *a,**k:None))
    monkeypatch.setattr(windows, 'com_thread', __import__('contextlib').nullcontext)
    monkeypatch.setattr(ui, '_window', lambda selector: (_ for _ in ()).throw(Denied('policy')))
    with pytest.raises(Denied): ui.window_exists(Selector('a.exe','A','root','Window'))

def test_toggle_and_expand_are_bounded_and_verify(monkeypatch):
    monkeypatch.setattr(windows, 'com_thread', __import__('contextlib').nullcontext)
    class Toggle:
        CurrentToggleState = 0
        def Toggle(self): self.CurrentToggleState = 1
    toggle = Toggle()
    element = SimpleNamespace(element_info=SimpleNamespace(name='Safe'), is_enabled=lambda:True, iface_toggle=toggle)
    ui = windows.WindowsUIA(SimpleNamespace(app=lambda *a,**k:None, window=lambda *a,**k:None))
    monkeypatch.setattr(ui,'_find',lambda s:element)
    ui.toggle(Selector('a.exe','A','check','CheckBox'), 'on')
    assert toggle.CurrentToggleState == 1
    with pytest.raises(Denied): ui.toggle(Selector('a.exe','A','check','CheckBox'),'invalid')

def test_expand_is_single_verified_operation(monkeypatch):
    monkeypatch.setattr(windows, 'com_thread', __import__('contextlib').nullcontext)
    class Pattern:
        CurrentExpandCollapseState = 0
        def Expand(self): self.CurrentExpandCollapseState = 1
    pattern = Pattern()
    element = SimpleNamespace(element_info=SimpleNamespace(name='Safe'), is_enabled=lambda:True, iface_expand_collapse=pattern)
    ui = windows.WindowsUIA(SimpleNamespace(app=lambda *a,**k:None, window=lambda *a,**k:None))
    monkeypatch.setattr(ui,'_find',lambda s:element)
    ui.expand(Selector('a.exe','A','tree','TreeItem'),'expanded')
    assert pattern.CurrentExpandCollapseState == 1

def test_toggle_cannot_loop_forever(monkeypatch):
    monkeypatch.setattr(windows, 'com_thread', __import__('contextlib').nullcontext)
    class Pattern:
        CurrentToggleState = 0
        count = 0
        def Toggle(self): self.count += 1
    pattern = Pattern()
    element = SimpleNamespace(element_info=SimpleNamespace(name='Safe'), is_enabled=lambda:True, iface_toggle=pattern)
    ui = windows.WindowsUIA(SimpleNamespace(app=lambda *a,**k:None, window=lambda *a,**k:None))
    monkeypatch.setattr(ui,'_find',lambda s:element)
    with pytest.raises(RuntimeError): ui.toggle(Selector('a.exe','A','check','CheckBox'),'on')
    assert pattern.count == 3

def test_dangerous_noninvoke_actions_are_denied():
    with pytest.raises(Denied):
        windows.deny_dangerous_name('Send payment')

def test_elevated_window_is_excluded_before_tree_access(monkeypatch):
    from types import ModuleType
    import sys
    desktop = ModuleType('pywinauto')
    root = SimpleNamespace(process_id=lambda:7,window_text=lambda:'A')
    desktop.Desktop = lambda **kwargs: SimpleNamespace(windows=lambda **kwargs:[root])
    monkeypatch.setitem(sys.modules,'pywinauto',desktop)
    monkeypatch.setattr(windows,'process_name',lambda pid:'a.exe')
    monkeypatch.setattr(windows,'process_path',lambda pid:r'C:\a.exe')
    monkeypatch.setattr(windows,'check_process_access',lambda pid:(_ for _ in ()).throw(Denied('elevated')))
    policy = SimpleNamespace(app=lambda *a,**k:None,window=lambda *a,**k:None,identity=lambda *a,**k:None)
    with pytest.raises(Denied): windows.WindowsUIA(policy)._window(Selector('a.exe','A','root','Window'))

def test_default_event_payload_never_coerces_text_value():
    class Secret:
        def __str__(self): raise AssertionError('value must not be inspected')
    assert windows._record_value('set_text', Secret(), False) == ''
    assert windows._record_value('set_text', 'Synthetic', True) == 'Synthetic'
    assert windows._record_value('toggle', 1, False) == 'on'
    with pytest.raises(Denied): windows._record_value('toggle', 2, False)

def test_inventory_reports_nominal_controls_missing_patterns_without_text():
    element = SimpleNamespace(element_info=SimpleNamespace(control_type='Button',automation_id='b',element=Raw()))
    assert windows._inventory_reason(element) == 'unsupported_pattern'
    element.iface_invoke = object()
    assert windows._inventory_reason(element) is None

@pytest.mark.parametrize('callback', [True, False])
def test_capture_timestamp_precedes_slow_provider_and_pause_resume(monkeypatch, callback):
    """Exercise real child handler wiring; a stalled callback retains arrival time."""
    import sys
    from bender_ai.learning import Recorder
    from bender_ai.security import Policy
    clock = [0.0]
    running = [False]
    handlers = {}
    root_raw = Raw()
    sender = Raw(root_raw)
    def title():
        if running[0]: clock[0] = 30.0  # COM resumes after pause ended at 20.
        return 'A'
    root = SimpleNamespace(handle=42,window_text=title,process_id=lambda:7,
                           descendants=lambda:[],element_info=SimpleNamespace(element=root_raw))
    class NativeAPI(API):
        def __init__(self):
            super().__init__()
            self.UIA_dll = SimpleNamespace(IUIAutomationEventHandler=object,
                                          IUIAutomationPropertyChangedEventHandler=object)
            self.tree_scope = {'subtree':7}
            self.iuia.AddAutomationEventHandler = lambda *args:None
            self.iuia.AddPropertyChangedEventHandlerNativeArray = lambda *args:handlers.update(value=args[3])
            self.iuia.RemoveAutomationEventHandler = lambda *args:None
            self.iuia.RemovePropertyChangedEventHandler = lambda *args:None
    messages = []
    class Connection:
        polled = False
        def send(self, event):
            messages.append(event)
            if event[0] == 'ready':
                running[0] = True
                clock[0] = 15.0
                if callback:
                    handlers['value'].HandlePropertyChangedEvent(sender,30045,'synthetic')
        def poll(self, timeout):
            if callback or self.polled: return True
            self.polled = True
            return False
        def close(self): pass
    monkeypatch.setitem(sys.modules,'pythoncom',SimpleNamespace(COINIT_MULTITHREADED=0,
                        CoInitializeEx=lambda *args:None,CoUninitialize=lambda:None))
    monkeypatch.setitem(sys.modules,'comtypes',SimpleNamespace(COMObject=object))
    monkeypatch.setitem(sys.modules,'win32gui',SimpleNamespace(IsWindow=lambda hwnd:True,
                                                            GetForegroundWindow=lambda:42))
    monkeypatch.setitem(sys.modules,'pywinauto',SimpleNamespace())
    monkeypatch.setitem(sys.modules,'pywinauto.uia_defines',SimpleNamespace(IUIA=NativeAPI))
    monkeypatch.setattr(windows.WindowsUIA,'_window',lambda *args:root)
    monkeypatch.setattr(windows.WindowsUIA,'_find',lambda *args:SimpleNamespace(element_info=SimpleNamespace(element=sender)))
    monkeypatch.setattr(windows,'process_path',lambda pid:r'C:\a.exe')
    monkeypatch.setattr(windows,'check_process_access',lambda pid:None)
    monkeypatch.setattr(windows.time,'perf_counter',lambda:clock[0])
    policy = Policy()
    policy.grant_app('a.exe','A',executable=r'C:\a.exe')
    recorder = Recorder(windows=[('a.exe','A')])
    recorder.start()
    recorder.pause_intervals = [(10.0,20.0)]
    windows._record_events(Connection(),(('a.exe','A'),),policy.to_dict(),False)
    events = [event for event in messages if event[0] == 'event']
    assert events, messages
    assert all(event[4] == 15.0 for event in events)
    for _,kind,selector,value,timestamp in events:
        recorder.capture(kind,Selector.from_dict(selector),value,timestamp=timestamp)
    assert recorder.steps == []


@pytest.mark.parametrize('unknown', [None, '', 0.5])
def test_non_boolean_password_status_is_unknown_and_rejected(unknown):
    raw = SimpleNamespace(CurrentIsPassword=unknown)
    assert not windows.safe_raw_element(raw)
    assert not windows.safe_element(SimpleNamespace(element_info=SimpleNamespace(element=raw)))
