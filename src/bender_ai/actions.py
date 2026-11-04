from dataclasses import dataclass
from pathlib import Path
import os
import signal
import itertools
import subprocess
import sys
import threading
import time
from .models import Plan, Step, Selector, ValidationError, MUTATING
from .security import Denied


class Cancelled(RuntimeError):
    pass


class Cancellation:
    def __init__(self, timeout=120):
        self.event = threading.Event()
        self.deadline = time.monotonic() + timeout

    def check(self):
        if self.event.is_set():
            raise Cancelled('Остановлено пользователем')
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Превышено общее время выполнения')

    def wait(self, seconds):
        self.event.wait(min(seconds, max(0, self.deadline - time.monotonic())))
        self.check()


@dataclass
class RunResult:
    run_id: str
    status: str
    outputs: list
    error: str = ''


class ActionRegistry:
    def __init__(self, policy, store, ui=None, desktop=None):
        self.policy, self.store = policy, store
        self.ui, self.desktop = ui, desktop
        self.apps = {'notepad': 'notepad.exe', 'calculator': 'calc.exe', 'explorer': 'explorer.exe'}

    def folder(self, raw):
        if raw == 'downloads':
            if sys.platform == 'win32':
                from .windows import downloads_folder
                return str(downloads_folder())
            return str(Path.home() / 'Downloads')
        return raw

    def validate(self, step):
        Step(step.action, step.args)
        args, action = step.args, step.action
        if self.policy.observer and (action in MUTATING or action in ('open_app', 'open_folder')):
            raise Denied('Режим наблюдателя запрещает управление')
        if 'selector' in args:
            selector = Selector.from_dict(args['selector'])
            self.policy.window(selector.app, selector.window)
            self.policy.app(selector.app, control=action in ('uia_click', 'uia_set_text', 'focus_window'))
        if action == 'open_app' and args['app_id'] not in self.apps:
            raise Denied('Приложение отсутствует в белом списке')
        if action in ('open_folder', 'list_folder'):
            self.policy.path(self.folder(args['path']), 'read')
        if action == 'create_folder':
            parent = self.policy.path(args['parent'], 'write')
            self.policy.path(str(parent / args['name']), 'write')
        if action == 'launch_approved_gradle_task':
            self.gradle(args)

    def gradle(self, args):
        project = str(Path(args['project_path']).resolve())
        config = self.policy.gradle_projects.get(project)
        if not config or args['task'] not in config.get('tasks', []):
            raise Denied('Проект и задача Gradle не утверждены')
        executable = Path(config.get('executable', '')).resolve()
        # Windows .bat/.cmd invoke a shell implicitly. Require an approved native executable.
        if not executable.is_file() or (sys.platform == 'win32' and executable.suffix.lower() != '.exe') or not os.access(executable, os.X_OK):
            raise Denied('Нужен утверждённый нативный gradle/java executable, не .bat/.cmd')
        return executable, project, config.get('prefix_args', [])

    def execute(self, step, cancel):
        cancel.check()
        if self.ui is not None and hasattr(type(self.ui), '__getattr__'):
            self.ui.cancellation = cancel
        if self.desktop is not None and hasattr(type(self.desktop), '__getattr__'):
            self.desktop.cancellation = cancel
        self.validate(step)
        a, p = step.action, step.args
        if a == 'stop':
            cancel.event.set()
            cancel.check()
        if a == 'list_skills':
            return self.store.skills()
        if a == 'list_folder':
            folder = self.policy.path(self.folder(p['path']), 'read')
            result = []
            for entry in itertools.islice(folder.iterdir(), 1000):
                cancel.check()
                result.append(entry.name)
            return result
        if a == 'create_folder':
            parent = self.policy.path(p['parent'], 'write')
            target = parent / p['name']
            target.mkdir(exist_ok=False)
            if not target.is_dir():
                raise RuntimeError('Папка не создана')
            return 'Папка создана'
        if a in ('open_app', 'open_folder', 'get_active_window'):
            if self.desktop is None:
                raise RuntimeError('Действие доступно только в Windows')
            if a == 'open_app':
                return self.desktop.open_app(self.apps[p['app_id']])
            if a == 'open_folder':
                return self.desktop.open_folder(self.policy.path(self.folder(p['path']), 'read'))
            return self.desktop.active(self.policy)
        if a == 'launch_approved_gradle_task':
            executable, project, prefix = self.gradle(p)
            process = subprocess.Popen([str(executable), *prefix, p['task']], cwd=project,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, start_new_session=sys.platform != 'win32')
            job = None
            if sys.platform == 'win32':
                import win32job, win32api
                job = win32job.CreateJobObject(None, '')
                try:
                    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
                    limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
                    win32job.AssignProcessToJobObject(job, int(process._handle))
                except Exception as exc:
                    process.terminate()
                    process.wait(timeout=2)
                    job.Close()
                    raise Denied('Не удалось изолировать дерево Gradle в Windows Job Object: ' + type(exc).__name__ + ', code=' + str(getattr(exc, 'winerror', 'unknown')))
            try:
                while process.poll() is None:
                    cancel.wait(.05)
                if process.returncode:
                    raise RuntimeError('Gradle завершился с ошибкой; код ' + str(process.returncode))
                return 'Gradle: успешно'
            finally:
                if job is not None:
                    job.Close()
                if process.poll() is None:
                    if sys.platform != 'win32':
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        if sys.platform != 'win32':
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                        process.wait(timeout=2)
        if self.ui is None:
            raise RuntimeError('UI Automation доступна только в Windows')
        selector = Selector.from_dict(p['selector'])
        if a == 'wait_for_element':
            end = time.monotonic() + p['timeout']
            while True:
                cancel.check()
                if self.ui.exists(selector):
                    return 'Элемент найден'
                if time.monotonic() >= end:
                    raise TimeoutError('Элемент не появился')
                cancel.wait(.05)
        if a == 'assert_element':
            if not self.ui.assert_state(selector, p['expected_state']):
                raise RuntimeError('Проверка состояния элемента не пройдена')
            return 'Проверка пройдена'
        if a == 'focus_window':
            self.ui.focus(selector)
        elif a == 'uia_click':
            self.ui.click(selector)
        elif a == 'uia_set_text':
            self.ui.set_text(selector, p['text'])
        else:
            raise ValidationError('Инструмент не реализован')
        cancel.check()
        return 'Выполнено'


