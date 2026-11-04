"""Scoped demonstrations, no keystroke stream. Values default to parameters."""
import time
from .models import Plan, Step, Selector, ValidationError


class Recorder:
    def __init__(self, app, window, literal_text=False):
        self.app, self.window = app.casefold(), window
        self.literal_text = literal_text
        self.steps, self.parameters = [], []
        self.active = False
        self.paused = False
        self.pause_intervals = []
        self.pause_started = None
        self.started_at = 0

    def start(self):
        self.steps, self.parameters = [], []
        self.active, self.paused = True, False
        self.started_at = time.perf_counter()
        self.pause_intervals = []
        self.pause_started = None

    def pause(self):
        if not self.paused:
            self.pause_started = time.perf_counter()
        self.paused = True

    def resume(self):
        if self.paused and self.pause_started is not None:
            self.pause_intervals.append((self.pause_started, time.perf_counter()))
        self.pause_started = None
        self.paused = False

    def capture(self, kind, selector, value='', password=False, timestamp=None):
        if not self.active or password or (timestamp is None and self.paused):
            return
        moment = time.perf_counter() if timestamp is None else timestamp
        if moment < self.started_at or (self.pause_started is not None and moment >= self.pause_started):
            return
        if any(start <= moment < end for start,end in self.pause_intervals):
            return
        if selector.app.casefold() != self.app or selector.window != self.window:
            return
        args = {'selector': selector.to_dict()}
        if kind == 'set_text':
            if self.steps and self.steps[-1].action == 'uia_set_text' and self.steps[-1].args['selector'] == args['selector']:
                if self.literal_text:
                    self.steps[-1].args['text'] = value
                return
            if self.literal_text:
                args['text'] = value
            else:
                param = f'value_{len(self.parameters)+1}'
                self.parameters.append(param)
                args['text'] = '${' + param + '}'
            action = 'uia_set_text'
        elif kind == 'click':
            action = 'uia_click'
        elif kind == 'assert':
            action = 'assert_element'
            args['expected_state'] = 'text:' + value if self.literal_text else 'exists'
        else:
            raise ValidationError('Неподдерживаемое событие записи')
        if len(self.steps) >= 100:
            self.pause()
            raise ValidationError('Лимит записи: 100 шагов')
        self.steps.append(Step(action, args))

    def finish(self, name):
        self.active = False
        return Plan(list(self.steps), name, list(self.parameters))

    def cancel(self):
        self.active = False
        self.steps.clear()
        self.parameters.clear()
