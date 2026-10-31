"""Etiket öğeleri: metin, barkod, kutu, çizgi, elips, resim.

Sahne koordinatları yazıcı noktalarıdır (dot). Her öğe döndürülmemiş içeriğini
paint_content() ile çizer; döndürme taban sınıfta uygulanır. Öğenin konumu,
döndürülmüş alanın sol üst köşesidir (ZPL ^FO ile aynı).
"""

import base64
import re

from PyQt6.QtCore import Qt, QRectF, QPointF, QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import (QPainter, QPen, QBrush, QColor, QFont, QFontMetricsF, QImage, QTransform,
                         QFontDatabase, QTextOption)
from PyQt6.QtWidgets import QGraphicsItem, QStyle

from .. import zpl as Z
from . import barcodes as B
from ..i18n import tr

ZEBRA0 = "ZEBRA0"
ROT_CODES = {0: "N", 90: "R", 180: "I", 270: "B"}
ROTATIONS = [("0°", 0), ("90°", 90), ("180°", 180), ("270°", 270)]
ALIGNS = [(tr('Sol'), "L"), (tr('Orta'), "C"), (tr('Sağ'), "R"), (tr('İki yana'), "J")]

_zebra_family = None

# Yazıcının 0 fontunda (^A0N,h,h) bu metnin genişliği yaklaşık ZEBRA0_REF_WIDTH × h noktadır.
# ZD230 çıktısından ölçüldü (h=42 için ~220 nokta); önizleme fontu buna göre genişletilir.
ZEBRA0_REF_TEXT = "VENCIMENTO:"
ZEBRA0_REF_WIDTH = 5.25


def zebra_font_family():
    """Yazıcının 0 fontuna (CG Triumvirate Bold Condensed) en yakın sistem fontu."""
    global _zebra_family
    if _zebra_family is None:
        fams = set(QFontDatabase.families())
        for cand in ("Roboto Condensed", "Liberation Sans Narrow", "Arial Narrow", "Nimbus Sans Narrow",
                     "DejaVu Sans Condensed", "Noto Sans Display", "Noto Sans", "DejaVu Sans"):
            if cand in fams:
                _zebra_family = cand
                break
        else:
            _zebra_family = QFont().defaultFamily()
    return _zebra_family


VAR_RE = re.compile(r"\{([^{}]+)\}")


def substitute(text, values):
    if not values:
        return text
    return VAR_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)


class ZplContext:
    def __init__(self, dpi=203, values=None):
        self.dpi = dpi
        self.values = values or {}

    def sub(self, text):
        return substitute(text, self.values)


