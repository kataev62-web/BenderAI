"""Test-only native fixture launch at medium integrity.

Never changes production UIA policy. Prefer a genuine linked non-elevated token;
if the runner has no linked logon session, create and verify a restricted token.
Windows-only implementation: exercise on native CI before relying on this helper.
"""
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys


def _verify_medium_token(token, reference):
    import win32security
    if win32security.GetTokenInformation(token, win32security.TokenElevation):
        raise RuntimeError('Fixture token is elevated')
    integrity = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
    sid = integrity[0] if isinstance(integrity, tuple) else integrity
    if sid.GetSubAuthority(sid.GetSubAuthorityCount() - 1) != 0x2000:
        raise RuntimeError('Fixture token must have medium integrity')
    administrators = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None)
    for group, attributes in win32security.GetTokenInformation(token, win32security.TokenGroups):
        if str(group) == str(administrators) and attributes & 0x00000004:  # SE_GROUP_ENABLED
            raise RuntimeError('Fixture token must not enable the administrators group')
    for information in (win32security.TokenUser, win32security.TokenSessionId):
        actual = win32security.GetTokenInformation(token, information)
        expected = win32security.GetTokenInformation(reference, information)
        if information == win32security.TokenUser:
            actual, expected = str(actual[0]), str(expected[0])
        if actual != expected:
            raise RuntimeError('Fixture token must retain the test user and session')


def _fixture_token(current):
    """Prefer linked token; only WinError 1312 permits documented LUA fallback."""
    import win32security
    restricted = False
    try:
        token = win32security.GetTokenInformation(current, win32security.TokenLinkedToken)
    except Exception as exc:
        code = getattr(exc, 'winerror', 0)
        if code != 1312:  # ERROR_NO_SUCH_LOGON_SESSION, observed on UAC-disabled CI
            raise RuntimeError('Elevated test runner has no usable linked token: ' + str(code)) from None
        administrators = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None)
        # Documented DISABLE_MAX_PRIVILEGE | LUA_TOKEN. Never SANDBOX_INERT.
        token = win32security.CreateRestrictedToken(current, 0x00000005,
                                                    [(administrators, 0)], None, None)
        restricted = True
    try:
        if restricted:
            medium = win32security.ConvertStringSidToSid('S-1-16-8192')
            win32security.SetTokenInformation(token, win32security.TokenIntegrityLevel,
                                             (medium, 0x00000020))  # SE_GROUP_INTEGRITY
        _verify_medium_token(token, current)
        return token, restricted
    except BaseException:
        token.Close()
        raise


class _NativeProcess:
    def __init__(self, handle, pid, args):
        self._handle, self.pid, self.args = handle, pid, args
        self.returncode = None

    def poll(self):
        import win32event
        import win32process
        if self.returncode is None and win32event.WaitForSingleObject(self._handle, 0) == 0:
            self.returncode = win32process.GetExitCodeProcess(self._handle)
        return self.returncode

    def wait(self, timeout=None):
        import win32event
        milliseconds = win32event.INFINITE if timeout is None else max(0, int(timeout * 1000))
        result = win32event.WaitForSingleObject(self._handle, milliseconds)
        if result == win32event.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != win32event.WAIT_OBJECT_0:
            raise RuntimeError('Fixture process wait failed: ' + str(result))
        return self.poll()

    def terminate(self):
        import win32api
        if self.poll() is None:
            win32api.TerminateProcess(self._handle, 1)

    kill = terminate

    def __del__(self):
        try:
            import win32api
            win32api.CloseHandle(self._handle)
        except Exception:
            pass


def _spawn_restricted_fixture(token, args):
    """Documented launch API for a restricted copy of the caller's own token."""
    import win32api
    import win32process
    start = win32process.STARTUPINFO()
    start.lpDesktop = 'winsta0\\default'
    try:
        process, thread, pid, _ = win32process.CreateProcessAsUser(
            token, args[0], subprocess.list2cmdline(args), None, None, False,
            0, None, os.getcwd(), start)
    except Exception as exc:
        raise RuntimeError('CreateProcessAsUser failed: ' +
                           str(getattr(exc, 'winerror', 0))) from None
    win32api.CloseHandle(thread)
    return _NativeProcess(process, pid, args)


def spawn_fixture(args):
    """Popen-like fixture spawn; require an absolute executable."""
    if sys.platform != 'win32':
        raise RuntimeError('Native fixture launcher requires Windows')
    if not args or not os.path.isabs(args[0]):
        raise ValueError('Fixture executable must be absolute')
    import win32api
    import win32security
    # Restricted tokens inherit handle rights: QUERY|DUPLICATE|ASSIGN_PRIMARY|
    # ADJUST_DEFAULT are needed for verification, integrity setting and launch.
    # These are handle access rights, not privilege activation.
    current = win32security.OpenProcessToken(win32api.GetCurrentProcess(), 0x008B)
    linked = None
    try:
        if not win32security.GetTokenInformation(current, win32security.TokenElevation):
            _verify_medium_token(current, current)
            return subprocess.Popen(args)
        linked, restricted = _fixture_token(current)
        if restricted:
            return _spawn_restricted_fixture(linked, args)

        class StartupInfo(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR),
                        ('lpDesktop', wintypes.LPWSTR), ('lpTitle', wintypes.LPWSTR),
                        ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD),
                        ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD),
                        ('dwXCountChars', wintypes.DWORD), ('dwYCountChars', wintypes.DWORD),
                        ('dwFillAttribute', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                        ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
                        ('lpReserved2', ctypes.POINTER(wintypes.BYTE)),
                        ('hStdInput', wintypes.HANDLE), ('hStdOutput', wintypes.HANDLE),
                        ('hStdError', wintypes.HANDLE)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE),
                        ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]

        start, process = StartupInfo(), ProcessInfo()
        start.cb = ctypes.sizeof(start)
        start.lpDesktop = 'winsta0\\default'
        api = ctypes.WinDLL('advapi32', use_last_error=True).CreateProcessWithTokenW
        api.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPCWSTR,
                        wintypes.LPWSTR, wintypes.DWORD, wintypes.LPVOID,
                        wintypes.LPCWSTR, ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)]
        api.restype = wintypes.BOOL
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
        if not api(int(linked), 0, args[0], command, 0, None, os.getcwd(),
                   ctypes.byref(start), ctypes.byref(process)):
            # Numeric Windows error only; command arguments can contain test data.
            raise RuntimeError('CreateProcessWithTokenW failed: ' + str(ctypes.get_last_error()))
        win32api.CloseHandle(process.hThread)
        return _NativeProcess(process.hProcess, process.dwProcessId, args)
    finally:
        if linked is not None:
            linked.Close()
        current.Close()
