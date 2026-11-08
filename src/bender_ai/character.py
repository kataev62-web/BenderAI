"""Original procedural robot, deliberately unrelated to third-party character art."""
from pathlib import Path
import json
import math
import time
from PySide6.QtCore import Qt, QTimer, Signal, QPoint, QRect
from PySide6.QtGui import QColor, QPainter, QPen, QCursor, QPixmap, QRegion
from PySide6.QtWidgets import QWidget, QApplication

STATES = ('idle', 'walk', 'thinking', 'talk', 'working', 'success', 'error', 'sleep')


def clamp_position(x, y, width, height, screens):
    if not screens:
        return x, y
    screen = min(screens, key=lambda r: (r.center().x() - x)**2 + (r.center().y() - y)**2)
    for r in screens:
        if r.contains(QPoint(x + width//2, y + height//2)):
            screen = r
            break
    return (max(screen.left(), min(x, screen.right() - width + 1)),
            max(screen.top(), min(y, screen.bottom() - height + 1)))


class CharacterPack:
    def __init__(self, animations):
        self.animations = animations

    @classmethod
    def load(cls, folder):
        root = Path(folder).resolve()
        raw = (root / 'manifest.json').read_text('utf-8')
        if len(raw) > 100000:
            raise ValueError('Слишком большой manifest')
        data = json.loads(raw)
        if data.get('schema') != 1 or set(data.get('animations', {})) != set(STATES):
            raise ValueError('Нужны schema=1 и все восемь анимаций')
        animations = {}
        for state, files in data['animations'].items():
            if not isinstance(files, list) or not 1 <= len(files) <= 60:
                raise ValueError('От 1 до 60 PNG кадров на анимацию')
            frames = []
            for name in files:
                path = (root / name).resolve()
                if not path.is_relative_to(root) or path.suffix.casefold() != '.png' or path.stat().st_size > 4_000_000:
                    raise ValueError('Недопустимый ресурс пакета')
                pixmap = QPixmap(str(path))
                if pixmap.isNull() or pixmap.width() > 2048 or pixmap.height() > 2048:
                    raise ValueError('Повреждённый или слишком большой PNG')
                frames.append(pixmap)
            animations[state] = frames
        return cls(animations)


class Character(QWidget):
    activated = Signal()

    @property
    def pack(self):
        return getattr(self, '_pack', None)

    @pack.setter
    def pack(self, value):
        self._pack = value
        self._pack_mask_key = None
        self.apply_mask()
        self.update()

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(116, 146)
        self.state, self.pack = 'idle', None
        self.dnd, self.pinned, self.animate = False, False, True
        self.topmost = True
        self.speed = 30
        self.direction, self.phase, self._x = 1, 0., 100.
        self.drag_origin, self.moved = None, False
        self._screens = set()
        self.last = time.monotonic()
        self.move(100, 100)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)
        app = QApplication.instance()
        app.screenAdded.connect(self.rebind_screens)
        app.screenRemoved.connect(self.rebind_screens)
        self.rebind_screens()
        self.apply_mask()

    def rebind_screens(self, *_):
        for screen in QApplication.screens():
            if screen not in self._screens:
                screen.availableGeometryChanged.connect(self.ensure_visible)
                self._screens.add(screen)
        self.ensure_visible()

    def apply_mask(self):
        if self.pack:
            # Imported silhouettes can extend beyond the built-in robot's mask.
            # Keep PNG proportions and transparent pixels click-through.
            frames = self.pack.animations[self.state]
            index = int(self.phase*8) % len(frames)
            key = (id(self.pack), self.state, index, self.width(), self.height())
            if getattr(self, '_pack_mask_key', None) == key:
                return
            pixmap = self.pack_frame()
            self.setMask(QRegion(pixmap.mask()).translated(
                (self.width()-pixmap.width())//2, (self.height()-pixmap.height())//2))
            self._pack_mask_key = key
            return
        # Minimal silhouette mask leaves empty surrounding desktop clickable.
        w, h = self.width(), self.height()
        self.setMask(QRegion(int(w*.14), int(h*.15), int(w*.72), int(h*.68), QRegion.RegionType.Ellipse)
                     .united(QRegion(int(w*.28), int(h*.77), int(w*.44), int(h*.18))))

    def set_dnd(self, enabled):
        self.dnd = enabled
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        self.state = 'sleep' if enabled else 'idle'
        self.show()

    def set_topmost(self, enabled):
        self.topmost = bool(enabled)
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.topmost)
        if visible:
            self.show()

    def pack_frame(self):
        frames = self.pack.animations[self.state]
        return frames[int(self.phase*8) % len(frames)].scaled(
            max(1, round(self.width()*100/116)), max(1, round(self.height()*132/146)),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def ensure_visible(self, *_):
        screens = [s.availableGeometry() for s in QApplication.screens()]
        x, y = clamp_position(self.x(), self.y(), self.width(), self.height(), screens)
        self.move(x, y)
        self._x = float(x)

    def tick(self):
        now = time.monotonic()
        elapsed, self.last = min(.1, now-self.last), now
        self.phase += elapsed if self.animate else 0
        if not self.pinned and not self.dnd and self.drag_origin is None and self.state in ('idle', 'walk'):
            if (QCursor.pos() - self.geometry().center()).manhattanLength() > 150:
                self._x += self.direction * self.speed * elapsed
                old_x = self.x()
                self.move(round(self._x), self.y())
                x, y = clamp_position(self.x(), self.y(), self.width(), self.height(), [s.availableGeometry() for s in QApplication.screens()])
                if x != self.x():
                    self.direction *= -1
                    self._x = float(x)
                self.move(x, y)
                self.state = 'walk' if old_x != x else 'idle'
        self.apply_mask()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.pack:
            pixmap = self.pack_frame()
            p.drawPixmap((self.width()-pixmap.width())//2, (self.height()-pixmap.height())//2, pixmap)
            return
        p.scale(self.width()/116, self.height()/146)
        colors = {'idle': '#7de2d1', 'walk': '#7de2d1', 'thinking': '#beadff', 'talk': '#84bcff',
                  'working': '#ffd27d', 'success': '#85f29e', 'error': '#ff8a95', 'sleep': '#76849b'}
        color = QColor(colors.get(self.state, '#7de2d1'))
        bob = math.sin(self.phase*5)*2
        p.translate(0, bob)
        p.setPen(QPen(QColor('#25364a'), 3))
        p.setBrush(QColor('#24334b'))
        p.drawRoundedRect(24, 57, 68, 62, 22, 22)
        p.setBrush(color)
        p.drawRoundedRect(19, 24, 78, 55, 21, 21)
        p.drawEllipse(52, 11, 12, 12)
        p.drawLine(58, 23, 58, 17)
        p.setBrush(QColor('#142237'))
        p.drawRoundedRect(28, 35, 60, 27, 12, 12)
        p.setPen(QPen(color, 4))
        blink = self.state == 'sleep' or int(self.phase*2) % 13 == 0
        for x in (43, 72):
            p.drawLine(x-4, 48, x+4, 48) if blink else p.drawEllipse(x-2, 43, 5, 9)
        p.setPen(QPen(color, 3))
        if self.state == 'talk':
            p.drawEllipse(48, 63, 20, 4+int(abs(math.sin(self.phase*12))*6))
        else:
            p.drawLine(49, 68, 67, 68)
        p.drawEllipse(48, 87, 20, 20)
        angle = math.sin(self.phase*8)*6 if self.state in ('walk', 'working') else 0
        p.drawLine(24, 87, 17, 104+int(angle))
        p.drawLine(92, 87, 99, 104-int(angle))
        p.setBrush(QColor('#536b87'))
        for x, offset in ((36, angle), (65, -angle)):
            p.drawRoundedRect(x, 119+int(offset/2), 18, 13, 5, 5)
        p.end()

    def resizeEvent(self, event):
        self.apply_mask()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_origin = event.globalPosition().toPoint() - self.pos()
            self.moved = False

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None:
            target = event.globalPosition().toPoint() - self.drag_origin
            self.moved |= (target - self.pos()).manhattanLength() > 3
            self.move(target)
            self.ensure_visible()

    def mouseReleaseEvent(self, event):
        self.drag_origin = None
        if not self.moved:
            self.activated.emit()
