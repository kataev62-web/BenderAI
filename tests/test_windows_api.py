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

    names = {10: 'ApplicationFrameHost.exe', 11: 'CalculatorApp.exe', 12: 'Other.exe'}
    monkeypatch.setattr(windows, 'process_name', lambda pid: names[pid])

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
