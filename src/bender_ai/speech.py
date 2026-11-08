"""Optional Windows SAPI speech and user-supplied WAV reactions. No bundled samples.

A separate spawn process is terminated by STOP so a stuck TTS driver cannot
freeze the assistant. Only text explicitly sent for speech crosses this boundary.
"""
import multiprocessing
import sys


MAX_SPEECH_CHARS = 600


def speech_text(value):
    if not isinstance(value, str):
        raise ValueError("Speech input must be text")
    # Never interpret model output as SAPI XML or commands.
    clean = "".join(ch for ch in value if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return clean.strip()[:MAX_SPEECH_CHARS]


def _speak_windows(text, result, clip_path=None):
    try:
        if sys.platform != "win32":
            raise RuntimeError("Голос доступен только в Windows")
        if clip_path:
            import winsound
            winsound.PlaySound(clip_path, winsound.SND_FILENAME | winsound.SND_NODEFAULT | winsound.SND_SYNC)
        if not text:
            result.send(("ok", ""))
            return
        import pythoncom
        pythoncom.CoInitialize()
        try:
            from win32com.client import Dispatch
            synth = Dispatch("SAPI.SpVoice")
            # Prefer an installed Russian SAPI voice; never fetch one silently.
            voices = synth.GetVoices()
            russian = None
            for index in range(voices.Count):
                voice = voices.Item(index)
                lang = voice.GetAttribute("Language") or ""
                if any(part.strip().lower().lstrip("0") == "419" for part in lang.split(";")):
                    russian = voice
                    break
            if russian is None:
                raise RuntimeError("В Windows не найден русский SAPI-голос")
            synth.Voice = russian
            synth.Rate = -1
            synth.Volume = 90
            # Speak plain text. The character's personality is in its text,
            # not in unlicensed samples of a copyrighted performance.
            synth.Speak(text)
            result.send(("ok", ""))
        finally:
            pythoncom.CoUninitialize()
    except Exception as exc:
        # Do not send exceptions that might contain the spoken text.
        reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        try:
            result.send(("error", reason))
        except (OSError, EOFError):
            pass
    finally:
        result.close()


class SpeechController:
    def __init__(self):
        self.process = None
        self.receiver = None

    def speak(self, text, clip_path=None):
        message = speech_text(text)
        self.stop()
        if not message and clip_path is None:
            return
        if sys.platform != "win32":
            raise RuntimeError("Озвучивание доступно только в Windows")
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        if clip_path is not None:
            from pathlib import Path
            path = Path(clip_path)
            if not path.is_absolute() or path.suffix.casefold() != ".wav":
                raise ValueError("Нужен абсолютный путь к WAV-файлу")
            clip_path = str(path)
        process = context.Process(target=_speak_windows, args=(message, sender, clip_path), daemon=True)
        try:
            process.start()
        except Exception:
            receiver.close()
            sender.close()
            raise
        sender.close()
        self.receiver, self.process = receiver, process

    def poll(self):
        if self.receiver is None:
            return None
        if self.receiver.poll():
            try:
                status, message = self.receiver.recv()
            except EOFError:
                status, message = "error", "Синтез речи завершился без результата"
            self._cleanup()
            return status, message
        if self.process and not self.process.is_alive():
            self._cleanup()
            return "error", "Процесс синтеза речи завершился неожиданно"
        return None

    def _cleanup(self):
        if self.process is not None:
            self.process.join(timeout=0.1)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=0.3)
            self.process.close()
        if self.receiver is not None:
            self.receiver.close()
        self.process = None
        self.receiver = None

    def stop(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=0.5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=0.5)
            self.process.close()
        if self.receiver is not None:
            self.receiver.close()
        self.process = None
        self.receiver = None
