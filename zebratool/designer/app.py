"""Zebra Etiket Tasarımcısı - ZebraDesigner benzeri Linux uygulaması."""

import json
import os
import sys

from PyQt6.QtCore import Qt, QTimer, QPointF, QSettings
from PyQt6.QtGui import QAction, QKeySequence, QIcon, QPainter, QActionGroup, QImage, QPageSize
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QDockWidget, QToolBar, QFileDialog, QMessageBox,
    QLabel, QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox, QDoubleSpinBox, QWidget, QHBoxLayout,
    QCheckBox, QComboBox, QInputDialog,
)

from .. import printers as P
from .document import LabelScene, dots_to_mm, mm_to_dots
from .items import TextItem, BarcodeItem, BoxItem, LineItem, EllipseItem, ImageItem, LabelItem, item_from_dict
from .panels import PropertyPanel, LabelDialog, PrintDialog
from ..qtutil import add_language_menu
from ..i18n import tr, LANG

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_PATH = os.path.join(APP_DIR, "icons", "zebra-designer.svg")
FILE_FILTER = tr('Zebra etiketi (*.zlbl);;Tüm dosyalar (*)')


class LabelView(QGraphicsView):
    def __init__(self, scene, window):
        super().__init__(scene)
        self.window_ = window
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setMouseTracking(True)
        self._pan = None

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            f = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
            self.zoom_by(f)
        else:
            super().wheelEvent(ev)

    def zoom_by(self, f):
        z = self.transform().m11() * f
        if 0.05 < z < 40:
            self.scale(f, f)
            self.window_.update_zoom_label()

    def fit(self):
        s = self.scene()
        from PyQt6.QtCore import QRectF
        self.fitInView(QRectF(-20, -20, s.width_dots + 40, s.height_dots + 40), Qt.AspectRatioMode.KeepAspectRatio)
        self.window_.update_zoom_label()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.MiddleButton:
            self._pan = ev.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._pan is not None:
            d = ev.position() - self._pan
            self._pan = ev.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            return
        p = self.mapToScene(ev.position().toPoint())
        self.window_.show_cursor(p)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.MiddleButton and self._pan is not None:
            self._pan = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        super().mouseReleaseEvent(ev)

    def keyPressEvent(self, ev):
        sel = [i for i in self.scene().selectedItems() if isinstance(i, LabelItem) and not i.p.get("locked")]
        step = 10 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        d = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
             Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}.get(ev.key())
        if d and sel:
            snap = self.scene().snap
            self.scene().snap = False
            for it in sel:
                it.setPos(it.pos() + QPointF(*d))
            self.scene().snap = snap
            self.window_.schedule_commit()
            return
        super().keyPressEvent(ev)


class DesignerWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(1280, 800)
        self.settings = QSettings("zebratool", "designer")
        self.path = None
        self.dirty = False
        self.history = []
        self.hpos = -1
        self._restoring = False
        self._commit_timer = QTimer(self, singleShot=True, interval=350)
        self._commit_timer.timeout.connect(self.commit)

        self.scene = LabelScene()
        self.scene.selectionChanged.connect(self._selection_changed)
        self.scene.moved.connect(self._item_moved)
        self.scene.committed.connect(self.schedule_commit)
        self.view = LabelView(self.scene, self)
        self.setCentralWidget(self.view)

        self.props = PropertyPanel()
        self.props.changed.connect(lambda *_: self.schedule_commit())
        dock = QDockWidget(tr('Özellikler'), self)
        dock.setObjectName("props")
        dock.setWidget(self.props)
        dock.setMinimumWidth(220)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        self.pos_label = QLabel()
        self.zoom_label = QLabel()
        self.size_label = QLabel()
        sb = self.statusBar()
        sb.addWidget(self.pos_label, 1)
        sb.addPermanentWidget(self.size_label)
        sb.addPermanentWidget(self.zoom_label)

        self._build_actions()
        self.clipboard = []

        if path:
            self.open_path(path)
        else:
            self.new_doc(ask=False)
        QTimer.singleShot(0, self.view.fit)

    # ------------------------------------------------------------- eylemler
    def _act(self, text, slot, shortcut=None, tip=None, icon=None):
        a = QAction(text, self)
        if icon:
            a.setIcon(QIcon.fromTheme(icon))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.triggered.connect(slot)
        return a

    def _build_actions(self):
        mb = self.menuBar()
        fm = mb.addMenu(tr('&Dosya'))
        self.a_new = self._act(tr('Yeni'), self.new_doc, QKeySequence.StandardKey.New, icon="document-new")
        self.a_open = self._act(tr('Aç…'), self.open_doc, QKeySequence.StandardKey.Open, icon="document-open")
        self.a_save = self._act(tr('Kaydet'), self.save_doc, QKeySequence.StandardKey.Save, icon="document-save")
        self.a_saveas = self._act(tr('Farklı kaydet…'), self.save_as, QKeySequence.StandardKey.SaveAs,
                                  icon="document-save-as")
        self.a_export = self._act(tr('ZPL olarak dışa aktar…'), self.export_zpl, "Ctrl+E")
        self.a_png = self._act(tr('PNG olarak dışa aktar…'), self.export_png)
        self.a_print = self._act(tr('Yazdır…'), self.print_doc, QKeySequence.StandardKey.Print,
                                 icon="document-print")
        self.a_label = self._act(tr('Etiket ayarları…'), self.label_settings, "Ctrl+L", icon="document-properties")
        for a in (self.a_new, self.a_open, self.a_save, self.a_saveas, None, self.a_label, None,
                  self.a_export, self.a_png, None, self.a_print):
            fm.addSeparator() if a is None else fm.addAction(a)
        self.recent_menu = fm.addMenu(tr('Son açılanlar'))
        self._update_recent()
        fm.addSeparator()
        fm.addAction(self._act(tr('Kurulum Aracını aç'), self._open_setup))
        fm.addAction(self._act(tr('Çıkış'), self.close, QKeySequence.StandardKey.Quit))

        em = mb.addMenu(tr('Dü&zen'))
        self.a_undo = self._act(tr('Geri al'), self.undo, QKeySequence.StandardKey.Undo, icon="edit-undo")
        self.a_redo = self._act(tr('Yinele'), self.redo, QKeySequence.StandardKey.Redo, icon="edit-redo")
        self.a_cut = self._act(tr('Kes'), self.cut, QKeySequence.StandardKey.Cut)
        self.a_copy = self._act(tr('Kopyala'), self.copy, QKeySequence.StandardKey.Copy)
        self.a_paste = self._act(tr('Yapıştır'), self.paste, QKeySequence.StandardKey.Paste)
        self.a_dup = self._act(tr('Çoğalt'), self.duplicate, "Ctrl+D")
        self.a_del = self._act(tr('Sil'), self.delete_selected, QKeySequence.StandardKey.Delete, icon="edit-delete")
        self.a_all = self._act(tr('Tümünü seç'), lambda: [i.setSelected(True) for i in self.scene.label_items()],
                               QKeySequence.StandardKey.SelectAll)
        self.a_front = self._act(tr('Öne getir'), lambda: self.z_order(True), "Ctrl+]", icon="go-top")
        self.a_back = self._act(tr('Arkaya gönder'), lambda: self.z_order(False), "Ctrl+[", icon="go-bottom")
        for a in (self.a_undo, self.a_redo, None, self.a_cut, self.a_copy, self.a_paste, self.a_dup, self.a_del,
                  None, self.a_all, None, self.a_front, self.a_back):
            em.addSeparator() if a is None else em.addAction(a)
        am = em.addMenu(tr('Hizala'))
        for text, mode in ((tr('Sola'), "left"), (tr('Yatay ortala'), "hcenter"), (tr('Sağa'), "right"),
                           (tr('Üste'), "top"), (tr('Dikey ortala'), "vcenter"), (tr('Alta'), "bottom"),
                           (tr('Etikette yatay ortala'), "label_h"), (tr('Etikette dikey ortala'), "label_v")):
            am.addAction(self._act(text, lambda _=False, m=mode: self.align(m)))

        im = mb.addMenu(tr('&Ekle'))
        self.a_text = self._act(tr('Metin'), lambda: self.add_item(TextItem()), "T", icon="insert-text")
        self.a_barcode = self._act(tr('Barkod'), lambda: self.add_item(BarcodeItem()), "B")
        self.a_qr = self._act(tr('QR kod'), lambda: self.add_item(
            BarcodeItem({"symbology": "qr", "data": "https://example.com", "module": 5})), "Q")
        self.a_box = self._act(tr('Kutu'), lambda: self.add_item(BoxItem()), "R")
        self.a_line = self._act(tr('Çizgi'), lambda: self.add_item(LineItem()), "L")
        self.a_ellipse = self._act(tr('Elips'), lambda: self.add_item(EllipseItem()), "E")
        self.a_image = self._act(tr('Resim…'), self.add_image, "I", icon="insert-image")
        for a in (self.a_text, self.a_barcode, self.a_qr, self.a_box, self.a_line, self.a_ellipse, self.a_image):
            im.addAction(a)
        im.addSeparator()
        vm = im.addMenu(tr('Değişken alan ekle'))
        names = ("SAYAC", "TARIH", "SAAT", "AD", "URUN", "FIYAT") if LANG == "tr" else \
            ("COUNTER", "DATE", "TIME", "NAME", "PRODUCT", "PRICE")
        for name in names:
            vm.addAction(self._act("{" + name + "}", lambda _=False, n=name: self.add_item(TextItem({"text": "{" + n + "}"}))))

        wm = mb.addMenu(tr('&Görünüm'))
        self.a_zin = self._act(tr('Yakınlaştır'), lambda: self.view.zoom_by(1.25), QKeySequence.StandardKey.ZoomIn,
                               icon="zoom-in")
        self.a_zout = self._act(tr('Uzaklaştır'), lambda: self.view.zoom_by(0.8), QKeySequence.StandardKey.ZoomOut,
                                icon="zoom-out")
        self.a_fit = self._act(tr('Sığdır'), self.view.fit, "Ctrl+0", icon="zoom-fit-best")
        self.a_actual = self._act(tr('Gerçek boyut'), self.actual_size, "Ctrl+1", icon="zoom-original")
        self.a_grid = self._act(tr('Izgara'), self.toggle_grid, "Ctrl+'")
        self.a_grid.setCheckable(True)
        self.a_grid.setChecked(True)
        self.a_snap = self._act(tr('Izgaraya yasla'), self.toggle_snap, "Ctrl+Shift+'")
        self.a_snap.setCheckable(True)
        self.a_snap.setChecked(True)
        self.a_zpl = self._act(tr('ZPL kodunu göster'), self.show_zpl, "F5")
        for a in (self.a_zin, self.a_zout, self.a_fit, self.a_actual, None, self.a_grid, self.a_snap, None, self.a_zpl):
            wm.addSeparator() if a is None else wm.addAction(a)
        gm = wm.addMenu(tr('Izgara aralığı'))
        grp = QActionGroup(self)
        for g in (0.5, 1.0, 2.0, 2.5, 5.0):
            a = QAction(f"{g:g} mm", self, checkable=True)
            a.setChecked(g == 1.0)
            a.triggered.connect(lambda _=False, gg=g: self.set_grid(gg))
            grp.addAction(a)
            gm.addAction(a)

        add_language_menu(self, mb, "zebratool.designer", lambda: [self.path] if self.path else [])
        hm = mb.addMenu(tr('&Yardım'))
        hm.addAction(self._act(tr('Kısayollar ve ipuçları'), self.show_help, "F1"))

        tb = QToolBar(tr('Dosya'))
        tb.setObjectName("tb_file")
        for a in (self.a_new, self.a_open, self.a_save, None, self.a_undo, self.a_redo, None, self.a_label):
            tb.addSeparator() if a is None else tb.addAction(a)
        self.addToolBar(tb)
        tb = QToolBar(tr('Ekle'))
        tb.setObjectName("tb_insert")
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for a in (self.a_text, self.a_barcode, self.a_qr, self.a_box, self.a_line, self.a_ellipse, self.a_image,
                  None, self.a_del, self.a_front, self.a_back):
            tb.addSeparator() if a is None else tb.addAction(a)
        self.addToolBar(tb)
        tb = QToolBar(tr('Görünüm'))
        tb.setObjectName("tb_view")
        for a in (self.a_zout, self.a_zin, self.a_fit, None, self.a_zpl):
            tb.addSeparator() if a is None else tb.addAction(a)
        self.a_print.setText(tr('Yazdır…'))
        tb.addAction(self.a_print)
        self.addToolBar(tb)

    # ------------------------------------------------------------- belge
    def _title(self):
        name = os.path.basename(self.path) if self.path else tr('Adsız')
        self.setWindowTitle(tr('{0}{1} — Zebra Etiket Tasarımcısı').format('*' if self.dirty else '', name))
        L = self.scene.label
        self.size_label.setText(f"{L['width_mm']:g} × {L['height_mm']:g} mm · {L['dpi']} dpi")

    def _confirm_discard(self):
        if not self.dirty:
            return True
        r = QMessageBox.question(self, tr('Kaydedilmemiş değişiklikler'), tr('Değişiklikler kaydedilsin mi?'),
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                 | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save_doc()
        return r == QMessageBox.StandardButton.Discard

    def new_doc(self, ask=True):
        if ask and not self._confirm_discard():
            return
        self.scene.clear()
        last = self.settings.value("last_label")
        if last:
            try:
                self.scene.set_label(json.loads(last))
            except ValueError:
                pass
        self.path = None
        self._reset_history()
        if ask:
            self.label_settings()
        self.view.fit()

    def open_doc(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, tr('Etiket aç'), self.settings.value("dir", ""), FILE_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path):
        try:
            self.scene.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, tr('Açılamadı'), f"{path}\n\n{e}")
            return
        self.path = path
        self.settings.setValue("dir", os.path.dirname(path))
        self._add_recent(path)
        self._reset_history()
        self.view.fit()

    def save_doc(self):
        if not self.path:
            return self.save_as()
        try:
            self.scene.save(self.path)
        except OSError as e:
            QMessageBox.critical(self, tr('Kaydedilemedi'), str(e))
            return False
        self.dirty = False
        self._add_recent(self.path)
        self._title()
        return True

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, tr('Farklı kaydet'), self.path or os.path.join(
            self.settings.value("dir", ""), tr('etiket.zlbl')), FILE_FILTER)
        if not path:
            return False
        if not path.endswith(".zlbl"):
            path += ".zlbl"
        self.path = path
        self.settings.setValue("dir", os.path.dirname(path))
        return self.save_doc()

    def _add_recent(self, path):
        rec = [p for p in (self.settings.value("recent") or []) if p != path]
        rec.insert(0, path)
        self.settings.setValue("recent", rec[:10])
        self._update_recent()

    def _update_recent(self):
        self.recent_menu.clear()
        rec = self.settings.value("recent") or []
        for p in rec:
            if os.path.exists(p):
                self.recent_menu.addAction(self._act(p, lambda _=False, pp=p: self._open_recent(pp)))
        self.recent_menu.setEnabled(bool(self.recent_menu.actions()))

    def _open_recent(self, p):
        if self._confirm_discard():
            self.open_path(p)

    def closeEvent(self, ev):
        if self._confirm_discard():
            ev.accept()
        else:
            ev.ignore()

    # ------------------------------------------------------------- geri al
    def _reset_history(self):
        self.history = [json.dumps(self.scene.to_dict())]
        self.hpos = 0
        self.dirty = False
        self._title()
        self._selection_changed()

    def schedule_commit(self):
        self._commit_timer.start()

    def commit(self):
        if self._restoring:
            return
        snap = json.dumps(self.scene.to_dict())
        if self.history and snap == self.history[self.hpos]:
            return
        del self.history[self.hpos + 1:]
        self.history.append(snap)
        if len(self.history) > 200:
            self.history.pop(0)
        self.hpos = len(self.history) - 1
        self.dirty = True
        self._title()

    def _restore(self, snap):
        self._restoring = True
        sel_idx = [i for i, it in enumerate(self.scene.label_items()) if it.isSelected()]
        self.scene.load_dict(json.loads(snap))
        items = self.scene.label_items()
        for i in sel_idx:
            if i < len(items):
                items[i].setSelected(True)
        self._restoring = False
        self.dirty = True
        self._title()
        self._selection_changed()

    def undo(self):
        self._commit_timer.stop()
        self.commit()
        if self.hpos > 0:
            self.hpos -= 1
            self._restore(self.history[self.hpos])

    def redo(self):
        if self.hpos < len(self.history) - 1:
            self.hpos += 1
            self._restore(self.history[self.hpos])

    # ------------------------------------------------------------- öğeler
    def _selected(self):
        return [i for i in self.scene.selectedItems() if isinstance(i, LabelItem)]

    def _selection_changed(self):
        if self._restoring:
            return
        sel = self._selected()
        self.props.set_item(sel[0] if len(sel) == 1 else None, self.scene.dpi)

    def _item_moved(self, item):
        if self.props.item is item:
            self.props.refresh()

    def add_item(self, item):
        # görünür alanın ortasına yakın, ızgaraya hizalı
        c = self.view.mapToScene(self.view.viewport().rect().center())
        w, h = item.outer_size()
        x = min(max(0, c.x() - w / 2), max(0, self.scene.width_dots - w))
        y = min(max(0, c.y() - h / 2), max(0, self.scene.height_dots - h))
        g = self.scene.snap_step or 1
        item.set_prop("x", int(round(x / g) * g))
        item.set_prop("y", int(round(y / g) * g))
        self.scene.add(item)
        self.scene.clearSelection()
        item.setSelected(True)
        self.commit()

    def add_image(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('Resim seç'), self.settings.value("imgdir", ""),
                                              tr('Resimler (*.png *.jpg *.jpeg *.bmp *.gif *.svg *.webp)'))
        if not path:
            return
        self.settings.setValue("imgdir", os.path.dirname(path))
        try:
            self.add_item(ImageItem.from_file(path, self.scene.dpi))
        except ValueError as e:
            QMessageBox.warning(self, tr('Resim'), str(e))

    def delete_selected(self):
        sel = self._selected()
        if not sel:
            return
        for it in sel:
            self.scene.removeItem(it)
        self.commit()
        self._selection_changed()

    def copy(self):
        self.clipboard = [i.to_dict() for i in sorted(self._selected(), key=lambda i: i.zValue())]
        QApplication.clipboard().setText(json.dumps({"zebratool_items": self.clipboard}))

    def cut(self):
        self.copy()
        self.delete_selected()

    def paste(self):
        items = self.clipboard
        try:
            d = json.loads(QApplication.clipboard().text())
            items = d.get("zebratool_items", items)
        except (ValueError, AttributeError):
            pass
        if not items:
            return
        self.scene.clearSelection()
        off = mm_to_dots(2, self.scene.dpi)
        for d in items:
            d = dict(d, x=d["x"] + off, y=d["y"] + off)
            it = self.scene.add(item_from_dict(d))
            it.setSelected(True)
        self.clipboard = [dict(d, x=d["x"] + off, y=d["y"] + off) for d in items]
        self.commit()

    def duplicate(self):
        self.copy()
        self.paste()

    def z_order(self, front):
        sel = self._selected()
        if not sel:
            return
        zs = [i.zValue() for i in self.scene.label_items()]
        for it in sel:
            it.setZValue((max(zs) + 1) if front else (min(zs) - 1))
        # z değerlerini yeniden numarala
        for z, it in enumerate(self.scene.label_items(), 1):
            it.setZValue(z)
        self.commit()

    def align(self, mode):
        sel = self._selected()
        if not sel:
            return
        rects = [(it, it.p["x"], it.p["y"], *it.outer_size()) for it in sel]
        x0 = min(r[1] for r in rects)
        y0 = min(r[2] for r in rects)
        x1 = max(r[1] + r[3] for r in rects)
        y1 = max(r[2] + r[4] for r in rects)
        W, H = self.scene.width_dots, self.scene.height_dots
        for it, x, y, w, h in rects:
            nx, ny = x, y
            if mode == "left":
                nx = x0
            elif mode == "right":
                nx = x1 - w
            elif mode == "hcenter":
                nx = (x0 + x1) / 2 - w / 2
            elif mode == "top":
                ny = y0
            elif mode == "bottom":
                ny = y1 - h
            elif mode == "vcenter":
                ny = (y0 + y1) / 2 - h / 2
            elif mode == "label_h":
                nx = (W - w) / 2
            elif mode == "label_v":
                ny = (H - h) / 2
            it.set_prop("x", int(round(nx)))
            it.set_prop("y", int(round(ny)))
        self.props.refresh()
        self.commit()

    # ------------------------------------------------------------- görünüm
    def update_zoom_label(self):
        self.zoom_label.setText(tr('Yakınlık: %{0:.0f}').format(self.view.transform().m11() * 100))
        self.scene.update()

    def actual_size(self):
        # ekranda gerçek mm boyutu
        screen = self.screen()
        px_per_mm = screen.physicalDotsPerInch() / 25.4 if screen else 96 / 25.4
        z = px_per_mm / (self.scene.dpi / 25.4)
        self.view.resetTransform()
        self.view.scale(z, z)
        self.update_zoom_label()

    def show_cursor(self, p):
        d = self.scene.dpi
        self.pos_label.setText(f"X: {dots_to_mm(p.x(), d):.1f} mm ({int(p.x())})   "
                               f"Y: {dots_to_mm(p.y(), d):.1f} mm ({int(p.y())})")

    def toggle_grid(self):
        self.scene.show_grid = self.a_grid.isChecked()
        self.scene.update()

    def toggle_snap(self):
        self.scene.snap = self.a_snap.isChecked()

    def set_grid(self, g):
        self.scene.grid_mm = g
        self.scene.update()

    # ------------------------------------------------------------- diyaloglar
    def label_settings(self):
        d = LabelDialog(self.scene.label, P.load_printers(), self)
        if d.exec():
            new = d.label()
            if new["dpi"] != self.scene.dpi and self.scene.label_items():
                r = QMessageBox.question(self, tr('Çözünürlük değişti'),
                                         tr('Öğeler yeni çözünürlüğe göre ölçeklensin mi?\n(Hayır derseniz nokta değerleri aynı kalır.)'))
                if r != QMessageBox.StandardButton.Yes:
                    self.scene.label["dpi"] = new["dpi"]
            self.scene.set_label(new)
            self.settings.setValue("last_label", json.dumps(new))
            self.commit()
            self._title()
            self.view.fit()
            self._selection_changed()

    def show_zpl(self):
        try:
            code = self.scene.zpl()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "ZPL", str(e))
            return
        d = QDialog(self)
        d.setWindowTitle(tr('ZPL kodu'))
        d.resize(700, 500)
        v = QVBoxLayout(d)
        t = QPlainTextEdit(code)
        t.setReadOnly(True)
        t.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        from PyQt6.QtGui import QFont
        f = QFont("monospace")
        f.setStyleHint(QFont.StyleHint.Monospace)
        t.setFont(f)
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        cp = bb.addButton(tr('Panoya kopyala'), QDialogButtonBox.ButtonRole.ActionRole)
        cp.clicked.connect(lambda: QApplication.clipboard().setText(code))
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        d.exec()

    def export_zpl(self):
        base = os.path.splitext(self.path)[0] + ".zpl" if self.path else tr('etiket.zpl')
        path, _ = QFileDialog.getSaveFileName(self, tr('ZPL olarak dışa aktar'), base, "ZPL (*.zpl *.prn)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.scene.zpl())
            self.statusBar().showMessage(tr('Kaydedildi: {0}').format(path), 5000)

    def render_image(self):
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor
        W, H = self.scene.width_dots, self.scene.height_dots
        img = QImage(W, H, QImage.Format.Format_ARGB32)
        img.fill(QColor(255, 255, 255))
        sel = self._selected()
        self.scene.clearSelection()
        grid = self.scene.show_grid
        self.scene.show_grid = False
        p = QPainter(img)
        # arka planı çizmeden yalnızca öğeler
        bg = self.scene.backgroundBrush()
        self.scene.setBackgroundBrush(QColor(255, 255, 255))
        for it in self.scene.label_items():
            p.save()
            p.translate(it.pos())
            it.paint(p, _NoSelOption(), None)
            p.restore()
        p.end()
        self.scene.setBackgroundBrush(bg)
        self.scene.show_grid = grid
        for it in sel:
            it.setSelected(True)
        return img

    def export_png(self):
        base = os.path.splitext(self.path)[0] + ".png" if self.path else tr('etiket.png')
        path, _ = QFileDialog.getSaveFileName(self, tr('PNG olarak dışa aktar'), base, "PNG (*.png)")
        if path:
            img = self.render_image()
            dpm = int(round(self.scene.dpi / 0.0254))
            img.setDotsPerMeterX(dpm)
            img.setDotsPerMeterY(dpm)
            img.save(path)
            self.statusBar().showMessage(tr('Kaydedildi: {0}').format(path), 5000)

    def print_doc(self):
        d = PrintDialog(self.scene, self)
        if d.exec():
            self.statusBar().showMessage(tr('Yazıcıya gönderildi.'), 5000)

    def _open_setup(self):
        import subprocess
        subprocess.Popen([sys.executable, "-m", "zebratool.setup_app"], cwd=os.path.dirname(APP_DIR))

    def show_help(self):
        QMessageBox.information(self, tr('Kısayollar ve ipuçları'), tr('\n<b>Öğe ekleme:</b> T metin · B barkod · Q QR · R kutu · L çizgi · E elips · I resim<br>\n<b>Taşıma:</b> sürükle; ok tuşları 1 nokta, Shift+ok 10 nokta<br>\n<b>Boyutlandırma:</b> kutu/çizgi/elips/resimde sağ alt tutamaç<br>\n<b>Yakınlaştırma:</b> Ctrl+tekerlek · Ctrl+0 sığdır · Ctrl+1 gerçek boyut · orta tuşla kaydır<br>\n<b>Düzen:</b> Ctrl+Z/Ctrl+Y · Ctrl+C/V/X · Ctrl+D çoğalt · Del sil · Ctrl+] / Ctrl+[ öne/arkaya<br>\n<b>ZPL:</b> F5 kodu göster · Ctrl+E dışa aktar · Ctrl+P yazdır<br><br>\n<b>Değişkenler:</b> Metin veya barkod verisine <code>{AD}</code> gibi alanlar yazın. Yazdırırken elle,\nCSV dosyasından ya da sayaçla doldurulur. Hazır alanlar: <code>{SAYAC}</code>, <code>{TARIH}</code>,\n<code>{SAAT}</code>, <code>{DATE}</code>, <code>{TIME}</code>.<br><br>\n<b>Fontlar:</b> "Zebra 0" yazıcının kendi fontudur (hızlı, keskin). Başka bir font seçerseniz metin\ngrafik olarak gönderilir; ekranda ne görürseniz o basılır (Türkçe karakterler dahil).\n'))


class _NoSelOption:
    """paint() için seçim çerçevesi olmayan stil seçeneği."""
    from PyQt6.QtWidgets import QStyle as _S
    state = _S.StateFlag.State_None


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(tr('Zebra Etiket Tasarımcısı'))
    app.setDesktopFileName("zebra-designer")
    path = sys.argv[1] if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]) else None
    w = DesignerWindow(path)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
