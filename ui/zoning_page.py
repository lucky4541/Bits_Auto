"""Zoning: review and correct the zones of a book, or zone a PDF by hand, then generate XML.

Left   : structure tree of the book and the list of flagged zones
Centre : the PDF page with every zone drawn in its tag colour; dashed grey = text not in any zone
Right  : selected zone (tag, source, flags, text), tag palette, actions, attributes

Select mode (V): click a zone (Ctrl+click adds). Draw mode (D): drag a box, the
chosen tag is applied to the text inside it ("Auto-detect" runs the detector on
just that box). Tag keys apply to the selection: P, 1-4, L, E, R, N, F, T, B, C, K.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from bits_tool import services
from bits_tool.zoning import line_sort_key, zone_label, zone_text

from .app_state import theme_tokens
from .widgets.common import Card, Page, button, label
from .workers import Worker, start

ROOT = Path(__file__).resolve().parents[1]
CONTAINERS = ("book", "front-matter", "book-body", "book-back", "body", "back", "named-book-part-body")


def load_tags(cfg) -> dict:
    p = ROOT / "config" / "zone_tags.json"
    lib = ROOT / "config" / "bits_tag_library.json"
    tags = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"tags": [], "kind_colors": {}}
    tags["library"] = json.loads(lib.read_text(encoding="utf-8")) if lib.exists() else {"elements": {}, "url_pattern": ""}
    return tags


# =================================================================== canvas

class PageCanvas(QGraphicsView):
    zoneClicked = Signal(str, bool)          # zid, additive
    rectDrawn = Signal(QRectF)               # page coordinates
    lineClicked = Signal(str)                # uid of the line under the click
    contextRequested = Signal(object)        # global QPoint

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFrameShape(QFrame.NoFrame)
        self.zoom = 1.5
        self.mode = "select"
        self._origin = None
        self._band = None
        self._zones = []          # (zid, rect page coords)
        self._lines = []          # (uid, rect)
        self.setMouseTracking(True)

    def set_mode(self, mode):
        self.mode = mode
        self.viewport().setCursor(Qt.CrossCursor if mode == "draw" else Qt.ArrowCursor)

    def show_page(self, pixmap: QPixmap, zones: list[dict], unzoned: list[tuple], lines: list[tuple], show_unzoned=True):
        sc = self.scene()
        sc.clear()
        self._band = None
        z = self.zoom
        sc.addPixmap(pixmap)
        sc.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        self._zones = []
        self._lines = lines
        if show_unzoned:
            pen = QPen(QColor(120, 120, 120), 1.0, Qt.DashLine)
            for uid, r in unzoned:
                it = QGraphicsRectItem(QRectF(r[0] * z, r[1] * z, (r[2] - r[0]) * z, (r[3] - r[1]) * z))
                it.setPen(pen)
                it.setBrush(QBrush(QColor(150, 150, 150, 40)))
                sc.addItem(it)
        # big zones first so small ones stay clickable on top
        for zd in sorted(zones, key=lambda d: -((d["rect"][2] - d["rect"][0]) * (d["rect"][3] - d["rect"][1]))):
            r = zd["rect"]
            col = QColor(zd["color"])
            rect = QRectF(r[0] * z - 2, r[1] * z - 2, (r[2] - r[0]) * z + 4, (r[3] - r[1]) * z + 4)
            it = QGraphicsRectItem(rect)
            frame = zd.get("frame", False)
            width = 3.0 if zd.get("selected") else (1.6 if not frame else 1.2)
            pen = QPen(col, width, Qt.DashLine if zd.get("flagged") and not zd.get("selected") else Qt.SolidLine)
            it.setPen(pen)
            fill = QColor(col)
            fill.setAlpha(0 if frame else (70 if zd.get("selected") else 28))
            it.setBrush(QBrush(fill))
            sc.addItem(it)
            tag = QGraphicsSimpleTextItem(zd["label"])
            f = QFont()
            f.setPointSizeF(7.5)
            f.setBold(True)
            tag.setFont(f)
            tag.setBrush(QBrush(QColor("#FFFFFF")))
            br = tag.boundingRect()
            bg = QGraphicsRectItem(QRectF(rect.left(), rect.top() - br.height() - 1, br.width() + 6, br.height() + 1))
            bg.setBrush(QBrush(col))
            bg.setPen(QPen(Qt.NoPen))
            sc.addItem(bg)
            tag.setPos(rect.left() + 3, rect.top() - br.height() - 0.5)
            sc.addItem(tag)
            self._zones.append((zd["zid"], r, frame))

    def _page_pt(self, ev) -> QPointF:
        p = self.mapToScene(ev.position().toPoint())
        return QPointF(p.x() / self.zoom, p.y() / self.zoom)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.mode == "draw":
            self._origin = self.mapToScene(ev.position().toPoint())
            self._band = QGraphicsRectItem(QRectF(self._origin, self._origin))
            t = theme_tokens()
            self._band.setPen(QPen(QColor(t["accent"]), 1.5, Qt.DashLine))
            self._band.setBrush(QBrush(QColor(37, 99, 235, 30)))
            self.scene().addItem(self._band)
            return
        if ev.button() == Qt.LeftButton and self.mode == "select":
            pt = self._page_pt(ev)
            hits = [(zid, r, fr) for zid, r, fr in self._zones if r[0] - 2 <= pt.x() <= r[2] + 2 and r[1] - 2 <= pt.y() <= r[3] + 2]
            for uid, r in self._lines:
                if r[0] <= pt.x() <= r[2] and r[1] - 1 <= pt.y() <= r[3] + 1:
                    self.lineClicked.emit(uid)
                    break
            if hits:
                # smallest non-frame zone wins
                hits.sort(key=lambda h: (h[2], (h[1][2] - h[1][0]) * (h[1][3] - h[1][1])))
                self.zoneClicked.emit(hits[0][0], bool(ev.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)))
            else:
                self.zoneClicked.emit("", False)
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._band is not None and self._origin is not None:
            cur = self.mapToScene(ev.position().toPoint())
            self._band.setRect(QRectF(self._origin, cur).normalized())
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._band is not None and self._origin is not None:
            r = self._band.rect()
            self.scene().removeItem(self._band)
            self._band, self._origin = None, None
            if r.width() > 6 and r.height() > 4:
                z = self.zoom
                self.rectDrawn.emit(QRectF(r.left() / z, r.top() / z, r.width() / z, r.height() / z))
            return
        super().mouseReleaseEvent(ev)

    def contextMenuEvent(self, ev):
        self.contextRequested.emit(ev.globalPos())

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier:
            self.parent_page.change_zoom(1.15 if ev.angleDelta().y() > 0 else 1 / 1.15)
            return
        super().wheelEvent(ev)


# ===================================================================== page

class ZoningPage(Page):
    def __init__(self, ctx):
        super().__init__("Zoning", "Review and correct zones (or zone by hand), then generate XML. XML is always produced from the zones.",
                         scroll=False)
        self.ctx = ctx
        self.ed = None
        self.book_dir: Path | None = None
        self.page_i = 0
        self.sel: list[str] = []
        self.split_uid: str | None = None
        self.tags = load_tags(ctx.cfg)
        self.kind_colors = self.tags.get("kind_colors", {})
        self._doc_cache = {}
        self._tree_items = {}
        self._build()
        self._shortcuts()
        self._set_enabled(False)

    # ------------------------------------------------------------- layout
    def _build(self):
        bar = Card(margins=(12, 10, 12, 10))
        h = QHBoxLayout()
        h.addWidget(label("Book", "muted"))
        self.book_combo = QComboBox()
        self.book_combo.setMinimumWidth(260)
        self.book_combo.activated.connect(self._combo_open)
        h.addWidget(self.book_combo)
        b_open = button("Open Folder…", icon_name="folder", tooltip="Open a converted book folder (output/<ISBN>)")
        b_open.clicked.connect(self.pick_folder)
        h.addWidget(b_open)
        b_new = button("New Zoning from PDF…", icon_name="file", tooltip="Analyse a PDF and start zoning: with auto zones or empty (manual)")
        b_new.clicked.connect(self.new_from_pdf)
        h.addWidget(b_new)
        h.addStretch(1)
        self.b_undo = button("Undo", icon_name="chevron-left", tooltip="Undo (Ctrl+Z)")
        self.b_undo.clicked.connect(self.undo)
        self.b_redo = button("Redo", icon_name="chevron-right", tooltip="Redo (Ctrl+Y)")
        self.b_redo.clicked.connect(self.redo)
        self.b_save = button("Save", icon_name="check", tooltip="Save zones (Ctrl+S)")
        self.b_save.clicked.connect(self.save)
        self.b_gen = button("Generate XML", "primary", "play", tooltip="Rebuild edited zones and generate validated BITS XML (Ctrl+G)")
        self.b_gen.clicked.connect(self.generate)
        for w in (self.b_undo, self.b_redo, self.b_save, self.b_gen):
            h.addWidget(w)
        bar.lay.addLayout(h)
        self.info_lbl = label("No zoning project open. Convert a PDF (zones are saved automatically) or start a new zoning from a PDF.",
                              "muted", wrap=True)
        bar.lay.addWidget(self.info_lbl)
        self.prog = QProgressBar()
        self.prog.setMaximumHeight(6)
        self.prog.setTextVisible(False)
        self.prog.hide()
        bar.lay.addWidget(self.prog)
        self.add(bar)

        sp = QSplitter(Qt.Horizontal)
        # ---- left
        self.left_tabs = QTabWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        self.tree.itemExpanded.connect(self._tree_expand)
        self.left_tabs.addTab(self.tree, "Structure")
        fl = QWidget()
        fv = QVBoxLayout(fl)
        fv.setContentsMargins(0, 4, 0, 0)
        self.flag_list = QListWidget()
        self.flag_list.itemClicked.connect(lambda it: self.select([it.data(Qt.UserRole)], goto=True))
        fv.addWidget(self.flag_list, 1)
        nb = button("Next Flagged  (F7)", icon_name="chevron-right")
        nb.clicked.connect(self.next_flagged)
        fv.addWidget(nb)
        self.left_tabs.addTab(fl, "Flagged")
        self.left_tabs.setMinimumWidth(240)
        sp.addWidget(self.left_tabs)
        # ---- centre
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(6)
        tb = QHBoxLayout()
        self.b_prev = button("", icon_name="chevron-left", tooltip="Previous page (PgUp)")
        self.b_prev.clicked.connect(lambda: self.show_page(self.page_i - 1))
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMinimumWidth(70)
        self.page_spin.valueChanged.connect(lambda v: self.show_page(v - 1))
        self.page_total = label("of —", "muted")
        self.b_next = button("", icon_name="chevron-right", tooltip="Next page (PgDn)")
        self.b_next.clicked.connect(lambda: self.show_page(self.page_i + 1))
        self.folio_lbl = label("", "muted")
        for w in (self.b_prev, self.page_spin, self.page_total, self.b_next, self.folio_lbl):
            tb.addWidget(w)
        tb.addStretch(1)
        zm = button("−", tooltip="Zoom out (Ctrl+wheel)")
        zm.clicked.connect(lambda: self.change_zoom(1 / 1.15))
        zp = button("+", tooltip="Zoom in (Ctrl+wheel)")
        zp.clicked.connect(lambda: self.change_zoom(1.15))
        tb.addWidget(zm)
        tb.addWidget(zp)
        mv.addLayout(tb)
        tb = QHBoxLayout()
        self.b_select = button("Select", tooltip="Select mode (V): click zones, Ctrl+click for several")
        self.b_select.setCheckable(True)
        self.b_select.setChecked(True)
        self.b_select.clicked.connect(lambda: self.set_mode("select"))
        self.b_draw = button("Draw", tooltip="Draw mode (D): drag a box around text to create a zone")
        self.b_draw.setCheckable(True)
        self.b_draw.clicked.connect(lambda: self.set_mode("draw"))
        tb.addWidget(self.b_select)
        tb.addWidget(self.b_draw)
        tb.addWidget(label("as", "muted"))
        self.draw_tag = QComboBox()
        self.draw_tag.setToolTip("Tag applied to a drawn box")
        self.draw_tag.addItem("Ask after drawing", "")
        for t in self.tags["tags"]:
            self.draw_tag.addItem(t["label"], t["id"])
        self.draw_tag.setMinimumWidth(170)
        tb.addWidget(self.draw_tag)
        az = button("Auto-zone Page", tooltip="Run the detector on this page's unzoned text (useful in manual zoning)")
        az.clicked.connect(self.auto_zone_page)
        tb.addWidget(az)
        tb.addStretch(1)
        self.chk_unzoned = QCheckBox("Unzoned text")
        self.chk_unzoned.setToolTip("Show text that belongs to no zone (dashed grey)")
        self.chk_unzoned.setChecked(True)
        self.chk_unzoned.toggled.connect(lambda: self.render_page())
        tb.addWidget(self.chk_unzoned)
        mv.addLayout(tb)
        self.canvas = PageCanvas()
        self.canvas.parent_page = self
        self.canvas.zoneClicked.connect(self._canvas_click)
        self.canvas.rectDrawn.connect(self._drawn)
        self.canvas.lineClicked.connect(self._line_clicked)
        self.canvas.contextRequested.connect(self._context_menu)
        mv.addWidget(self.canvas, 1)
        self.page_stats = label("", "faint")
        mv.addWidget(self.page_stats)
        sp.addWidget(mid)
        # ---- right
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        pc = Card(margins=(12, 10, 12, 10), spacing=6)
        self.z_title = label("No zone selected", "sectionTitle")
        pc.lay.addWidget(self.z_title)
        self.z_meta = label("", "muted", wrap=True)
        pc.lay.addWidget(self.z_meta)
        self.z_flags = label("", "warn", wrap=True)
        pc.lay.addWidget(self.z_flags)
        self.z_text = QPlainTextEdit()
        self.z_text.setReadOnly(True)
        self.z_text.setMaximumHeight(110)
        pc.lay.addWidget(self.z_text)
        self.z_doc = label("", "faint", wrap=True)
        self.z_doc.setOpenExternalLinks(True)
        pc.lay.addWidget(self.z_doc)
        self.chk_reviewed = QCheckBox("Reviewed (clears its flags in the report)")
        self.chk_reviewed.toggled.connect(self._reviewed)
        pc.lay.addWidget(self.chk_reviewed)
        rv.addWidget(pc)
        tc = Card(margins=(12, 10, 12, 10), spacing=6)
        tc.lay.addWidget(label("Tag selection as", "fieldLabel"))
        grid = QGridLayout()
        grid.setSpacing(4)
        self.tag_buttons = {}
        r = c = 0
        for t in self.tags["tags"]:
            if t["id"] == "auto":
                continue
            b = QPushButton(f"{t['label']}" + (f"  ({t['key']})" if t.get("key") else ""))
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ text-align:left; padding:4px 8px; border-left:4px solid {t['color']}; }}")
            b.clicked.connect(lambda _=False, tid=t["id"]: self.apply_tag(tid))
            el = t.get("element")
            desc = self.tags["library"]["elements"].get(el, "")
            b.setToolTip(f"<{el}> {desc}" if el else t["label"])
            grid.addWidget(b, r, c)
            self.tag_buttons[t["id"]] = b
            c += 1
            if c == 2:
                c, r = 0, r + 1
        tc.lay.addLayout(grid)
        rv.addWidget(tc)
        ac = Card(margins=(12, 10, 12, 10), spacing=6)
        ac.lay.addWidget(label("Actions", "fieldLabel"))
        ag = QGridLayout()
        ag.setSpacing(4)
        acts = [("Merge (M)", self.merge, "Join the selected zones into one (e.g. a paragraph split in two)"),
                ("Split (S)", self.split, "Split the zone at the line you clicked last"),
                ("Unwrap (U)", self.unwrap, "Remove a box/list/heading wrapper, keep its content"),
                ("Delete (Del)", self.delete, "Remove the zone; its text becomes unzoned (flagged in QA)"),
                ("Move Up", lambda: self.move(-1), "Move the zone before its previous sibling"),
                ("Move Down", lambda: self.move(1), "Move the zone after its next sibling"),
                ("Level −  ([)", lambda: self.level(-1), "Heading one level up"),
                ("Level +  (])", lambda: self.level(1), "Heading one level down")]
        for i, (txt, fn, tip) in enumerate(acts):
            b = button(txt, tooltip=tip)
            b.clicked.connect(fn)
            ag.addWidget(b, i // 2, i % 2)
        ac.lay.addLayout(ag)
        rv.addWidget(ac)
        atc = Card(margins=(12, 10, 12, 10), spacing=6)
        atc.lay.addWidget(label("Attributes", "fieldLabel"))
        self.attr_table = QTableWidget(0, 2)
        self.attr_table.setHorizontalHeaderLabels(["Attribute", "Value"])
        self.attr_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.attr_table.verticalHeader().hide()
        self.attr_table.setMaximumHeight(130)
        self.attr_table.itemChanged.connect(self._attr_changed)
        atc.lay.addWidget(self.attr_table)
        ab = button("Add Attribute", "ghost")
        ab.clicked.connect(self._attr_add)
        atc.lay.addWidget(ab)
        rv.addWidget(atc)
        rv.addStretch(1)
        from PySide6.QtWidgets import QScrollArea
        rs = QScrollArea()
        rs.setWidgetResizable(True)
        rs.setFrameShape(QFrame.NoFrame)
        rs.setWidget(right)
        rs.setMinimumWidth(340)
        rs.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sp.addWidget(rs)
        sp.setSizes([250, 720, 360])
        sp.setStretchFactor(1, 1)
        self.add(sp, 1)

    def _shortcuts(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
        sc("Ctrl+Z", self.undo)
        sc("Ctrl+Y", self.redo)
        sc("Ctrl+Shift+Z", self.redo)
        sc("Ctrl+S", self.save)
        sc("Ctrl+G", self.generate)
        sc("PgUp", lambda: self.show_page(self.page_i - 1))
        sc("PgDown", lambda: self.show_page(self.page_i + 1))
        sc("F7", self.next_flagged)
        sc("V", lambda: self.set_mode("select"))
        sc("D", lambda: self.set_mode("draw"))
        sc("M", self.merge)
        sc("S", self.split)
        sc("U", self.unwrap)
        sc("Del", self.delete)
        sc("[", lambda: self.level(-1))
        sc("]", lambda: self.level(1))
        for t in self.tags["tags"]:
            if t.get("key") and t["id"] != "auto":
                sc(t["key"], lambda tid=t["id"]: self.apply_tag(tid))

    def _set_enabled(self, on: bool):
        for w in (self.b_undo, self.b_redo, self.b_save, self.b_gen, self.canvas, self.tree, self.flag_list):
            w.setEnabled(on)

    # ------------------------------------------------------------- opening
    def on_show(self, book_dir=None, **kw):
        self._fill_combo()
        if book_dir:
            self.open_book(Path(book_dir))
        elif self.ed is None and self.ctx.last_book_dir and (Path(self.ctx.last_book_dir) / "zoning" / "zones.json").exists():
            self.open_book(Path(self.ctx.last_book_dir))

    def _fill_combo(self):
        cur = str(self.book_dir) if self.book_dir else None
        self.book_combo.blockSignals(True)
        self.book_combo.clear()
        for b in services.zoning_books(self.ctx.cfg.path("output_dir")):
            mode = {"auto": "auto zones", "auto-review": "auto zones", "manual": "manual"}.get(b.get("mode"), b.get("mode") or "")
            self.book_combo.addItem(f"{b['book']}  ·  {mode}  ·  {b.get('edits', 0)} edit(s)", b["dir"])
        if cur:
            i = self.book_combo.findData(cur)
            if i >= 0:
                self.book_combo.setCurrentIndex(i)
        self.book_combo.blockSignals(False)

    def _combo_open(self, i):
        d = self.book_combo.itemData(i)
        if d and (self.book_dir is None or Path(d) != self.book_dir):
            self.open_book(Path(d))

    def pick_folder(self):
        p = QFileDialog.getExistingDirectory(self, "Select a converted book folder (output/<ISBN>)", str(self.ctx.cfg.path("output_dir")))
        if p:
            self.open_book(Path(p))

    def _confirm_discard(self) -> bool:
        if self.ed is not None and self.ed.dirty_file:
            r = QMessageBox.question(self, "Unsaved zoning changes", "Save the zoning changes of the current book first?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return False
            if r == QMessageBox.Save:
                self.save()
        return True

    def open_book(self, d: Path):
        if not (d / "zoning" / "zones.json").exists():
            QMessageBox.information(self, "No zoning project",
                                    f"{d.name} has no zoning project.\nConvert the PDF again (zones are now saved automatically) "
                                    "or use 'New Zoning from PDF…'.")
            return
        if not self._confirm_discard():
            return
        self.info_lbl.setText(f"Opening {d.name}…")
        self.prog.setRange(0, 0)
        self.prog.show()
        self._pending_dir = d
        self.worker = Worker(lambda progress=None, cancel=None: services.load_zoning(self.ctx.cfg, d))
        self.worker.finished.connect(self._opened)
        self.worker.failed.connect(self._failed)
        start(self, self.worker)

    def _opened(self, ed):
        d = self._pending_dir
        self.prog.hide()
        self.ed = ed
        self.book_dir = d
        self.ctx.last_book_dir = d
        self.sel = []
        n = len(ed.pages)
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, n))
        self.page_spin.blockSignals(False)
        self.page_total.setText(f"of {n:,}")
        self._set_enabled(True)
        self._fill_combo()
        self.refresh_all(goto_page=0)
        self.ctx.status(f"Zoning: {d.name}")

    def new_from_pdf(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select the PDF (or the first PDF of a split book)", str(self.ctx.cfg.path("input_dir")),
                                           "PDF (*.pdf)")
        if not p:
            return
        mb = QMessageBox(self)
        mb.setWindowTitle("New zoning")
        mb.setText("How should the zones start?")
        mb.setInformativeText("Auto zones: the converter proposes every zone and you correct them.\n"
                              "Empty: you draw the zones yourself (Draw + 'Auto-detect' still helps).")
        b_auto = mb.addButton("Auto zones (recommended)", QMessageBox.AcceptRole)
        b_man = mb.addButton("Empty (manual)", QMessageBox.AcceptRole)
        mb.addButton(QMessageBox.Cancel)
        mb.exec()
        if mb.clickedButton() not in (b_auto, b_man):
            return
        manual = mb.clickedButton() is b_man
        src = Path(p)
        if len(list(src.parent.glob("*.pdf"))) > 1 and re.search(r"(?i)(fm|ch\d+|sec\d+|app[a-z]|index)\.pdf$", src.name):
            src = src.parent           # split book: the folder is the book
        if not self._confirm_discard():
            return
        self.info_lbl.setText(f"Analysing {src.name}…")
        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog.show()
        self.worker = Worker(services.start_zoning, self.ctx.cfg, src, self.ctx.cfg.path("output_dir"), manual=manual)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._created)
        self.worker.failed.connect(self._failed)
        start(self, self.worker)

    def _created(self, d):
        self.open_book(Path(d))

    def _gen_failed(self, msg, detail):
        self.b_gen.setEnabled(True)
        self._failed(msg, detail)

    def _progress(self, info):
        f = info.get("frac")
        if isinstance(f, (int, float)):
            self.prog.setValue(int(f * 100))
        self.info_lbl.setText(f"{info.get('stage', '')}  {info.get('detail', '')}")

    def _failed(self, msg, detail):
        self.prog.hide()
        self.info_lbl.setText(f"⚠ {msg}")
        QMessageBox.warning(self, "Zoning", f"{msg}\n\n{detail[-1500:] if detail else ''}")

    # ------------------------------------------------------------- refresh
    def refresh_all(self, goto_page=None):
        self._fill_tree()
        self._fill_flags()
        if goto_page is not None:
            self.show_page(goto_page)
        else:
            self.render_page()
        self._show_props()
        self._update_info()

    def _update_info(self):
        if not self.ed:
            return
        info = self.ed.project.info
        unz = sum(len(self.ed.unzoned_lines(p.index)) for p in self.ed.pages) if len(self.ed.pages) < 1500 else None
        txt = (f"{self.book_dir.name}  ·  mode: {info.get('mode')}  ·  {info.get('edits', 0)} edit(s)"
               + ("  ·  unsaved changes" if self.ed.dirty_file else "  ·  saved")
               + (f"  ·  {unz} unzoned line(s)" if unz is not None else ""))
        self.info_lbl.setText(txt)
        self.b_undo.setEnabled(bool(self.ed.undo_stack))
        self.b_redo.setEnabled(bool(self.ed.redo_stack))

    def _tree_label(self, n) -> str:
        t = zone_text(n, self.ed, 70)
        lab = zone_label(n)
        flag = " ⚠" if (n.flags and not n.meta.get("reviewed")) else ""
        src = n.meta.get("src")
        mark = " ✎" if src in ("edited", "manual", "manual-auto") else ""
        return f"{lab}{flag}{mark}  {t}" if t else f"{lab}{flag}{mark}"

    def _fill_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        self._tree_items = {}
        for c in self.ed.root.children:
            self._add_tree_item(None, c)
        self.tree.blockSignals(False)

    def _add_tree_item(self, parent_item, n):
        it = QTreeWidgetItem([self._tree_label(n)])
        it.setData(0, Qt.UserRole, n.meta.get("zid"))
        col = self.kind_colors.get(n.kind)
        if col:
            it.setForeground(0, QBrush(QColor(col)))
        if parent_item is None:
            self.tree.addTopLevelItem(it)
        else:
            parent_item.addChild(it)
        self._tree_items[n.meta.get("zid")] = it
        if n.children:
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            it.setData(0, Qt.UserRole + 1, False)      # children not loaded yet
        return it

    def _tree_expand(self, it):
        if it.data(0, Qt.UserRole + 1) is False:
            it.setData(0, Qt.UserRole + 1, True)
            n = self.ed.node(it.data(0, Qt.UserRole))
            self.tree.blockSignals(True)
            for c in (n.children if n else []):
                self._add_tree_item(it, c)
            self.tree.blockSignals(False)

    def _reveal(self, zid):
        n = self.ed.node(zid)
        if n is None:
            return None
        chain = [n] + list(n.ancestors())
        chain = [c for c in reversed(chain) if c.parent is not None or c is n]
        item = None
        for c in chain:
            z = c.meta.get("zid")
            if z in self._tree_items:
                item = self._tree_items[z]
                if c is not n:
                    self.tree.blockSignals(True)
                    item.setExpanded(True)
                    self._tree_expand(item)
                    self.tree.blockSignals(False)
        return self._tree_items.get(zid)

    def _fill_flags(self):
        self.flag_list.clear()
        fl = self.ed.flagged(self.ctx.cfg.get("confidence.warn", 0.75))
        for n in fl[:3000]:
            pg = self.ed.page_by.get(n.page) if n.page is not None else None
            where = f"p.{pg.folio}" if pg and pg.folio else (f"pdf {n.page + 1}" if n.page is not None else "")
            why = ", ".join(n.flags) or f"low confidence {n.conf:.2f}"
            it = QListWidgetItem(f"{zone_label(n)}  ·  {where}\n{why}")
            it.setData(Qt.UserRole, n.meta.get("zid"))
            self.flag_list.addItem(it)
        self.left_tabs.setTabText(1, f"Flagged ({len(fl)})")

    # ------------------------------------------------------------- page view
    def _doc(self, path):
        import pymupdf
        if path not in self._doc_cache:
            if len(self._doc_cache) > 4:
                k = next(iter(self._doc_cache))
                self._doc_cache.pop(k).close()
            self._doc_cache[path] = pymupdf.open(path)
        return self._doc_cache[path]

    def show_page(self, i):
        if not self.ed:
            return
        n = len(self.ed.pages)
        if not (0 <= i < n):
            return
        self.page_i = i
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(i + 1)
        self.page_spin.blockSignals(False)
        self.render_page()

    def change_zoom(self, f):
        self.canvas.zoom = max(0.5, min(4.0, self.canvas.zoom * f))
        self.render_page()

    def set_mode(self, mode):
        self.canvas.set_mode(mode)
        self.b_select.setChecked(mode == "select")
        self.b_draw.setChecked(mode == "draw")

    def _pixmap(self, pg) -> QPixmap:
        import pymupdf
        st = self.ed.st
        src = st.src
        fi, pno = src.page_map[pg.index]
        doc = self._doc(str(src.files[fi].path))
        page = doc[pno]
        mat = pymupdf.Matrix(self.canvas.zoom, self.canvas.zoom)
        if pg.rot:
            mat = mat.prerotate(-pg.rot)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(img)

    def render_page(self):
        if not self.ed:
            return
        pg = self.ed.pages[self.page_i]
        try:
            pm = self._pixmap(pg)
        except Exception as e:
            self.page_stats.setText(f"Cannot render page: {e}")
            return
        zones = []
        sel = set(self.sel)
        warn = self.ctx.cfg.get("confidence.warn", 0.75)
        for n, b in self.ed.page_zones(pg.index):
            zid = n.meta.get("zid")
            frame = n.kind in ("boxed-text", "book-part", "toc", "index") and bool(n.children or n.kind in ("toc", "index"))
            zones.append({"zid": zid, "rect": b, "color": self.kind_colors.get(n.kind, "#64748B"), "label": zone_label(n),
                          "selected": zid in sel, "frame": frame and n.kind != "book-part",
                          "flagged": bool(n.flags and not n.meta.get("reviewed")) or (n.conf < warn and n.kind in ("p", "sec", "list"))})
        unz = [(l.uid, l.bbox) for l in self.ed.unzoned_lines(pg.index)]
        lines = [(l.uid, l.bbox) for l in self.ed.page_lines(pg.index)]
        self.canvas.show_page(pm, zones, unz, lines, self.chk_unzoned.isChecked())
        self.folio_lbl.setText(f"printed page {pg.folio}" if pg.folio else "")
        self.page_stats.setText(f"{len(zones)} zone(s) on this page  ·  {len(unz)} unzoned line(s)"
                                + (f"  ·  {pg.kind}" if pg.kind not in ("body",) else ""))

    # ------------------------------------------------------------- selection
    def _canvas_click(self, zid, additive):
        if not zid:
            if not additive:
                self.select([])
            return
        if additive:
            s = list(self.sel)
            if zid in s:
                s.remove(zid)
            else:
                s.append(zid)
            self.select(s)
        else:
            self.select([zid])

    def _line_clicked(self, uid):
        self.split_uid = uid

    def _tree_selected(self):
        zids = [it.data(0, Qt.UserRole) for it in self.tree.selectedItems()]
        if zids == self.sel:
            return
        self.sel = zids
        if zids:
            n = self.ed.node(zids[0])
            pages = sorted({l.page for l in self.ed.zone_lines(n)}) if n else []
            pgi = pages[0] if pages else (n.page if n and n.page is not None else None)
            if pgi is not None and pgi != self.page_i:
                self.show_page(pgi)
            else:
                self.render_page()
        else:
            self.render_page()
        self._show_props()

    def select(self, zids, goto=False):
        self.sel = [z for z in zids if self.ed and self.ed.node(z) is not None]
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        for z in self.sel:
            it = self._reveal(z)
            if it is not None:
                it.setSelected(True)
                self.tree.scrollToItem(it)
        self.tree.blockSignals(False)
        if goto and self.sel:
            n = self.ed.node(self.sel[0])
            pages = sorted({l.page for l in self.ed.zone_lines(n)})
            pgi = pages[0] if pages else n.page
            if pgi is not None and pgi != self.page_i:
                self.show_page(pgi)
                self._show_props()
                return
        self.render_page()
        self._show_props()

    def _show_props(self):
        self.chk_reviewed.blockSignals(True)
        self.attr_table.blockSignals(True)
        try:
            if not self.ed or not self.sel:
                self.z_title.setText("No zone selected")
                self.z_meta.setText("Click a zone on the page or in the structure. Ctrl+click selects several.")
                self.z_flags.setText("")
                self.z_text.setPlainText("")
                self.z_doc.setText("")
                self.chk_reviewed.setChecked(False)
                self.attr_table.setRowCount(0)
                return
            n = self.ed.node(self.sel[0])
            if n is None:
                return
            more = f"  (+{len(self.sel) - 1} more)" if len(self.sel) > 1 else ""
            self.z_title.setText(f"{zone_label(n)}  <{n.kind}>{more}")
            pages = sorted({l.page for l in self.ed.zone_lines(n)})
            folios = [self.ed.page_by[p].folio or str(p + 1) for p in pages[:4]]
            src = {"edited": "edited", "manual": "drawn by hand", "manual-auto": "drawn (auto inside)"}.get(n.meta.get("src"), "automatic")
            self.z_meta.setText(f"Source: {src}  ·  confidence {n.conf:.2f}  ·  {len(n.meta.get('_ln') or [])} line(s)"
                                + (f"  ·  page {', '.join(folios)}" if folios else "") + f"  ·  {n.meta.get('zid')}")
            self.z_flags.setText(("⚠ " + ", ".join(n.flags)) if n.flags else "")
            lines = sorted(self.ed.zone_lines(n), key=line_sort_key)
            self.z_text.setPlainText("\n".join(l.text for l in lines[:60]) if lines else zone_text(n, self.ed, 1200))
            lib = self.tags["library"]
            desc = lib["elements"].get(n.kind)
            if desc:
                url = lib.get("url_pattern", "").format(name=n.kind)
                self.z_doc.setText(f"&lt;{n.kind}&gt; {desc} — <a href='{url}'>BITS Tag Library</a>")
            else:
                self.z_doc.setText("")
            self.chk_reviewed.setChecked(bool(n.meta.get("reviewed")))
            self.attr_table.setRowCount(0)
            for k, v in n.attrs.items():
                r = self.attr_table.rowCount()
                self.attr_table.insertRow(r)
                ki = QTableWidgetItem(k)
                ki.setFlags(ki.flags() & ~Qt.ItemIsEditable)
                self.attr_table.setItem(r, 0, ki)
                self.attr_table.setItem(r, 1, QTableWidgetItem(str(v)))
        finally:
            self.chk_reviewed.blockSignals(False)
            self.attr_table.blockSignals(False)

    # ------------------------------------------------------------- editing
    def _after_edit(self, select=None, msg=""):
        if select is not None:
            self.sel = [n.meta.get("zid") for n in select if n is not None and n.meta.get("zid")]
        self.sel = [z for z in self.sel if self.ed.node(z) is not None]
        self.refresh_all()
        if self.sel:
            self.select(self.sel)
        if msg:
            self.ctx.status(msg)

    def apply_tag(self, tid):
        if not self.ed:
            return
        if not self.sel:
            self.ctx.status("Select one or more zones first (or switch to Draw mode and drag a box).")
            return
        t = next((x for x in self.tags["tags"] if x["id"] == tid), None)
        if t is None:
            return
        try:
            kind, opt = self._tag_to_op(t)
            out = self.ed.retag(list(self.sel), kind, **opt)
            self._after_edit(out, f"Tagged as {t['label']}")
        except Exception as e:
            self._edit_error(e)

    def _tag_to_op(self, t):
        tid = t["id"]
        if t.get("element") == "sec":
            return "sec", {"level": t.get("level", 1)}
        if tid in ("chapter", "appendix", "part-title", "toc", "index"):
            return tid, {}
        return t.get("element") or tid, {}

    def _drawn(self, rect: QRectF):
        tid = self.draw_tag.currentData()
        if not tid:
            menu = QMenu(self)
            for t in self.tags["tags"]:
                if t["id"] in ("part-title",):
                    continue
                a = menu.addAction(t["label"])
                a.setData(t["id"])
            a = menu.exec(self.cursor().pos())
            if a is None:
                self.render_page()
                return
            tid = a.data()
        t = next((x for x in self.tags["tags"] if x["id"] == tid), None)
        r = (rect.left(), rect.top(), rect.right(), rect.bottom())
        try:
            if tid in ("chapter", "appendix", "part-title", "toc", "index"):
                made = self.ed.draw(self.ed.pages[self.page_i].index, r, tid)
            else:
                kind, opt = ("auto", {}) if tid == "auto" else self._tag_to_op(t)
                made = self.ed.draw(self.ed.pages[self.page_i].index, r, kind, **opt)
            if not made:
                self.ctx.status("No text inside the box (draw around the lines you want to zone).")
            self._after_edit(made, f"Drew {t['label'] if t else tid}")
        except Exception as e:
            self._edit_error(e)

    def auto_zone_page(self):
        if not self.ed:
            return
        pg = self.ed.pages[self.page_i]
        has_zones = bool(self.ed.page_zones(pg.index))
        try:
            made = self.ed.draw(pg.index, pg.trim, "auto", only_unzoned=has_zones)
            self._after_edit(made, f"Auto-zoned {len(made)} zone(s)" if made else "Nothing unzoned on this page")
        except Exception as e:
            self._edit_error(e)

    def _context_menu(self, pos):
        if not self.ed or not self.sel:
            return
        menu = QMenu(self)
        for t in self.tags["tags"]:
            if t["id"] == "auto":
                continue
            a = menu.addAction(f"Tag as {t['label']}")
            a.triggered.connect(lambda _=False, tid=t["id"]: self.apply_tag(tid))
        menu.addSeparator()
        for txt, fn in (("Merge", self.merge), ("Split at clicked line", self.split), ("Unwrap", self.unwrap), ("Delete", self.delete)):
            menu.addAction(txt).triggered.connect(fn)
        menu.exec(pos)

    def _edit_error(self, e):
        import traceback
        self.ed.reindex()
        QMessageBox.warning(self, "Zoning", f"That edit could not be applied: {e}\n\n{traceback.format_exc()[-1200:]}\n"
                                            "Use Undo if the structure looks wrong.")
        self.refresh_all()

    def merge(self):
        if self.ed and len(self.sel) >= 2:
            try:
                n = self.ed.merge(list(self.sel))
                self._after_edit([n] if n else None, "Merged")
            except Exception as e:
                self._edit_error(e)
        else:
            self.ctx.status("Select two or more neighbouring zones (Ctrl+click) to merge.")

    def split(self):
        if not (self.ed and self.sel):
            return
        n = self.ed.node(self.sel[0])
        target = n
        if n is not None and n.kind == "list-item":
            target = next((c for c in n.children if c.kind == "p"), n)
        if not self.split_uid or target is None or self.split_uid not in (target.meta.get("_ln") or []):
            self.ctx.status("Click the line where the second part should start (inside the selected zone), then press S.")
            return
        try:
            new = self.ed.split(target.meta["zid"], self.split_uid)
            self._after_edit([target, new] if new else None, "Split")
        except Exception as e:
            self._edit_error(e)

    def unwrap(self):
        if self.ed and self.sel:
            try:
                out = self.ed.unwrap(self.sel[0])
                self._after_edit(out[:1], "Unwrapped")
            except Exception as e:
                self._edit_error(e)

    def delete(self):
        if self.ed and self.sel:
            try:
                self.ed.delete(list(self.sel))
                self.sel = []
                self._after_edit(None, "Deleted — the text is now unzoned (grey). Draw or tag it again, or leave it out on purpose.")
            except Exception as e:
                self._edit_error(e)

    def move(self, d):
        if self.ed and self.sel:
            self.ed.move(self.sel[0], d)
            self._after_edit(None, "Moved")

    def level(self, d):
        if not (self.ed and self.sel):
            return
        n = self.ed.node(self.sel[0])
        if n is None or n.kind != "sec":
            self.ctx.status("Level changes apply to headings (sections).")
            return
        self.ed.set_level(n.meta["zid"], int(n.attrs.get("disp-level") or 1) + d)
        self._after_edit([n], f"Heading level {n.attrs.get('disp-level')}")

    def _reviewed(self, on):
        if self.ed:
            for z in self.sel:
                self.ed.set_reviewed(z, on)
            self._fill_flags()
            self.render_page()
            self._update_info()

    def _attr_changed(self, item):
        if not (self.ed and self.sel) or item.column() != 1:
            return
        key = self.attr_table.item(item.row(), 0).text()
        self.ed.set_attr(self.sel[0], key, item.text())
        self._update_info()

    def _attr_add(self):
        if not (self.ed and self.sel):
            return
        from PySide6.QtWidgets import QInputDialog
        key, ok = QInputDialog.getText(self, "Add attribute", "Attribute name (e.g. content-type, specific-use):")
        if ok and key.strip():
            val, ok2 = QInputDialog.getText(self, "Add attribute", f"Value for {key.strip()}:")
            if ok2:
                self.ed.set_attr(self.sel[0], key.strip(), val)
                self._show_props()
                self._update_info()

    def undo(self):
        if self.ed:
            lab = self.ed.undo()
            if lab:
                self._after_edit(None, f"Undo: {lab}")

    def redo(self):
        if self.ed:
            lab = self.ed.redo()
            if lab:
                self._after_edit(None, f"Redo: {lab}")

    def next_flagged(self):
        if not self.ed:
            return
        fl = self.ed.flagged(self.ctx.cfg.get("confidence.warn", 0.75))
        if not fl:
            self.ctx.status("No flagged zones left.")
            return
        cur = self.ed.node(self.sel[0]) if self.sel else None
        ids = [n.meta.get("zid") for n in fl]
        k = (ids.index(cur.meta.get("zid")) + 1) if cur is not None and cur.meta.get("zid") in ids else 0
        nxt = fl[k % len(fl)]
        self.left_tabs.setCurrentIndex(1)
        self.select([nxt.meta.get("zid")], goto=True)

    # ------------------------------------------------------------- save / generate
    def save(self):
        if not self.ed:
            return
        try:
            self.ed.project.save(tree_only=True)
            self.ed.dirty_file = False
            self._update_info()
            self._fill_combo()
            self.ctx.status("Zones saved")
        except Exception as e:
            QMessageBox.warning(self, "Save failed", str(e))

    def generate(self):
        if not self.ed:
            return
        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog.show()
        self.b_gen.setEnabled(False)
        self.worker = Worker(services.zoning_generate, self.ctx.cfg, self.ed)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._generated)
        self.worker.failed.connect(self._gen_failed)
        start(self, self.worker)

    def _generated(self, r: dict):
        self.prog.hide()
        self.b_gen.setEnabled(True)
        self._update_info()
        self._fill_combo()
        v = r.get("validation") or {}
        crit = v.get("critical") or []
        lines = [f"Status: {r['status']}",
                 f"DTD: {'valid' if v.get('dtd_valid') else str(v.get('dtd_errors')) + ' error(s)'}",
                 f"Links: {v.get('links_valid')}/{v.get('links')} valid  ·  broken {v.get('broken_links')}",
                 f"Content coverage: {v.get('coverage', 0):.1%}",
                 f"Figures {v.get('figures', {}).get('total', 0)}  ·  Tables {v.get('tables', {}).get('total', 0)}"]
        if crit:
            lines.append("Blocking: " + "; ".join(crit))
        mb = QMessageBox(self)
        mb.setWindowTitle("XML generated")
        mb.setIcon(QMessageBox.Information if r["status"] != "FAILED" else QMessageBox.Warning)
        mb.setText(f"{Path(r['xml']).name if r.get('xml') else ''}\n\n" + "\n".join(lines))
        b_rep = mb.addButton("Open Report", QMessageBox.ActionRole)
        b_cmp = mb.addButton("Compare PDF ↔ XML", QMessageBox.ActionRole)
        b_val = mb.addButton("Validate", QMessageBox.ActionRole)
        mb.addButton(QMessageBox.Close)
        mb.exec()
        c = mb.clickedButton()
        if c is b_rep and r.get("report"):
            self.ctx.open_path(r["report"])
        elif c is b_cmp:
            self.ctx.navigate("compare", book_dir=r.get("out_dir"))
        elif c is b_val and r.get("xml"):
            self.ctx.navigate("validate", paths=[Path(r["xml"])])
        self.ctx.status(f"XML generated: {r['status']}")
        self.ctx.refresh_status()
