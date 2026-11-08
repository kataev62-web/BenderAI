"""Local SAPI synthesis; selected installed voices, no samples or voice cloning."""
import multiprocessing
import sys
import time

MAX_SPEECH_CHARS = 600


class SpeechError(RuntimeError):
    """Predefined safe synthesis diagnostic."""


def speech_text(value):
    if not isinstance(value, str):
        raise ValueError('Speech input must be text')
    return ''.join(ch for ch in value if ch in '\n\t' or ord(ch) >= 32).strip()[:MAX_SPEECH_CHARS]


def _voice_rows(synth):
    voices = synth.GetVoices()
    result = []
    for index in range(min(voices.Count, 100)):
        voice = voices.Item(index)
        def attribute(key):
            try: return str(voice.GetAttribute(key) or '')
            except Exception: return ''
        language = attribute('Language')
        result.append((voice, {'id': str(voice.Id)[:1024], 'name': str(voice.GetDescription())[:200],
                              'language': language[:80], 'gender': attribute('Gender')[:40],
                              'russian': any(x.strip().lower().lstrip('0') == '419' for x in language.split(';'))}))
    return result


def _voices_service(connection):
    initialized = False
    try:
        connection.recv()
        if sys.platform != 'win32':
            connection.send(('ok', [])); return
        import pythoncom
        pythoncom.CoInitialize(); initialized = True
        from win32com.client import Dispatch
        connection.send(('ok', [row for _, row in _voice_rows(Dispatch('SAPI.SpVoice'))]))
    except Exception:
        connection.send(('error', 'Не удалось получить установленные голоса Windows SAPI'))
    finally:
        if initialized: pythoncom.CoUninitialize()
        connection.close()


def list_voices():
    """Bounded enumeration, never opens the microphone or downloads a model."""
    if sys.platform != 'win32': return []
    from .isolation import IsolatedChannel
    channel = IsolatedChannel(_voices_service, timeout=5)
    try:
        status, value = channel.call('list')
        if status != 'ok': raise SpeechError(value)
        return value
    finally:
        channel.close()


def _speak_windows(text, result, voice_id='', rate=-1):
    initialized = False
    try:
        if sys.platform != 'win32': raise SpeechError('Голос доступен только в Windows')
        import pythoncom
        pythoncom.CoInitialize(); initialized = True
        from win32com.client import Dispatch
        synth = Dispatch('SAPI.SpVoice')
        rows = _voice_rows(synth)
        if voice_id:
            choices = [(v, r) for v, r in rows if r['id'] == voice_id]
            if not choices: raise SpeechError('Выбранный голос больше не установлен; выберите другой')
        else:
            choices = sorted([(v, r) for v, r in rows if r['russian']],
                             key=lambda pair: pair[1]['gender'].casefold() != 'male')
            if not choices:
                raise SpeechError('Русский SAPI-голос отсутствует. Ответ остаётся текстом; установите русский голос Windows или явно выберите доступный голос в настройках')
        synth.Voice = choices[0][0]
        synth.Rate = max(-3, min(3, int(rate)))
        synth.Volume = 90
        # Async + SVSFIsNotXML: user/model text can never inject SAPI markup.
        synth.Speak(speech_text(text), 17)
        started = False
        deadline = time.monotonic() + 90
        while True:
            if not started and synth.Status.RunningState == 2:
                result.send(('started', '')); started = True
            if synth.WaitUntilDone(50): break
            if time.monotonic() > deadline: raise SpeechError('Синтез речи превысил лимит времени')
        result.send(('ok', ''))
    except Exception as exc:
        reason = str(exc) if isinstance(exc, SpeechError) else 'Не удалось воспроизвести речь через Windows SAPI'
        try: result.send(('error', reason))
        except (OSError, EOFError): pass
    finally:
        if initialized: pythoncom.CoUninitialize()
        result.close()


class SpeechController:
    def __init__(self):
        self.process = self.receiver = None
        self.started = 0.0

    @property
    def active(self): return self.process is not None

    def speak(self, text, voice_id='', rate=-1):
        message = speech_text(text)
        if not isinstance(voice_id, str) or len(voice_id) > 1024 or type(rate) is not int or not -3 <= rate <= 3:
            raise ValueError('Некорректные настройки голоса')
        self.stop()
        if not message: return
        if sys.platform != 'win32': raise SpeechError('Озвучивание доступно только в Windows')
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_speak_windows, args=(message, sender, voice_id, rate), daemon=True)
        try: process.start()
        except Exception:
            receiver.close(); sender.close(); raise
        sender.close()
        self.receiver, self.process = receiver, process
        self.started = time.monotonic()

    def poll(self):
        if self.receiver is None: return None
        if self.receiver.poll():
            try: status, message = self.receiver.recv()
            except EOFError: status, message = 'error', 'Синтез речи завершился без результата'
            if status != 'started': self.stop()
            return status, message
        if self.process and (not self.process.is_alive() or time.monotonic()-self.started > 95):
            self.stop()
            return 'error', 'Процесс синтеза речи завершился или превысил лимит времени'
        return None

    def stop(self):
        if self.process is not None:
            if self.process.is_alive(): self.process.terminate()
            self.process.join(timeout=.3)
            if self.process.is_alive():
                self.process.kill(); self.process.join(timeout=.3)
            self.process.close()
        if self.receiver is not None: self.receiver.close()
        self.process = self.receiver = None
