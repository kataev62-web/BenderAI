"""User-supplied local WAV reactions. Never downloads, embeds or ships TV audio."""
from pathlib import Path
import json
import re
import wave

MAX_CLIPS = 80
MAX_WAV_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024


def _trigger(value):
    if not isinstance(value, str):
        raise ValueError("Ключевое слово должно быть строкой")
    text = " ".join(value.strip().casefold().replace("_", " ").split())
    if not (2 <= len(text) <= 60) or any(ch in text for ch in "/\\<>|*?"):
        raise ValueError("Недопустимое ключевое слово для звуковой фразы")
    return text


def _safe_wav(folder, name):
    if not isinstance(name, str) or len(name) > 100 or name.startswith(".") or Path(name).name != name:
        raise ValueError("Некорректное имя WAV-файла")
    path = folder / name
    if path.suffix.lower() != ".wav" or path.is_symlink() or not path.is_file():
        raise ValueError("Разрешены только локальные WAV-файлы без ссылок")
    path = path.resolve()
    if not path.is_relative_to(folder) or path.stat().st_size > MAX_WAV_BYTES:
        raise ValueError("WAV-файл слишком большой или вне выбранной папки")
    try:
        with wave.open(str(path), "rb") as wav:
            if (wav.getcomptype() != "NONE" or wav.getnchannels() not in (1, 2)
                    or wav.getsampwidth() not in (1, 2)
                    or not 8000 <= wav.getframerate() <= 48000
                    or not 0 < wav.getnframes() <= wav.getframerate() * 20):
                raise ValueError("Нужен PCM WAV 8/16 бит, до 20 секунд")
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError("Некорректный или неподдерживаемый WAV-файл") from exc
    return path


class ClipLibrary:
    """Matches explicit, locally configured reaction triggers to validated files."""

    def __init__(self, clips):
        self.clips = tuple(clips)

    @classmethod
    def load(cls, raw_folder):
        folder = Path(raw_folder).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError("Папка звуковых реплик отсутствует")
        manifest = folder / "manifest.json"
        entries = []
        if manifest.is_file():
            if manifest.is_symlink() or manifest.stat().st_size > MAX_MANIFEST_BYTES:
                raise ValueError("Небезопасный или слишком большой manifest.json")
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("Не удалось прочитать manifest.json") from exc
            if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("clips"), list):
                raise ValueError("Ожидается manifest.json со schema=1 и массивом clips")
            if len(data["clips"]) > MAX_CLIPS:
                raise ValueError("Слишком много звуковых фраз")
            for item in data["clips"]:
                if not isinstance(item, dict) or set(item) != {"file", "triggers"}:
                    raise ValueError("Неверное описание фразы")
                keys = item["triggers"]
                if not isinstance(keys, list) or not 1 <= len(keys) <= 12:
                    raise ValueError("Для WAV требуется от 1 до 12 ключевых слов")
                entries.append((item["file"], keys))
        else:
            # Zero-config option: "привет.wav" matches a greeting in an AI reply.
            wavs = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".wav")
            if len(wavs) > MAX_CLIPS:
                raise ValueError("В папке слишком много WAV-файлов")
            for path in wavs:
                entries.append((path.name, [path.stem.split("__", 1)[0]]))
        if not entries:
            raise ValueError("В папке нет звуковых фраз WAV")
        clips = []
        for filename, words in entries:
            path = _safe_wav(folder, filename)
            for word in words:
                clips.append((_trigger(word), path))
        clips.sort(key=lambda item: -len(item[0]))
        return cls(clips)

    def match(self, reply):
        if not isinstance(reply, str) or len(reply) > 4000:
            return None
        value = " ".join(reply.casefold().split())
        for trigger, path in self.clips:
            if re.search(r"(?<!\w)" + re.escape(trigger) + r"(?!\w)", value):
                return path
        return None

    def first(self):
        return self.clips[0][1] if self.clips else None