class Executor:
    def __init__(self, registry, store):
        self.registry, self.store = registry, store
        self.lock = threading.Lock()
        self.cancel = None

    def dry_run(self, plan):
        # No adapter calls and no side effects during preview.
        for step in plan.steps:
            self.registry.validate(step)
        return [s.action for s in plan.steps]

    def stop(self):
        if self.cancel:
            self.cancel.event.set()

    def run(self, plan, confirmed=False, progress=lambda *_: None, cancellation=None):
        if not self.lock.acquire(blocking=False):
            raise Denied('Другая задача уже выполняется')
        try:
            self.dry_run(plan)
            if plan.parameters:
                raise ValidationError('Перед запуском заполните параметры')
            if plan.needs_confirmation and not confirmed:
                raise Denied('Требуется подтверждение показанного плана')
            self.cancel = cancellation or Cancellation()
            run = self.store.start_run(plan)
            outputs, status, error = [], 'success', ''
            for i, step in enumerate(plan.steps):
                if status != 'success':
                    self.store.step_status(run, i, 'skipped')
                    continue
                self.store.step_status(run, i, 'running')
                progress(i + 1, step.action)
                try:
                    self.cancel.check()
                    outputs.append(self.registry.execute(step, self.cancel))
                    self.store.step_status(run, i, 'success')
                    self.store.log('success', step.action)
                except Exception as exc:
                    status = 'cancelled' if isinstance(exc, Cancelled) else 'failed'
                    # Persist error type only: external exception messages can contain UI values/secrets.
                    self.store.step_status(run, i, status, type(exc).__name__)
                    self.store.log(status, step.action)
                    error = str(exc) if isinstance(exc, (Denied, Cancelled, ValidationError, TimeoutError)) else 'Ошибка инструмента: ' + type(exc).__name__
            self.store.finish_run(run, status)
            return RunResult(run, status, outputs, error)
        finally:
            for adapter in (self.registry.ui, self.registry.desktop):
                if adapter is not None and hasattr(type(adapter), '__getattr__'):
                    adapter.cancellation = None
            self.cancel = None
            self.lock.release()
