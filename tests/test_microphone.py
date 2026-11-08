import json
import sys
from types import SimpleNamespace
import pytest
from bender_ai import microphone


def model_fixture(tmp_path):
    for name in ('am/final.mdl','conf/model.conf','graph/HCLG.fst'):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'synthetic')
    return tmp_path


def test_microphone_never_starts_without_explicit_consent(tmp_path, monkeypatch):
    monkeypatch.setattr(microphone,'microphone_status',lambda p:{'available':True,'reason':'ready'})
    controller=microphone.MicrophoneController()
    assert not controller.active
    with pytest.raises(PermissionError): controller.start(str(tmp_path))
    assert not controller.active
    controller.stop()


def test_model_selection_requires_real_local_model_files(tmp_path):
    state=microphone.microphone_status(str(tmp_path))
    assert not state['available'] and state['reason']


def test_local_recognition_worker_closes_stream_and_only_returns_preview_text(tmp_path,monkeypatch):
    model=model_fixture(tmp_path);events=[];closed=[]
    class Stream:
        def __init__(self,**kw):assert kw['channels']==1 and kw['dtype']=='int16'
        def __enter__(self):return self
        def __exit__(self,*a):closed.append(True)
        def read(self,n):return b'\x00'*n*2,False
    class Recognizer:
        def __init__(self,*a):pass
        def AcceptWaveform(self,data):return True
        def Result(self):return json.dumps({'text':'открой разрешённую папку'})
    monkeypatch.setitem(sys.modules,'sounddevice',SimpleNamespace(RawInputStream=Stream))
    monkeypatch.setitem(sys.modules,'vosk',SimpleNamespace(Model=lambda p:object(),KaldiRecognizer=Recognizer,SetLogLevel=lambda n:None))
    microphone._recognize_local(str(model),None,3,SimpleNamespace(send=events.append,close=lambda:None))
    assert events == [('listening',''),('text','открой разрешённую папку')]
    assert closed == [True]


def test_microphone_status_does_not_follow_required_model_file_outside_folder(tmp_path):
    model=model_fixture(tmp_path/'model');outside=tmp_path/'outside';outside.write_bytes(b'synthetic')
    (model/'am/final.mdl').unlink()
    try:(model/'am/final.mdl').symlink_to(outside)
    except OSError:pytest.skip('symlinks unavailable')
    assert not microphone.microphone_status(str(model))['available']


def test_backend_runtime_error_cannot_leak_model_path(tmp_path,monkeypatch):
    model=model_fixture(tmp_path);events=[]
    def fail(path):raise RuntimeError('private-secret-path-should-not-escape')
    monkeypatch.setitem(sys.modules,'vosk',SimpleNamespace(Model=fail,SetLogLevel=lambda n:None))
    monkeypatch.setitem(sys.modules,'sounddevice',SimpleNamespace())
    microphone._recognize_local(str(model),None,3,SimpleNamespace(send=events.append,close=lambda:None))
    assert events[0][0]=='error' and 'private-secret' not in events[0][1]
