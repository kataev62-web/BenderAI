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
