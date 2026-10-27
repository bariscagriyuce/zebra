"""Özellik paneli ve diyaloglar (etiket ayarları, yazdırma)."""

import csv
import os

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QImage, QFontDatabase
from PyQt6.QtWidgets import (
    QWidget, QFormLayout, QSpinBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit, QCheckBox, QComboBox,
    QLabel, QPushButton, QFileDialog, QVBoxLayout, QHBoxLayout, QDialog, QDialogButtonBox, QGroupBox,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea, QFontComboBox,
)

from .. import printers as P
from ..connection import make_connection
from ..qtutil import run_async
from .items import ZEBRA0, ImageItem
from .document import mm_to_dots, dots_to_mm


class PropertyPanel(QScrollArea):
    """Seçili öğenin özelliklerini düzenler."""

    changed = pyqtSignal(object, str)   # öğe, anahtar

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.item = None
        self.editors = {}
        self._updating = False
        self._dpi = 203
        self._set_empty()

    def _set_empty(self, msg="Bir öğe seçin ya da araç çubuğundan ekleyin."):
        w = QLabel(msg)
        w.setWordWrap(True)
        w.setAlignment(Qt.AlignmentFlag.AlignTop)
        w.setMargin(10)
        self.setWidget(w)
        self.editors = {}

    def set_item(self, item, dpi=203):
        self.item = item
        self._dpi = dpi
        if item is None:
            self._set_empty()
            return
        host = QWidget()
        form = QFormLayout(host)
        form.addRow(QLabel(f"<b>{item.TITLE}</b>"))
        self.editors = {}
        for key, label, kind, extra in item.fields():
            ed = self._make_editor(key, kind, extra)
            self.editors[key] = (ed, kind)
            if kind == "bool":
                form.addRow(ed)
            else:
                form.addRow(label + ":", ed)
        self.mm_label = QLabel()
        form.addRow(self.mm_label)
        self.setWidget(host)
        self.refresh()

    def refresh(self):
        if not self.item:
            return
        self._updating = True
        try:
            for key, (ed, kind) in self.editors.items():
                v = self.item.p.get(key)
                if kind == "int":
                    ed.setValue(int(v))
                elif kind == "float":
                    ed.setValue(float(v))
                elif kind == "str":
                    if ed.text() != str(v):
                        ed.setText(str(v))
                elif kind == "text":
                    if ed.toPlainText() != str(v):
                        ed.setPlainText(str(v))
                elif kind == "bool":
                    ed.setChecked(bool(v))
                elif kind == "choice":
                    i = ed.findData(v)
                    ed.setCurrentIndex(max(0, i))
                elif kind == "font":
                    i = ed.findData(v)
                    if i < 0:
                        ed.addItem(v, v)
                        i = ed.count() - 1
                    ed.setCurrentIndex(i)
            x, y = self.item.p["x"], self.item.p["y"]
            w, h = self.item.outer_size()
            d = self._dpi
            self.mm_label.setText(f"<small>Konum: {dots_to_mm(x, d):.1f} × {dots_to_mm(y, d):.1f} mm · "
                                  f"Boyut: {dots_to_mm(w, d):.1f} × {dots_to_mm(h, d):.1f} mm</small>")
        finally:
            self._updating = False

    def _emit(self, key, value):
        if self._updating or not self.item:
            return
        if self.item.p.get(key) == value:
            return
        self.item.set_prop(key, value)
        self.changed.emit(self.item, key)
        if key in self.item.REBUILD_KEYS or (key in ("symbology", "direction", "font")):
            QTimer.singleShot(0, lambda it=self.item: self.set_item(it, self._dpi))
        else:
            self.refresh()

    def _make_editor(self, key, kind, extra):
        if kind == "int":
            ed = QSpinBox()
            ed.setRange(*extra)
            ed.setKeyboardTracking(False)
            ed.valueChanged.connect(lambda v, k=key: self._emit(k, int(v)))
        elif kind == "float":
            ed = QDoubleSpinBox()
            ed.setRange(*extra)
            ed.setSingleStep(0.1)
            ed.valueChanged.connect(lambda v, k=key: self._emit(k, float(v)))
        elif kind == "str":
            ed = QLineEdit()
            ed.textEdited.connect(lambda v, k=key: self._emit(k, v))
        elif kind == "text":
            ed = QPlainTextEdit()
            ed.setMaximumHeight(90)
            ed.textChanged.connect(lambda k=key, e=ed: self._emit(k, e.toPlainText()))
        elif kind == "bool":
            ed = QCheckBox(dict((f[0], f[1]) for f in self.item.fields())[key])
            ed.toggled.connect(lambda v, k=key: self._emit(k, bool(v)))
        elif kind == "choice":
            ed = QComboBox()
            for label, val in extra:
                ed.addItem(label, val)
            ed.currentIndexChanged.connect(lambda i, k=key, e=ed: self._emit(k, e.itemData(i)))
        elif kind == "font":
            ed = QComboBox()
            ed.addItem("Zebra 0 (yazıcı fontu, hızlı)", ZEBRA0)
            for fam in QFontDatabase.families():
                ed.addItem(f"{fam}  (grafik olarak)", fam)
            ed.setMaxVisibleItems(20)
            ed.currentIndexChanged.connect(lambda i, k=key, e=ed: self._emit(k, e.itemData(i)))
        elif kind == "image":
            ed = QPushButton("Resim seç…")
            ed.clicked.connect(self._pick_image)
        else:
            ed = QLabel("?")
        return ed

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Resim seç", "",
                                              "Resimler (*.png *.jpg *.jpeg *.bmp *.gif *.svg *.webp)")
        if path and isinstance(self.item, ImageItem):
            img = QImage(path)
            if img.isNull():
                QMessageBox.warning(self, "Resim", "Resim açılamadı.")
                return
            self.item.set_image(img)
            self.item.set_prop("h", self.item.p["h"]) if not self.item.p.get("keep_aspect") else \
                self.item.set_prop("w", self.item.p["w"])
            self.changed.emit(self.item, "png")
            self.refresh()


