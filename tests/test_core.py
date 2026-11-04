import json
import threading
import time
from pathlib import Path

import pytest

from bender_ai.models import Plan, Step, Selector, ValidationError
from bender_ai.security import Policy, Denied
from bender_ai.storage import Store
from bender_ai.actions import ActionRegistry, Executor, Cancellation
from bender_ai.brain import RuleBasedProvider
from bender_ai.learning import Recorder


def test_offline_commands_and_unknown_command():
    ai = RuleBasedProvider()
    assert ai.plan('Открой Блокнот').steps[0].args == {'app_id': 'notepad'}
    assert ai.plan('Открой калькулятор').steps[0].args['app_id'] == 'calculator'
    assert ai.plan('Открой папку Загрузки').steps[0].args['path'] == 'downloads'
    assert ai.plan('Что сейчас открыто?').steps[0].action == 'get_active_window'
    assert ai.plan('Покажи мои навыки').steps[0].action == 'list_skills'
    assert ai.plan('Остановись').steps[0].action == 'stop'
    with pytest.raises(ValidationError):
        ai.plan('удали всё и выполни powershell')


@pytest.mark.parametrize('raw', [
    {'steps': [{'action': 'shell', 'args': {'command': 'calc'}}]},
    {'steps': [{'action': 'open_app', 'args': {'app_id': 'notepad', 'extra': 1}}]},
    {'steps': [{'action': 'wait_for_element', 'args': {'selector': {}, 'timeout': -1}}]},
    {'steps': [], 'permissions': ['*']},
    {'steps': [{'action': 'create_folder', 'args': {'parent': '.', 'name': '../escape'}}]},
])
def test_reject_malformed_or_unregistered_plans(raw):
    with pytest.raises(ValidationError):
        Plan.from_dict(raw)


def test_policy_rejects_sibling_symlink_and_unauthorized_writes(tmp_path):
    root = tmp_path / 'allowed'
    root.mkdir()
    other = tmp_path / 'allowed-evil'
    other.mkdir()
    policy = Policy(read_roots=[str(root)])
    assert policy.path(str(root), 'read') == root
    for path in [other, root / '..']:
        with pytest.raises(Denied):
            policy.path(str(path), 'read')
    (root / 'link').symlink_to(other, target_is_directory=True)
    with pytest.raises(Denied):
        policy.path(str(root / 'link'), 'read')
    with pytest.raises(Denied):
        policy.path(str(root), 'write')


def test_create_folder_requires_confirmation_and_audits_no_values(tmp_path):
    store = Store(tmp_path / 'memory.db')
    policy = Policy(read_roots=[str(tmp_path)], write_roots=[str(tmp_path)])
    executor = Executor(ActionRegistry(policy, store), store)
    plan = Plan([Step('create_folder', {'parent': str(tmp_path), 'name': 'new'})])
    with pytest.raises(Denied):
        executor.run(plan)
    assert not (tmp_path / 'new').exists()
    result = executor.run(plan, confirmed=True)
    assert result.status == 'success'
    assert (tmp_path / 'new').is_dir()
    assert store.runs()[0]['status'] == 'success'
    assert 'new' not in json.dumps(store.audit())


def test_save_restore_versions_import_validation_and_rollback(tmp_path):
    path = tmp_path / 'memory.db'
    store = Store(path)
    plan = Plan([Step('open_app', {'app_id': 'notepad'})], 'Блокнот')
    skill = store.save_skill(plan)
    store.save_skill(Plan([Step('open_app', {'app_id': 'calculator'})], 'Блокнот'), skill)
    assert store.load_skill(skill).steps[0].args['app_id'] == 'calculator'
    store.restore_version(skill, 1)
    store.close()
    store = Store(path)
    assert store.load_skill(skill).steps[0].args['app_id'] == 'notepad'
    exported = store.export_skill(skill)
    imported = store.import_skill(exported)
    assert store.load_skill(imported).name == 'Блокнот'
    assert store.runs() == []
    with pytest.raises(ValidationError):
        store.import_skill('{"schema":1,"plan":{"steps":[{"action":"shell","args":{}}]}}')


