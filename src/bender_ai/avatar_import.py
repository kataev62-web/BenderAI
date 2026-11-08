"""Bounded, local-only GLB -> PNG import. Blender is a user-installed executable.

This is a converter, not a realtime renderer or a sandbox for Blender's native
decoders. Keep Blender current and import only assets from a trusted source.
No asset, texture, executable or Python dependency is downloaded here.
"""
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import zlib

MAX_GLB = 32 * 1024 * 1024
STATES = ('idle', 'walk', 'thinking', 'talk', 'working', 'success', 'error', 'sleep')


class AvatarImportError(ValueError):
    pass


class AvatarImportCancelled(AvatarImportError):
    pass


def _local_file(raw):
    value = os.fspath(raw)
    # UNC and device paths can perform network access even during stat/read.
    if value.startswith(('\\\\', '//', '\\??\\')) or '://' in value:
        raise AvatarImportError('Нужен локальный файл, сетевые пути запрещены')
    path = Path(value).expanduser().resolve()
    if str(path).startswith(('\\\\', '//')):
        raise AvatarImportError('Сетевые пути запрещены')
    if os.name == 'nt':
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
            raise AvatarImportError('Сетевые диски запрещены')
    return path


def _no_duplicate_keys(items):
    result = {}
    for key, value in items:
        if key in result:
            raise AvatarImportError('Повторяющееся поле GLB')
        result[key] = value
    return result


def validate_glb(source):
    """Accept only a bounded GLB v2 with embedded BIN resources; reject all URIs."""
    source = _local_file(source)
    try:
        if source.suffix.casefold() != '.glb' or not source.is_file() or source.stat().st_size > MAX_GLB:
            raise AvatarImportError('Нужен локальный GLB до 32 МБ')
        with source.open('rb') as stream:
            raw = stream.read(MAX_GLB + 1)
        if len(raw) > MAX_GLB or len(raw) < 20:
            raise AvatarImportError('Повреждённый GLB')
        magic, version, total = struct.unpack_from('<4sII', raw)
        if magic != b'glTF' or version != 2 or total != len(raw):
            raise AvatarImportError('Нужен корректный GLB версии 2')
        chunks, offset = [], 12
        while offset < len(raw):
            if offset + 8 > len(raw):
                raise AvatarImportError('Повреждённый GLB chunk')
            size, kind = struct.unpack_from('<II', raw, offset)
            offset += 8
            if size % 4 or size == 0 or offset + size > len(raw):
                raise AvatarImportError('Повреждённый GLB chunk')
            chunks.append((kind, raw[offset:offset+size]))
            offset += size
        if not chunks or chunks[0][0] != 0x4E4F534A or len(chunks[0][1]) > 4_000_000:
            raise AvatarImportError('Нет допустимого JSON GLB')
        if len(chunks) > 2 or (len(chunks) == 2 and chunks[1][0] != 0x004E4942):
            raise AvatarImportError('Неподдерживаемые GLB chunks')
        data = json.loads(chunks[0][1].decode('utf-8'), object_pairs_hook=_no_duplicate_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(AvatarImportError('Не конечное число GLB')))
        def inspect(value, depth=0):
            if depth > 50:
                raise AvatarImportError('Слишком сложный GLB')
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.casefold() == 'uri':
                        raise AvatarImportError('URI запрещены: используйте GLB со встроенными ресурсами')
                    inspect(item, depth+1)
            elif isinstance(value, list):
                for item in value:
                    inspect(item, depth+1)
            elif isinstance(value, float) and not math.isfinite(value):
                raise AvatarImportError('Не конечное число GLB')
        inspect(data)
        if not isinstance(data, dict) or data.get('asset', {}).get('version') != '2.0':
            raise AvatarImportError('Нужен glTF 2.0')
        if data.get('extensionsRequired'):
            raise AvatarImportError('Обязательные расширения GLB не поддерживаются')
        for key, maximum in [('meshes', 500), ('nodes', 2000), ('accessors', 4000),
                             ('bufferViews', 4000), ('images', 32), ('animations', 100)]:
            value = data.get(key, [])
            if not isinstance(value, list) or len(value) > maximum:
                raise AvatarImportError('Превышен лимит GLB: ' + key)
        if not data.get('meshes'):
            raise AvatarImportError('В GLB нет mesh')
        binary_size = len(chunks[1][1]) if len(chunks) == 2 else 0
        buffers = data.get('buffers', [])
        if not isinstance(buffers, list) or len(buffers) != 1 or not isinstance(buffers[0], dict):
            raise AvatarImportError('Нужен один встроенный GLB buffer')
        length = buffers[0].get('byteLength')
        if type(length) is not int or not 0 < length <= binary_size or binary_size-length > 3:
            raise AvatarImportError('Недопустимый размер GLB buffer')
        for view in data.get('bufferViews', []):
            if not isinstance(view, dict) or view.get('buffer', 0) != 0:
                raise AvatarImportError('Недопустимый bufferView')
            start, size = view.get('byteOffset', 0), view.get('byteLength')
            if type(start) is not int or type(size) is not int or start < 0 or size < 1 or start+size > length:
                raise AvatarImportError('bufferView за пределами GLB')
        counts = [accessor.get('count') for accessor in data.get('accessors', []) if isinstance(accessor, dict)]
        if (len(counts) != len(data.get('accessors', [])) or
                any(type(count) is not int or not 0 < count <= 1_000_000 for count in counts) or sum(counts) > 2_000_000):
            raise AvatarImportError('Превышен лимит геометрии GLB')
        texture_pixels = 0
        for image in data.get('images', []):
            if not isinstance(image, dict) or image.get('mimeType') not in ('image/png', 'image/jpeg'):
                raise AvatarImportError('Поддерживаются только встроенные PNG/JPEG текстуры')
            index = image.get('bufferView')
            views = data.get('bufferViews', [])
            if type(index) is not int or not 0 <= index < len(views) or views[index]['byteLength'] > 8_000_000:
                raise AvatarImportError('Недопустимая встроенная текстура')
            view = views[index]
            start = view.get('byteOffset', 0)
            image_raw = chunks[1][1][start:start+view['byteLength']]
            if image['mimeType'] == 'image/png':
                if len(image_raw) < 24 or image_raw[:8] != b'\x89PNG\r\n\x1a\n':
                    raise AvatarImportError('Повреждённая PNG текстура')
                width, height = struct.unpack_from('>II', image_raw, 16)
            else:
                width, height = _jpeg_dimensions(image_raw)
            if not 0 < width <= 4096 or not 0 < height <= 4096:
                raise AvatarImportError('Текстура должна быть не более 4096×4096')
            texture_pixels += width*height
            if texture_pixels > 16_777_216:
                raise AvatarImportError('Суммарный размер текстур превышает 16 мегапикселей')
        return data
    except (OSError, UnicodeError, json.JSONDecodeError, struct.error, RecursionError, AttributeError, TypeError) as exc:
        raise AvatarImportError('Не удалось прочитать корректный локальный GLB') from exc


