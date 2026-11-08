import json
from pathlib import Path
import struct
import threading

import pytest

from bender_ai.avatar_import import AvatarImportError, AvatarImportCancelled, validate_glb, import_glb


def make_glb(path, document=None):
    # One embedded triangle, with no external files, scripts or downloaded assets.
    document = document or {
        'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
        'nodes': [{'mesh': 0}], 'meshes': [{'primitives': [{'attributes': {'POSITION': 0}}]}],
        'buffers': [{'byteLength': 36}], 'bufferViews': [{'buffer': 0, 'byteLength': 36}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3',
                       'min': [-1, -1, 0], 'max': [1, 1, 0]}],
    }
    raw = json.dumps(document).encode()
    raw += b' ' * (-len(raw) % 4)
    binary = struct.pack('<9f', -1, -1, 0, 1, -1, 0, 0, 1, 0)
    body = struct.pack('<II', len(raw), 0x4E4F534A) + raw + struct.pack('<II', len(binary), 0x004E4942) + binary
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body)
    return path


def test_glb_self_contained_and_format_limits(tmp_path):
    source = make_glb(tmp_path / 'triangle.glb')
    assert validate_glb(source)['asset']['version'] == '2.0'
    good = source.read_bytes()
    for raw in (b'not a model', good[:-4], good[:8] + struct.pack('<I', 999) + good[12:]):
        source.write_bytes(raw)
        with pytest.raises(AvatarImportError):
            validate_glb(source)


@pytest.mark.parametrize('uri', ['https://example.com/texture.png', '../secret.png',
                                r'\\server\share\texture.png', 'file:///C:/secret',
                                'data:image/png;base64,AA=='])
def test_glb_rejects_every_uri_before_blender(tmp_path, uri):
    source = make_glb(tmp_path / 'external.glb', {
        'asset': {'version': '2.0'}, 'images': [{'uri': uri}], 'meshes': [{}],
    })
    with pytest.raises(AvatarImportError, match='URI'):
        validate_glb(source)


def test_glb_limits_geometry_before_render(tmp_path):
    source = make_glb(tmp_path/'model.glb')
    document = validate_glb(source)
    document['accessors'][0]['count'] = 10_000_000
    make_glb(source,document)
    with pytest.raises(AvatarImportError,match='геометрии'):
        validate_glb(source)


def test_import_stopped_before_launch_does_not_touch_output(tmp_path):
    source = make_glb(tmp_path / 'triangle.glb')
    stop = threading.Event()
    stop.set()
    with pytest.raises(AvatarImportCancelled):
        import_glb(source, tmp_path / 'missing-blender', tmp_path / 'packs', cancel=stop)
    assert not (tmp_path / 'packs').exists()


def test_import_fails_closed_without_blender_or_complete_pack(tmp_path):
    source = make_glb(tmp_path / 'triangle.glb')
    with pytest.raises(AvatarImportError):
        import_glb(source, tmp_path / 'missing-blender', tmp_path / 'packs')
    assert not (tmp_path / 'packs').exists()


def test_import_timeout_kills_child_and_cleans_staging(tmp_path, monkeypatch):
    import bender_ai.avatar_import as module
    source = make_glb(tmp_path / 'triangle.glb')
    executable = tmp_path / ('blender.exe' if module.os.name == 'nt' else 'blender')
    executable.write_bytes(b'test fixture, never executed')
    calls = []
    class Hanging:
        returncode = None
        def poll(self): return None
        def terminate(self): calls.append('terminate')
        def wait(self, timeout=None): return 0
    def launch(args, **kwargs):
        calls.append((args, kwargs))
        return Hanging()
    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    with pytest.raises(AvatarImportError, match='время'):
        import_glb(source, executable, tmp_path / 'packs', timeout=.01)
    args, kwargs = calls[0]
    assert '--factory-startup' in args and '--disable-autoexec' in args and '-P' in args
    assert kwargs['shell'] is False
    assert 'terminate' in calls
    assert list((tmp_path / 'packs').iterdir()) == []