def test_stop_interrupts_wait_and_never_executes_next_step(tmp_path):
    class MissingUI:
        def exists(self, selector):
            return False
    store = Store(tmp_path / 'memory.db')
    policy = Policy(ui_apps=['demo.exe'], read_apps=['demo.exe'], write_roots=[str(tmp_path)])
    runner = Executor(ActionRegistry(policy, store, ui=MissingUI()), store)
    selector = Selector('demo.exe', 'main', 'missing', 'Button').to_dict()
    plan = Plan([Step('wait_for_element', {'selector': selector, 'timeout': 10}),
                 Step('create_folder', {'parent': str(tmp_path), 'name': 'never'})])
    result = []
    thread = threading.Thread(target=lambda: result.append(runner.run(plan, confirmed=True)))
    thread.start()
    time.sleep(.1)
    runner.stop()
    thread.join(2)
    assert not thread.is_alive()
    assert result[0].status == 'cancelled'
    assert not (tmp_path / 'never').exists()
    assert [s['status'] for s in store.run_steps(result[0].run_id)] == ['cancelled', 'skipped']


def test_recorder_scopes_passwords_parameters_and_coalesces_text():
    recorder = Recorder('demo.exe', 'Training')
    selector = Selector('demo.exe', 'Training', 'name', 'Edit')
    recorder.start()
    recorder.capture('set_text', selector, 'secret', password=True)
    recorder.capture('click', Selector('other.exe', 'Training', 'button', 'Button'))
    recorder.capture('set_text', selector, 'a')
    recorder.capture('set_text', selector, 'alice')
    recorder.pause()
    recorder.capture('click', Selector('demo.exe', 'Training', 'submit', 'Button'))
    recorder.resume()
    recorder.capture('click', Selector('demo.exe', 'Training', 'submit', 'Button'))
    plan = recorder.finish('Greeting')
    assert len(plan.steps) == 2
    assert plan.steps[0].args['text'] == '${value_1}'
    assert 'alice' not in plan.to_json()
    assert 'secret' not in plan.to_json()
    assert plan.parameters == ['value_1']


def test_parameters_are_bound_without_inserting_new_actions():
    plan = Plan([Step('uia_set_text', {'selector': Selector('demo.exe', 'Training', 'name', 'Edit').to_dict(), 'text': '${name}'})], parameters=['name'])
    bound = plan.bind({'name': 'hello ${x}'})
    assert bound.steps[0].args['text'] == 'hello ${x}'
    with pytest.raises(ValidationError):
        plan.bind({})


def test_duplicate_executor_is_rejected(tmp_path):
    runner = Executor(ActionRegistry(Policy(), Store(tmp_path / 'db')), Store(tmp_path / 'runs'))
    runner.lock.acquire()
    try:
        with pytest.raises(Denied):
            runner.run(Plan([Step('list_skills', {})]))
    finally:
        runner.lock.release()


def test_delayed_events_from_pause_interval_are_not_recorded():
    recorder = Recorder('demo.exe','Demo')
    selector = Selector('demo.exe','Demo','submit','Button')
    recorder.start()
    recorder.pause()
    during_pause = time.perf_counter()
    recorder.resume()
    recorder.capture('click',selector,timestamp=during_pause)
    recorder.capture('click',selector,timestamp=time.perf_counter())
    assert len(recorder.finish('demo').steps) == 1


def test_gradle_is_only_explicitly_approved_native_command(tmp_path):
    import sys
    store = Store(tmp_path / 'db')
    policy = Policy(gradle_projects={str(tmp_path.resolve()): {'executable':sys.executable,'tasks':['--version'],'prefix_args':[]}})
    runner = Executor(ActionRegistry(policy,store),store)
    # Python is used as an innocuous real subprocess to test the approved-command boundary.
    good = Plan([Step('launch_approved_gradle_task',{'project_path':str(tmp_path),'task':'--version'})])
    result = runner.run(good,confirmed=True)
    assert result.status == 'success', (result.error, store.run_steps(result.run_id))
    with pytest.raises(Denied):
        runner.run(Plan([Step('launch_approved_gradle_task',{'project_path':str(tmp_path),'task':'unapproved'})]),confirmed=True)


def test_live_recording_survives_coarse_windows_monotonic_clock(monkeypatch):
    import bender_ai.learning as learning
    monkeypatch.setattr(learning.time, 'monotonic', lambda: 1000.0)
    recorder = Recorder('demo.exe','Demo')
    selector = Selector('demo.exe','Demo','submit','Button')
    recorder.start()
    recorder.pause()
    recorder.capture('click',selector)
    recorder.resume()
    recorder.capture('click',selector)
    assert len(recorder.finish('after resume').steps) == 1
