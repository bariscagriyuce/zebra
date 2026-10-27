"""Etiket belgesi: sahne, etiket ayarları, kaydet/aç ve ZPL üretimi."""

import datetime
import json

from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPen, QBrush
from PyQt6.QtWidgets import QGraphicsScene

from .items import item_from_dict, ZplContext, VAR_RE, LabelItem

FILE_VERSION = 1

DEFAULT_LABEL = {
    "width_mm": 100.0,
    "height_mm": 50.0,
    "dpi": 203,
    "speed": 0,          # 0 = yazıcı ayarı
    "darkness": -1,      # -1 = yazıcı ayarı
    "invert": False,     # ^POI (180° ters bas)
    "media": "",         # "" = yazıcı ayarı, W/M/N
    "method": "",        # "" = yazıcı ayarı, T/D
    "home_x": 0,
    "home_y": 0,
}


def mm_to_dots(mm, dpi):
    return int(round(mm / 25.4 * dpi))


def dots_to_mm(d, dpi):
    return d * 25.4 / dpi


class LabelScene(QGraphicsScene):
    moved = pyqtSignal(object)      # öğe sürüklendi/boyutlandı (canlı)
    committed = pyqtSignal()        # kullanıcı işlemi bitti (geri al için)

    def __init__(self):
        super().__init__()
        self.label = dict(DEFAULT_LABEL)
        self.grid_mm = 1.0
        self.show_grid = True
        self.snap = True
        self._update_rect()

    # --- ölçüler ----------------------------------------------------------------
    @property
    def dpi(self):
        return int(self.label["dpi"])

    @property
    def width_dots(self):
        return mm_to_dots(self.label["width_mm"], self.dpi)

    @property
    def height_dots(self):
        return mm_to_dots(self.label["height_mm"], self.dpi)

    @property
    def snap_step(self):
        return max(1, mm_to_dots(self.grid_mm, self.dpi)) if self.snap else 0

    def _update_rect(self):
        m = 40
        self.setSceneRect(QRectF(-m, -m, self.width_dots + 2 * m, self.height_dots + 2 * m))
        self.update()

    def set_label(self, label):
        old_dpi = self.dpi
        self.label = dict(DEFAULT_LABEL, **label)
        if self.dpi != old_dpi:
            f = self.dpi / old_dpi
            for it in self.label_items():
                it.scale_by(f)
        self._update_rect()

    # --- öğeler -----------------------------------------------------------------
    def label_items(self):
        """Öğeler, arkadan öne (ZPL çizim sırası)."""
        its = [i for i in self.items() if isinstance(i, LabelItem)]
        return sorted(its, key=lambda i: i.zValue())

    def add(self, item, z=None):
        if z is None:
            z = max([i.zValue() for i in self.label_items()] + [0]) + 1
        item.setZValue(z)
        self.addItem(item)
        return item

    def item_moved(self, item):
        self.moved.emit(item)

    def item_committed(self):
        self.committed.emit()

    def mouseReleaseEvent(self, ev):
        moved = any(isinstance(i, LabelItem) for i in self.selectedItems()) and \
            ev.button() == Qt.MouseButton.LeftButton and ev.buttonDownScenePos(Qt.MouseButton.LeftButton) != ev.scenePos()
        super().mouseReleaseEvent(ev)
        if moved:
            self.committed.emit()

    # --- çizim ------------------------------------------------------------------
    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor(120, 124, 130))
        lr = QRectF(0, 0, self.width_dots, self.height_dots)
        painter.fillRect(lr.translated(4, 4), QColor(0, 0, 0, 60))
        painter.fillRect(lr, QColor(255, 255, 255))
        if self.show_grid:
            step = mm_to_dots(self.grid_mm, self.dpi)
            view_scale = self.views()[0].transform().m11() if self.views() else 1
            while step * view_scale < 6:
                step *= 2 if step * view_scale >= 3 else 5
            pen = QPen(QColor(215, 220, 228), 0)
            painter.setPen(pen)
            x = 0
            lines = []
            while x <= self.width_dots:
                lines.append((x, 0, x, self.height_dots))
                x += step
            y = 0
            while y <= self.height_dots:
                lines.append((0, y, self.width_dots, y))
                y += step
            for l in lines:
                painter.drawLine(*[int(v) for v in l])
            # 10 mm çizgileri
            painter.setPen(QPen(QColor(185, 195, 210), 0))
            s10 = mm_to_dots(10, self.dpi)
            x = 0
            while x <= self.width_dots:
                painter.drawLine(int(x), 0, int(x), self.height_dots)
                x += s10
            y = 0
            while y <= self.height_dots:
                painter.drawLine(0, int(y), self.width_dots, int(y))
                y += s10
        painter.setPen(QPen(QColor(60, 60, 60), 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(lr)

    # --- kalıcılık ----------------------------------------------------------------
    def to_dict(self):
        return {"version": FILE_VERSION, "label": dict(self.label),
                "items": [i.to_dict() for i in self.label_items()]}

    def load_dict(self, d):
        self.clear()
        self.label = dict(DEFAULT_LABEL, **d.get("label", {}))
        self._update_rect()
        for z, it in enumerate(d.get("items", []), 1):
            self.add(item_from_dict(it), z)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)

    def load(self, path):
        with open(path, encoding="utf-8") as f:
            self.load_dict(json.load(f))

    # --- değişkenler ve ZPL --------------------------------------------------------
    def variables(self):
        names = []
        for it in self.label_items():
            for key in ("text", "data"):
                if key in it.p:
                    for m in VAR_RE.finditer(str(it.p[key])):
                        if m.group(1) not in names:
                            names.append(m.group(1))
        return names

    def zpl(self, values=None, copies=1):
        values = dict(values or {})
        now = datetime.datetime.now()
        values.setdefault("TARIH", now.strftime("%d.%m.%Y"))
        values.setdefault("SAAT", now.strftime("%H:%M"))
        values.setdefault("DATE", now.strftime("%Y-%m-%d"))
        values.setdefault("TIME", now.strftime("%H:%M:%S"))
        ctx = ZplContext(self.dpi, values)
        L = self.label
        out = []
        if int(L.get("darkness", -1)) >= 0:
            out.append(f"~SD{int(L['darkness']):02d}")
        out.append("^XA")
        out.append("^CI28")
        if L.get("method"):
            out.append(f"^MT{L['method']}")
        if L.get("media"):
            out.append(f"^MN{L['media']}")
        out.append(f"^PW{self.width_dots}")
        out.append(f"^LL{self.height_dots}")
        out.append(f"^LH{int(L.get('home_x', 0))},{int(L.get('home_y', 0))}")
        if int(L.get("speed", 0)):
            out.append(f"^PR{int(L['speed'])}")
        out.append("^POI" if L.get("invert") else "^PON")
        for it in self.label_items():
            z = it.zpl(ctx)
            if z:
                out.append(z)
        if copies > 1:
            out.append(f"^PQ{int(copies)},0,1,Y")
        out.append("^XZ")
        return "\n".join(out) + "\n"