def _jpeg_dimensions(raw):
    if raw[:2] != b'\xff\xd8':
        raise AvatarImportError('Повреждённая JPEG текстура')
    offset = 2
    while offset+4 <= len(raw):
        if raw[offset] != 255:
            break
        while offset < len(raw) and raw[offset] == 255:
            offset += 1
        if offset >= len(raw):
            break
        marker = raw[offset]
        offset += 1
        if marker in (0xD9, 0xDA):
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            continue
        if offset+2 > len(raw):
            break
        size, = struct.unpack_from('>H', raw, offset)
        if size < 2 or offset+size > len(raw):
            break
        if marker in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF) and size >= 8:
            height, width = struct.unpack_from('>HH', raw, offset+3)
            return width, height
        offset += size
    raise AvatarImportError('Нет допустимого размера JPEG текстуры')


def _validate_png(path):
    raw = path.read_bytes()
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise AvatarImportError('Повреждённый PNG')
    offset, parts, kinds = 8, [], []
    while offset < len(raw):
        if offset+12 > len(raw):
            raise AvatarImportError('Повреждённый PNG chunk')
        size, = struct.unpack_from('>I', raw, offset)
        kind = raw[offset+4:offset+8]
        end = offset+12+size
        if end > len(raw):
            raise AvatarImportError('Повреждённый PNG chunk')
        value = raw[offset+8:offset+8+size]
        crc, = struct.unpack_from('>I', raw, offset+8+size)
        if zlib.crc32(kind+value) & 0xffffffff != crc:
            raise AvatarImportError('Неверная контрольная сумма PNG')
        if not kinds:
            if kind != b'IHDR' or size != 13 or value != struct.pack('>IIBBBBB', 256, 384, 8, 6, 0, 0, 0):
                raise AvatarImportError('Ожидается RGBA PNG 256×384')
        if kind == b'IDAT':
            parts.append(value)
        kinds.append(kind)
        offset = end
        if kind == b'IEND':
            if size or offset != len(raw):
                raise AvatarImportError('Повреждённое окончание PNG')
            break
    if not kinds or kinds[-1] != b'IEND' or not parts:
        raise AvatarImportError('Неполный PNG')
    expected = (256*4+1)*384
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(b''.join(parts), expected+1)
    except zlib.error as exc:
        raise AvatarImportError('Повреждённые пиксели PNG') from exc
    if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise AvatarImportError('Недопустимый размер пикселей PNG')


