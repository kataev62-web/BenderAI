import threading
import time
import pytest
from bender_ai.learning import Recorder
from bender_ai.models import Plan, Selector, Step, ValidationError
from bender_ai.security import Policy, Denied
from bender_ai.actions import ActionRegistry, Executor, Cancellation
from bender_ai.storage import Store


def sel(app='alpha.exe', window='Alpha', ident='name', kind='Edit'):
    return Selector(app, window, ident, kind)


def recorder():
    r = Recorder(windows=[('alpha.exe', 'Alpha'), ('beta.exe', 'Beta')])
    r.start()
    return r


def test_multiwindow_switches_are_explicit_and_parameter_order_survives_restart(tmp_path):
    r = recorder()
    r.capture('set_text', sel(), 'neverpersist')
    r.capture('set_text', sel(), 'neverpersist2')
    r.capture('click', sel('beta.exe', 'Beta', 'go', 'Button'))
    r.capture('set_text', sel(), 'again')
    plan = r.finish('Two applications')
    assert [s.action for s in plan.steps] == ['wait_for_window','focus_window','uia_set_text',
        'wait_for_window','focus_window','uia_click','wait_for_window','focus_window','uia_set_text']
    assert plan.parameters == ['value_1', 'value_2']
    assert 'neverpersist' not in plan.to_json()
    assert all(s.args['selector']['app'] in ('alpha.exe','beta.exe') for s in plan.steps)
    store = Store(tmp_path/'db')
    skill = store.save_skill(plan)
    store.close()
    store = Store(tmp_path/'db')
    loaded = store.load_skill(skill)
    assert loaded.to_dict() == plan.to_dict()
    assert [s.args['text'] for s in loaded.bind({'value_1':'one','value_2':'two'}).steps if 'text' in s.args] == ['one','two']
    store.close()


def test_unselected_password_and_paused_events_never_become_steps_or_parameters():
    r = recorder()
    r.capture('set_text', sel('foreign.exe','Foreign'), 'private')
    r.capture('set_text', sel(), 'password', password=True)
    r.pause()
    timestamp = time.perf_counter()
    r.resume()
    r.capture('set_text', sel(), 'delayed', timestamp=timestamp)
    assert r.steps == [] and r.parameters == []
    r.capture('click', sel(ident='go',kind='Button'))
    assert len(r.steps) == 3
    assert all(x not in r.finish('safe').to_json() for x in ('private','password','delayed'))


def test_focus_events_and_state_operations_do_not_create_duplicate_window_switches():
    r = recorder()
    r.capture('focus', sel(ident='__window__',kind='Window'))
    r.capture('select', sel(ident='item',kind='ListItem'))
    r.capture('toggle', sel(ident='check',kind='CheckBox'), 'on')
    r.capture('expand', sel(ident='tree',kind='TreeItem'), 'expanded')
    assert [s.action for s in r.steps] == ['wait_for_window','focus_window','uia_select','uia_toggle','uia_expand']
    assert r.steps[-2].args['state'] == 'on'
    assert r.steps[-1].args['state'] == 'expanded'
    with pytest.raises(ValidationError):
        Step('uia_toggle', {'selector':sel().to_dict(), 'state':'run-shell'})


def test_unsupported_capture_is_reported_without_value_and_keeps_valid_steps():
    r = recorder()
    r.capture('drag', sel(), 'sensitive-unhandled-value')
    assert not r.steps and not r.parameters
    assert r.diagnostics
    assert 'sensitive-unhandled-value' not in str(r.diagnostics)
    r.capture('click', sel(ident='go',kind='Button'))
    assert r.finish('partial').steps[-1].action == 'uia_click'


