"""Explicit selected-window demonstrations; never an input/keystroke stream."""
import threading
import time
from .models import Plan, Step, Selector, ValidationError
from .security import check_private_title

DIAGNOSTICS = {
    'state_snapshot':'UIA не передала пригодное состояние события; записано проверенное текущее состояние контрола. Проверьте порядок шагов.',
    'unsupported_control': 'Контрол не предоставляет поддерживаемое UIA-действие; действие не записано.',
    'unsupported_pattern': 'Нужный UIA Pattern недоступен; требуется отдельный адаптер.',
    'password_or_unknown': 'Защищённое поле или неизвестный статус пароля исключены до чтения значения.',
    'dangerous_action': 'Опасное действие заблокировано; разрешение приложению не снимает этот запрет.',
    'ambiguous_selector': 'Неоднозначный элемент: нужен уникальный ID и путь родителей.',
    'unsupported_state': 'Состояние UIA не поддерживается; действие не записано.',
    'unstable_selector': 'У элемента нет устойчивого селектора; координатная запись не используется.',
    'unselected_window': 'Событие вне выбранных окон исключено.',
    'unsupported_event': 'Событие не поддерживается; действие не записано.',
}


class Recorder:
    def __init__(self, app=None, window=None, literal_text=False, *, windows=None):
        self.multi_window = windows is not None
        choices = windows if windows is not None else [(app, window)]
        if not choices or len(choices)>12:
            raise ValidationError('Выберите от 1 до 12 окон')
        selected = []
        for app_name, title in choices:
            # Reuse the strict selector validator; never broaden titles with wildcards.
            Selector(app_name, title, '__window__', 'Window')
            check_private_title(title)
            pair = (app_name.casefold(), title)
            if pair in selected:
                raise ValidationError('Окно выбрано повторно')
            selected.append(pair)
        self.windows = tuple(selected)
        self.app, self.window = self.windows[0]  # legacy single-window API
        self.literal_text = literal_text
        self.steps, self.parameters, self.diagnostics = [], [], []
        self.active = False
        self.paused = False
        self.pause_intervals = []
        self.pause_started = None
        self.started_at = 0
        self.current_window = None
        self.lock = threading.RLock()

    def start(self):
        with self.lock:
            self.steps, self.parameters, self.diagnostics = [], [], []
            self.active, self.paused = True, False
            self.started_at = time.perf_counter()
            self.pause_intervals = []
            self.pause_started = None
            self.current_window = None

    def pause(self):
        with self.lock:
            if not self.paused:
                self.pause_started = time.perf_counter()
            self.paused = True

    def resume(self):
        with self.lock:
            if self.paused and self.pause_started is not None:
                self.pause_intervals.append((self.pause_started, time.perf_counter()))
            self.pause_started = None
            self.paused = False
            # Re-focus after a pause even if the last recorded app is the same.
            self.current_window = None

    def report(self, reason, selector=None):
        # Never retain arbitrary provider messages, UI labels or values in diagnostics.
        message = DIAGNOSTICS.get(reason, DIAGNOSTICS['unsupported_event'])
        with self.lock:
            if message not in self.diagnostics and len(self.diagnostics)<100:
                self.diagnostics.append(message)

    def capture(self, kind, selector, value='', password=False, timestamp=None, observation_start=None):
        with self.lock:
            if not self.active or (timestamp is None and self.paused):
                return
            moment = time.perf_counter() if timestamp is None else timestamp
            if moment < self.started_at or (self.pause_started is not None and moment >= self.pause_started):
                return
            if any(start <= moment < end for start,end in self.pause_intervals):
                return
            if observation_start is not None:
                # Sampling only proves a transition somewhere between two reads.
                # Reject ambiguous intervals spanning any pause or recorder start.
                if (observation_start < self.started_at or observation_start > moment or
                        any(start <= moment and end > observation_start for start,end in self.pause_intervals)):
                    return
            pair = (selector.app.casefold(), selector.window)
            if pair not in self.windows:
                self.report('unselected_window')
                return
            if password:
                self.report('password_or_unknown')
                return
            if kind not in ('set_text','click','assert','focus','select','toggle','expand'):
                self.report('unsupported_event')
                return
            if kind == 'focus' and not self.multi_window:
                return
            if kind in ('toggle','expand'):
                allowed = ('on','off') if kind=='toggle' else ('expanded','collapsed')
                if value not in allowed:
                    self.report('unsupported_state')
                    return
            switch = self.multi_window and pair != self.current_window
            if kind=='focus' and not switch:
                return
            args = {'selector': selector.to_dict()}
            if kind=='set_text' and not switch and self.steps and self.steps[-1].action=='uia_set_text' and self.steps[-1].args['selector']==args['selector']:
                if self.literal_text:
                    # Validate every value before replacing an already valid step.
                    checked = Step('uia_set_text', {**args,'text':value})
                    self.steps[-1] = checked
                return
            param = None
            if kind=='set_text':
                if self.literal_text:
                    args['text'] = value
                else:
                    param = f'value_{len(self.parameters)+1}'
                    args['text'] = '${'+param+'}'
                action = 'uia_set_text'
            elif kind=='assert':
                action = 'assert_element'
                args['expected_state'] = 'text:'+value if self.literal_text else 'exists'
            elif kind in ('toggle','expand'):
                action = 'uia_'+kind
                args['state'] = value
            else:
                action = {'click':'uia_click','select':'uia_select','focus':'focus_window'}[kind]
            if (not switch and kind in ('select','toggle','expand') and self.steps
                    and self.steps[-1].action==action and self.steps[-1].args==args):
                return
            additions=[]
            if switch:
                target = Selector(selector.app,selector.window,'__window__','Window').to_dict()
                additions.extend([Step('wait_for_window',{'selector':target,'timeout':10}),
                                  Step('focus_window',{'selector':target})])
            if kind!='focus':
                additions.append(Step(action,args))
            if len(self.steps)+len(additions)>100 or (param and len(self.parameters)>=30):
                self.pause()
                raise ValidationError('Лимит записи: 100 шагов / 30 параметров')
            self.steps.extend(additions)
            if param:
                self.parameters.append(param)
            self.current_window=pair

    def finish(self, name):
        with self.lock:
            self.active = False
            return Plan(list(self.steps), name, list(self.parameters))

    def cancel(self):
        with self.lock:
            self.active = False
            self.steps.clear()
            self.parameters.clear()
            self.diagnostics.clear()
