"""Windows API checks that do not require an interactive desktop."""
import os
from pathlib import Path
import sys
import pytest
from bender_ai.windows import process_name


@pytest.mark.skipif(sys.platform != 'win32', reason='requires Windows process metadata API')
def test_process_metadata_uses_supported_windows_api():
    assert process_name(os.getpid()).casefold() == Path(sys.executable).name.casefold()
    with pytest.raises(OSError):
        process_name(0)


def test_calculator_packaged_window_process_detection(monkeypatch):
    from types import SimpleNamespace
    import bender_ai.windows as windows

    import ntpath
    paths = {10: r'C:\Windows\System32\ApplicationFrameHost.exe',
             11: r'C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_1.0_x64__8wekyb3d8bbwe\CalculatorApp.exe',
             12: r'C:\Other\Other.exe'}
    monkeypatch.setenv('WINDIR', r'C:\Windows')
    monkeypatch.setattr(windows, 'process_name', lambda pid: ntpath.basename(paths[pid]))
    monkeypatch.setattr(windows, 'process_path', lambda pid: paths[pid])
    monkeypatch.setattr(windows, 'process_package_family', lambda pid:
                        'Microsoft.WindowsCalculator_8wekyb3d8bbwe' if pid == 11 else '', raising=False)

    class Window:
        def __init__(self, root_pid, children=()):
            self.root_pid, self.children = root_pid, children

        def process_id(self):
            return self.root_pid

        def descendants(self):
            return [SimpleNamespace(element_info=SimpleNamespace(process_id=pid)) for pid in self.children]

    assert windows.window_owned_by_app(Window(11), ('calculatorapp.exe', 'calculator.exe'))
    assert windows.window_owned_by_app(Window(10, (12, 11)), ('calculatorapp.exe', 'calculator.exe'))
    assert not windows.window_owned_by_app(Window(10, (12,)), ('calculatorapp.exe', 'calculator.exe'))
    assert not windows.window_owned_by_app(Window(10, (11,)), ('notepad.exe',))
    assert not windows.window_owned_by_app(Window(12), ('calculatorapp.exe', 'calculator.exe'))


@pytest.mark.parametrize('path,family,expected,accepted', [
    (r'C:\Other\notepad.exe', '', ('notepad.exe',), False),
    (r'C:\Windows\System32\notepad.exe', '', ('notepad.exe',), True),
    (r'C:\Program Files\WindowsApps\FakePackage\CalculatorApp.exe', '', ('calculatorapp.exe',), False),
    (r'C:\Program Files\WindowsApps\Microsoft.WindowsCalculator\CalculatorApp.exe',
     'Microsoft.WindowsCalculator_8wekyb3d8bbwe', ('calculatorapp.exe',), True),
    (r'C:\Program Files\WindowsApps\FakePackage\notepad.exe',
     'Untrusted.Notepad_123', ('notepad.exe',), False),
])
def test_builtin_identity_requires_system_path_or_os_package_family(monkeypatch, path, family, expected, accepted):
    import ntpath
    from types import SimpleNamespace
    from bender_ai import windows
    monkeypatch.setenv('WINDIR', r'C:\Windows')
    monkeypatch.setattr(windows,'process_name',lambda pid:ntpath.basename(path))
    monkeypatch.setattr(windows,'process_path',lambda pid:path)
    monkeypatch.setattr(windows,'process_package_family',lambda pid:family,raising=False)
    root = SimpleNamespace(process_id=lambda:7,descendants=lambda:[])
    assert windows.window_owned_by_app(root,expected) is accepted


def test_untrusted_application_frame_host_cannot_vouch_for_trusted_child(monkeypatch):
    from types import SimpleNamespace
    from bender_ai import windows
    monkeypatch.setenv('WINDIR',r'C:\Windows')
    monkeypatch.setattr(windows,'process_name',lambda pid:'ApplicationFrameHost.exe' if pid == 7 else 'CalculatorApp.exe')
    monkeypatch.setattr(windows,'process_path',lambda pid:r'C:\Other\ApplicationFrameHost.exe' if pid == 7 else r'C:\Apps\CalculatorApp.exe')
    monkeypatch.setattr(windows,'process_package_family',lambda pid:'Microsoft.WindowsCalculator_8wekyb3d8bbwe',raising=False)
    root = SimpleNamespace(process_id=lambda:7,descendants=lambda:[SimpleNamespace(element_info=SimpleNamespace(process_id=8))])
    assert not windows.window_owned_by_app(root,('calculatorapp.exe',))


@pytest.mark.parametrize('packaged', [False, True])
def test_process_package_identity_uses_os_query_and_closes_handle(monkeypatch, packaged):
    from types import SimpleNamespace
    from bender_ai import windows
    closed = []
    family = 'Microsoft.WindowsCalculator_8wekyb3d8bbwe'
    def query(handle, length, buffer):
        assert handle == 42
        if not packaged:
            return 15700
        if buffer is None:
            length._obj.value = len(family) + 1
            return 122
        buffer.value = family
        return 0
    kernel = SimpleNamespace(OpenProcess=lambda *args:42,CloseHandle=lambda handle:closed.append(handle),
                             GetPackageFamilyName=query)
    monkeypatch.setattr(windows,'require_windows',lambda:None)
    monkeypatch.setattr(windows.ctypes,'WinDLL',lambda *args,**kwargs:kernel,raising=False)
    assert windows.process_package_family(7) == (family if packaged else '')
    assert closed == [42]


@pytest.mark.parametrize('window_class,accepted', [
    ('Shell_TrayWnd', False), ('Progman', False), ('WorkerW', False),
    ('CabinetWClass', True), ('ExploreWClass', True),
])
def test_explorer_verification_requires_file_browser_window(monkeypatch, window_class, accepted):
    from types import SimpleNamespace
    from bender_ai import windows
    monkeypatch.setenv('WINDIR',r'C:\Windows')
    monkeypatch.setattr(windows,'process_path',lambda pid:r'C:\Windows\explorer.exe')
    root = SimpleNamespace(process_id=lambda:7,class_name=lambda:window_class,
                           descendants=lambda:[])
    assert windows.window_owned_by_app(root,('explorer.exe',)) is accepted
