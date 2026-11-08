"""Native voice dependency/STOP checks; no claim of audible speaker acceptance."""
import multiprocessing
import os
import sys
import time

import pytest

pytestmark = [pytest.mark.windows, pytest.mark.skipif(
    sys.platform != 'win32' or os.environ.get('BENDER_WINDOWS_INTEGRATION') != '1',
    reason='requires opt-in Windows integration')]


def test_native_sapi_inventory_and_speech_process_stop():
    from bender_ai.speech import SpeechController, list_voices

    voices = list_voices()
    assert isinstance(voices, list)
    for voice in voices:
        assert {'id', 'name', 'language', 'gender', 'russian'} <= voice.keys()
        assert isinstance(voice['russian'], bool)
    speaker = SpeechController()
    try:
        # Explicit opt-in test utterance; STOP is tested without waiting for playback.
        speaker.speak('Проверка остановки синтеза речи.', voice_id=voices[0]['id'] if voices else '')
        assert speaker.process is not None
        pid = speaker.process.pid
        started = time.monotonic()
        speaker.stop()
        assert time.monotonic() - started < 3
        assert speaker.process is None and speaker.poll() is None
        assert pid not in {process.pid for process in multiprocessing.active_children()}
        speaker.stop()
    finally:
        speaker.stop()


def test_native_voice_dependencies_and_missing_model_do_not_capture(tmp_path, monkeypatch):
    import sounddevice
    import vosk
    from bender_ai.microphone import MicrophoneController, microphone_status

    assert callable(vosk.Model)
    assert sounddevice.get_portaudio_version()[0] > 0
    def unexpected_capture(*args, **kwargs):
        raise AssertionError('Availability check opened an audio capture stream')
    monkeypatch.setattr(sounddevice, 'InputStream', unexpected_capture)
    monkeypatch.setattr(sounddevice, 'RawInputStream', unexpected_capture)
    missing = tmp_path / 'model-not-installed'
    status = microphone_status(missing)
    assert status['available'] is False
    assert isinstance(status['reason'], str) and status['reason']
    microphone = MicrophoneController()
    try:
        assert microphone.active is False
        microphone.stop()
        assert microphone.active is False
        # No start() call: availability inspection never authorizes capture.
    finally:
        microphone.stop()
