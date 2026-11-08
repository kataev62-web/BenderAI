"""Optional local push-to-talk. No device opens before explicit consent.

Vosk models remain user-installed data. Neither audio nor partial text is saved.
A separate process owns the stream so STOP also interrupts a blocked audio driver.
"""
import importlib.util
import json
import multiprocessing
from pathlib import Path
import time


class RecordingError(RuntimeError):
    """Predefined safe diagnostic, never a backend exception body."""


def _model_path(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError('Выберите локальную русскую модель Vosk; автоматической загрузки нет')
    root = Path(value).expanduser().resolve()
    if not root.is_dir(): raise ValueError('Папка модели Vosk не найдена')
    files = ['am/final.mdl','conf/model.conf']
    if (root/'graph/HCLG.fst').is_file(): files += ['graph/HCLG.fst']
    else: files += ['graph/Gr.fst','graph/HCLr.fst']
    for name in files:
        file = root/name
        if not file.resolve().is_relative_to(root) or not file.is_file() or not 0 < file.stat().st_size < 2**31:
            raise ValueError('Выбрана неполная или небезопасная папка модели Vosk')
    return root


def microphone_status(model_path=''):
    try: _model_path(model_path)
    except (ValueError, OSError):
        return {'available':False,'reason':'Выберите распакованную локальную русскую модель Vosk (am, conf, graph). Модели не загружаются автоматически'}
    try:
        dependencies = all(importlib.util.find_spec(name) is not None for name in ('vosk','sounddevice'))
    except (ImportError, ValueError): dependencies = False
    return {'available':dependencies,'reason': 'Готово к записи по кнопке; микрофон не открыт' if dependencies else
            'Локальное распознавание требует дополнительных пакетов Vosk и sounddevice (voice)'}


def _recognize_local(model_path, device, timeout, result):
    try:
        root = _model_path(model_path)
        import vosk
        import sounddevice
        vosk.SetLogLevel(-1)
        recognizer = vosk.KaldiRecognizer(vosk.Model(str(root)),16000)
        with sounddevice.RawInputStream(samplerate=16000,blocksize=1200,device=device,channels=1,dtype='int16') as stream:
            result.send(('listening',''))
            deadline = time.monotonic()+timeout
            value = None
            while time.monotonic() < deadline:
                data, overflowed = stream.read(1200)
                if overflowed: raise RecordingError('Микрофон не успевает передать звук; попробуйте ещё раз')
                if recognizer.AcceptWaveform(bytes(data)):
                    value = recognizer.Result(); break
            if value is None: value = recognizer.FinalResult()
        parsed = json.loads(value)
        text = parsed.get('text') if isinstance(parsed, dict) else None
        if not isinstance(text,str) or not text.strip():
            raise RecordingError('Речь не распознана; проверьте микрофон и русскую модель')
        text = ''.join(ch for ch in text if ch in '\t\n' or ord(ch)>=32).strip()[:2000]
        if not text: raise RecordingError('Речь не распознана')
        result.send(('text',text))
    except Exception as exc:
        # Device/model exceptions may include private paths; show only fixed guidance.
        message = str(exc) if isinstance(exc,RecordingError) else 'Локальное распознавание недоступно: проверьте модель, пакеты и разрешение Windows на микрофон'
        try: result.send(('error',message))
        except (OSError,EOFError): pass
    finally: result.close()


class MicrophoneController:
    def __init__(self):
        self.process = self.receiver = None
        self.deadline = 0.0

    @property
    def active(self): return self.process is not None

    def start(self, model_path, *, consent=False, device=None, timeout=15):
        if consent is not True: raise PermissionError('Запись микрофона требует явного согласия')
        if type(timeout) not in (int,float) or not 3 <= timeout <= 30:
            raise ValueError('Длительность записи должна быть от 3 до 30 секунд')
        state = microphone_status(model_path)
        if not state['available']: raise RuntimeError(state['reason'])
        root = str(_model_path(model_path))
        if device is not None and type(device) is not int: raise ValueError('Некорректное устройство записи')
        self.stop()
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_recognize_local,args=(root,device,timeout,sender),daemon=True)
        try: process.start()
        except Exception:
            receiver.close();sender.close();raise
        sender.close()
        self.process,self.receiver = process,receiver
        # Loading a local model also has a hard bound, independently of audio time.
        self.deadline = time.monotonic()+timeout+30

    def poll(self):
        if self.receiver is None: return None
        if self.receiver.poll():
            try: status,value = self.receiver.recv()
            except EOFError: status,value = 'error','Запись микрофона завершилась без результата'
            if status != 'listening': self.stop()
            return status,value
        if not self.process.is_alive() or time.monotonic()>self.deadline:
            self.stop()
            return 'error','Запись микрофона завершилась или превысила лимит времени'
        return None

    def stop(self):
        if self.process is not None:
            if self.process.is_alive(): self.process.terminate()
            self.process.join(.3)
            if self.process.is_alive():
                self.process.kill();self.process.join(.3)
            self.process.close()
        if self.receiver is not None:self.receiver.close()
        self.process = self.receiver = None
