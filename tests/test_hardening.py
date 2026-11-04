import json
import pytest
from bender_ai.models import Plan, Selector, Step, ValidationError
from bender_ai.actions import ActionRegistry, Executor, Cancellation
from bender_ai.security import Policy, Denied
from bender_ai.storage import Store


@pytest.mark.parametrize('data', [
    {'steps':[{'action':[], 'args':{}}]},
    {'steps':[{'action':'open_app', 'args':[]}]},
    {'steps':[{'action':'open_app', 'args':{'app_id':'notepad'}}], 'parameters':'x'},
    {'steps':[{'action':'uia_click','args':{'selector':{'app':'a.exe','window':'W','automation_id':'x','control_type':'Button','parents':'foo'}}}]},
    {'steps':[{'action':'uia_click','args':{'selector':{'app':5,'window':'W','automation_id':'x','control_type':'Button'}}}]},
    {'steps':[{'action':'create_folder','args':{'parent':'C:/test','name':'CON'}}]},
])
def test_untrusted_schema_types_fail_as_validation_error(data):
    with pytest.raises(ValidationError):
        Plan.from_json(json.dumps(data))


def test_cancel_before_worker_starts_prevents_side_effect(tmp_path):
    store = Store(tmp_path / 'db')
    runner = Executor(ActionRegistry(Policy(write_roots=[str(tmp_path)]),store),store)
    token = Cancellation()
    token.event.set()
    result = runner.run(Plan([Step('create_folder',{'parent':str(tmp_path),'name':'never'})]), confirmed=True, cancellation=token)
    assert result.status == 'cancelled'
    assert not (tmp_path / 'never').exists()


def test_python_uia_is_scoped_to_explicit_window(tmp_path):
    store = Store(tmp_path / 'db')
    policy = Policy(ui_apps=['python.exe'], read_apps=['python.exe'], window_scopes={'python.exe':['BENDER AI Training']})
    registry = ActionRegistry(policy,store)
    with pytest.raises(Denied):
        registry.validate(Step('uia_click',{'selector':Selector('python.exe','Other private app','1002','Button').to_dict()}))


def test_secret_values_not_in_audit_or_failure_history(tmp_path):
    class BrokenUI:
        def set_text(self, selector, text):
            raise RuntimeError('SECRET=' + text)
    store = Store(tmp_path / 'db')
    policy = Policy(ui_apps=['demo.exe'],read_apps=['demo.exe'])
    runner = Executor(ActionRegistry(policy,store,ui=BrokenUI()),store)
    plan = Plan([Step('uia_set_text',{'selector':Selector('demo.exe','Demo','name','Edit').to_dict(),'text':'very-secret-value'})])
    result = runner.run(plan,confirmed=True)
    assert result.status == 'failed'
    assert 'very-secret-value' not in json.dumps([result.error,store.audit(),store.run_steps(result.run_id)])


def test_storage_rejects_newer_schema_and_recovers_interrupted_runs(tmp_path):
    import sqlite3
    db = tmp_path / 'db'
    store = Store(db)
    run = store.start_run(Plan([Step('list_skills',{})]))
    store.close()
    store = Store(db)
    assert store.runs()[0]['status'] == 'interrupted'
    assert store.run_steps(run)[0]['status'] == 'skipped'
    store.close()
    connection = sqlite3.connect(db)
    connection.execute('PRAGMA user_version=99')
    connection.close()
    with pytest.raises(ValidationError):
        Store(db)
