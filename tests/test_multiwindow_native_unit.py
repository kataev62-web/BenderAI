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

@pytest.mark.parametrize('callback,event_property,state_payload,control_type,selection_pattern', [
    (True,30045,1,50004,True),(False,30045,1,50004,True),
    (True,30086,1,50002,True),(True,30070,1,50024,True),
    (True,30086,None,50002,True),(True,30086,True,50002,True),
    (True,30086,1,50013,True),(True,30086,0,50013,True),
    (True,30086,1,50013,False),(True,30079,True,50013,True),
    (True,30079,False,50013,True),(True,30079,None,50013,True)])
def test_capture_timestamp_precedes_slow_provider_and_pause_resume(monkeypatch, callback, event_property,
                                                                  state_payload, control_type, selection_pattern):
    """Exercise real child handler wiring; a stalled callback retains arrival time."""
    import sys
    from bender_ai.learning import Recorder
    from bender_ai.security import Policy
    clock = [0.0]
    running = [False]
    handlers = {}
    root_raw = Raw()
    sender = Raw(root_raw)
    sender.CurrentControlType = control_type
    sender.CurrentName = 'Safe'
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
                    handlers['value'].HandlePropertyChangedEvent(sender,event_property,
                        'synthetic' if event_property == 30045 else SimpleNamespace(value=state_payload))
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
    resolved = SimpleNamespace(element_info=SimpleNamespace(element=sender),
        iface_toggle=SimpleNamespace(CurrentToggleState=1),iface_expand_collapse=SimpleNamespace(CurrentExpandCollapseState=1))
    if selection_pattern:
        resolved.iface_selection_item = SimpleNamespace(CurrentIsSelected=True)
    monkeypatch.setattr(windows.WindowsUIA,'_find',lambda *args:resolved)
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
    ignored = callback and ((event_property == 30086 and control_type == 50013 and state_payload == 0) or
                            (event_property == 30079 and state_payload is False) or not selection_pattern)
    if ignored:
        assert events == [], messages
        if not selection_pattern:
            assert any(message[:2] == ('diagnostic','unsupported_pattern') for message in messages), messages
    else:
        assert events, messages
        if callback:
            output = ('select' if control_type == 50013 else
                      {30045:'set_text',30086:'toggle',30070:'expand'}[event_property])
            assert any(event[1] == output for event in events), messages
    assert all(event[4] == 15.0 for event in events)
    for _,kind,selector,value,timestamp in events:
        recorder.capture(kind,Selector.from_dict(selector),value,timestamp=timestamp)
    assert recorder.steps == []


@pytest.mark.parametrize('unknown', [None, '', 0.5])
def test_non_boolean_password_status_is_unknown_and_rejected(unknown):
    raw = SimpleNamespace(CurrentIsPassword=unknown)
    assert not windows.safe_raw_element(raw)
    assert not windows.safe_element(SimpleNamespace(element_info=SimpleNamespace(element=raw)))


def test_active_empty_permission_policy_denies_before_os_metadata(monkeypatch):
    import sys
    from bender_ai.security import Policy
    monkeypatch.setattr(windows,'require_windows',lambda:None)
    def forbidden(): raise AssertionError('metadata must not be queried')
    monkeypatch.setitem(sys.modules,'win32gui',SimpleNamespace(GetForegroundWindow=forbidden))
    monkeypatch.setitem(sys.modules,'win32process',SimpleNamespace())
    with pytest.raises(Denied): windows.WindowsDesktop().active(Policy())


def test_active_missing_or_stale_foreground_is_safe_denial(monkeypatch):
    import sys
    from bender_ai.security import Policy
    monkeypatch.setattr(windows,'require_windows',lambda:None)
    monkeypatch.setitem(sys.modules,'win32gui',SimpleNamespace(GetForegroundWindow=lambda:0,IsWindow=lambda hwnd:False))
    def stale(hwnd): raise OSError('private native error')
    monkeypatch.setitem(sys.modules,'win32process',SimpleNamespace(GetWindowThreadProcessId=stale))
    with pytest.raises(Denied) as exc:
        windows.WindowsDesktop().active(Policy(read_apps=['a.exe']))
    assert 'private' not in str(exc.value)


@pytest.mark.parametrize('needs_attachment,refuses,attachment_error', [
    (False,False,False),(True,False,False),(True,True,False),(True,True,True)])