class LabelItem(QGraphicsItem):
    TYPE = "base"
    TITLE = tr('Öğe')
    DEFAULTS = {}
    ROTATABLE = False
    RESIZABLE = False
    SCALE_KEYS = ()          # DPI değişince ölçeklenecek sayısal özellikler
    REBUILD_KEYS = ()        # değişince özellik panelinin yeniden kurulacağı anahtarlar

    def __init__(self, props=None):
        super().__init__()
        self.p = {"x": 20, "y": 20, "rotation": 0, "locked": False}
        self.p.update(self.DEFAULTS)
        if props:
            self.p.update({k: v for k, v in props.items() if k != "type"})
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
                      | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable)
        self._apply_lock()
        self.setPos(self.p["x"], self.p["y"])
        self._resizing = False
        self._error = None
        self.setAcceptHoverEvents(True)

    # --- model ---------------------------------------------------------------
    def to_dict(self):
        d = {"type": self.TYPE}
        d.update(self.p)
        return d

    def fields(self):
        """Özellik paneli alanları: (anahtar, etiket, tür, ek)."""
        base = [("x", tr('X (nokta)'), "int", (-5000, 20000)),
                ("y", tr('Y (nokta)'), "int", (-5000, 20000))]
        if self.ROTATABLE:
            base.append(("rotation", tr('Döndürme'), "choice", ROTATIONS))
        return base

    def tail_fields(self):
        return [("locked", tr('Kilitli (taşınamaz)'), "bool", None)]

    def set_prop(self, key, value):
        self.prepareGeometryChange()
        self.p[key] = value
        if key in ("x", "y"):
            self.setPos(self.p["x"], self.p["y"])
        if key == "locked":
            self._apply_lock()
        self.invalidate()

    def _apply_lock(self):
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not self.p.get("locked"))

    def invalidate(self):
        self.prepareGeometryChange()
        self.update()

    def scale_by(self, f):
        self.p["x"] = round(self.p["x"] * f)
        self.p["y"] = round(self.p["y"] * f)
        for k in self.SCALE_KEYS:
            if k in self.p:
                self.p[k] = max(1, round(self.p[k] * f))
        self.setPos(self.p["x"], self.p["y"])
        self.invalidate()

    # --- geometri -------------------------------------------------------------
    def content_size(self, ctx=None):
        return 10, 10

    def rot(self):
        return int(self.p.get("rotation", 0)) % 360 if self.ROTATABLE else 0

    def outer_size(self, ctx=None):
        w, h = self.content_size(ctx)
        return (h, w) if self.rot() in (90, 270) else (w, h)

    def boundingRect(self):
        w, h = self.outer_size()
        return QRectF(0, 0, max(w, 1), max(h, 1))

    def content_transform(self, ctx=None):
        w, h = self.outer_size(ctx)
        t = QTransform()
        r = self.rot()
        if r == 90:
            t.translate(w, 0)
            t.rotate(90)
        elif r == 180:
            t.translate(w, h)
            t.rotate(180)
        elif r == 270:
            t.translate(0, h)
            t.rotate(270)
        return t

    def paint(self, painter, option, widget=None):
        painter.save()
        painter.setTransform(self.content_transform(), True)
        cw, ch = self.content_size()
        try:
            self.paint_content(painter, cw, ch)
        finally:
            painter.restore()
        if option.state & QStyle.StateFlag.State_Selected:
            z = self._zoom()
            pen = QPen(QColor(0, 120, 215), 1.5 / z, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.boundingRect())
            if self.RESIZABLE and not self.p.get("locked"):
                hs = 8 / z
                r = self.boundingRect()
                painter.setBrush(QColor(0, 120, 215))
                painter.drawRect(QRectF(r.right() - hs, r.bottom() - hs, hs, hs))

    def paint_content(self, painter, w, h, ctx=None):
        pass

    def paint_error(self, painter, w, h, msg):
        painter.setPen(QPen(QColor(200, 0, 0), 1))
        painter.setBrush(QBrush(QColor(255, 220, 220), Qt.BrushStyle.BDiagPattern))
        painter.drawRect(QRectF(0, 0, w, h))
        f = QFont()
        f.setPixelSize(max(10, int(min(h / 4, 18))))
        painter.setFont(f)
        painter.drawText(QRectF(2, 2, w - 4, h - 4),
                         Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, msg)

    def _zoom(self):
        s = self.scene()
        if s and s.views():
            return max(s.views()[0].transform().m11(), 0.01)
        return 1.0

    # --- etkileşim -------------------------------------------------------------
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            g = getattr(self.scene(), "snap_step", 0)
            if g and not self._resizing:
                value = QPointF(round(value.x() / g) * g, round(value.y() / g) * g)
            else:
                value = QPointF(round(value.x()), round(value.y()))
            return value
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.p["x"] = int(round(self.pos().x()))
            self.p["y"] = int(round(self.pos().y()))
            s = self.scene()
            if s and hasattr(s, "item_moved"):
                s.item_moved(self)
        return super().itemChange(change, value)

    def _in_handle(self, pos):
        if not (self.RESIZABLE and self.isSelected() and not self.p.get("locked")):
            return False
        hs = 10 / self._zoom()
        r = self.boundingRect()
        return pos.x() >= r.right() - hs and pos.y() >= r.bottom() - hs

    def hoverMoveEvent(self, ev):
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._in_handle(ev.pos()) else Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._in_handle(ev.pos()):
            self._resizing = True
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._resizing:
            g = getattr(self.scene(), "snap_step", 0) or 1
            w = max(g, round(ev.pos().x() / g) * g)
            h = max(g, round(ev.pos().y() / g) * g)
            self.resize_to(int(w), int(h))
            s = self.scene()
            if s and hasattr(s, "item_moved"):
                s.item_moved(self)
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._resizing:
            self._resizing = False
            s = self.scene()
            if s and hasattr(s, "item_committed"):
                s.item_committed()
            return
        super().mouseReleaseEvent(ev)

    def resize_to(self, w, h):
        pass

    # --- ZPL -------------------------------------------------------------------
    def zpl(self, ctx: ZplContext) -> str:
        return ""

    def fo(self):
        return f"^FO{int(self.p['x'])},{int(self.p['y'])}"

    def rendered_image(self, ctx=None):
        """Öğeyi (döndürülmüş) siyah-beyaz görüntü olarak çiz -> QImage."""
        w, h = self.outer_size(ctx)
        img = QImage(max(1, int(round(w))), max(1, int(round(h))), QImage.Format.Format_ARGB32)
        img.fill(QColor(255, 255, 255))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setTransform(self.content_transform(ctx))
        cw, ch = self.content_size(ctx)
        self.paint_content(p, cw, ch, ctx=ctx)
        p.end()
        return img


