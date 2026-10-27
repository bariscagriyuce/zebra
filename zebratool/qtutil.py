"""Qt yardımcıları: arka planda iş çalıştırma, ortak diyaloglar."""

import os
import sys
import traceback

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QProcess, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QComboBox,
                             QSpinBox, QMessageBox, QHBoxLayout, QPushButton, QFileDialog, QWidget)

from .connection import PrinterConfig
from . import i18n
from .i18n import tr


class _Signals(QObject):
    done = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int)


class _Task(QRunnable):
    def __init__(self, fn, signals, with_progress):
        super().__init__()
        self.fn = fn
        self.signals = signals
        self.with_progress = with_progress

    def run(self):
        try:
            if self.with_progress:
                res = self.fn(lambda i, n: self.signals.progress.emit(i, n))
            else:
                res = self.fn()
            self.signals.done.emit(res)
        except Exception as e:  # noqa: BLE001 - kullanıcıya gösterilecek
            traceback.print_exc()
            self.signals.error.emit(str(e) or e.__class__.__name__)


_live = set()


def run_async(fn, on_done=None, on_error=None, on_progress=None):
    """fn'i iş parçacığı havuzunda çalıştır; geri çağırmalar ana iş parçacığında gelir."""
    sig = _Signals()
    _live.add(sig)

    def finish(*_):
        _live.discard(sig)

    if on_done:
        sig.done.connect(on_done)
    if on_error:
        sig.error.connect(on_error)
    if on_progress:
        sig.progress.connect(on_progress)
    sig.done.connect(finish)
    sig.error.connect(finish)
    QThreadPool.globalInstance().start(_Task(fn, sig, on_progress is not None))


class PrinterDialog(QDialog):
    """Yazıcı ekle / düzenle."""

    def __init__(self, parent=None, cfg: PrinterConfig = None):
        super().__init__(parent)
        self.setWindowTitle(tr('Yazıcı') if cfg else tr('Yazıcı ekle'))
        lay = QFormLayout(self)
        self.name = QLineEdit(cfg.name if cfg else "Zebra")
        self.kind = QComboBox()
        self.kind.addItem(tr('Ağ (TCP/IP, port 9100)'), "network")
        self.kind.addItem(tr('USB (/dev/usb/lpN)'), "usb")
        self.kind.addItem(tr('CUPS kuyruğu (yalnızca gönderim)'), "cups")
        self.address = QLineEdit(cfg.address if cfg else "")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(cfg.port if cfg else 9100)
        self.dpi = QComboBox()
        for d in (203, 300, 600):
            self.dpi.addItem(f"{d} dpi", d)
        if cfg:
            self.kind.setCurrentIndex(max(0, self.kind.findData(cfg.kind)))
            self.dpi.setCurrentIndex(max(0, self.dpi.findData(cfg.dpi)))
        lay.addRow(tr('Ad:'), self.name)
        lay.addRow(tr('Bağlantı:'), self.kind)
        lay.addRow(tr('Adres:'), self.address)
        lay.addRow(tr('Port:'), self.port)
        lay.addRow(tr('Çözünürlük:'), self.dpi)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def _kind_changed(self):
        k = self.kind.currentData()
        self.port.setEnabled(k == "network")
        self.address.setPlaceholderText({"network": "192.168.1.50",
                                         "usb": "/dev/usb/lp0",
                                         "cups": tr('kuyruk adı (lpstat -v)')}[k])

    def _accept(self):
        if not self.address.text().strip():
            QMessageBox.warning(self, tr('Eksik'), tr('Adres boş olamaz.'))
            return
        self.accept()

    def config(self):
        return PrinterConfig(name=self.name.text().strip() or "Zebra",
                             kind=self.kind.currentData(),
                             address=self.address.text().strip(),
                             port=self.port.value(),
                             dpi=self.dpi.currentData())


def error_box(parent, title, msg):
    QMessageBox.critical(parent, title, msg)


class FilePicker(QWidget):
    def __init__(self, caption=tr('Dosya seç'), filt=tr('Tüm dosyalar (*)'), parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        btn = QPushButton(tr('Gözat…'))
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)
        btn.clicked.connect(lambda: self._browse(caption, filt))

    def _browse(self, caption, filt):
        path, _ = QFileDialog.getOpenFileName(self, caption, "", filt)
        if path:
            self.edit.setText(path)

    def path(self):
        return self.edit.text().strip()


def add_language_menu(win, menubar, module, extra_args=lambda: []):
    """Dil menüsü. Seçim kaydedilir; uygulama isteğe bağlı olarak yeniden başlatılır."""
    m = menubar.addMenu(tr("Dil / Language"))
    grp = QActionGroup(win)
    for code, name in i18n.LANGUAGES.items():
        a = QAction(name, win, checkable=True)
        a.setChecked(code == i18n.LANG)
        a.triggered.connect(lambda checked=False, c=code: _switch_language(win, c, module, extra_args))
        grp.addAction(a)
        m.addAction(a)
    return m


def _switch_language(win, code, module, extra_args):
    if code == i18n.LANG:
        return
    i18n.set_language(code)
    msg = i18n.EN["Dil değiştirildi. Uygulama şimdi yeniden başlatılsın mı?"] if code == "en" else \
        "Dil değiştirildi. Uygulama şimdi yeniden başlatılsın mı?"
    if QMessageBox.question(win, "Dil / Language", msg) != QMessageBox.StandardButton.Yes:
        return
    args = ["-m", module] + list(extra_args())
    if win.close():
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        QProcess.startDetached(sys.executable, args, root)