def test_pack_validation_checks_complete_png_pixels_and_crc(tmp_path):
    import zlib
    from bender_ai.avatar_import import validate_rendered_pack, STATES
    def chunk(kind, value):
        return struct.pack('>I', len(value))+kind+value+struct.pack('>I', zlib.crc32(kind+value)&0xffffffff)
    pixels = (b'\0'+b'\xff\0\0\xff'*256)*384
    png = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB',256,384,8,6,0,0,0))
    png += chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
    animations = {}
    for state in STATES:
        animations[state] = []
        for index in range(6):
            name = f'{state}_{index}.png'
            (tmp_path/name).write_bytes(png)
            animations[state].append(name)
    (tmp_path/'manifest.json').write_text(json.dumps({'schema':1,'animations':animations}))
    assert validate_rendered_pack(tmp_path)['schema'] == 1
    target = tmp_path/'idle_0.png'
    target.write_bytes(png[:-1]+bytes([png[-1]^1]))
    with pytest.raises(AvatarImportError, match='сумма'):
        validate_rendered_pack(tmp_path)
    target.unlink()
    with pytest.raises(AvatarImportError):
        validate_rendered_pack(tmp_path)


def test_import_stop_during_render_kills_child_and_cleans(tmp_path, monkeypatch):
    import bender_ai.avatar_import as module
    source = make_glb(tmp_path / 'triangle.glb')
    executable = tmp_path / ('blender.exe' if module.os.name == 'nt' else 'blender')
    executable.write_bytes(b'not executed')
    stop, terminated = threading.Event(), []
    class Process:
        def poll(self):
            stop.set()
            return None
        def terminate(self): terminated.append(True)
        def wait(self, timeout=None): return 0
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**kw:Process())
    with pytest.raises(AvatarImportCancelled):
        import_glb(source,executable,tmp_path/'packs',cancel=stop)
    assert terminated
    assert list((tmp_path/'packs').iterdir()) == []


def test_pack_mask_follows_current_png_and_preserves_topmost_toggle():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QPixmap, QPainter, QColor
    from PySide6.QtWidgets import QApplication
    from bender_ai.character import Character, CharacterPack, STATES
    app = QApplication.instance() or QApplication([])
    frames = []
    for x in (0,80):
        pixmap = QPixmap(100,132)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.fillRect(x,0,20,132,QColor('red'))
        painter.end()
        frames.append(pixmap)
    character = Character()
    character.pinned = True
    character.pack = CharacterPack({state:frames for state in STATES})
    assert character.mask().contains(QPoint(10,40))
    assert not character.mask().contains(QPoint(100,40))
    character.phase = .13
    character.apply_mask()
    assert not character.mask().contains(QPoint(10,40))
    assert character.mask().contains(QPoint(100,40))
    character.set_topmost(False)
    assert not character.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    character.set_topmost(True)
    assert character.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    character.timer.stop()
    character.close()


@pytest.mark.skipif(not __import__('os').environ.get('BENDER_BLENDER_INTEGRATION'),
                    reason='opt in with BENDER_BLENDER_INTEGRATION=/path/to/blender')
def test_real_blender_triangle_pack(tmp_path):
    import hashlib
    import os
    source = make_glb(tmp_path / 'triangle.glb')
    result = import_glb(source, os.environ['BENDER_BLENDER_INTEGRATION'], tmp_path / 'packs', timeout=180)
    data = json.loads((result / 'manifest.json').read_text('utf-8'))
    assert set(data['animations']) == set(__import__('bender_ai.character', fromlist=['STATES']).STATES)
    assert not list(result.parent.rglob('*.glb'))
    hashes = {state: [hashlib.sha256((result / frame).read_bytes()).hexdigest() for frame in frames]
              for state, frames in data['animations'].items()}
    assert len({tuple(values) for values in hashes.values()}) == 8
    assert all(len(set(values)) > 1 for values in hashes.values())