# ============================================================================ Metin

class TextItem(LabelItem):
    TYPE = "text"
    TITLE = tr('Metin')
    ROTATABLE = True
    DEFAULTS = {"text": tr('Metin'), "font": ZEBRA0, "height": 40, "width": 0, "bold": False, "italic": False,
                "block_width": 0, "align": "L", "reverse": False}
    SCALE_KEYS = ("height", "width", "block_width")
    REBUILD_KEYS = ("font",)

    def fields(self):
        f = super().fields() + [
            ("text", tr('Metin  ({DEĞİŞKEN} kullanılabilir)'), "text", None),
            ("font", "Font", "font", None),
            ("height", tr('Yükseklik (nokta)'), "int", (4, 2000)),
            ("width", tr('Genişlik (0 = orantılı)'), "int", (0, 2000)),
        ]
        if self.p["font"] != ZEBRA0:
            f += [("bold", tr('Kalın'), "bool", None), ("italic", tr('İtalik'), "bool", None)]
        f += [("block_width", tr('Paragraf genişliği (0 = yok)'), "int", (0, 20000)),
              ("align", tr('Hizalama'), "choice", ALIGNS),
              ("reverse", tr('Ters baskı (beyaz yazı)'), "bool", None)]
        return f + self.tail_fields()

    def _qfont(self):
        h = int(self.p["height"])
        if self.p["font"] == ZEBRA0:
            f = QFont(zebra_font_family())
            f.setBold(True)
        else:
            f = QFont(self.p["font"])
            f.setBold(bool(self.p["bold"]))
            f.setItalic(bool(self.p["italic"]))
        f.setPixelSize(max(1, h))
        f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        # satır yüksekliği (ascent+descent) tam 'height' olsun
        fm = QFontMetricsF(f)
        if fm.height() > 0:
            f.setPixelSize(max(1, int(round(h * h / fm.height()))))
        w = int(self.p["width"]) or h
        if self.p["font"] != ZEBRA0:
            if w != h:
                f.setStretch(max(1, min(4000, int(round(100 * w / h)))))
            return f
        # Sistem fontunu yazıcının 0 fontunun genişliğine ayarla. Stretch doğrusal
        # değil (fontconfig başka bir yüz seçebilir), bu yüzden ölçerek yaklaş.
        target = ZEBRA0_REF_WIDTH * w
        stretch = 100.0
        for _ in range(4):
            adv = QFontMetricsF(f).horizontalAdvance(ZEBRA0_REF_TEXT)
            if adv <= 0 or abs(adv - target) / target < 0.01:
                break
            stretch = max(1.0, min(4000.0, stretch * target / adv))
            f.setStretch(int(round(stretch)))
        return f

    def _lines(self, text, fm, bw):
        lines = []
        for para in text.split("\n"):
            if not bw:
                lines.append(para)
                continue
            words = para.split(" ")
            cur = ""
            for wd in words:
                cand = wd if not cur else cur + " " + wd
                if fm.horizontalAdvance(cand) <= bw or not cur:
                    cur = cand
                else:
                    lines.append(cur)
                    cur = wd
            lines.append(cur)
        return lines

    def _layout(self, ctx=None):
        text = ctx.sub(self.p["text"]) if ctx else self.p["text"]
        f = self._qfont()
        fm = QFontMetricsF(f)
        bw = int(self.p["block_width"])
        lines = self._lines(text, fm, bw)
        width = bw or max([fm.horizontalAdvance(l) for l in lines] + [1])
        return f, fm, lines, width

    def content_size(self, ctx=None):
        f, fm, lines, width = self._layout(ctx)
        return max(1, int(round(width))), int(self.p["height"]) * max(1, len(lines))

    def paint_content(self, painter, w, h, ctx=None):
        f, fm, lines, width = self._layout(ctx)
        lh = int(self.p["height"])
        fg, bg = QColor(0, 0, 0), None
        if self.p["reverse"]:
            fg, bg = QColor(255, 255, 255), QColor(0, 0, 0)
        if bg is not None:
            painter.fillRect(QRectF(0, 0, w, h), bg)
        painter.setFont(f)
        painter.setPen(fg)
        al = self.p["align"]
        for i, line in enumerate(lines):
            lw = fm.horizontalAdvance(line)
            x = 0
            if al == "C":
                x = (w - lw) / 2
            elif al == "R":
                x = w - lw
            y = i * lh + fm.ascent()
            if al == "J" and i < len(lines) - 1 and " " in line and self.p["block_width"]:
                words = line.split(" ")
                total = sum(fm.horizontalAdvance(wd) for wd in words)
                gap = (w - total) / (len(words) - 1)
                xx = 0
                for wd in words:
                    painter.drawText(QPointF(xx, y), wd)
                    xx += fm.horizontalAdvance(wd) + gap
            else:
                painter.drawText(QPointF(x, y), line)

    def zpl(self, ctx):
        text = ctx.sub(self.p["text"])
        if self.p["font"] != ZEBRA0:
            gf, _, _ = Z.qimage_to_gf(self.rendered_image(ctx))
            return f"{self.fo()}{gf}^FS"
        o = ROT_CODES[self.rot()]
        h = int(self.p["height"])
        w = int(self.p["width"]) or h
        font = f"^A0{o},{h},{w}"
        fr = "^FR" if self.p["reverse"] else ""
        x, y = int(self.p["x"]), int(self.p["y"])
        bw = int(self.p["block_width"])
        align = self.p["align"]
        lines = text.split("\n")

        if not bw and len(lines) == 1:
            return f"{self.fo()}{font}{fr}{Z.fd(text)}"

        if not bw and self.rot() == 0 and align in ("L", "J"):
            # Satırları ayrı alanlar olarak bas: yazıcı fontu önizlemeden geniş olsa
            # bile satır kaydırma ve üst üste binme olmaz.
            return "\n".join(f"^FO{x},{y + i * h}{font}{fr}{Z.fd(line)}"
                             for i, line in enumerate(lines))

        if bw:
            # Kullanıcı paragrafı: yazıcı kaydırır; satır sınırı koyma (taşan metin
            # ^FB'de son satırın üstüne basılır).
            fo, maxlines = self.fo(), 99
        else:
            # Ortalı / sağa yaslı ya da döndürülmüş çok satırlı metin: kutuyu genişlet ki
            # yazıcı fontu sığsın, hizalama noktası (orta / sağ kenar) yerinde kalsın.
            cw = self.content_size(ctx)[0]
            if self.rot() == 0:
                margin = min(x, max(200, cw // 2))
                fo = f"^FO{x - margin},{y}"
                bw = cw + (2 * margin if align == "C" else margin)
            else:
                fo, bw = self.fo(), cw * 2 + 100
            maxlines = len(lines) + 5
        data = text.replace("\\", "\\\\").replace("\n", "\\&")
        fh = ""
        if any(c in data for c in "^~"):
            data = "".join("_%02X" % ord(c) if c in "^~_" else c for c in data)
            fh = "^FH_"
        return f"{fo}{font}^FB{bw},{maxlines},0,{align},0{fr}{fh}^FD{data}^FS"


# ============================================================================ Barkod

class BarcodeItem(LabelItem):
    TYPE = "barcode"
    TITLE = tr('Barkod')
    ROTATABLE = True
    DEFAULTS = {"symbology": "code128", "data": "123456789012", "module": 2, "height": 80, "hr": True,
                "ratio": 3.0, "ecc": "M", "columns": 0}
    SCALE_KEYS = ("module", "height")
    REBUILD_KEYS = ("symbology",)

    def fields(self):
        sym = self.p["symbology"]
        is2d = B.SYMBOLOGIES[sym][2]
        f = super().fields() + [
            ("symbology", tr('Tür'), "choice", [(v[0], k) for k, v in B.SYMBOLOGIES.items()]),
            ("data", tr('Veri  ({DEĞİŞKEN} kullanılabilir)'), "str", None),
            ("module", tr('Büyütme (nokta)') if sym in ("qr",) else tr('Modül genişliği (nokta)'), "int", (1, 10 if sym == "qr" else 30)),
        ]
        if not is2d:
            f += [("height", tr('Yükseklik (nokta)'), "int", (1, 2000)),
                  ("hr", tr('Altına okunur metin yaz'), "bool", None)]
            if sym in ("code39", "i2of5"):
                f += [("ratio", tr('Kalın/ince oranı'), "float", (2.0, 3.0))]
        if sym == "qr":
            f = [x for x in f if x[0] != "rotation"]   # ^BQ döndürülemez
            f += [("ecc", tr('Hata düzeltme'), "choice", B.QR_ECC)]
        if sym == "pdf417":
            f += [("height", tr('Satır yüksekliği (nokta)'), "int", (1, 255)),
                  ("columns", tr('Sütun (0 = otomatik)'), "int", (0, 30))]
        return f + self.tail_fields()

    def set_prop(self, key, value):
        if key == "symbology":
            if value == "qr":
                self.p["module"] = max(int(self.p["module"]), 4)
                self.p["rotation"] = 0
            elif value == "datamatrix":
                self.p["module"] = max(int(self.p["module"]), 5)
            elif value == "pdf417":
                self.p["height"] = int(self.p["module"]) * 3
            elif self.p["symbology"] in ("pdf417",):
                self.p["height"] = 80
            if value == "ean13" and not self.p["data"].isdigit():
                self.p["data"] = "590123412345"
        super().set_prop(key, value)

    def rot(self):
        return 0 if self.p["symbology"] == "qr" else super().rot()

    def _matrix(self, ctx=None):
        data = ctx.sub(self.p["data"]) if ctx else self.p["data"]
        try:
            self._error = None
            return B.matrix(self.p["symbology"], data, self.p.get("ecc", "M"), int(self.p.get("columns", 0)))
        except B.BarcodeError as e:
            self._error = str(e)
            return None

    def _hr_height(self):
        return max(14, int(self.p["module"]) * 9 + 4)

    def content_size(self, ctx=None):
        m = int(self.p["module"])
        sym = self.p["symbology"]
        mat = self._matrix()
        if mat is None:
            return 200, max(40, int(self.p["height"]))
        cols, rows = len(mat[0]), len(mat)
        if sym in ("qr", "datamatrix"):
            return cols * m, rows * m
        if sym == "pdf417":
            return cols * m, rows * int(self.p["height"])
        h = int(self.p["height"])
        if self.p["hr"]:
            h += self._hr_height()
        return cols * m, h

    def paint_content(self, painter, w, h, ctx=None):
        mat = self._matrix(ctx)
        if mat is None:
            self.paint_error(painter, w, h, self._error or tr('Barkod hatası'))
            return
        m = int(self.p["module"])
        sym = self.p["symbology"]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0))
        if B.SYMBOLOGIES[sym][2]:
            rh = int(self.p["height"]) if sym == "pdf417" else m
            for y, row in enumerate(mat):
                x = 0
                while x < len(row):
                    if row[x]:
                        x2 = x
                        while x2 < len(row) and row[x2]:
                            x2 += 1
                        painter.drawRect(QRectF(x * m, y * rh, (x2 - x) * m, rh))
                        x = x2
                    else:
                        x += 1
            return
        bh = int(self.p["height"])
        row = mat[0]
        x = 0
        while x < len(row):
            if row[x]:
                x2 = x
                while x2 < len(row) and row[x2]:
                    x2 += 1
                painter.drawRect(QRectF(x * m, 0, (x2 - x) * m, bh))
                x = x2
            else:
                x += 1
        if self.p["hr"]:
            data = ctx.sub(self.p["data"]) if ctx else self.p["data"]
            f = QFont(zebra_font_family())
            f.setPixelSize(self._hr_height() - 4)
            painter.setFont(f)
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(QRectF(0, bh + 2, w, self._hr_height() - 2),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                             B.human_readable(sym, data))

    def zpl(self, ctx):
        o = ROT_CODES[self.rot()]
        return self.fo() + B.zpl(self.p["symbology"], o, self.p, Z.fd(ctx.sub(self.p["data"])))