def validate_rendered_pack(folder):
    """Worker-safe PNG checks; QPixmap loading stays on the GUI thread."""
    root = Path(folder).resolve()
    manifest = root / 'manifest.json'
    if not manifest.is_file() or manifest.stat().st_size > 100_000:
        raise AvatarImportError('Blender не создал manifest пакета')
    try:
        data = json.loads(manifest.read_text('utf-8'))
        animations = data['animations']
        if data['schema'] != 1 or set(animations) != set(STATES):
            raise AvatarImportError('Blender вернул неполный пакет')
        total = 0
        for state in STATES:
            files = animations[state]
            if not isinstance(files, list) or len(files) != 6 or len(set(files)) != 6:
                raise AvatarImportError('Нужно шесть кадров каждого состояния')
            for name in files:
                if not isinstance(name, str) or Path(name).name != name or '\\' in name:
                    raise AvatarImportError('Недопустимое имя кадра')
                path = root / name
                if path.is_symlink() or not path.is_file() or path.suffix != '.png':
                    raise AvatarImportError('Отсутствует PNG кадр')
                size = path.stat().st_size
                total += size
                if not 33 <= size <= 4_000_000 or total > 64_000_000:
                    raise AvatarImportError('Слишком большой пакет PNG')
                _validate_png(path)
        return data
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, AvatarImportError):
            raise
        raise AvatarImportError('Повреждённый результат Blender') from exc


def import_glb(source, blender, destination_root, *, cancel=None, timeout=180, author='', source_url=''):
    """Return a fresh local PNG pack; cancel is a threading.Event, never a GUI object.

    Run on a worker. The GUI may load CharacterPack only after this succeeds.
    The original GLB is neither retained in the pack nor added to the repository.
    """
    def check():
        if cancel is not None and cancel.is_set():
            raise AvatarImportCancelled('Импорт отменён')
    check()
    executable = _local_file(blender)
    if not executable.is_file() or (os.name == 'nt' and executable.suffix.casefold() != '.exe'):
        raise AvatarImportError('Выберите установленный локальный Blender executable')
    if type(timeout) not in (float, int) or not .01 <= timeout <= 600:
        raise AvatarImportError('Недопустимое время импорта')
    original = _local_file(source)
    validate_glb(original)
    trusted_script = Path(__file__).with_name('avatar_blender.py')
    if not trusted_script.is_file():
        raise AvatarImportError('В сборке отсутствует trusted Blender script')
    destination = _local_file(destination_root)
    destination.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='avatar-', dir=destination))
    process = None
    success = False
    try:
        work = stage / 'work'
        work.mkdir()
        snapshot = work / 'model.glb'
        # Bounded snapshot eliminates source-path changes between validation/import.
        with original.open('rb') as stream:
            raw = stream.read(MAX_GLB+1)
        if len(raw) > MAX_GLB:
            raise AvatarImportError('Слишком большой GLB')
        snapshot.write_bytes(raw)
        validate_glb(snapshot)
        worker = work / 'render.py'
        shutil.copyfile(trusted_script, worker)
        pack = stage / 'pack'
        command = [str(executable), '--factory-startup', '--disable-autoexec', '-b', '-t', '2', '--python-exit-code', '1',
                   '-P', str(worker), '--', '--input', str(snapshot), '--output', str(pack),
                   '--author', str(author)[:500], '--source-url', str(source_url)[:1000]]
        check()
        # Isolate Blender preferences and never inherit a user's startup/add-on profile.
        env = dict(os.environ, BLENDER_USER_CONFIG=str(work/'config'), BLENDER_USER_SCRIPTS=str(work/'scripts'))
        process = subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic()+timeout
        while process.poll() is None:
            check()
            if time.monotonic() >= deadline:
                raise AvatarImportError('Истекло время импорта Blender')
            if cancel is not None:
                cancel.wait(.05)
            else:
                time.sleep(.05)
        check()
        if process.returncode:
            raise AvatarImportError('Blender не смог отрендерить GLB; проверьте модель и версию Blender')
        validate_rendered_pack(pack)
        shutil.rmtree(work)
        check()
        success = True
        return pack
    except OSError as exc:
        raise AvatarImportError('Не удалось запустить локальный Blender или сохранить пакет') from exc
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if not success:
            shutil.rmtree(stage, ignore_errors=True)
