"""Portable selected-Explorer/Shell permission and semantic recording tests."""
from types import SimpleNamespace
from pathlib import Path
import pytest
from bender_ai.models import Selector
from bender_ai.security import Policy, Denied


@pytest.fixture
def shell_backend(tmp_path,monkeypatch):
    from bender_ai import explorer
    parent = tmp_path/'Allowed'; parent.mkdir()
    outside = tmp_path/'Other'; outside.mkdir()
    state = {'path':str(parent),'title':'Allowed'}
    class Browser:
        HWND = 42
        @property
        def Document(self):
            return SimpleNamespace(Folder=SimpleNamespace(Self=SimpleNamespace(Path=state['path'],IsFileSystem=True)))
        def Navigate2(self,path): state.update(path=str(path),title=Path(path).name)
    browser = Browser()
    monkeypatch.setattr(explorer,'_native_windows',lambda:[42])
    monkeypatch.setattr(explorer,'_native_metadata',lambda hwnd:{'pid':7,'executable':r'C:\Windows\explorer.exe',
        'title':state['title'],'class_name':'CabinetWClass','active':True})
    policy = Policy(read_roots=[str(parent)],write_roots=[str(parent)])
    policy.grant_app('explorer.exe','Allowed',executable=r'C:\Windows\explorer.exe')
    backend = explorer.WindowsExplorer(policy,shell_windows=lambda:[browser])
    return backend,policy,Selector('explorer.exe','Allowed','__window__','Window'),parent,outside,state


def test_shell_navigation_is_same_bound_window_and_exact_allowed_path(shell_backend):
    backend,policy,selector,parent,_,state = shell_backend
    child = parent/'Child'; child.mkdir()
    assert backend.bind(selector)['hwnd'] == 42
    result = backend.navigate(selector,str(child))
    assert result['path'] == str(child.resolve())
    assert result['title'] == 'Child'
    assert backend.snapshot(selector)['hwnd'] == 42  # title changes don't widen another window grant


def test_shell_denies_missing_path_before_navigation_and_returns_exact_jit_scope(shell_backend):
    from bender_ai.explorer import ExplorerPermissionRequired
    backend,_,selector,_,outside,state = shell_backend
    before = state.copy()
    with pytest.raises(ExplorerPermissionRequired) as exc: backend.navigate(selector,str(outside))
    assert exc.value.mode == 'read' and exc.value.path == str(outside.resolve())
    assert state == before


def test_shell_create_requires_explicit_confirmation_and_write_permission(shell_backend):
    from bender_ai.explorer import ExplorerPermissionRequired
    backend,policy,selector,parent,_,_ = shell_backend
    with pytest.raises(Denied): backend.create_subfolder(selector,'Child')
    assert not (parent/'Child').exists()
    policy.write_roots.clear()
    with pytest.raises(ExplorerPermissionRequired) as exc:
        backend.create_subfolder(selector,'Child',confirmed=True)
    assert exc.value.mode == 'write' and exc.value.path == str(parent.resolve())
    policy.write_roots.append(str(parent))
    result = backend.create_subfolder(selector,'Child',confirmed=True)
    assert (parent/'Child').is_dir() and result['created'] == str((parent/'Child').resolve())


@pytest.mark.parametrize('name',['../escape','a/b','a\\b','CON','name:stream','trailing.'])
def test_shell_create_rejects_unsafe_child_names(shell_backend,name):
    backend,_,selector,_,_,_ = shell_backend
    with pytest.raises((Denied,ValueError)): backend.create_subfolder(selector,name,confirmed=True)


def test_shell_binding_cannot_survive_permission_revocation(shell_backend):
    backend,policy,selector,_,_,_ = shell_backend
    backend.bind(selector)
    policy.revoke_app('explorer.exe')
    with pytest.raises(Denied): backend.snapshot(selector)


