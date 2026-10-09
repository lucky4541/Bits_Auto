"""Convert: the primary screen (input → options → run → result)."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLineEdit, QMessageBox, QStackedWidget,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from bits_tool import services

from . import app_state, icons
from .app_state import theme_tokens
from .widgets.common import Card, FitStack, Page, PathField, button, label
from .widgets.figure_table_view import FigureTableView
from .widgets.file_drop import DropZone, FileList
from .widgets.issue_list import IssueList
from .widgets.progress_panel import ProgressPanel, StageList
from .widgets.status_card import StatGrid
from .workers import Worker, start

OCR = [("Automatic", "auto"), ("Tesseract", "tesseract"), ("PaddleOCR", "paddle"), ("Off", "none")]
LANGS = [("Auto Detect", None), ("Spanish", ["spa", "eng"]), ("English", ["eng"]), ("French", ["fra", "eng"]),
         ("German", ["deu", "eng"]), ("Portuguese", ["por", "eng"]), ("Italian", ["ita", "eng"]), ("Hindi", ["hin", "eng"]),
         ("Arabic", ["ara", "eng"]), ("Chinese (simplified)", ["chi_sim", "eng"]), ("Japanese", ["jpn", "eng"])]


class ConvertPage(Page):
    def __init__(self, ctx):
        super().__init__("Convert PDF", "Convert one or more PDFs using the validated BITS mapping profile.")
        self.ctx = ctx
        self.running = False
        self.results = []
        # safety / resume banners
        self.banner = Card("banner_warn")
        self.banner_lbl = label("", wrap=True)
        self.banner.lay.addWidget(self.banner_lbl)
        self.banner.hide()
        self.add(self.banner)
        self.resume_card = Card("banner_info")
        self.resume_lbl = label("", wrap=True)
        self.resume_card.lay.addWidget(label("Previous conversion detected.", "sectionTitle"))
        self.resume_card.lay.addWidget(self.resume_lbl)
        rh = QHBoxLayout()
        rb = button("Resume", "primary", "resume")
        rb.clicked.connect(lambda: self.start(resume=True))
        sb = button("Start Again", icon_name="refresh")
        sb.clicked.connect(lambda: self.start(resume=False))
        rh.addWidget(rb)
        rh.addWidget(sb)
        rh.addStretch(1)
        self.resume_card.lay.addLayout(rh)
        self.resume_card.hide()
        self.add(self.resume_card)

        self.views = FitStack()
        self.add(self.views)
        self._build_setup()
        self._build_running()
        self._build_result()
        self.finish()

    # ---------------------------------------------------------------- setup
    def _build_setup(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        inp = Card()
        inp.lay.addWidget(label("PDF / Folder", "fieldLabel"))
        self.drop = DropZone()
        self.drop.dropped.connect(self.add_paths)
        inp.lay.addWidget(self.drop)
        self.files = FileList()
        self.files.changed.connect(self._files_changed)
        self.files.hide()
        inp.lay.addWidget(self.files)
        qi = QHBoxLayout()
        self.use_inputs = button("Use the inputs folder", "ghost", "folder")
        self.use_inputs.clicked.connect(lambda: self.add_paths([self.ctx.cfg.path("input_dir")]))
        qi.addWidget(self.use_inputs)
        qi.addStretch(1)
        inp.lay.addLayout(qi)
        v.addWidget(inp)
        opt = Card()
        self.out = PathField("Output")
        opt.lay.addWidget(self.out)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        self.profile = QComboBox()
        self.profile.addItem("Production Mapping")
        self.profile.setToolTip("Mapping learned from the approved samples (Sample Analysis).")
        self.ocr = QComboBox()
        for t, _k in OCR:
            self.ocr.addItem(t)
        self.ocr.setToolTip("Automatically OCR scanned pages.")
        self.lang = QComboBox()
        for t, _k in LANGS:
            self.lang.addItem(t)
        self.lang.setToolTip("OCR language(s). Auto Detect uses the configured languages.")
        for i, (t, wdg) in enumerate((("BITS Profile", self.profile), ("OCR", self.ocr), ("Language", self.lang))):
            g.addWidget(label(t, "fieldLabel"), 0, i)
            g.addWidget(wdg, 1, i)
        opt.lay.addLayout(g)
        self.meta_toggle = button("Book metadata (optional)", "ghost", "layers")
        self.meta_toggle.setCheckable(True)
        mt = QHBoxLayout()
        mt.addWidget(self.meta_toggle)
        mt.addStretch(1)
        opt.lay.addLayout(mt)
        self.meta_box = QWidget()
        mg = QGridLayout(self.meta_box)
        mg.setContentsMargins(0, 0, 0, 0)
        mg.setHorizontalSpacing(12)
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("from file name / title page")
        self.prefix.setToolTip("Author surname used in every ID (e.g. martin → martin-ch001-p001).")
        self.short = QLineEdit()
        self.short.setPlaceholderText("e.g. ayrn03")
        self.short.setToolTip("Short code (alt-title short-name and thumbnail name).")
        self.serial = QLineEdit()
        self.serial.setPlaceholderText("from the Book PPM")
        for i, (t, wdg) in enumerate((("ID prefix", self.prefix), ("Short code", self.short), ("Serial code", self.serial))):
            mg.addWidget(label(t, "fieldLabel"), 0, i)
            mg.addWidget(wdg, 1, i)
        self.meta_box.hide()
        self.meta_toggle.toggled.connect(self.meta_box.setVisible)
        opt.lay.addWidget(self.meta_box)
        h = QHBoxLayout()
        h.addStretch(1)
        self.go = button("Convert", "primary", "play", tooltip="Ctrl+Enter")
        self.go.setMinimumWidth(160)
        self.go.clicked.connect(lambda: self.start())
        h.addWidget(self.go)
        opt.lay.addLayout(h)
        v.addWidget(opt)
        v.addStretch(1)
        self.views.addWidget(w)

    # -------------------------------------------------------------- running
    def _build_running(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        self.panel = ProgressPanel()
        v.addWidget(self.panel)
        row = QHBoxLayout()
        sc = Card()
        sc.lay.addWidget(label("Pipeline", "sectionTitle"))
        self.stages = StageList()
        sc.lay.addWidget(self.stages)
        row.addWidget(sc, 1)
        live = Card()
        live.lay.addWidget(label("Detected", "sectionTitle"))
        lg = QGridLayout()
        self.live = {}
        for i, k in enumerate(("Pages", "Figures", "Tables", "Citations", "OCR pages")):
            lg.addWidget(label(k, "muted"), i, 0)
            val = label("—")
            lg.addWidget(val, i, 1)
            self.live[k] = val
        lg.setColumnStretch(2, 1)
        live.lay.addLayout(lg)
        live.lay.addStretch(1)
        row.addWidget(live, 1)
        v.addLayout(row)
        h = QHBoxLayout()
        self.cancel_btn = button("Cancel", icon_name="stop", tooltip="Esc — stops safely and keeps completed work")
        self.cancel_btn.clicked.connect(self.cancel)
        det = button("View Details", icon_name="logs")
        det.clicked.connect(lambda: self.ctx.navigate("logs"))
        h.addWidget(self.cancel_btn)
        h.addWidget(det)
        h.addStretch(1)
        v.addLayout(h)
        v.addStretch(1)
        self.views.addWidget(w)

    # --------------------------------------------------------------- result
    def _build_result(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        self.res_card = Card()
        head = QHBoxLayout()
        self.res_icon = label("")
        head.addWidget(self.res_icon)
        tv = QVBoxLayout()
        self.res_title = label("Conversion Complete", "hero")
        self.res_sub = label("", "muted")
        tv.addWidget(self.res_title)
        tv.addWidget(self.res_sub)
        head.addLayout(tv, 1)
        self.res_card.lay.addLayout(head)
        self.res_msgs = label("", wrap=True)
        self.res_card.lay.addWidget(self.res_msgs)
        self.summary = StatGrid(["Pages processed", "OCR pages", "Figures", "Tables", "References", "Citations",
                                 "Internal links", "Broken links"], cols=4)
        self.res_card.lay.addWidget(self.summary)
        bh = QHBoxLayout()
        self.b_zone = button("Review Zones", "primary", "zoning", tooltip="Open this book in Zoning Review to correct flagged zones")
        self.b_out = button("Open Output", icon_name="code")
        self.b_rep = button("View Report", icon_name="reports")
        self.b_val = button("Validate XML", icon_name="validate")
        self.b_fold = button("Open Folder", icon_name="folder")
        self.b_new = button("New Conversion", "ghost", "refresh")
        for b in (self.b_zone, self.b_out, self.b_rep, self.b_val, self.b_fold):
            bh.addWidget(b)
        bh.addStretch(1)
        bh.addWidget(self.b_new)
        self.res_card.lay.addLayout(bh)
        self.b_zone.clicked.connect(lambda: self.ctx.navigate("zoning", book_dir=self._book().get("out_dir")))
        self.b_out.clicked.connect(lambda: self.ctx.open_path(self._book().get("xml")))
        self.b_rep.clicked.connect(lambda: self.ctx.open_path(self._book().get("report")))
        self.b_val.clicked.connect(lambda: self.ctx.navigate("validate", paths=[Path(self._book().get("xml"))] if self._book().get("xml") else None))
        self.b_fold.clicked.connect(lambda: self.ctx.open_path(self._book().get("out_dir")))
        self.b_new.clicked.connect(lambda: self.views.setCurrentIndex(0))
        v.addWidget(self.res_card)
        sel = QHBoxLayout()
        sel.addWidget(label("Book", "fieldLabel"))
        self.book_sel = QComboBox()
        self.book_sel.currentIndexChanged.connect(self._show_book)
        sel.addWidget(self.book_sel, 1)
        v.addLayout(sel)
        self.tabs = QTabWidget()
        self.issues = IssueList()
        self.placement = FigureTableView()
        self.placement.openXml.connect(lambda ident: self.ctx.navigate("validate", paths=[Path(self._book().get("xml"))], goto=ident))
        self.placement.openPdfPage.connect(lambda pg: self.ctx.navigate("compare", book_dir=self._book().get("out_dir"), page=pg))
        self.qa = QTreeWidget()
        self.qa.setHeaderLabels(["Check", "Result"])
        self.qa.setColumnWidth(0, 300)
        self.explorer = QTreeWidget()
        self.explorer.setHeaderLabels(["Output", "Size"])
        self.explorer.setColumnWidth(0, 420)
        self.explorer.itemDoubleClicked.connect(lambda it, _c: self.ctx.open_path(it.data(0, Qt.UserRole)))
        self.tabs.addTab(self.qa, "QA Summary")
        self.tabs.addTab(self.placement, "Figure && Table Placement")
        self.tabs.addTab(self.issues, "Review Issues")
        self.tabs.addTab(self.explorer, "Output")
        self.tabs.setMinimumHeight(360)
        v.addWidget(self.tabs, 1)
        self.views.addWidget(w)

    # ----------------------------------------------------------------- logic
    def on_show(self, paths=None, **kw):
        if not self.out.text():
            self.out.setText(str(self.ctx.cfg.path("output_dir")))
        if paths:
            self.views.setCurrentIndex(0)
            self.add_paths(paths)
        self._check_resume()

    def on_status(self, m, d):
        msgs = []
        if m["state"] == "missing":
            msgs.append("✕  No mapping profile. Run Sample Analysis before converting — the tool will not silently use a generic mapping.")
        elif m["state"] == "stale":
            msgs.append("⚠  The Samples folder changed since the mapping was built. Re-run Sample Analysis to rebuild the mapping.")
        if d["state"] != "ok":
            msgs.append(f"✕  DTD {d['label'].lower()}: {d.get('detail', '')}")
        self.banner_lbl.setText("<br>".join(msgs))
        self.banner.setVisible(bool(msgs))

    def _check_resume(self):
        if self.running:
            return
        st = services.interrupted_batch(Path(self.out.text() or self.ctx.cfg.path("output_dir")))
        if st:
            self.resume_lbl.setText(f"{Path(st['book']).name} was interrupted. Finished pages are cached and will not be processed again.")
            self.resume_card.show()
            if not self.files.paths:
                self.add_paths([Path(st["book"])])
        else:
            self.resume_card.hide()

    def browse_files(self):
        self.drop._files()

    def add_paths(self, paths):
        paths = [Path(p) for p in paths]
        self.files.add(paths)
        for p in paths:
            app_state.add_recent("pdf", str(p))

    def _files_changed(self):
        has = bool(self.files.paths)
        self.files.setVisible(has)

    def _overrides(self) -> dict:
        o = {}
        if self.prefix.text().strip():
            o["id_prefix"] = self.prefix.text().strip().lower()
        if self.short.text().strip():
            o["shortcode"] = self.short.text().strip()
        if self.serial.text().strip():
            o["serial_code"] = self.serial.text().strip()
        return o

    def start(self, resume=True):
        if self.running:
            return
        if not self.files.paths:
            QMessageBox.information(self, "Convert", "Select a PDF or a folder first (drop it on the drop zone or click Browse).")
            return
        m = getattr(self.ctx, "mapping_state", {"state": "ok"})
        d = getattr(self.ctx, "dtd_state", {"state": "ok"})
        if d.get("state") != "ok":
            QMessageBox.warning(self, "DTD missing", "The BITS DTD is not configured. Set the DTD directory in Configuration.")
            return
        if m.get("state") != "ok":
            r = QMessageBox.question(self, "Mapping", ("No mapping profile exists." if m.get("state") == "missing" else
                                     "The mapping profile is out of date.") + "\n\nAnalyze the samples first (recommended)?",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Yes:
                self.ctx.navigate("samples")
                return
            if r == QMessageBox.Cancel:
                return
        cfg = self.ctx.cfg
        cfg.set("ocr.engine", OCR[self.ocr.currentIndex()][1])
        langs = LANGS[self.lang.currentIndex()][1]
        if langs:
            cfg.set("ocr.languages", langs)
        out = Path(self.out.text() or cfg.path("output_dir"))
        self.running = True
        self.resume_card.hide()
        self.views.setCurrentIndex(1)
        self.panel.reset()
        self.stages.reset()
        for v in self.live.values():
            v.setText("—")
        self.ctx.status("Converting…")
        self.worker = Worker(services.convert, cfg, list(self.files.paths), out, resume=resume,
                             book_overrides={"*": self._overrides()} if self._overrides() else None)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._done)
        self.worker.failed.connect(self._failed)
        start(self, self.worker)

    def cancel(self):
        if self.running and getattr(self, "worker", None):
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("Cancelling…")

    def _progress(self, info):
        self.panel.update(info)
        st = info.get("stage", "")
        if st and st != "Batch":
            self.stages.update_stage(st, float(info.get("frac") or 0), str(info.get("detail") or ""))
        if info.get("total_pages"):
            self.live["Pages"].setText(f"{info['total_pages']:,}")
        for k, key in (("Figures", "figures"), ("Tables", "tables"), ("Citations", "citations"), ("OCR pages", "ocr_pages")):
            if info.get(key) is not None:
                self.live[k].setText(f"{info[key]:,}")
        if info.get("page") and info.get("total_pages"):
            self.ctx.status(f"Processing {info['page']:,} / {info['total_pages']:,}")

    def _finish(self):
        self.running = False
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Cancel")

    def _done(self, results):
        self._finish()
        self.results = results or []
        self.ctx.last_results = self.results
        self._show_result()
        self.ctx.status("Ready")
        self.ctx.refresh_status()

    def _failed(self, msg, detail):
        self._finish()
        self.views.setCurrentIndex(2)
        t = theme_tokens()
        self.res_icon.setPixmap(icons.pixmap("error", t["error"], 40))
        self.res_title.setText("Conversion cancelled" if msg == "Cancelled" else "Conversion failed")
        self.res_sub.setText(detail if msg == "Cancelled" else msg)
        self.res_msgs.setText("" if msg == "Cancelled" else "Technical details are in the logs (View Details).")
        self.ctx.status("Stopped")
        self._check_resume()

    def _book(self) -> dict:
        i = self.book_sel.currentIndex()
        return self.results[i] if 0 <= i < len(self.results) else {}

    def _show_result(self):
        t = theme_tokens()
        self.views.setCurrentIndex(2)
        res = self.results
        n_pass = sum(1 for r in res if r.get("status") == "PASS")
        n_warn = sum(1 for r in res if r.get("status") == "PASS WITH WARNINGS")
        n_fail = sum(1 for r in res if r.get("status") in ("FAILED", "CRASHED"))
        n_cancel = sum(1 for r in res if r.get("status") == "CANCELLED")
        if n_fail:
            icon, col, title = "error", t["error"], "Conversion failed" if n_fail == len(res) else "Conversion completed with failures"
        elif n_warn:
            icon, col, title = "warning", t["warning"], "Conversion completed with warnings"
        else:
            icon, col, title = "check-circle", t["success"], "Conversion Complete"
        self.res_icon.setPixmap(icons.pixmap(icon, col, 40))
        self.res_title.setText(title)
        parts = [f"{len(res)} PDF{'s' if len(res) != 1 else ''} processed"]
        if n_pass:
            parts.append(f"✓ {n_pass} passed")
        if n_warn:
            parts.append(f"⚠ {n_warn} passed with warnings")
        if n_fail:
            parts.append(f"✕ {n_fail} failed")
        if n_cancel:
            parts.append(f"{n_cancel} cancelled")
        self.res_sub.setText("   ·   ".join(parts))
        msgs = []
        for r in res:
            v = r.get("validation") or {}
            if r.get("status") == "FAILED" and v.get("critical"):
                msgs.append(f"<b>{Path(r['book']).name}</b>: " + "; ".join(v["critical"]))
            elif r.get("error"):
                msgs.append(f"<b>{Path(r['book']).name}</b>: {r['error']}")
        self.res_msgs.setText("<br>".join(msgs))
        tot = lambda k: sum((r.get("stats") or {}).get(k, 0) or 0 for r in res)
        self.summary.set("Pages processed", tot("pages"))
        self.summary.set("OCR pages", tot("ocr_pages"))
        self.summary.set("Figures", tot("fig"))
        self.summary.set("Tables", tot("table-wrap"))
        self.summary.set("References", tot("ref"))
        self.summary.set("Citations", tot("citations"))
        self.summary.set("Internal links", sum((r.get("validation") or {}).get("links", 0) for r in res))
        self.summary.set("Broken links", sum((r.get("validation") or {}).get("broken_links", 0) for r in res))
        self.book_sel.blockSignals(True)
        self.book_sel.clear()
        for r in res:
            self.book_sel.addItem(f"{Path(r['book']).name}   —   {r.get('status')}")
        self.book_sel.blockSignals(False)
        self._show_book(0)

    def _show_book(self, i):
        b = self._book()
        if not b.get("out_dir"):
            return
        self.ctx.last_book_dir = Path(b["out_dir"])
        data = services.load_book_result(Path(b["out_dir"]))
        xml_text = ""
        if data.get("xml"):
            try:
                xml_text = Path(data["xml"]).read_text(encoding="utf-8")
            except Exception:
                xml_text = ""
        self.placement.load(data.get("placement") or [], Path(b["out_dir"]) / "images", xml_text)
        v = data.get("validation") or {}
        issues = []
        rep = Path(b["out_dir"]) / "qa" / "issues.json"
        if rep.exists():
            issues = json.loads(rep.read_text(encoding="utf-8"))
        self.issues.load(issues)
        self._fill_qa(v, data)
        self._fill_explorer(Path(b["out_dir"]))
        if b.get("report"):
            self.b_rep.setEnabled(True)
        self.b_zone.setEnabled((Path(b["out_dir"]) / "zoning" / "zones.json").exists())

    def _fill_qa(self, v, data):
        t = theme_tokens()
        self.qa.clear()
        if not v:
            return
        lk = v.get("links", {})
        ids = v.get("ids", {})
        cov = (v.get("content") or {}).get("coverage")

        def row(name, ok, text, warn=False):
            it = QTreeWidgetItem([name, text])
            ic = ("check-circle", t["success"]) if ok else (("warning", t["warning"]) if warn else ("error", t["error"]))
            it.setIcon(0, icons.icon(ic[0], ic[1], 14))
            self.qa.addTopLevelItem(it)
            return it
        row("CONTENT", (cov or 0) >= self.ctx.cfg.get("qa.content_coverage_min", 0.97), f"{(cov or 0):.1%} of PDF words present")
        row("DTD", v.get("dtd", {}).get("valid"), "PASS" if v.get("dtd", {}).get("valid") else f"{v.get('dtd', {}).get('error_count')} errors")
        row("LINKS", not lk.get("broken") and not lk.get("wrong_type"), f"{lk.get('valid', 0):,} / {lk.get('total', 0):,} valid")
        fg, tb = lk.get("figures", {}), lk.get("tables", {})
        row("FIGURES", fg.get("linked", 0) == fg.get("total", 0), f"{fg.get('linked', 0)} / {fg.get('total', 0)} cited in the text", warn=True)
        row("TABLES", tb.get("linked", 0) == tb.get("total", 0), f"{tb.get('linked', 0)} / {tb.get('total', 0)} cited in the text", warn=True)
        row("IDS", not ids.get("duplicates") and not ids.get("invalid"), f"{ids.get('total', 0):,} IDs · {'unique' if not ids.get('duplicates') else str(len(ids['duplicates'])) + ' duplicates'}")
        im = v.get("images", {})
        row("IMAGES", not im.get("missing") and not im.get("unreadable"), f"{im.get('ok', 0)} readable · {len(im.get('missing', []))} missing")
        pages = data.get("pages") or []
        bad = [p for p in pages if p.get("status") != "PASS"]
        row("PAGES", not bad, f"{len(pages) - len(bad)} pass · {len(bad)} need review",
            warn=not any(p.get("status") == "ERROR" for p in pages))

    def _fill_explorer(self, root: Path):
        self.explorer.clear()
        top = QTreeWidgetItem([root.name + "/", ""])
        top.setData(0, Qt.UserRole, str(root))
        self.explorer.addTopLevelItem(top)
        for p in sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if p.is_dir():
                n = sum(1 for _ in p.iterdir())
                it = QTreeWidgetItem([p.name + "/", f"{n} files"])
            else:
                it = QTreeWidgetItem([p.name, f"{p.stat().st_size / 1e6:.1f} MB"])
            it.setData(0, Qt.UserRole, str(p))
            top.addChild(it)
        top.setExpanded(True)
