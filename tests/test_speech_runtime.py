import sys
from types import SimpleNamespace
from bender_ai import speech


def test_sapi_uses_selected_russian_voice_plain_text_and_observed_talk_state(monkeypatch):
    calls=[];events=[]
    voice=SimpleNamespace(Id='ru-male',GetDescription=lambda:'Local Russian',GetAttribute=lambda key: {'Language':'419','Gender':'Male'}[key])
    class Synth:
        Status=SimpleNamespace(RunningState=2)
        def GetVoices(self): return SimpleNamespace(Count=1,Item=lambda n:voice)
        def Speak(self,text,flags):calls.append((text,flags))
        def WaitUntilDone(self,ms):return True
    synth=Synth()
    monkeypatch.setattr(speech.sys,'platform','win32')
    monkeypatch.setitem(sys.modules,'pythoncom',SimpleNamespace(CoInitialize=lambda:None,CoUninitialize=lambda:None))
    monkeypatch.setitem(sys.modules,'win32com',SimpleNamespace())
    monkeypatch.setitem(sys.modules,'win32com.client',SimpleNamespace(Dispatch=lambda name:synth))
    speech._speak_windows('<speak>ordinary text</speak>',SimpleNamespace(send=events.append,close=lambda:None),'ru-male',-2)
    assert calls==[('<speak>ordinary text</speak>',17)]
    assert events==[('started',''),('ok','')]
    assert synth.Voice is voice and synth.Rate == -2


def test_missing_russian_voice_does_not_silently_speak_another_language(monkeypatch):
    events=[]
    voice=SimpleNamespace(Id='en',GetDescription=lambda:'English',GetAttribute=lambda k:'409' if k=='Language' else 'Male')
    synth=SimpleNamespace(GetVoices=lambda:SimpleNamespace(Count=1,Item=lambda n:voice))
    monkeypatch.setattr(speech.sys,'platform','win32')
    monkeypatch.setitem(sys.modules,'pythoncom',SimpleNamespace(CoInitialize=lambda:None,CoUninitialize=lambda:None))
    monkeypatch.setitem(sys.modules,'win32com',SimpleNamespace())
    monkeypatch.setitem(sys.modules,'win32com.client',SimpleNamespace(Dispatch=lambda name:synth))
    speech._speak_windows('Привет',SimpleNamespace(send=events.append,close=lambda:None))
    assert events[0][0]=='error' and 'русск' in events[0][1].casefold()
    assert not any(e[0]=='started' for e in events)