def test_semantic_capture_records_verified_navigation_and_confirmed_create(shell_backend):
    from bender_ai.explorer import ExplorerCapture
    from bender_ai.learning import Recorder
    backend,_,selector,parent,_,_ = shell_backend
    child = parent/'Child'; child.mkdir()
    recorder = Recorder(windows=[('explorer.exe','Allowed')]); recorder.start()
    capture = ExplorerCapture(backend,recorder,selector)
    capture.poll()
    capture.navigate(str(child))
    capture.create_subfolder('New',confirmed=True)
    assert [step.action for step in recorder.steps] == ['explorer_navigate','create_folder']
    assert recorder.steps[0].args['path'] == str(child)
    assert recorder.steps[1].args == {'parent':str(child),'name':'New'}
    assert 'hwnd' not in recorder.finish('Explorer').to_json()


def test_external_folder_appearance_is_review_proposal_not_silent_action(shell_backend):
    from bender_ai.explorer import ExplorerCapture
    from bender_ai.learning import Recorder
    backend,_,selector,parent,_,_ = shell_backend
    recorder = Recorder(windows=[('explorer.exe','Allowed')]); recorder.start()
    capture = ExplorerCapture(backend,recorder,selector); capture.poll()
    (parent/'External').mkdir()
    capture.poll()
    assert recorder.steps == []
    assert capture.pending_creations == [{'parent':str(parent),'name':'External'}]


def test_explorer_navigation_action_uses_confirmed_executor(shell_backend,tmp_path):
    from bender_ai.models import Plan,Step
    from bender_ai.actions import ActionRegistry,Executor
    from bender_ai.storage import Store
    backend,policy,selector,parent,_,state = shell_backend
    child = parent/'Child'; child.mkdir()
    plan = Plan([Step('explorer_navigate',{'selector':selector.to_dict(),'path':str(child)})])
    store = Store(tmp_path/'db')
    try:
        executor = Executor(ActionRegistry(policy,store,explorer=backend),store)
        with pytest.raises(Denied): executor.run(plan,confirmed=False)
        assert state['path'] == str(parent)
        assert executor.run(plan,confirmed=True).status == 'success'
        assert state['path'] == str(child)
    finally: store.close()


def test_explorer_rpc_preserves_only_missing_scoped_permission(monkeypatch,tmp_path):
    from bender_ai import explorer,isolation
    responses = []
    class Backend:
        def __init__(self,policy): self.policy=policy
        def bind(self,selector): raise explorer.ExplorerPermissionRequired('read',tmp_path)
        def close(self): pass
    monkeypatch.setattr(explorer,'WindowsExplorer',Backend)
    class Connection:
        count=0
        def recv(self):
            self.count+=1
            if self.count>1: raise EOFError
            return ('explorer','bind',({},),Policy().to_dict())
        def send(self,event): responses.append(event)
    isolation.windows_service(Connection())
    assert responses == [('permission_required',{'mode':'read','path':str(tmp_path)})]
    bridge=SimpleNamespace(policy=Policy(),channel=SimpleNamespace(call=lambda *args:responses[0]))
    with pytest.raises(explorer.ExplorerPermissionRequired) as exc:
        isolation._RemoteAdapter(bridge,'explorer').bind({})
    assert exc.value.path==str(tmp_path) and exc.value.mode=='read'


def test_passive_capture_ignores_background_and_resets_on_foreground_reentry():
    from bender_ai.explorer import ExplorerCapture
    from bender_ai.learning import Recorder
    selector=Selector('explorer.exe','Allowed','__window__','Window')
    states=iter([
        {'hwnd':42,'path':'/a','folders':[],'active':True,'observation_started':10,'timestamp':10},
        {'hwnd':42,'path':'/b','folders':['Background'],'active':False,'observation_started':20,'timestamp':20},
        {'hwnd':42,'path':'/b','folders':['Background'],'active':True,'observation_started':30,'timestamp':30},
        {'hwnd':42,'path':'/c','folders':[],'active':True,'observation_started':40,'timestamp':40},
    ])
    recorder=Recorder(windows=[('explorer.exe','Allowed')]);recorder.start();recorder.started_at=0
    capture=ExplorerCapture(SimpleNamespace(snapshot=lambda s:next(states)),recorder,selector)
    for _ in range(4):capture.poll()
    assert [step.args['path'] for step in recorder.steps]==['/c']
    assert capture.pending_creations==[]


