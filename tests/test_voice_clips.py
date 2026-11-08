"""Local user clip registry: file boundaries, formats, matching and zero-config usage."""
import json
from pathlib import Path
import wave

import pytest

from bender_ai.voice_clips import ClipLibrary


def wav(path, frames=800, rate=8000):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * frames)


def test_simple_filename_triggers_reaction(tmp_path):
    greeting = tmp_path / "привет.wav"
    wav(greeting)
    clips = ClipLibrary.load(tmp_path)
    assert clips.match("Ну привет, кожаный!") == greeting.resolve()
    assert clips.match("Скажи приветы") is None
    assert clips.match("Пока") is None


def test_manifest_maps_many_keywords_and_uses_longest_first(tmp_path):
    wav(tmp_path / "bender_greeting.wav")
    wav(tmp_path / "short.wav")
    manifest = {
        "schema": 1,
        "clips": [
            {"file": "short.wav", "triggers": ["привет"]},
            {"file": "bender_greeting.wav", "triggers": ["большой привет", "здравствуй"]},
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    clips = ClipLibrary.load(tmp_path)
    assert clips.match("Большой привет тебе!") == (tmp_path / "bender_greeting.wav").resolve()
    assert clips.match("Здравствуй!") == (tmp_path / "bender_greeting.wav").resolve()


def test_manifest_rejects_path_escape(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema": 1, "clips": [{"file": "../secret.wav", "triggers": ["привет"]}]
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        ClipLibrary.load(tmp_path)


def test_manifest_rejects_empty_triggers(tmp_path):
    wav(tmp_path / "greet.wav")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema": 1, "clips": [{"file": "greet.wav", "triggers": []}]
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        ClipLibrary.load(tmp_path)


def test_damaged_wav_rejected(tmp_path):
    (tmp_path / "привет.wav").write_bytes(b"not audio")
    with pytest.raises(ValueError):
        ClipLibrary.load(tmp_path)


def test_empty_folder_rejected(tmp_path):
    with pytest.raises(ValueError):
        ClipLibrary.load(tmp_path)


def test_symlink_to_external_audio_rejected(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-voice.wav")
    wav(outside)
    try:
        (tmp_path / "привет.wav").symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlink unavailable in this environment")
    with pytest.raises(ValueError):
        ClipLibrary.load(tmp_path)