# ============================================================================ Etiket ayarları

PRESETS = [
    ("Özel", None, None),
    ("100 × 150 mm (kargo)", 100, 150),
    ("100 × 100 mm", 100, 100),
    ("100 × 50 mm", 100, 50),
    ("102 × 76 mm (4×3\")", 101.6, 76.2),
    ("58 × 40 mm", 58, 40),
    ("58 × 30 mm", 58, 30),
    ("50 × 25 mm", 50, 25),
    ("40 × 30 mm", 40, 30),
    ("38 × 25 mm", 38, 25),
    ("30 × 20 mm", 30, 20),
]


class LabelDialog(QDialog):
    def __init__(self, label, printers, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Etiket ayarları")
        self.printers = printers
        v = QVBoxLayout(self)

        box = QGroupBox("Boyut")
        f = QFormLayout(box)
        self.preset = QComboBox()
        for name, w, h in PRESETS:
            self.preset.addItem(name, (w, h))
        self.preset.currentIndexChanged.connect(self._preset)
        f.addRow("Hazır boyut:", self.preset)
        self.w = QDoubleSpinBox()
        self.w.setRange(5, 1000)
        self.w.setDecimals(1)
        self.w.setSuffix(" mm")
        self.w.setValue(label["width_mm"])
        self.h = QDoubleSpinBox()
        self.h.setRange(5, 3000)
        self.h.setDecimals(1)
        self.h.setSuffix(" mm")
        self.h.setValue(label["height_mm"])
        f.addRow("Genişlik:", self.w)
        f.addRow("Yükseklik:", self.h)
        self.dpi = QComboBox()
        for d in (152, 203, 300, 600):
            self.dpi.addItem(f"{d} dpi ({round(d / 25.4)} nokta/mm)", d)
        self.dpi.setCurrentIndex(max(0, self.dpi.findData(int(label["dpi"]))))
        f.addRow("Çözünürlük:", self.dpi)
        self.dots = QLabel()
        f.addRow("", self.dots)
        for s in (self.w, self.h):
            s.valueChanged.connect(self._upd)
        self.dpi.currentIndexChanged.connect(self._upd)
        h = QHBoxLayout()
        self.from_printer = QComboBox()
        for p in printers:
            if p.kind != "cups":
                self.from_printer.addItem(p.name, p)
        b = QPushButton("Yazıcıdan al")
        b.setToolTip("Baskı genişliği, etiket uzunluğu ve çözünürlüğü yazıcıdan okur")
        b.clicked.connect(self._read_printer)
        h.addWidget(self.from_printer, 1)
        h.addWidget(b)
        if self.from_printer.count():
            f.addRow("Yazıcı:", h)
        v.addWidget(box)

        box = QGroupBox("Baskı (boş bırakılırsa yazıcı ayarı kullanılır)")
        f = QFormLayout(box)
        self.speed = QComboBox()
        self.speed.addItem("Yazıcı ayarı", 0)
        for s in range(1, 15):
            self.speed.addItem(f"{s} ips", s)
        self.speed.setCurrentIndex(max(0, self.speed.findData(int(label.get("speed", 0)))))
        f.addRow("Hız:", self.speed)
        self.dark = QSpinBox()
        self.dark.setRange(-1, 30)
        self.dark.setSpecialValueText("Yazıcı ayarı")
        self.dark.setValue(int(label.get("darkness", -1)))
        f.addRow("Koyuluk:", self.dark)
        self.method = QComboBox()
        for t, c in (("Yazıcı ayarı", ""), ("Termal transfer", "T"), ("Direkt termal", "D")):
            self.method.addItem(t, c)
        self.method.setCurrentIndex(max(0, self.method.findData(label.get("method", ""))))
        f.addRow("Baskı yöntemi:", self.method)
        self.media = QComboBox()
        for t, c in (("Yazıcı ayarı", ""), ("Boşluklu", "W"), ("Siyah işaretli", "M"), ("Sürekli", "N")):
            self.media.addItem(t, c)
        self.media.setCurrentIndex(max(0, self.media.findData(label.get("media", ""))))
        f.addRow("Medya:", self.media)
        self.invert = QCheckBox("Etiketi 180° döndürerek bas (^POI)")
        self.invert.setChecked(bool(label.get("invert")))
        f.addRow(self.invert)
        self.hx = QSpinBox()
        self.hx.setRange(-2000, 2000)
        self.hx.setValue(int(label.get("home_x", 0)))
        self.hy = QSpinBox()
        self.hy.setRange(-2000, 2000)
        self.hy.setValue(int(label.get("home_y", 0)))
        hh = QHBoxLayout()
        hh.addWidget(QLabel("X"))
        hh.addWidget(self.hx)
        hh.addWidget(QLabel("Y"))
        hh.addWidget(self.hy)
        f.addRow("Kaydırma (^LH, nokta):", hh)
        v.addWidget(box)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._upd()

    def _preset(self, i):
        w, h = self.preset.itemData(i)
        if w:
            self.w.setValue(w)
            self.h.setValue(h)

    def _upd(self):
        d = self.dpi.currentData()
        self.dots.setText(f"= {mm_to_dots(self.w.value(), d)} × {mm_to_dots(self.h.value(), d)} nokta")

    def _read_printer(self):
        cfg = self.from_printer.currentData()
        if not cfg:
            return

        def fn():
            with make_connection(cfg) as c:
                return (P.getvar(c, "ezpl.print_width"), P.getvar(c, "zpl.label_length"),
                        P.getvar(c, "head.resolution.in_dpi"))

        def done(r):
            pw, ll, dpi = r
            try:
                d = int(dpi) if dpi else cfg.dpi
                if d not in (152, 203, 300, 600):
                    d = min((152, 203, 300, 600), key=lambda x: abs(x - d))
                self.dpi.setCurrentIndex(max(0, self.dpi.findData(d)))
                if pw:
                    self.w.setValue(round(dots_to_mm(int(pw), d), 1))
                if ll:
                    self.h.setValue(round(dots_to_mm(int(ll), d), 1))
            except ValueError:
                QMessageBox.warning(self, "Yazıcı", f"Beklenmeyen yanıt: {r}")

        run_async(fn, done, lambda m: QMessageBox.critical(self, "Yazıcı", m))

    def label(self):
        return {"width_mm": self.w.value(), "height_mm": self.h.value(), "dpi": self.dpi.currentData(),
                "speed": self.speed.currentData(), "darkness": self.dark.value(),
                "method": self.method.currentData(), "media": self.media.currentData(),
                "invert": self.invert.isChecked(), "home_x": self.hx.value(), "home_y": self.hy.value()}


# ============================================================================ Yazdırma

def all_printers():
    """Kayıtlı yazıcılar + kayıtlı olmayan CUPS kuyrukları."""
    saved = P.load_printers()
    known = {(p.kind, p.address) for p in saved}
    for p in P.discover_cups():
        if (p.kind, p.address) not in known:
            saved.append(p)
    return saved


class PrintDialog(QDialog):
    def __init__(self, scene, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.setWindowTitle("Yazdır")
        self.resize(560, 520)
        v = QVBoxLayout(self)
        f = QFormLayout()
        self.printer = QComboBox()
        for p in all_printers():
            self.printer.addItem(f"{p.name}  —  {p.describe()}", p)
        f.addRow("Yazıcı:", self.printer)
        self.copies = QSpinBox()
        self.copies.setRange(1, 99999)
        f.addRow("Kopya (her etiket için):", self.copies)
        v.addLayout(f)

        self.vars = scene.variables()
        builtin = {"TARIH", "SAAT", "DATE", "TIME", "SAYAC"}
        user_vars = [x for x in self.vars if x not in builtin]

        box = QGroupBox("Değişkenler")
        bv = QVBoxLayout(box)
        if not self.vars:
            bv.addWidget(QLabel("Tasarımda {DEĞİŞKEN} yok. Metin/barkod içine {AD}, {SAYAC}, {TARIH} gibi "
                                "alanlar yazarak değişkenli baskı yapabilirsiniz."))
        else:
            bv.addWidget(QLabel("Hazır: {TARIH} {SAAT} {DATE} {TIME} {SAYAC}. Diğerleri aşağıdan elle ya da CSV "
                                "dosyasından doldurulur (sütun adları değişken adlarıyla eşleşmeli)."))
        h = QHBoxLayout()
        self.csv_path = QLineEdit()
        self.csv_path.setPlaceholderText("CSV dosyası (isteğe bağlı) — her satır bir etiket")
        h.addWidget(self.csv_path, 1)
        b = QPushButton("CSV…")
        b.clicked.connect(self._pick_csv)
        h.addWidget(b)
        bv.addLayout(h)
        self.table = QTableWidget(1, len(user_vars))
        self.table.setHorizontalHeaderLabels(user_vars)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setVisible(bool(user_vars))
        bv.addWidget(self.table)
        hb = QHBoxLayout()
        b1 = QPushButton("Satır ekle")
        b1.clicked.connect(lambda: self.table.insertRow(self.table.rowCount()))
        b2 = QPushButton("Satır sil")
        b2.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()) if self.table.rowCount() > 1 else None)
        hb.addWidget(b1)
        hb.addWidget(b2)
        hb.addStretch(1)
        if user_vars:
            bv.addLayout(hb)
        self.user_vars = user_vars
        hs = QHBoxLayout()
        hs.addWidget(QLabel("{SAYAC} başlangıç:"))
        self.cstart = QSpinBox()
        self.cstart.setRange(-10 ** 9, 10 ** 9)
        self.cstart.setValue(1)
        hs.addWidget(self.cstart)
        hs.addWidget(QLabel("artış:"))
        self.cstep = QSpinBox()
        self.cstep.setRange(-10 ** 6, 10 ** 6)
        self.cstep.setValue(1)
        hs.addWidget(self.cstep)
        hs.addWidget(QLabel("hane:"))
        self.cpad = QSpinBox()
        self.cpad.setRange(0, 20)
        hs.addWidget(self.cpad)
        self.ccount = QSpinBox()
        self.ccount.setRange(1, 99999)
        hs.addWidget(QLabel("adet:"))
        hs.addWidget(self.ccount)
        hs.addStretch(1)
        if "SAYAC" in self.vars:
            bv.addLayout(hs)
        v.addWidget(box)

        self.summary = QLabel()
        v.addWidget(self.summary)
        bb = QDialogButtonBox()
        self.btn_print = bb.addButton("Yazdır", QDialogButtonBox.ButtonRole.AcceptRole)
        bzpl = bb.addButton("ZPL kaydet…", QDialogButtonBox.ButtonRole.ActionRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._print)
        bb.rejected.connect(self.reject)
        bzpl.clicked.connect(self._save_zpl)
        v.addWidget(bb)
        for w in (self.copies, self.ccount):
            w.valueChanged.connect(self._summary)
        self.table.itemChanged.connect(self._summary)
        self.csv_path.textChanged.connect(self._summary)
        self._summary()

    def _pick_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "CSV seç", "", "CSV (*.csv *.txt);;Tüm dosyalar (*)")
        if path:
            self.csv_path.setText(path)

    def _records(self):
        path = self.csv_path.text().strip()
        if path:
            with open(path, newline="", encoding="utf-8-sig") as f:
                sample = f.read(4096)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                except csv.Error:
                    dialect = csv.excel
                return [dict(r) for r in csv.DictReader(f, dialect=dialect)]
        if self.user_vars:
            recs = []
            for r in range(self.table.rowCount()):
                rec = {}
                for c, name in enumerate(self.user_vars):
                    it = self.table.item(r, c)
                    rec[name] = it.text() if it else ""
                recs.append(rec)
            return recs
        return [{}]

    def _expanded(self):
        """Her etiket için değer sözlükleri (sayaç dahil)."""
        recs = self._records()
        if "SAYAC" in self.vars:
            n = self.ccount.value() if len(recs) <= 1 else len(recs)
            base = recs[0] if len(recs) == 1 else None
            out = []
            for i in range(n):
                rec = dict(base if base is not None else recs[i])
                val = self.cstart.value() + i * self.cstep.value()
                rec.setdefault("SAYAC", str(val).zfill(self.cpad.value()) if self.cpad.value() else str(val))
                out.append(rec)
            return out
        return recs

    def _summary(self):
        try:
            n = len(self._expanded())
            self.summary.setText(f"<b>{n}</b> farklı etiket × {self.copies.value()} kopya = "
                                 f"<b>{n * self.copies.value()}</b> etiket basılacak.")
        except Exception as e:  # noqa: BLE001
            self.summary.setText(f"<span style='color:#c00'>CSV okunamadı: {e}</span>")

    def build_zpl(self):
        recs = self._expanded()
        return "".join(self.scene.zpl(r, self.copies.value()) for r in recs)

    def _save_zpl(self):
        try:
            data = self.build_zpl()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "ZPL", str(e))
            return
        path, _ = QFileDialog.getSaveFileName(self, "ZPL kaydet", "etiket.zpl", "ZPL (*.zpl *.prn)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)

    def _print(self):
        cfg = self.printer.currentData()
        if not cfg:
            QMessageBox.warning(self, "Yazıcı", "Yazıcı yok. Zebra Kurulum Aracı ile yazıcı ekleyin.")
            return
        try:
            data = self.build_zpl()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Hata", str(e))
            return
        self.btn_print.setEnabled(False)

        def fn():
            with make_connection(cfg) as c:
                c.send(data)

        def done(_):
            self.accept()

        def err(m):
            self.btn_print.setEnabled(True)
            QMessageBox.critical(self, "Yazdırma hatası", m)

        run_async(fn, done, err)
