"""Zebra Kurulum Aracı - Zebra Setup Utilities benzeri Linux uygulaması."""

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QFont, QIcon, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QListWidget, QListWidgetItem, QVBoxLayout,
    QHBoxLayout, QPushButton, QTabWidget, QLabel, QFormLayout, QGroupBox, QSpinBox, QComboBox,
    QMessageBox, QPlainTextEdit, QCheckBox, QProgressDialog, QGridLayout, QLineEdit,
    QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QStatusBar,
    QInputDialog,
)

from . import printers as P
from .connection import PrinterConfig, make_connection
from .qtutil import run_async, PrinterDialog, FilePicker, error_box

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(APP_DIR, "icons", "zebra-setup.svg")


def mm(dots, dpi):
    return dots * 25.4 / dpi


def _num(s):
    """'0208', '+000', '22.0', '5 IPS' gibi değerlerden sayı çıkar."""
    import re
    m = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", s or "")
    return int(round(float(m.group(1)))) if m else None


# --- Yeniden kullanılabilir ayar eşlemeleri ---------------------------------

SPEEDS = [(f"{s} ips  ({s * 25.4:.0f} mm/sn)", s) for s in range(1, 15)]
PRINT_METHODS = [("Termal transfer (ribonlu)", "T", "thermal trans"),
                 ("Direkt termal (ribonsuz)", "D", "direct thermal")]
MEDIA_TYPES = [("Boşluklu / çentikli etiket", "W", "gap/notch"),
               ("Siyah işaretli", "M", "mark"),
               ("Sürekli (continuous)", "N", "continuous")]
PRINT_MODES = [("Yırtma (Tear-off)", "T", "tear off"),
               ("Soyma (Peel-off)", "P", "peel off"),
               ("Kesici (Cutter)", "C", "cutter"),
               ("Geri sarma (Rewind)", "R", "rewind"),
               ("Aplikatör", "A", "applicator"),
               ("Gecikmeli kesici", "D", "delayed cut")]

QUICK_COMMANDS = [
    ("Kimlik (~HI)", "~HI", True),
    ("Durum (~HS)", "~HS", True),
    ("Yapılandırma metni (^HH)", "^XA^HH^XZ", True),
    ("Tüm SGD değişkenleri (allcv)", '! U1 getvar "allcv"\r\n', True),
    ("Model (SGD)", '! U1 getvar "device.product_name"\r\n', True),
    ("Yapılandırma etiketi bas (~WC)", "~WC", False),
    ("Ağ etiketi bas (~WL)", "~WL", False),
    ("Kalibre et (~JC)", "~JC", False),
]


class SetupWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Zebra Kurulum Aracı")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(1100, 720)
        self.printers = P.load_printers()

        split = QSplitter()
        self.setCentralWidget(split)

        # --- sol: yazıcı listesi
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("<b>Yazıcılar</b>"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._printer_changed)
        self.list.itemDoubleClicked.connect(lambda *_: self._edit_printer())
        ll.addWidget(self.list, 1)
        grid = QGridLayout()
        for i, (txt, fn) in enumerate([("Ekle…", self._add_printer), ("Bul…", self._discover),
                                       ("Düzenle…", self._edit_printer), ("Kaldır", self._remove_printer)]):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        ll.addLayout(grid)
        split.addWidget(left)

        # --- sağ: sekmeler
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_status_tab(), "Durum")
        self.tabs.addTab(self._build_settings_tab(), "Ayarlar")
        self.tabs.addTab(self._build_actions_tab(), "Eylemler")
        self.tabs.addTab(self._build_network_tab(), "Ağ")
        self.tabs.addTab(self._build_send_tab(), "Dosya gönder")
        self.tabs.addTab(self._build_files_tab(), "Yazıcı belleği")
        self.tabs.addTab(self._build_terminal_tab(), "Terminal")
        split.addWidget(self.tabs)
        split.setSizes([260, 840])

        self.setStatusBar(QStatusBar())
        self._build_menu()
        self._refresh_list()
        if not self.printers:
            self.statusBar().showMessage("Yazıcı yok. 'Bul…' ile ağda/USB'de/CUPS'ta arayın ya da 'Ekle…' ile ekleyin.")

    # ------------------------------------------------------------------ menü
    def _build_menu(self):
        m = self.menuBar().addMenu("&Dosya")
        a = QAction("Etiket Tasarımcısını aç", self)
        a.triggered.connect(self._open_designer)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Çıkış", self)
        a.setShortcut("Ctrl+Q")
        a.triggered.connect(self.close)
        m.addAction(a)
        h = self.menuBar().addMenu("&Yardım")
        a = QAction("Hakkında", self)
        a.triggered.connect(lambda: QMessageBox.about(
            self, "Hakkında",
            "<b>Zebra Kurulum Aracı</b><br>Linux için Zebra yazıcı yapılandırma aracı.<br>"
            "Ağ (9100), USB (/dev/usb/lp*) ve CUPS raw kuyrukları desteklenir."))
        h.addAction(a)

    def _open_designer(self):
        import subprocess
        subprocess.Popen([sys.executable, "-m", "zebratool.designer"],
                         cwd=os.path.dirname(APP_DIR))

    # --------------------------------------------------------- yazıcı listesi
    def _refresh_list(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.printers:
            it = QListWidgetItem(f"{p.name}\n{p.describe()}")
            self.list.addItem(it)
        self.list.blockSignals(False)
        if self.printers:
            self.list.setCurrentRow(select if select is not None else 0)
        else:
            self._printer_changed(-1)

    def current(self) -> PrinterConfig:
        row = self.list.currentRow()
        return self.printers[row] if 0 <= row < len(self.printers) else None

    def _require(self, bidirectional=False):
        cfg = self.current()
        if not cfg:
            QMessageBox.information(self, "Yazıcı yok", "Önce soldan bir yazıcı seçin.")
            return None
        if bidirectional and cfg.kind == "cups":
            QMessageBox.information(
                self, "Okuma desteklenmiyor",
                "CUPS kuyruğu üzerinden yazıcıdan bilgi okunamaz.\n"
                "Ağ veya USB bağlantısı olarak ekleyin.")
            return None
        return cfg

    def _add_printer(self):
        d = PrinterDialog(self)
        if d.exec():
            self.printers.append(d.config())
            P.save_printers(self.printers)
            self._refresh_list(len(self.printers) - 1)

    def _edit_printer(self):
        row = self.list.currentRow()
        if row < 0:
            return
        d = PrinterDialog(self, self.printers[row])
        if d.exec():
            self.printers[row] = d.config()
            P.save_printers(self.printers)
            self._refresh_list(row)

    def _remove_printer(self):
        row = self.list.currentRow()
        if row < 0:
            return
        if QMessageBox.question(self, "Kaldır", f"'{self.printers[row].name}' listeden kaldırılsın mı?") \
                == QMessageBox.StandardButton.Yes:
            del self.printers[row]
            P.save_printers(self.printers)
            self._refresh_list()

    def _discover(self):
        dlg = QProgressDialog("Yazıcılar aranıyor (CUPS, USB, yerel ağ port 9100)…", "Gizle", 0, 0, self)
        dlg.setWindowTitle("Yazıcı bul")
        dlg.setMinimumDuration(0)
        dlg.show()

        def work(progress):
            found = P.discover_cups() + P.discover_usb()
            found += P.discover_network(progress)
            # tekilleştir
            seen, uniq = set(), []
            for f in found:
                key = (f.kind, f.address, f.port)
                if key not in seen:
                    seen.add(key)
                    uniq.append(f)
            return uniq

        def progress(i, n):
            dlg.setMaximum(n)
            dlg.setValue(i)

        def done(found):
            dlg.close()
            known = {(p.kind, p.address, p.port) for p in self.printers}
            new = [f for f in found if (f.kind, f.address, f.port) not in known]
            if not new:
                QMessageBox.information(self, "Yazıcı bul", "Listede olmayan yeni yazıcı bulunamadı.")
                return
            items = [f"{f.name}  —  {f.describe()}" for f in new]
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QListWidget as LW
            d = QDialog(self)
            d.setWindowTitle("Bulunan yazıcılar")
            v = QVBoxLayout(d)
            v.addWidget(QLabel("Eklenecek yazıcıları seçin:"))
            lw = LW()
            for t in items:
                it = QListWidgetItem(t)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked)
                lw.addItem(it)
            v.addWidget(lw)
            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            bb.accepted.connect(d.accept)
            bb.rejected.connect(d.reject)
            v.addWidget(bb)
            if d.exec():
                for i, f in enumerate(new):
                    if lw.item(i).checkState() == Qt.CheckState.Checked:
                        self.printers.append(f)
                P.save_printers(self.printers)
                self._refresh_list(len(self.printers) - 1)

        def err(msg):
            dlg.close()
            error_box(self, "Arama hatası", msg)

        run_async(work, done, err, progress)

    def _printer_changed(self, row):
        cfg = self.current()
        self.status_header.setText(f"<h2>{cfg.name}</h2>{cfg.describe()}" if cfg else "<h2>Yazıcı seçilmedi</h2>")
        for lbl in self.info_labels.values():
            lbl.setText("—")
        self.status_flags.setText("")
        if cfg and cfg.kind != "cups":
            self._refresh_status()

    # ------------------------------------------------------------ yardımcı
    def _run(self, cfg, fn, done=None, busy="Çalışıyor…"):
        """Yazıcıya bağlan, fn(conn) çalıştır."""
        self.statusBar().showMessage(f"{cfg.name}: {busy}")
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)

        def work():
            with make_connection(cfg) as c:
                return fn(c)

        def ok(res):
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(f"{cfg.name}: tamam", 4000)
            if done:
                done(res)

        def err(msg):
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(f"{cfg.name}: hata", 4000)
            error_box(self, "Yazıcı hatası", msg)

        run_async(work, ok, err)

    def _send(self, data, confirm=None, done_msg=None):
        cfg = self._require()
        if not cfg:
            return
        if confirm and QMessageBox.question(self, "Onay", confirm) != QMessageBox.StandardButton.Yes:
            return
        self._run(cfg, lambda c: c.send(data),
                  done=(lambda _: self.statusBar().showMessage(done_msg, 5000)) if done_msg else None,
                  busy="gönderiliyor…")

    # ------------------------------------------------------------- Durum
    def _build_status_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.status_header = QLabel()
        v.addWidget(self.status_header)
        self.status_flags = QLabel()
        self.status_flags.setWordWrap(True)
        f = QFont()
        f.setPointSize(f.pointSize() + 2)
        f.setBold(True)
        self.status_flags.setFont(f)
        v.addWidget(self.status_flags)

        self.prefix_box = QGroupBox("Uyarı")
        pb = QHBoxLayout(self.prefix_box)
        self.prefix_label = QLabel()
        self.prefix_label.setWordWrap(True)
        pb.addWidget(self.prefix_label, 1)
        b = QPushButton("Öneki '~' yap ve kaydet")
        b.clicked.connect(self._fix_prefix)
        pb.addWidget(b)
        self.prefix_box.setVisible(False)
        v.addWidget(self.prefix_box)

        box = QGroupBox("Yazıcı bilgileri")
        form = QFormLayout(box)
        self.info_labels = {}
        for key, title in [("model", "Model"), ("firmware", "Yazılım (firmware)"), ("serial", "Seri no"),
                           ("dpi", "Çözünürlük"), ("method", "Baskı yöntemi"), ("mode", "Baskı modu"),
                           ("length", "Etiket uzunluğu"), ("width", "Baskı genişliği"),
                           ("odometer", "Toplam baskı uzunluğu"), ("labels", "Kuyruktaki etiket"),
                           ("languages", "Diller"), ("ip", "IP adresi")]:
            lbl = QLabel("—")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.info_labels[key] = lbl
            form.addRow(title + ":", lbl)
        v.addWidget(box)
        h = QHBoxLayout()
        b = QPushButton("Yenile")
        b.clicked.connect(self._refresh_status)
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        v.addStretch(1)
        return w

    def _refresh_status(self):
        cfg = self._require(bidirectional=True)
        if not cfg:
            return

        def fn(c):
            r = {"hi": P.identify(c), "cfg": P.config_dump(c)}
            try:
                r["hs"] = P.host_status(c)
            except Exception:
                r["hs"] = None
            for var in ("device.unique_id", "odometer.total_print_length", "head.resolution.in_dpi",
                        "device.languages", "ezpl.print_width", "ip.addr", "internal_wired.ip.addr",
                        "wlan.ip.addr"):
                try:
                    r[var] = P.getvar(c, var, timeout=1.2)
                except Exception:
                    r[var] = None
            return r

        def done(r):
            hi, hs, hh = r["hi"], r["hs"], r["cfg"]
            L = self.info_labels
            L["model"].setText(hi.get("model", "?"))
            L["firmware"].setText(hi.get("firmware", "?"))
            L["serial"].setText(r.get("device.unique_id") or hh.get("SERIAL NUMBER") or "—")
            dpi = r.get("head.resolution.in_dpi")
            if not dpi and hi.get("dpmm"):
                dpi = str(round(int(hi["dpmm"]) * 25.4))
            if not dpi and P.dpi_from_config(hh):
                dpi = str(P.dpi_from_config(hh))
            L["dpi"].setText(f"{dpi} dpi ({round(int(dpi) / 25.4)} nokta/mm)" if dpi else "—")
            dpi_n = int(dpi) if dpi and dpi.isdigit() else cfg.dpi
            if hs:
                L["method"].setText("Termal transfer" if hs["thermal_transfer"] else "Direkt termal")
                L["mode"].setText(hs["print_mode"])
                L["labels"].setText(str(hs["labels_remaining"]))
            else:
                L["method"].setText(hh.get("PRINT METHOD", "—"))
                L["mode"].setText(hh.get("PRINT MODE", "—"))
                L["labels"].setText("—")
            ll = hs["label_length"] if hs else _num(hh.get("LABEL LENGTH"))
            if ll:
                L["length"].setText(f"{ll} nokta ({mm(ll, dpi_n):.1f} mm)")
            pw = _num(r.get("ezpl.print_width")) or _num(hh.get("PRINT WIDTH"))
            if pw:
                L["width"].setText(f"{pw} nokta ({mm(pw, dpi_n):.1f} mm)")
            L["odometer"].setText(r.get("odometer.total_print_length") or hh.get("TOTAL USAGE") or "—")
            L["languages"].setText(r.get("device.languages") or "—")
            ips = [r.get(k) for k in ("internal_wired.ip.addr", "wlan.ip.addr", "ip.addr")]
            ips = [i for i in ips if i and i != "0.0.0.0"]
            L["ip"].setText(", ".join(dict.fromkeys(ips)) or (cfg.address if cfg.kind == "network" else "—"))

            prefix = P.control_prefix(hh)
            self.prefix_box.setVisible(prefix is not None and prefix != 0x7E)
            if prefix is not None and prefix != 0x7E:
                self.prefix_label.setText(
                    f"<b>Kontrol öneki '~' yerine 0x{prefix:02X} olarak ayarlı.</b> Bu yüzden ~ ile başlayan "
                    f"komutlar (durum, kalibrasyon, koyuluk…) çalışmaz"
                    + (" ve 0xC3 Türkçe karakterlerin (ç, ö, ü, Ç, Ö, Ü…) UTF-8 kodunun ilk baytı olduğu "
                       "için bu harfler baskıyı bozar." if prefix == 0xC3 else "."))
            if hs is None:
                self.status_flags.setText("Durum bayrakları okunamadı (~HS yanıt vermedi)")
                self.status_flags.setStyleSheet("color: #d35400")
                return
            probs = P.status_summary(hs)
            if probs:
                self.status_flags.setText("⚠ " + " · ".join(probs))
                self.status_flags.setStyleSheet("color: #c0392b")
            else:
                self.status_flags.setText("✔ Hazır")
                self.status_flags.setStyleSheet("color: #27ae60")

        self._run(cfg, fn, done, busy="durum okunuyor…")

    def _fix_prefix(self):
        cfg = self._require()
        if not cfg:
            return
        if QMessageBox.question(
                self, "Kontrol öneki",
                "Yazıcının kontrol öneki standart '~' karakterine döndürülüp kalıcı olarak kaydedilecek "
                "(^XA^CT~^JUS^XZ).\n\nBu yazıcıyı öneki değiştirilmiş varsayan eski bir yazılım kullanıyorsa "
                "o yazılımın ~ komutları etkilenebilir. Devam edilsin mi?") != QMessageBox.StandardButton.Yes:
            return
        self._run(cfg, lambda c: c.send("^XA^CT~^JUS^XZ"),
                  lambda _: (self.prefix_box.setVisible(False), self._refresh_status()),
                  busy="kontrol öneki düzeltiliyor…")

    # ------------------------------------------------------------- Ayarlar
    def _build_settings_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)

        box = QGroupBox("Baskı")
        f = QFormLayout(box)
        self.s_dark = QSpinBox()
        self.s_dark.setRange(0, 30)
        self.s_dark.setToolTip("~SD — 0 (açık) … 30 (koyu)")
        f.addRow("Koyuluk:", self.s_dark)
        self.s_speed = QComboBox()
        for t, s in SPEEDS:
            self.s_speed.addItem(t, s)
        f.addRow("Baskı hızı:", self.s_speed)
        self.s_method = QComboBox()
        for t, code, _ in PRINT_METHODS:
            self.s_method.addItem(t, code)
        f.addRow("Baskı yöntemi:", self.s_method)
        self.s_mode = QComboBox()
        for t, code, _ in PRINT_MODES:
            self.s_mode.addItem(t, code)
        f.addRow("Baskı modu:", self.s_mode)
        v.addWidget(box)

        box = QGroupBox("Etiket / medya")
        f = QFormLayout(box)
        self.s_media = QComboBox()
        for t, code, _ in MEDIA_TYPES:
            self.s_media.addItem(t, code)
        f.addRow("Medya türü:", self.s_media)
        self.s_width = self._dots_spin(2, 3000)
        f.addRow("Baskı genişliği:", self.s_width[0])
        self.s_length = self._dots_spin(1, 32000)
        f.addRow("Etiket uzunluğu:", self.s_length[0])
        self.s_tear = QSpinBox()
        self.s_tear.setRange(-120, 120)
        self.s_tear.setSuffix(" nokta")
        f.addRow("Yırtma konumu (~TA):", self.s_tear)
        self.s_top = QSpinBox()
        self.s_top.setRange(-120, 120)
        self.s_top.setSuffix(" nokta")
        f.addRow("Etiket üstü (^LT):", self.s_top)
        self.s_left = QSpinBox()
        self.s_left.setRange(-9999, 9999)
        self.s_left.setSuffix(" nokta")
        f.addRow("Sol kaydırma (^LS):", self.s_left)
        v.addWidget(box)

        h = QHBoxLayout()
        b = QPushButton("Yazıcıdan oku")
        b.clicked.connect(self._read_settings)
        h.addWidget(b)
        b = QPushButton("Yazıcıya uygula ve kaydet")
        b.clicked.connect(self._apply_settings)
        h.addWidget(b)
        b = QPushButton("ZPL'i göster")
        b.clicked.connect(lambda: self._show_text("Ayar komutları", self._settings_zpl()))
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        v.addWidget(QLabel("<i>Not: Ayarlar ^JUS ile kalıcı olarak kaydedilir. "
                           "Medya türünü değiştirdikten sonra 'Eylemler → Kalibre et' önerilir.</i>"))
        v.addStretch(1)
        return w

    def _dots_spin(self, lo, hi):
        wdg = QWidget()
        h = QHBoxLayout(wdg)
        h.setContentsMargins(0, 0, 0, 0)
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setSuffix(" nokta")
        lbl = QLabel()
        sp.valueChanged.connect(lambda val: lbl.setText(f"= {mm(val, self._dpi()):.1f} mm"))
        h.addWidget(sp)
        h.addWidget(lbl)
        h.addStretch(1)
        return wdg, sp

    def _dpi(self):
        cfg = self.current()
        return cfg.dpi if cfg else 203

    def _read_settings(self):
        cfg = self._require(bidirectional=True)
        if not cfg:
            return
        names = ["print.tone", "media.speed", "ezpl.print_width", "zpl.label_length", "ezpl.media_type",
                 "ezpl.print_method", "media.printmode", "ezpl.tear_off", "zpl.label_top", "zpl.left_position"]

        # SGD değişkeni yoksa (eski yazıcılar) ^HH çıktısından al
        hh_map = {"print.tone": ["DARKNESS"], "media.speed": ["PRINT SPEED"],
                  "ezpl.print_width": ["PRINT WIDTH"], "zpl.label_length": ["LABEL LENGTH"],
                  "ezpl.media_type": ["MEDIA TYPE"], "ezpl.print_method": ["PRINT METHOD"],
                  "media.printmode": ["PRINT MODE"], "ezpl.tear_off": ["TEAR OFF ADJUST", "TEAR OFF"],
                  "zpl.label_top": ["LABEL TOP"], "zpl.left_position": ["LEFT POSITION"]}
        hh_text = {"THERMAL-TRANS.": "thermal trans", "DIRECT-THERMAL": "direct thermal",
                   "NON-CONTINUOUS": "gap/notch", "MARK": "mark", "CONTINUOUS": "continuous"}

        def fn(c):
            r = {n: P.getvar(c, n, timeout=1.5) for n in names}
            if any(v is None for v in r.values()):
                hh = P.config_dump(c)
                for n, keys in hh_map.items():
                    if r[n] is None:
                        for k in keys:
                            if k in hh:
                                v = hh[k]
                                r[n] = hh_text.get(v, v.lower()) if n in (
                                    "ezpl.media_type", "ezpl.print_method", "media.printmode") else str(_num(v))
                                break
            return r

        def num(s, default=0):
            try:
                return int(round(float(s)))
            except (TypeError, ValueError):
                return default

        def pick(combo, table, sgd_value):
            for i, row in enumerate(table):
                if sgd_value and row[2] == sgd_value.lower():
                    combo.setCurrentIndex(i)
                    return

        def done(r):
            self.s_dark.setValue(num(r["print.tone"], 15))
            sp = num(r["media.speed"], 4)
            self.s_speed.setCurrentIndex(max(0, self.s_speed.findData(sp)))
            self.s_width[1].setValue(num(r["ezpl.print_width"], 800))
            self.s_length[1].setValue(num(r["zpl.label_length"], 1200))
            pick(self.s_media, MEDIA_TYPES, r["ezpl.media_type"])
            pick(self.s_method, PRINT_METHODS, r["ezpl.print_method"])
            pick(self.s_mode, PRINT_MODES, r["media.printmode"])
            self.s_tear.setValue(num(r["ezpl.tear_off"]))
            self.s_top.setValue(num(r["zpl.label_top"]))
            self.s_left.setValue(num(r["zpl.left_position"]))

        self._run(cfg, fn, done, busy="ayarlar okunuyor…")

    def _settings_zpl(self):
        return (f"~SD{self.s_dark.value():02d}\n"
                f"~TA{self.s_tear.value():03d}\n"
                f"^XA\n"
                f"^MT{self.s_method.currentData()}\n"
                f"^MN{self.s_media.currentData()}\n"
                f"^MM{self.s_mode.currentData()}\n"
                f"^PR{self.s_speed.currentData()}\n"
                f"^PW{self.s_width[1].value()}\n"
                f"^LL{self.s_length[1].value()}\n"
                f"^LT{self.s_top.value()}\n"
                f"^LS{self.s_left.value()}\n"
                f"^JUS\n"
                f"^XZ\n")

    def _apply_settings(self):
        self._send(self._settings_zpl(), confirm="Ayarlar yazıcıya gönderilip kalıcı olarak kaydedilsin mi?",
                   done_msg="Ayarlar kaydedildi.")

    # ------------------------------------------------------------- Eylemler
    def _build_actions_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        groups = [
            ("Medya", [
                ("Kalibre et", "~JC", "Etiket boyu ve sensör otomatik kalibre edilir.", None),
                ("Sensör profilini bas", "~JG", "Medya sensör grafiğini basar.", None),
                ("Bir etiket ilerlet", "^XA^FO0,0^FD ^FS^XZ", "Boş bir etiket çıkarır.", None),
            ]),
            ("Rapor", [
                ("Yapılandırma etiketi bas", "~WC", "Tüm ayarları etiket olarak basar.", None),
                ("Ağ yapılandırmasını bas", "~WL", "Ağ ayarlarını basar.", None),
                ("Test etiketi bas", None, "Örnek metin ve barkodlu etiket.", self._test_label),
            ]),
            ("Kontrol", [
                ("Duraklat", "~PP", "Baskıyı duraklatır.", None),
                ("Devam et", "~PS", "Duraklatılmış baskıyı sürdürür.", None),
                ("Tüm işleri iptal et", "~JA", "Arabellekteki tüm formatları siler.", None),
                ("Yeniden başlat", "~JR", "Yazıcıyı yazılımsal olarak sıfırlar.", "confirm"),
            ]),
            ("Bakım", [
                ("Kayıtlı ayarları geri yükle", "^XA^JUR^XZ", "Son kaydedilen ayarlara döner.", "confirm"),
                ("Fabrika ayarlarına dön", "^XA^JUF^XZ",
                 "Yazıcı ayarlarını fabrika değerlerine döndürür (ağ hariç). Kaydetmek için ardından 'Ayarlar' "
                 "sekmesinden uygulayın.", "confirm"),
                ("Ağ ayarlarını fabrikaya döndür", "^XA^JUN^XZ", "Ağ ayarlarını sıfırlar.", "confirm"),
            ]),
        ]
        for title, items in groups:
            box = QGroupBox(title)
            g = QGridLayout(box)
            for i, (label, cmd, tip, extra) in enumerate(items):
                b = QPushButton(label)
                b.setToolTip(tip)
                if callable(extra):
                    b.clicked.connect(extra)
                else:
                    conf = f"{label}: {tip}\n\nDevam edilsin mi?" if extra == "confirm" else None
                    b.clicked.connect(lambda _=False, c=cmd, cf=conf, l=label: self._send(c, cf, f"{l}: gönderildi"))
                g.addWidget(b, i, 0)
                g.addWidget(QLabel(tip), i, 1)
            g.setColumnStretch(1, 1)
            v.addWidget(box)
        v.addStretch(1)
        return w

    def _test_label(self):
        cfg = self._require()
        if not cfg:
            return
        k = cfg.dpi / 203
        s = lambda n: int(n * k)
        zpl = (f"^XA^CI28"
               f"^FO{s(30)},{s(30)}^A0N,{s(40)},{s(40)}^FDZebra Kurulum Aracı^FS"
               f"^FO{s(30)},{s(80)}^A0N,{s(25)},{s(25)}^FD{cfg.name} - {cfg.dpi} dpi^FS"
               f"^FO{s(30)},{s(120)}^BY{max(1, s(2))}^BCN,{s(80)},Y,N,N^FD123456789012^FS"
               f"^FO{s(30)},{s(240)}^GB{s(300)},{s(4)},{s(4)}^FS"
               f"^XZ")
        self._send(zpl, done_msg="Test etiketi gönderildi.")

    # ------------------------------------------------------------- Ağ
    def _build_network_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        box = QGroupBox("IP ayarları")
        f = QFormLayout(box)
        self.n_iface = QComboBox()
        self.n_iface.addItem("Kablolu (internal_wired)", "internal_wired")
        self.n_iface.addItem("Kablosuz (wlan)", "wlan")
        self.n_iface.addItem("Genel (ip)", "ip")
        f.addRow("Arayüz:", self.n_iface)
        self.n_proto = QComboBox()
        self.n_proto.addItem("DHCP (otomatik)", "dhcp")
        self.n_proto.addItem("Sabit IP", "permanent")
        f.addRow("Adres alma:", self.n_proto)
        self.n_ip, self.n_mask, self.n_gw = QLineEdit(), QLineEdit(), QLineEdit()
        f.addRow("IP adresi:", self.n_ip)
        f.addRow("Alt ağ maskesi:", self.n_mask)
        f.addRow("Ağ geçidi:", self.n_gw)
        self.n_proto.currentIndexChanged.connect(self._proto_changed)
        self._proto_changed()
        v.addWidget(box)

        box = QGroupBox("Kablosuz (yalnızca Wi-Fi modüllü yazıcılar)")
        f = QFormLayout(box)
        self.n_ssid = QLineEdit()
        f.addRow("SSID (wlan.essid):", self.n_ssid)
        self.n_psk = QLineEdit()
        self.n_psk.setEchoMode(QLineEdit.EchoMode.Password)
        f.addRow("WPA parolası:", self.n_psk)
        v.addWidget(box)

        h = QHBoxLayout()
        b = QPushButton("Yazıcıdan oku")
        b.clicked.connect(self._read_network)
        h.addWidget(b)
        b = QPushButton("Uygula ve ağı yeniden başlat")
        b.clicked.connect(self._apply_network)
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        v.addWidget(QLabel("<i>Dikkat: IP adresini değiştirirseniz yazıcıya bu bilgisayardan yeni adresle "
                           "bağlanmak için yazıcı kaydını düzenlemeniz gerekir.</i>"))
        v.addStretch(1)
        return w

    def _proto_changed(self):
        static = self.n_proto.currentData() == "permanent"
        for e in (self.n_ip, self.n_mask, self.n_gw):
            e.setEnabled(static)

    def _read_network(self):
        cfg = self._require(bidirectional=True)
        if not cfg:
            return
        pfx = self.n_iface.currentData()
        base = "ip" if pfx == "ip" else f"{pfx}.ip"

        def fn(c):
            r = {k: P.getvar(c, f"{base}.{k}", timeout=1.2) for k in ("addr", "netmask", "gateway")}
            if pfx == "ip":
                d = P.getvar(c, "ip.dhcp.enable", timeout=1.2)
                r["proto"] = "dhcp" if d == "on" else "permanent"
            else:
                r["proto"] = P.getvar(c, f"{base}.protocol", timeout=1.2)
            if pfx == "wlan":
                r["ssid"] = P.getvar(c, "wlan.essid", timeout=1.2)
            return r

        def done(r):
            if all(r.get(k) is None for k in ("addr", "netmask", "gateway")):
                QMessageBox.information(self, "Ağ", "Bu arayüz yazıcıda bulunamadı.")
                return
            self.n_ip.setText(r.get("addr") or "")
            self.n_mask.setText(r.get("netmask") or "")
            self.n_gw.setText(r.get("gateway") or "")
            proto = (r.get("proto") or "").lower()
            self.n_proto.setCurrentIndex(0 if proto in ("dhcp", "all", "dhcp and bootp", "") else 1)
            if r.get("ssid") is not None:
                self.n_ssid.setText(r["ssid"])

        self._run(cfg, fn, done, busy="ağ ayarları okunuyor…")

    def _apply_network(self):
        cfg = self._require()
        if not cfg:
            return
        pfx = self.n_iface.currentData()
        base = "ip" if pfx == "ip" else f"{pfx}.ip"
        cmds = []
        static = self.n_proto.currentData() == "permanent"
        if pfx == "ip":
            cmds.append(("ip.dhcp.enable", "off" if static else "on"))
        else:
            cmds.append((f"{base}.protocol", "permanent" if static else "dhcp"))
        if static:
            cmds += [(f"{base}.addr", self.n_ip.text().strip()),
                     (f"{base}.netmask", self.n_mask.text().strip()),
                     (f"{base}.gateway", self.n_gw.text().strip())]
        if pfx == "wlan" and self.n_ssid.text().strip():
            cmds.append(("wlan.essid", self.n_ssid.text().strip()))
            if self.n_psk.text():
                cmds.append(("wlan.security", "wpa_psk"))
                cmds.append(("wlan.wpa.psk", self.n_psk.text()))
        summary = "\n".join(f"{k} = {'********' if 'psk' in k else v}" for k, v in cmds)
        if QMessageBox.question(self, "Ağ ayarları",
                                f"Şu ayarlar uygulanacak ve ağ yeniden başlatılacak:\n\n{summary}\n\nDevam?") \
                != QMessageBox.StandardButton.Yes:
            return

        def fn(c):
            for k, val in cmds:
                P.setvar(c, k, val)
            P.sgd_do(c, "device.reset")

        self._run(cfg, fn, lambda _: QMessageBox.information(
            self, "Ağ", "Ayarlar gönderildi; yazıcı yeniden başlıyor."), busy="ağ ayarları gönderiliyor…")

    # ------------------------------------------------------------- Dosya gönder
    def _build_send_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("ZPL/EPL/PRN dosyası, yazıcı yazılımı (firmware) veya font/grafik dosyasını "
                           "olduğu gibi yazıcıya gönderin."))
        self.send_file = FilePicker("Dosya seç", "Yazıcı dosyaları (*.zpl *.prn *.txt *.epl *.nrd *.zpl2 "
                                                 "*.grf *.ttf *.zsf *.zhf);;Tüm dosyalar (*)")
        v.addWidget(self.send_file)
        h = QHBoxLayout()
        h.addWidget(QLabel("Kopya:"))
        self.send_copies = QSpinBox()
        self.send_copies.setRange(1, 9999)
        h.addWidget(self.send_copies)
        b = QPushButton("Gönder")
        b.clicked.connect(self._send_file)
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        v.addWidget(QLabel("<i>Yazıcı yazılımı güncellemesi sırasında yazıcıyı kapatmayın; gönderim birkaç "
                           "dakika sürebilir.</i>"))
        v.addStretch(1)
        return w

    def _send_file(self):
        cfg = self._require()
        path = self.send_file.path()
        if not cfg:
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Dosya", "Geçerli bir dosya seçin.")
            return
        with open(path, "rb") as f:
            data = f.read()
        copies = self.send_copies.value()
        self._run(cfg, lambda c: c.send(data * copies),
                  lambda _: self.statusBar().showMessage(f"{os.path.basename(path)} gönderildi "
                                                         f"({len(data) * copies} bayt)", 6000),
                  busy=f"{os.path.basename(path)} gönderiliyor…")

    # ------------------------------------------------------------- Yazıcı belleği
    def _build_files_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        h.addWidget(QLabel("Sürücü:"))
        self.f_drive = QComboBox()
        for d in ("E:", "R:", "B:", "Z:", "A:"):
            self.f_drive.addItem(d)
        h.addWidget(self.f_drive)
        b = QPushButton("Listele")
        b.clicked.connect(self._list_files)
        h.addWidget(b)
        b = QPushButton("Seçileni sil")
        b.clicked.connect(self._delete_file)
        h.addWidget(b)
        b = QPushButton("Formatı bas…")
        b.setToolTip("Seçili .ZPL formatını ^XF ile çağırıp basar")
        b.clicked.connect(self._recall_format)
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        self.f_table = QTableWidget(0, 2)
        self.f_table.setHorizontalHeaderLabels(["Dosya", "Boyut (bayt)"])
        self.f_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.f_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.f_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.f_table)
        return w

    def _list_files(self):
        cfg = self._require(bidirectional=True)
        if not cfg:
            return
        drive = self.f_drive.currentText()

        def fn(c):
            return c.query(f"^XA^HW{drive}*.*^XZ", timeout=3.0,
                           until=lambda b: b"\x03" in b).decode("latin-1")

        def done(txt):
            import re
            rows = re.findall(r"\*\s+([A-Z]:\S+)\s+(\d+)", txt)
            self.f_table.setRowCount(len(rows))
            for i, (name, size) in enumerate(rows):
                self.f_table.setItem(i, 0, QTableWidgetItem(name))
                self.f_table.setItem(i, 1, QTableWidgetItem(size))
            if not rows:
                self.statusBar().showMessage(f"{drive} boş ya da yanıt çözümlenemedi.", 5000)

        self._run(cfg, fn, done, busy="dosyalar listeleniyor…")

    def _selected_file(self):
        r = self.f_table.currentRow()
        return self.f_table.item(r, 0).text() if r >= 0 else None

    def _delete_file(self):
        name = self._selected_file()
        if not name:
            return
        self._send(f"^XA^ID{name}^FS^XZ", confirm=f"{name} yazıcıdan silinsin mi?", done_msg=f"{name} silindi")
        self._list_files()

    def _recall_format(self):
        name = self._selected_file()
        if not name:
            return
        n, ok = QInputDialog.getInt(self, "Bas", "Kopya sayısı:", 1, 1, 9999)
        if ok:
            self._send(f"^XA^XF{name}^FS^PQ{n}^XZ", done_msg=f"{name} basıldı")

    # ------------------------------------------------------------- Terminal
    def _build_terminal_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        h.addWidget(QLabel("Hazır komut:"))
        self.t_quick = QComboBox()
        self.t_quick.addItem("— seçin —", None)
        for label, cmd, resp in QUICK_COMMANDS:
            self.t_quick.addItem(label, (cmd, resp))
        self.t_quick.activated.connect(self._quick_selected)
        h.addWidget(self.t_quick, 1)
        v.addLayout(h)
        self.t_in = QPlainTextEdit()
        self.t_in.setPlaceholderText("ZPL veya SGD komutu yazın, ör.  ^XA^FO50,50^A0N,40,40^FDMerhaba^FS^XZ\n"
                                     "ya da  ! U1 getvar \"device.product_name\"")
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.t_in.setFont(mono)
        self.t_in.setMaximumHeight(140)
        v.addWidget(self.t_in)
        h = QHBoxLayout()
        self.t_wait = QCheckBox("Yanıt bekle")
        self.t_wait.setChecked(True)
        h.addWidget(self.t_wait)
        h.addWidget(QLabel("Süre:"))
        self.t_timeout = QDoubleSpinBox()
        self.t_timeout.setRange(0.5, 60)
        self.t_timeout.setValue(3)
        self.t_timeout.setSuffix(" sn")
        h.addWidget(self.t_timeout)
        self.t_crlf = QCheckBox("Sonuna CR/LF ekle")
        self.t_crlf.setChecked(True)
        h.addWidget(self.t_crlf)
        h.addStretch(1)
        b = QPushButton("Temizle")
        b.clicked.connect(lambda: self.t_out.clear())
        h.addWidget(b)
        b = QPushButton("Gönder  (Ctrl+Enter)")
        b.setShortcut("Ctrl+Return")
        b.clicked.connect(self._terminal_send)
        h.addWidget(b)
        v.addLayout(h)
        self.t_out = QPlainTextEdit()
        self.t_out.setReadOnly(True)
        self.t_out.setFont(mono)
        v.addWidget(self.t_out, 1)
        return w

    def _quick_selected(self, idx):
        data = self.t_quick.itemData(idx)
        if data:
            cmd, resp = data
            self.t_in.setPlainText(cmd.rstrip("\r\n"))
            self.t_wait.setChecked(resp)

    def _terminal_send(self):
        cfg = self._require()
        if not cfg:
            return
        text = self.t_in.toPlainText()
        if not text.strip():
            return
        if self.t_crlf.isChecked() and not text.endswith("\n"):
            text += "\r\n"
        wait = self.t_wait.isChecked() and cfg.kind != "cups"
        timeout = self.t_timeout.value()
        self.t_out.appendPlainText(f">>> {text.rstrip()}")

        def fn(c):
            if wait:
                return c.query(text, timeout=timeout)
            c.send(text)
            return None

        def done(res):
            if res is None:
                self.t_out.appendPlainText("(gönderildi)\n")
            elif not res:
                self.t_out.appendPlainText("(yanıt yok)\n")
            else:
                txt = res.decode("utf-8", "replace").replace("\x02", "<STX>").replace("\x03", "<ETX>")
                self.t_out.appendPlainText(txt.rstrip() + "\n")

        self._run(cfg, fn, done, busy="komut gönderiliyor…")

    def _show_text(self, title, text):
        from PyQt6.QtWidgets import QDialog
        d = QDialog(self)
        d.setWindowTitle(title)
        d.resize(500, 400)
        v = QVBoxLayout(d)
        t = QPlainTextEdit(text)
        t.setReadOnly(True)
        v.addWidget(t)
        d.exec()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Zebra Kurulum Aracı")
    app.setDesktopFileName("zebra-setup")
    w = SetupWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
