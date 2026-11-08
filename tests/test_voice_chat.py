"""Regression checks for local conversational mode and safe voice input."""
import pytest

from bender_ai.brain import OllamaProvider
from bender_ai.models import ValidationError
from bender_ai.speech import MAX_SPEECH_CHARS, SpeechController, speech_text


def test_conversation_is_text_not_executable_plan(monkeypatch):
    captured = {}

    def fake_request(url, payload, timeout):
        captured.update(url=url, payload=payload, timeout=timeout)
        return {"message": {"content": "Кожаный, привет! Никаких команд без разрешения."}}

    monkeypatch.setattr("bender_ai.brain.request_json", fake_request)
    answer = OllamaProvider("qwen2.5-coder:3b").converse("Привет!")
    assert answer.startswith("Кожаный")
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["payload"]["think"] is False
    assert "format" not in captured["payload"]  # no forced JSON action schema
    assert len(captured["payload"]["messages"]) == 2
    assert captured["payload"]["messages"][-1]["content"] == "Привет!"


@pytest.mark.parametrize("bad", ["", "  ", "\x00bad", "А" * 2001, 42])
def test_conversation_rejects_invalid_text(bad):
    with pytest.raises(ValidationError):
        OllamaProvider().converse(bad)


@pytest.mark.parametrize("data", [None, {}, {"message": {}}, {"message": {"content": "   "}}])
def test_conversation_rejects_invalid_reply(monkeypatch, data):
    monkeypatch.setattr("bender_ai.brain.request_json", lambda *a: data)
    with pytest.raises(ValidationError):
        OllamaProvider().converse("Привет")


def test_speech_normalizes_and_bounds_untrusted_text():
    text = "Привет\x00\x01\n" + "Ж" * 900
    safe = speech_text(text)
    assert "\x00" not in safe and "\x01" not in safe
    assert safe.startswith("Привет\n")
    assert len(safe) == MAX_SPEECH_CHARS
    assert speech_text("   ") == ""


def test_speech_rejects_non_text():
    with pytest.raises(ValueError):
        speech_text({"command": "open"})


def test_stop_without_started_speech_is_noop():
    speaker = SpeechController()
    speaker.stop()
    assert speaker.poll() is None