# ============================================================================ Şekiller

COLORS = [(tr('Siyah'), "B"), (tr('Beyaz'), "W")]


class BoxItem(LabelItem):
    TYPE = "box"
    TITLE = tr('Kutu')
    RESIZABLE = True
    DEFAULTS = {"w": 200, "h": 100, "thickness": 3, "rounding": 0, "color": "B"}
    SCALE_KEYS = ("w", "h", "thickness")

    def fields(self):
        return super().fields() + [
            ("w", tr('Genişlik (nokta)'), "int", (1, 20000)),
            ("h", tr('Yükseklik (nokta)'), "int", (1, 20000)),
            ("thickness", tr('Kalınlık (nokta)'), "int", (1, 20000)),
            ("rounding", tr('Köşe yuvarlama (0-8)'), "int", (0, 8)),
            ("color", tr('Renk'), "choice", COLORS),
        ] + self.tail_fields()

    def content_size(self, ctx=None):
        t = int(self.p["thickness"])
        return max(int(self.p["w"]), t), max(int(self.p["h"]), t)

    def resize_to(self, w, h):
        self.prepareGeometryChange()
        self.p["w"], self.p["h"] = w, h
        self.update()

    def paint_content(self, painter, w, h, ctx=None):
        t = min(int(self.p["thickness"]), w, h)
        col = QColor(0, 0, 0) if self.p["color"] == "B" else QColor(255, 255, 255)
        r = int(self.p["rounding"]) / 8 * min(w, h) / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(col)
        if t * 2 >= min(w, h):
            painter.drawRoundedRect(QRectF(0, 0, w, h), r, r)
            return
        from PyQt6.QtGui import QPainterPath
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), r, r)
        inner = QPainterPath()
        ri = max(0.0, r - t)
        inner.addRoundedRect(QRectF(t, t, w - 2 * t, h - 2 * t), ri, ri)
        painter.drawPath(path.subtracted(inner))
        if self.p["color"] == "W":
            painter.setPen(QPen(QColor(150, 150, 150), 0, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(0, 0, w, h))

    def zpl(self, ctx):
        return (f"{self.fo()}^GB{int(self.p['w'])},{int(self.p['h'])},{int(self.p['thickness'])},"
                f"{self.p['color']},{int(self.p['rounding'])}^FS")


LINE_DIRS = [(tr('Yatay'), "H"), (tr('Dikey'), "V"), (tr('Çapraz ╲'), "L"), (tr('Çapraz ╱'), "R")]


class LineItem(LabelItem):
    TYPE = "line"
    TITLE = tr('Çizgi')
    RESIZABLE = True
    DEFAULTS = {"w": 300, "h": 100, "thickness": 3, "direction": "H", "color": "B"}
    SCALE_KEYS = ("w", "h", "thickness")

    def fields(self):
        d = self.p["direction"]
        f = super().fields() + [("direction", tr('Yön'), "choice", LINE_DIRS)]
        if d in ("H", "L", "R"):
            f.append(("w", tr('Uzunluk / genişlik (nokta)'), "int", (1, 20000)))
        if d in ("V", "L", "R"):
            f.append(("h", tr('Yükseklik (nokta)'), "int", (1, 20000)))
        f += [("thickness", tr('Kalınlık (nokta)'), "int", (1, 500)), ("color", tr('Renk'), "choice", COLORS)]
        return f + self.tail_fields()

    REBUILD_KEYS = ("direction",)

    def content_size(self, ctx=None):
        d, t = self.p["direction"], int(self.p["thickness"])
        if d == "H":
            return int(self.p["w"]), t
        if d == "V":
            return t, int(self.p["h"])
        return int(self.p["w"]), int(self.p["h"])

    def resize_to(self, w, h):
        self.prepareGeometryChange()
        d = self.p["direction"]
        if d in ("H", "L", "R"):
            self.p["w"] = w
        if d in ("V", "L", "R"):
            self.p["h"] = h
        self.update()

    def paint_content(self, painter, w, h, ctx=None):
        col = QColor(0, 0, 0) if self.p["color"] == "B" else QColor(255, 255, 255)
        d, t = self.p["direction"], int(self.p["thickness"])
        if d in ("H", "V"):
            painter.fillRect(QRectF(0, 0, w, h), col)
            return
        from PyQt6.QtGui import QPolygonF
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(col)
        if d == "R":   # ╱  sol alttan sağ üste
            poly = [QPointF(0, h), QPointF(t, h), QPointF(w, 0), QPointF(w - t, 0)]
        else:          # ╲  sol üstten sağ alta
            poly = [QPointF(0, 0), QPointF(t, 0), QPointF(w, h), QPointF(w - t, h)]
        painter.drawPolygon(QPolygonF(poly))

    def zpl(self, ctx):
        d, t, c = self.p["direction"], int(self.p["thickness"]), self.p["color"]
        if d == "H":
            return f"{self.fo()}^GB{int(self.p['w'])},{t},{t},{c},0^FS"
        if d == "V":
            return f"{self.fo()}^GB{t},{int(self.p['h'])},{t},{c},0^FS"
        return f"{self.fo()}^GD{int(self.p['w'])},{int(self.p['h'])},{t},{c},{d}^FS"


class EllipseItem(LabelItem):
    TYPE = "ellipse"
    TITLE = tr('Elips')
    RESIZABLE = True
    DEFAULTS = {"w": 150, "h": 100, "thickness": 3, "color": "B"}
    SCALE_KEYS = ("w", "h", "thickness")

    def fields(self):
        return super().fields() + [
            ("w", tr('Genişlik (nokta)'), "int", (3, 4095)),
            ("h", tr('Yükseklik (nokta)'), "int", (3, 4095)),
            ("thickness", tr('Kalınlık (nokta)'), "int", (1, 4095)),
            ("color", tr('Renk'), "choice", COLORS),
        ] + self.tail_fields()

    def content_size(self, ctx=None):
        return int(self.p["w"]), int(self.p["h"])

    def resize_to(self, w, h):
        self.prepareGeometryChange()
        self.p["w"], self.p["h"] = max(3, w), max(3, h)
        self.update()

    def paint_content(self, painter, w, h, ctx=None):
        from PyQt6.QtGui import QPainterPath
        col = QColor(0, 0, 0) if self.p["color"] == "B" else QColor(255, 255, 255)
        t = int(self.p["thickness"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(col)
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, w, h))
        if t * 2 < min(w, h):
            inner = QPainterPath()
            inner.addEllipse(QRectF(t, t, w - 2 * t, h - 2 * t))
            path = path.subtracted(inner)
        painter.drawPath(path)

    def zpl(self, ctx):
        return f"{self.fo()}^GE{int(self.p['w'])},{int(self.p['h'])},{int(self.p['thickness'])},{self.p['color']}^FS"


# ============================================================================ Resim

class ImageItem(LabelItem):
    TYPE = "image"
    TITLE = tr('Resim')
    ROTATABLE = True
    RESIZABLE = True
    DEFAULTS = {"png": "", "w": 200, "h": 200, "threshold": 128, "dither": True, "keep_aspect": True}
    SCALE_KEYS = ("w", "h")

    def __init__(self, props=None):
        super().__init__(props)
        self._src = None
        self._mono = None
        self._mono_key = None

    @staticmethod
    def from_file(path, dpi=203):
        img = QImage(path)
        if img.isNull():
            raise ValueError(tr('Resim açılamadı'))
        # varsayılan boyut: en fazla ~40 mm
        maxd = int(40 / 25.4 * dpi)
        w, h = img.width(), img.height()
        f = min(1.0, maxd / max(w, h))
        it = ImageItem({"w": max(1, int(w * f)), "h": max(1, int(h * f))})
        it.set_image(img)
        return it

    def set_image(self, img: QImage):
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        self.p["png"] = base64.b64encode(bytes(ba)).decode("ascii")
        self._src = None
        self._mono = None
        self.invalidate()

    def source(self):
        if self._src is None and self.p["png"]:
            self._src = QImage.fromData(base64.b64decode(self.p["png"]))
        return self._src

    def fields(self):
        return super().fields() + [
            ("png", tr('Resim dosyası'), "image", None),
            ("w", tr('Genişlik (nokta)'), "int", (1, 20000)),
            ("h", tr('Yükseklik (nokta)'), "int", (1, 20000)),
            ("keep_aspect", tr('En-boy oranını koru'), "bool", None),
            ("threshold", tr('Eşik (0-255)'), "int", (1, 254)),
            ("dither", tr('Titreme (gri tonlar için)'), "bool", None),
        ] + self.tail_fields()

    def set_prop(self, key, value):
        src = self.source()
        if key in ("w", "h") and self.p.get("keep_aspect") and src and not src.isNull() and src.width():
            ar = src.height() / src.width()
            if key == "w":
                self.p["h"] = max(1, int(round(value * ar)))
            else:
                self.p["w"] = max(1, int(round(value / ar)))
        if key == "png":
            self._src = None
        super().set_prop(key, value)

    def resize_to(self, w, h):
        src = self.source()
        if self.rot() in (90, 270):
            w, h = h, w
        if self.p.get("keep_aspect") and src and src.width():
            h = max(1, int(round(w * src.height() / src.width())))
        self.prepareGeometryChange()
        self.p["w"], self.p["h"] = w, h
        self.update()

    def content_size(self, ctx=None):
        return int(self.p["w"]), int(self.p["h"])

    def mono(self):
        key = (len(self.p["png"]), self.p["png"][:64], self.p["w"], self.p["h"], self.p["threshold"], self.p["dither"])
        if self._mono_key != key:
            src = self.source()
            if src is None or src.isNull():
                self._mono = None
            else:
                scaled = src.scaled(int(self.p["w"]), int(self.p["h"]), Qt.AspectRatioMode.IgnoreAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
                bg = QImage(scaled.size(), QImage.Format.Format_ARGB32)
                bg.fill(QColor(255, 255, 255))
                p = QPainter(bg)
                p.drawImage(0, 0, scaled)
                p.end()
                self._mono = _to_mono(bg, int(self.p["threshold"]), bool(self.p["dither"]))
            self._mono_key = key
        return self._mono

    def paint_content(self, painter, w, h, ctx=None):
        m = self.mono()
        if m is None:
            self.paint_error(painter, w, h, tr('Resim yok'))
            return
        painter.drawImage(QRectF(0, 0, w, h), m)

    def zpl(self, ctx):
        m = self.mono()
        if m is None:
            return ""
        img = m.transformed(QTransform().rotate(self.rot())) if self.rot() else m
        gf, _, _ = Z.qimage_to_gf(img)
        return f"{self.fo()}{gf}^FS"


def _to_mono(img: QImage, threshold, dither):
    g = img.convertToFormat(QImage.Format.Format_Grayscale8)
    w, h = g.width(), g.height()
    out = QImage(w, h, QImage.Format.Format_ARGB32)
    if dither:
        # Floyd-Steinberg
        px = [[g.pixelColor(x, y).value() + 0.0 for x in range(w)] for y in range(h)]
        for y in range(h):
            row = px[y]
            for x in range(w):
                old = row[x]
                new = 0 if old < threshold else 255
                err = old - new
                row[x] = new
                if x + 1 < w:
                    row[x + 1] += err * 7 / 16
                if y + 1 < h:
                    nr = px[y + 1]
                    if x > 0:
                        nr[x - 1] += err * 3 / 16
                    nr[x] += err * 5 / 16
                    if x + 1 < w:
                        nr[x + 1] += err / 16
        black, white = QColor(0, 0, 0).rgb(), QColor(255, 255, 255).rgb()
        for y in range(h):
            for x in range(w):
                out.setPixel(x, y, black if px[y][x] < 128 else white)
    else:
        black, white = QColor(0, 0, 0).rgb(), QColor(255, 255, 255).rgb()
        for y in range(h):
            for x in range(w):
                out.setPixel(x, y, black if g.pixelColor(x, y).value() < threshold else white)
    return out


ITEM_TYPES = {cls.TYPE: cls for cls in (TextItem, BarcodeItem, BoxItem, LineItem, EllipseItem, ImageItem)}


def item_from_dict(d):
    cls = ITEM_TYPES.get(d.get("type"))
    if not cls:
        raise ValueError(tr('Bilinmeyen öğe türü: {0}').format(d.get('type')))
    return cls(d)