def test_native_focus_waits_for_root_activation_and_detaches(monkeypatch, needs_attachment, refuses, attachment_error):
    import sys
    from contextlib import nullcontext
    clock = [0.0]
    state = {'foreground':41,'calls':0,'attached':set()}
    attachments = []
    restored = []
    def activate(hwnd):
        state['calls'] += 1
        if not refuses and state['calls'] >= 2 and (not needs_attachment or state['attached']):
            state['foreground'] = hwnd
    def attach(first,second,value):
        if attachment_error and value and second == 3:
            raise OSError('native attachment refused')
        attachments.append((first,second,value))
        if value: state['attached'].add((first,second))
        else: state['attached'].discard((first,second))
    monkeypatch.setitem(sys.modules,'win32gui',SimpleNamespace(GetForegroundWindow=lambda:state['foreground'],
        IsWindow=lambda hwnd:True,IsIconic=lambda hwnd:True,ShowWindow=lambda hwnd,mode:restored.append((hwnd,mode)),
        SetForegroundWindow=activate,PeekMessage=lambda *args:None))
    monkeypatch.setitem(sys.modules,'win32api',SimpleNamespace(GetCurrentThreadId=lambda:1))
    monkeypatch.setitem(sys.modules,'win32process',SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd:(3 if hwnd == 42 else 2,7),AttachThreadInput=attach))
    monkeypatch.setattr(windows,'com_thread',nullcontext)
    monkeypatch.setattr(windows.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(windows.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    ui = windows.WindowsUIA(SimpleNamespace(app=lambda *a,**k:None,window=lambda *a,**k:None))
    monkeypatch.setattr(ui,'_window',lambda selector:SimpleNamespace(handle=42,set_focus=lambda:None))
    selector = Selector('a.exe','A','root','Window')
    if refuses:
        with pytest.raises(Denied): ui.focus(selector)
    else:
        ui.focus(selector)
        assert state['foreground'] == 42
    assert restored == [(42,9)]
    assert not state['attached']
    assert {pair[:2] for pair in attachments if pair[2]} == {pair[:2] for pair in attachments if not pair[2]}
    assert clock[0] <= 2.1


@pytest.mark.parametrize('kind,value,expected', [
    ('toggle',1,'on'),('toggle',0,'off'),
    ('expand',1,'expanded'),('expand',0,'collapsed'),
])
def test_native_variant_state_events_decode_value_property(kind,value,expected):
    # comtypes passes input arguments unchanged; VARIANT exposes its scalar via .value.
    wrapped = SimpleNamespace(value=value)
    assert windows._record_value(kind,wrapped,False) == expected


@pytest.mark.parametrize('value', [2, None, '1', 1.0, True])
def test_native_variant_unknown_or_noninteger_state_is_rejected(value):
    with pytest.raises(Denied): windows._record_value('toggle',SimpleNamespace(value=value),False)


def test_default_text_capture_does_not_inspect_variant_value():
    class PrivateVariant:
        @property
        def value(self): raise AssertionError('default must never access text value')
        def __str__(self): raise AssertionError('default must never stringify text')
    assert windows._record_value('set_text',PrivateVariant(),False) == ''
    assert windows._record_value('set_text',SimpleNamespace(value='Synthetic'),True) == 'Synthetic'


@pytest.mark.parametrize('payload,actual,expected', [(None,1,'on'),(True,0,'off')])
def test_state_event_can_use_verified_pattern_snapshot_for_missing_payload(payload,actual,expected):
    element = SimpleNamespace(iface_toggle=SimpleNamespace(CurrentToggleState=actual))
    assert windows._record_state('toggle',payload,element) == (expected,True)


def test_unsupported_explicit_enum_cannot_be_hidden_by_snapshot():
    class NeverRead:
        @property
        def iface_toggle(self): raise AssertionError('unsupported enum must not query pattern')
    with pytest.raises(Denied): windows._record_state('toggle',SimpleNamespace(value=2),NeverRead())


def test_state_snapshot_does_not_read_text_and_requires_supported_actual_enum():
    class Element:
        iface_expand_collapse = SimpleNamespace(CurrentExpandCollapseState=1)
        @property
        def iface_value(self): raise AssertionError('state must never read text')
    assert windows._record_state('expand',None,Element()) == ('expanded',True)
    Element.iface_expand_collapse.CurrentExpandCollapseState = 3
    with pytest.raises(Denied): windows._record_state('expand',None,Element())


@pytest.mark.parametrize('state,expected', [(1,('select','',False)),(0,None)])
def test_radio_toggle_notifications_record_selection_only_for_on(state,expected):
    element = SimpleNamespace(iface_selection_item=object())
    assert windows._record_operation('toggle',state,element,'RadioButton',False) == expected


def test_radio_toggle_on_requires_actual_selection_pattern():
    with pytest.raises(Denied):
        windows._record_operation('toggle',1,SimpleNamespace(),'RadioButton',False)


@pytest.mark.parametrize('raw,actual,expected', [(True,False,('select','',False)),
    (False,True,None),(None,True,('select','',True)),(None,False,None)])
def test_selection_property_uses_boolean_and_authoritative_missing_payload(raw,actual,expected):
    element = SimpleNamespace(iface_selection_item=SimpleNamespace(CurrentIsSelected=actual))
    assert windows._record_operation('selection_state',raw,element,'RadioButton',False) == expected


def test_selection_property_unsupported_boolean_rejected_without_text_read():
    class Element:
        iface_selection_item = SimpleNamespace(CurrentIsSelected=2)
        @property
        def iface_value(self): raise AssertionError('selection cannot read text')
    with pytest.raises(Denied): windows._record_operation('selection_state',None,Element(),'RadioButton',False)
