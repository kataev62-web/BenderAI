"""Native Win32 fixture: stable numeric control IDs and standard UIA providers."""
import ctypes
from ctypes import wintypes
import sys

TITLE = 'BENDER AI Training'


def main(title=TITLE):
    if sys.platform != 'win32':
        raise SystemExit('Тестовая UIA-форма требует Windows')
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    class WNDCLASS(ctypes.Structure):
        _fields_ = [('style', wintypes.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                    ('cbWndExtra', ctypes.c_int), ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
                    ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HBRUSH),
                    ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]
    user.CreateWindowExW.restype = wintypes.HWND
    user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user.DefWindowProcW.restype = LRESULT
    user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
    user.DestroyWindow.argtypes = [wintypes.HWND]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    kernel.GetModuleHandleW.restype = wintypes.HMODULE
    name, submit, status, password = None, None, None, None
    shifted = False
    @WNDPROC
    def proc(hwnd, msg, wp, lp):
        nonlocal shifted
        if msg == 0x111 and (wp & 0xffff) == 1002 and (wp >> 16) == 0:
            text = ctypes.create_unicode_buffer(1024)
            user.GetWindowTextW(name, text, 1024)
            user.SetWindowTextW(status, 'Hello ' + text.value)
            return 0
        if msg == 0x111 and (wp & 0xffff) == 1005:
            shifted = not shifted
            user.MoveWindow(submit, 300 if shifted else 30, 150, 180, 35, True)
            return 0
        if msg == 0x10:
            user.DestroyWindow(hwnd)
            return 0
        if msg == 2:
            user.PostQuitMessage(0)
            return 0
        return user.DefWindowProcW(hwnd, msg, wp, lp)
    instance = kernel.GetModuleHandleW(None)
    wc = WNDCLASS(0, proc, 0, 0, instance, None, None, 6, None, 'BenderTrainingFixture')
    if not user.RegisterClassW(ctypes.byref(wc)):
        raise ctypes.WinError(ctypes.get_last_error())
    hwnd = user.CreateWindowExW(0, wc.lpszClassName, title, 0x00CF0000, 100, 100, 560, 440, None, None, instance, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    def control(cls, text, ident, x, y, w, h, style=0):
        return user.CreateWindowExW(0, cls, text, 0x50000000 | style, x, y, w, h, hwnd, ident, instance, None)
    control('STATIC', 'Name (non-secret):', 1010, 30, 20, 300, 25)
    name = control('EDIT', '', 1001, 30, 50, 360, 28, 0x00800080)
    control('STATIC', 'Password (never recorded):', 1011, 30, 85, 300, 25)
    password = control('EDIT', '', 1004, 30, 110, 360, 28, 0x008000A0)
    submit = control('BUTTON', 'Greet', 1002, 30, 150, 180, 35)
    control('BUTTON', 'Move button', 1005, 30, 195, 180, 30)
    status = control('STATIC', 'Ready', 1003, 30, 235, 470, 30)
    control('BUTTON', 'Enable notifications', 1007, 30, 275, 230, 30, 0x00000003)
    user.ShowWindow(hwnd, 5)
    message = wintypes.MSG()
    while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user.TranslateMessage(ctypes.byref(message))
        user.DispatchMessageW(ctypes.byref(message))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--title', default=TITLE)
    main(parser.parse_args().title)
