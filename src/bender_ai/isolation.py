"""Killable RPC boundary for unresponsive external COM providers."""
import multiprocessing
import threading
import time
from .security import Policy, Denied
from .models import OperationUnverified


class ChannelDisconnected(RuntimeError):
    """The worker exited before returning an operation outcome."""


class IsolatedChannel:
    def __init__(self, target, timeout=10):
        self.target, self.timeout = target, timeout
        self.process, self.connection = None, None
        self.lock = threading.Lock()

    def _start(self):
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        self.process = context.Process(target=self.target, args=(child,), daemon=True)
        self.process.start()
        child.close()
        self.connection = parent

    def call(self, request, cancellation=None):
        with self.lock:
            if cancellation:
                cancellation.check()
            if self.process is None or not self.process.is_alive():
                self.close()
                self._start()
            try:
                self.connection.send(request)
                end = time.monotonic() + self.timeout
                while not self.connection.poll(.025):
                    if cancellation:
                        cancellation.check()
                    if time.monotonic() >= end:
                        raise TimeoutError('UIA не ответила вовремя; изолированный процесс остановлен')
                    if not self.process.is_alive():
                        raise ChannelDisconnected('Процесс UIA завершился')
                result = self.connection.recv()
                if cancellation:
                    cancellation.check()
                return result
            except BaseException:
                self.close()
                raise

    def close(self):
        if self.process:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(1)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(1)
            self.process.close()
            self.process = None
        if self.connection:
            self.connection.close()
            self.connection = None


def windows_service(connection):
    from .windows import WindowsUIA, WindowsDesktop, AppLaunchError
    from .models import Selector
    from .explorer import WindowsExplorer, ExplorerPermissionRequired
    explorer = None
    methods = {'ui': {'click', 'set_text', 'exists', 'assert_state', 'focus', 'windows', 'snapshot', 'select', 'toggle', 'expand', 'window_exists', 'candidates'},
               'explorer': {'bind', 'snapshot', 'navigate', 'create_subfolder'},
               'desktop': {'open_app', 'open_approved_app', 'open_folder', 'active'}}
    while True:
        try:
            kind, method, args, policy_data = connection.recv()
        except EOFError:
            if explorer is not None:
                explorer.close()
            break
        try:
            if method not in methods.get(kind, set()):
                raise Denied('Неизвестный native-инструмент')
            policy = Policy(**policy_data)
            if kind == 'explorer':
                if explorer is None:
                    explorer = WindowsExplorer(policy)
                explorer.policy = policy
                target = explorer
            else:
                target = WindowsUIA(policy) if kind == 'ui' else WindowsDesktop(policy)
            if kind == 'desktop' and method == 'active':
                args = (policy,)
            value = getattr(target, method)(*args)
            connection.send(('ok', value))
        except ExplorerPermissionRequired as exc:
            connection.send(('permission_required', {'mode': exc.mode, 'path': exc.path}))
        except OperationUnverified:
            connection.send(('unverified', 'Действие отправлено, но результат не подтверждён; '
                             'автоматический повтор не выполнялся.'))
        except Exception as exc:
            # UI errors can contain private text. Return class only, plus trusted policy errors.
            connection.send(('error', str(exc) if isinstance(exc, (Denied, AppLaunchError)) else type(exc).__name__))


class WindowsBridge:
    def __init__(self, policy):
        self.policy, self.channel = policy, IsolatedChannel(windows_service, timeout=12)
        self.explorer = _RemoteAdapter(self, 'explorer')
        self.ui = _RemoteAdapter(self, 'ui')
        self.desktop = _RemoteAdapter(self, 'desktop')

    def close(self):
        self.channel.close()


class _RemoteAdapter:
    def __init__(self, bridge, kind):
        self.bridge, self.kind, self.cancellation = bridge, kind, None

    def __getattr__(self, name):
        def call(*args):
            # active() receives a Policy from ActionRegistry; never pickle it from this argument.
            if name == 'active':
                args = ()
            status, value = self.bridge.channel.call((self.kind, name, args, self.bridge.policy.to_dict()), self.cancellation)
            if status == 'permission_required':
                from .explorer import ExplorerPermissionRequired
                raise ExplorerPermissionRequired(value['mode'], value['path'])
            if status == 'unverified':
                raise OperationUnverified(value)
            if status != 'ok':
                raise Denied('Windows-адаптер: ' + value)
            return value
        return call
