"""Strict, closed schemas. Imported and model-generated data use the same validator."""
from dataclasses import dataclass, field, asdict
import json
import re


class ValidationError(ValueError):
    pass


def exact(data, required, optional=()):
    if not isinstance(data, dict) or set(data) - set(required) - set(optional) or set(required) - set(data):
        raise ValidationError('Неверные или лишние поля схемы')


def string(value, maximum=4096):
    if not isinstance(value, str) or not value or len(value) > maximum or '\x00' in value:
        raise ValidationError('Ожидается непустая строка допустимой длины')
    return value


@dataclass(frozen=True)
class Selector:
    app: str
    window: str
    automation_id: str
    control_type: str
    name: str = ''
    parents: tuple[str, ...] = ()

    def __post_init__(self):
        for v in (self.app, self.window, self.control_type):
            string(v, 512)
        if not self.automation_id and not self.name:
            raise ValidationError('Элемент должен иметь AutomationId или точное имя')
        if len(self.parents) > 12 or any(not isinstance(x, str) for x in self.parents):
            raise ValidationError('Некорректные родители элемента')
        if any(c in self.app for c in '/\\*?'):
            raise ValidationError('Приложение задаётся именем процесса')

    def to_dict(self):
        data = asdict(self)
        data['parents'] = list(self.parents)
        return data

    @classmethod
    def from_dict(cls, data):
        exact(data, ('app', 'window', 'automation_id', 'control_type'), ('name', 'parents'))
        for key in ('automation_id', 'name'):
            if not isinstance(data.get(key, ''), str) or len(data.get(key, '')) > 512:
                raise ValidationError('Некорректный селектор')
        if not isinstance(data.get('parents', []), list):
            raise ValidationError('Родители должны быть списком')
        return cls(**{**data, 'parents': tuple(data.get('parents', ()))})


SCHEMAS = {
    'open_app': ('app_id',), 'open_folder': ('path',), 'get_active_window': (),
    'list_folder': ('path',), 'create_folder': ('parent', 'name'),
    'focus_window': ('selector',), 'wait_for_window': ('selector', 'timeout'),
    'uia_select': ('selector',), 'uia_toggle': ('selector', 'state'), 'uia_expand': ('selector', 'state'),
    'uia_click': ('selector',),
    'uia_set_text': ('selector', 'text'), 'wait_for_element': ('selector', 'timeout'),
    'assert_element': ('selector', 'expected_state'),
    'launch_approved_gradle_task': ('project_path', 'task'), 'list_skills': (), 'stop': (),
}
MUTATING = {'create_folder', 'uia_click', 'uia_set_text', 'uia_select', 'uia_toggle', 'uia_expand', 'focus_window', 'launch_approved_gradle_task'}


@dataclass
class Step:
    action: str
    args: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.action, str) or self.action not in SCHEMAS:
            raise ValidationError('Неизвестный инструмент: ' + str(self.action))
        exact(self.args, SCHEMAS[self.action])
        for key, value in self.args.items():
            if key == 'selector':
                Selector.from_dict(value)
            elif key == 'timeout':
                if type(value) not in (int, float) or not 0.05 <= value <= 60:
                    raise ValidationError('Ожидание должно быть от 0.05 до 60 секунд')
            elif key == 'text':
                if not isinstance(value, str) or len(value) > 10000 or '\x00' in value:
                    raise ValidationError('Недопустимый текст')
            else:
                string(value)
        if self.action in ('uia_toggle', 'uia_expand'):
            allowed = ('on', 'off') if self.action == 'uia_toggle' else ('expanded', 'collapsed')
            if self.args['state'] not in allowed:
                raise ValidationError('Неподдерживаемое состояние UIA')
        if self.action == 'create_folder':
            name = self.args['name']
            if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))} or name in ('.', '..') or any(c in name for c in '\\/:*?"<>|') or name.endswith((' ', '.')):
                raise ValidationError('Недопустимое имя папки')
        if self.action == 'assert_element' and self.args['expected_state'] not in ('exists', 'enabled', 'visible'):
            # Exact text assertion uses text:<value>; never a script or expression.
            if not self.args['expected_state'].startswith('text:'):
                raise ValidationError('Неизвестная проверка')

    def to_dict(self):
        return {'action': self.action, 'args': self.args}


@dataclass
class Plan:
    steps: list[Step]
    name: str = 'Новый навык'
    parameters: list[str] = field(default_factory=list)

    def __post_init__(self):
        string(self.name, 160)
        if not isinstance(self.steps, list) or not 1 <= len(self.steps) <= 100:
            raise ValidationError('План должен содержать от 1 до 100 шагов')
        for step in self.steps:
            if not isinstance(step, Step):
                raise ValidationError('Неверный шаг')
            Step(step.action, step.args)
        if not isinstance(self.parameters, list) or any(not isinstance(p, str) for p in self.parameters):
            raise ValidationError('Параметры должны быть списком строк')
        if len(self.parameters) > 30 or len(set(self.parameters)) != len(self.parameters):
            raise ValidationError('Некорректные параметры')
        if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,39}', p) for p in self.parameters):
            raise ValidationError('Недопустимое имя параметра')
        for step in self.steps:
            for value in step.args.values():
                if isinstance(value, str):
                    for param in re.findall(r'\$\{([^}]+)\}', value):
                        if param not in self.parameters or step.action != 'uia_set_text':
                            raise ValidationError('Параметры разрешены только в тексте UIA')

    @property
    def needs_confirmation(self):
        return any(s.action in MUTATING for s in self.steps)

    def to_dict(self):
        return {'name': self.name, 'parameters': self.parameters, 'steps': [s.to_dict() for s in self.steps]}

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data):
        exact(data, ('steps',), ('name', 'parameters'))
        if not isinstance(data['steps'], list):
            raise ValidationError('Ожидается список шагов')
        steps = []
        for s in data['steps']:
            exact(s, ('action', 'args'))
            steps.append(Step(s['action'], s['args']))
        return cls(steps, data.get('name', 'Новый навык'), data.get('parameters', []))

    @classmethod
    def from_json(cls, raw):
        if len(raw) > 1_000_000:
            raise ValidationError('Слишком большой план')
        try:
            return cls.from_dict(json.loads(raw))
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValidationError('Некорректный JSON плана') from exc

    def bind(self, values):
        if set(values) != set(self.parameters):
            raise ValidationError('Заполните все параметры навыка')
        steps = []
        for step in self.steps:
            args = dict(step.args)
            if step.action == 'uia_set_text':
                args['text'] = re.sub(r'\$\{([^}]+)\}', lambda m: str(values[m[1]]), args['text'])
            steps.append(Step(step.action, args))
        # Bound text is literal; do not re-interpret placeholders supplied by users.
        result = object.__new__(Plan)
        result.steps, result.name, result.parameters = steps, self.name, []
        return result