def test_capture_path_observation_spanning_pause_is_not_recorded_after_delayed_read():
    from bender_ai.explorer import ExplorerCapture
    from bender_ai.learning import Recorder
    selector=Selector('explorer.exe','Allowed','__window__','Window')
    states=iter([
        {'hwnd':42,'path':'/a','folders':[],'active':True,'observation_started':10,'timestamp':20},
        {'hwnd':42,'path':'/b','folders':[],'active':True,'observation_started':30,'timestamp':30},
    ])
    recorder=Recorder(windows=[('explorer.exe','Allowed')]);recorder.start();recorder.started_at=0
    recorder.pause_intervals=[(15,18)]
    capture=ExplorerCapture(SimpleNamespace(snapshot=lambda s:next(states)),recorder,selector)
    capture.poll();capture.poll()
    assert recorder.steps==[]


def test_executor_clears_explorer_rpc_cancellation_after_run(shell_backend,tmp_path):
    from bender_ai.actions import ActionRegistry,Executor
    from bender_ai.models import Plan,Step
    from bender_ai.storage import Store
    backend,policy,selector,parent,_,_=shell_backend
    class Remote:
        cancellation=None
        def __getattr__(self,name):return getattr(backend,name)
    remote=Remote();store=Store(tmp_path/'cleanupdb')
    try:
        result=Executor(ActionRegistry(policy,store,explorer=remote),store).run(
            Plan([Step('explorer_navigate',{'selector':selector.to_dict(),'path':str(parent)})]),confirmed=True)
        assert result.status=='success'
        assert remote.cancellation is None
    finally:store.close()


def test_shell_transient_document_during_navigation_is_observed_without_redispatch(shell_backend,monkeypatch):
    backend,_,selector,parent,_,_=shell_backend
    child=parent/'Child';child.mkdir()
    backend.bind(selector)
    original=backend._current_path
    calls=[]
    def transient(binding):
        calls.append(1)
        if len(calls)==1:raise RuntimeError('private COM initialization detail')
        return original(binding)
    monkeypatch.setattr(backend,'_current_path',transient)
    assert backend.navigate(selector,str(child))['path']==str(child)


def test_shell_dispatch_exception_is_unverified_not_safe_to_retry(shell_backend):
    from bender_ai.models import OperationUnverified
    backend,_,selector,parent,_,_=shell_backend
    child=parent/'Child';child.mkdir()
    browser=backend._shell_windows()[0]
    calls=[]
    def partial_dispatch(path):
        calls.append(path)
        raise RuntimeError('private COM dispatch detail')
    browser.Navigate2=partial_dispatch
    with pytest.raises(OperationUnverified) as exc:backend.navigate(selector,str(child))
    assert len(calls)==1 and 'private' not in str(exc.value)


@pytest.mark.parametrize('still_requested', [True,False])
def test_capture_after_exact_jit_grant_records_only_fresh_verified_requested_navigation(shell_backend,monkeypatch,still_requested):
    from bender_ai.explorer import ExplorerCapture,ExplorerPermissionRequired
    from bender_ai.learning import Recorder
    from bender_ai import explorer
    backend,policy,selector,parent,outside,state=shell_backend
    clock=[10.0]
    monkeypatch.setattr(explorer.time,'perf_counter',lambda:clock[0])
    recorder=Recorder(windows=[('explorer.exe','Allowed')]);recorder.start()
    capture=ExplorerCapture(backend,recorder,selector);capture.poll()
    state.update(path=str(outside),title=outside.name);clock[0]=20
    with pytest.raises(ExplorerPermissionRequired):capture.poll()
    recorder.pause()
    assert recorder.steps==[] and policy.read_roots==[str(parent)]
    # The GUI explicitly grants this exact folder, then resumes the central recorder.
    policy.read_roots.append(str(outside))
    if not still_requested:state.update(path=str(parent),title=parent.name)
    clock[0]=30;recorder.resume()
    capture.poll();clock[0]=31;capture.poll()
    assert [step.action for step in recorder.steps]==(['explorer_navigate'] if still_requested else [])
    if still_requested:assert recorder.steps[0].args['path']==str(outside)
