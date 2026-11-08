from types import SimpleNamespace
from bender_ai.explorer import ExplorerPermissionRequired
from bender_ai.infrastructure import CaptureTask
from bender_ai.learning import Recorder


def test_capture_waits_for_explicit_missing_permission_before_retry(tmp_path):
    recorder=Recorder('explorer.exe','Test');recorder.start()
    calls=[]
    class Capture:
        ready=False
        def __init__(self):self.recorder=recorder
        def poll(self):
            calls.append('poll')
            if len(calls)==1:raise ExplorerPermissionRequired('read',tmp_path)
            assert not recorder.paused
            task.stop_event.set()
        def close(self):calls.append('closed')
    task=CaptureTask(Capture())
    def approve(mode,path):
        assert recorder.paused and calls==['poll']
        assert mode=='read' and path==str(tmp_path)
        recorder.resume();task.resolve_permission(True)
    task.permission_required.connect(approve)
    task.run()
    assert calls==['poll','poll','closed']


def test_rejected_capture_permission_keeps_recording_paused(tmp_path):
    recorder=Recorder('explorer.exe','Test');recorder.start()
    failures=[];closed=[]
    class Capture:
        ready=False
        def __init__(self):self.recorder=recorder
        def poll(self):raise ExplorerPermissionRequired('read',tmp_path)
        def close(self):closed.append(True)
    task=CaptureTask(Capture())
    task.permission_required.connect(lambda *a:task.resolve_permission(False))
    task.failure.connect(failures.append)
    task.run()
    assert recorder.paused and recorder.steps==[] and failures and closed


def test_stop_while_waiting_never_retries_native_access(tmp_path):
    recorder=Recorder('explorer.exe','Test');recorder.start()
    calls=[]
    class Capture:
        ready=False
        def __init__(self):self.recorder=recorder
        def poll(self):calls.append('poll');raise ExplorerPermissionRequired('read',tmp_path)
        def close(self):calls.append('closed')
    task=CaptureTask(Capture())
    task.permission_required.connect(lambda *a:task.stop_event.set())
    task.run()
    assert calls==['poll','closed'] and recorder.paused and not task.waiting_permission