def test_explicit_grants_extend_exact_windows_and_pin_executable_and_persist(tmp_path):
    p = Policy()
    p.grant_app('alpha.exe','Alpha', executable=str(tmp_path/'alpha.exe'), control=True)
    p.grant_app('alpha.exe','Other', executable=str(tmp_path/'alpha.exe'), control=False)
    p.app('alpha.exe',control=True)
    p.window('alpha.exe','Other')
    p.identity('alpha.exe',str(tmp_path/'alpha.exe'))
    with pytest.raises(Denied):p.identity('alpha.exe',str(tmp_path/'different.exe'))
    with pytest.raises(Denied):p.window('alpha.exe','unselected')
    with pytest.raises(Denied):p.grant_app('alpha.exe','InPrivate')
    with pytest.raises(Denied):p.grant_app('../alpha.exe','Alpha')
    store=Store(tmp_path/'db')
    store.set_setting('policy',p.to_dict())
    store.close()
    store=Store(tmp_path/'db')
    restored=Policy(**store.setting('policy'))
    restored.identity('alpha.exe',str(tmp_path/'alpha.exe'))
    restored.revoke_app('alpha.exe')
    with pytest.raises(Denied):restored.app('alpha.exe')
    with pytest.raises(Denied):restored.app('alpha.exe',control=True)
    store.close()


class UI:
    def __init__(self): self.calls=[]; self.present=True
    def window_exists(self,s):return self.present
    def focus(self,s):self.calls.append(('focus',s.app,s.window))
    def set_text(self,s,v):self.calls.append(('text',s.app,v))
    def select(self,s):self.calls.append(('select',s.app))
    def toggle(self,s,v):self.calls.append(('toggle',s.app,v))
    def expand(self,s,v):self.calls.append(('expand',s.app,v))


def test_replay_waits_switches_dispatches_state_operations_and_requires_confirmation(tmp_path):
    r=recorder()
    r.capture('set_text',sel(),'value')
    r.capture('select',sel('beta.exe','Beta','item','ListItem'))
    r.capture('toggle',sel('beta.exe','Beta','check','CheckBox'),'off')
    r.capture('expand',sel('beta.exe','Beta','tree','TreeItem'),'collapsed')
    p=Policy(read_apps=['alpha.exe','beta.exe'],ui_apps=['alpha.exe','beta.exe'])
    store=Store(tmp_path/'db');ui=UI()
    runner=Executor(ActionRegistry(p,store,ui=ui),store)
    plan=r.finish('sequence').bind({'value_1':'supplied'})
    with pytest.raises(Denied):runner.run(plan)
    assert not ui.calls
    assert runner.run(plan,confirmed=True).status=='success'
    assert ui.calls==[('focus','alpha.exe','Alpha'),('text','alpha.exe','supplied'),('focus','beta.exe','Beta'),
        ('select','beta.exe'),('toggle','beta.exe','off'),('expand','beta.exe','collapsed')]
    p.ui_apps.remove('beta.exe')
    ui.calls=[]
    with pytest.raises(Denied):runner.run(plan,confirmed=True)
    assert not ui.calls
    store.close()


def test_wait_for_window_timeout_stops_sequence_and_stop_interrupts_wait(tmp_path):
    store=Store(tmp_path/'db');ui=UI();ui.present=False
    p=Policy(read_apps=['alpha.exe'],ui_apps=['alpha.exe'])
    runner=Executor(ActionRegistry(p,store,ui=ui),store)
    plan=Plan([Step('wait_for_window',{'selector':sel().to_dict(),'timeout':.05}),
               Step('uia_set_text',{'selector':sel().to_dict(),'text':'never'})])
    result=runner.run(plan,confirmed=True)
    assert result.status=='failed' and not ui.calls
    assert [x['status'] for x in store.run_steps(result.run_id)]==['failed','skipped']
    plan.steps[0].args['timeout']=10
    cancel=Cancellation();out=[]
    t=threading.Thread(target=lambda:out.append(runner.run(plan,confirmed=True,cancellation=cancel)))
    t.start();cancel.event.set();t.join(2)
    assert not t.is_alive() and out[0].status=='cancelled' and not ui.calls
    store.close()
