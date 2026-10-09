"""Validate XML: well-formedness, DTD, IDs, links, figures, tables, images, content."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QTabWidget, QTreeWidget, QTreeWidgetItem, QWidget

from bits_tool import services

from . import icons
from .app_state import theme_tokens
from .widgets.common import Card, Page, button, label
from .widgets.file_drop import DropZone, FileList
from .widgets.xml_viewer import XmlViewer
from .workers import Worker, start

CHECKS = ["XML Well-Formed", "DTD Validation", "IDs", "Internal Links", "Figures", "Tables", "Images", "Content Coverage"]


def _files(paths):
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out.extend(sorted(x for x in p.rglob("*.xml") if x.parent.name not in ("validation", "qa", "intermediate", "logs")))
        elif p.suffix.lower() == ".xml":
            out.append(p)
    return out


class ValidationPage(Page):
    def _worker_progress(self, info):
        self.ctx.status(info.get("stage", ""))

    def __init__(self, ctx):
        super().__init__("Validate XML", "Check any BITS XML against the DTD, IDs, internal links, images and structure.")
        self.ctx = ctx
        self.running = False
        top = Card()
        self.drop = DropZone("Drop XML files or a folder here", exts=(".xml",))
        self.drop.dropped.connect(self.set_paths)
        top.lay.addWidget(self.drop)
        self.list = FileList()
        self.list.title.setText("Selected XML")
        self.list.hide()
        self.list.changed.connect(lambda: self.list.setVisible(bool(self.list.paths)))
        top.lay.addWidget(self.list)
        h = QHBoxLayout()
        h.addStretch(1)
        self.go = button("Validate", "primary", "validate")
        self.go.clicked.connect(self.start)
        h.addWidget(self.go)
        top.lay.addLayout(h)
        self.add(top)
        self.empty = label("Select an XML file to begin validation.", "muted")
        self.add(self.empty)
        self.result = Card()
        rh = QHBoxLayout()
        self.final_ic = QLabel()
        self.final = label("", "hero")
        rh.addWidget(self.final_ic)
        rh.addWidget(self.final, 1)
        self.file_sel = button("", "ghost")
        rh.addWidget(self.file_sel)
        self.result.lay.addLayout(rh)
        g = QGridLayout()
        g.setHorizontalSpacing(24)
        self.rows = {}
        for i, c in enumerate(CHECKS):
            ic = QLabel()
            tx = label(c)
            val = label("", "muted")
            g.addWidget(ic, i % 4, (i // 4) * 3)
            g.addWidget(tx, i % 4, (i // 4) * 3 + 1)
            g.addWidget(val, i % 4, (i // 4) * 3 + 2)
            self.rows[c] = (ic, val)
        g.setColumnStretch(5, 1)
        self.result.lay.addLayout(g)
        self.result.hide()
        self.add(self.result)
        self.tabs = QTabWidget()
        self.details = QTreeWidget()
        self.details.setHeaderLabels(["Section", "Detail"])
        self.details.setColumnWidth(0, 260)
        self.viewer = XmlViewer()
        self.tabs.addTab(self.details, "Details")
        self.tabs.addTab(self.viewer, "XML")
        self.tabs.setMinimumHeight(380)
        self.tabs.hide()
        self.add(self.tabs, 1)
        self.results = []

    def focus_search(self):
        self.tabs.setCurrentWidget(self.viewer)
        self.viewer.search.setFocus()

    def on_show(self, paths=None, goto=None, **kw):
        if paths:
            self.list.clear()
            self.set_paths(paths)
            if goto:
                self._goto = goto
            self.start()
        elif not self.list.paths and self.ctx.last_book_dir and not self.results:
            xs = sorted(Path(self.ctx.last_book_dir).glob("*.xml"))
            if xs:
                self.set_paths(xs[:1])

    def set_paths(self, paths):
        self.list.add([Path(p) for p in paths])

    def start(self):
        files = _files(self.list.paths)
        if not files or self.running:
            return
        self.running = True
        self.go.setEnabled(False)
        self.ctx.status("Validating…")

        def job(cfg, files, progress=None, cancel=None):
            out = []
            for i, f in enumerate(files):
                if cancel():
                    break
                progress(stage=f"Validating {f.name}", frac=i / len(files))
                out.append(services.validate_xml(cfg, f))
            return out
        self.worker = Worker(job, self.ctx.cfg, files)
        self.worker.progress.connect(self._worker_progress)
        self.worker.finished.connect(self._done)
        self.worker.failed.connect(self._failed)
        start(self, self.worker)

    def cancel(self):
        if self.running:
            self.worker.cancel()

    def _failed(self, msg, detail):
        self.running = False
        self.go.setEnabled(True)
        self.ctx.status("Validation stopped")
        self.final.setText("Validation could not run")
        self.result.show()

    def _done(self, results):
        self.running = False
        self.go.setEnabled(True)
        self.results = results
        self.ctx.status("Ready")
        if results:
            self.show_result(results[0])
            if len(results) > 1:
                bad = sum(1 for r in results if r["status"] == "FAILED")
                self.file_sel.setText(f"{len(results)} files · {bad} failed")

    def show_result(self, r):
        t = theme_tokens()
        self.empty.hide()
        self.result.show()
        self.tabs.show()
        st = r.get("status")
        ic, col, txt = {"VALID": ("check-circle", t["success"], "✓ VALID"), "VALID WITH WARNINGS": ("warning", t["warning"], "⚠ VALID WITH WARNINGS")}.get(
            st, ("error", t["error"], "✕ FAILED"))
        self.final_ic.setPixmap(icons.pixmap(ic, col, 30))
        self.final.setText(f"{txt}  —  {Path(r['file']).name}")

        def set_row(name, state, text):
            name_ic = {"ok": ("check", t["success"]), "warn": ("warning", t["warning"]), "err": ("error", t["error"]),
                       "na": ("circle", t["faint"])}[state]
            self.rows[name][0].setPixmap(icons.pixmap(name_ic[0], name_ic[1], 16))
            self.rows[name][1].setText(text)
        wf = r.get("well_formed", {})
        set_row("XML Well-Formed", "ok" if wf.get("ok") else "err", "" if wf.get("ok") else wf.get("error", ""))
        if not wf.get("ok"):
            return
        d = r["dtd"]
        set_row("DTD Validation", "ok" if d["valid"] else "err", "valid" if d["valid"] else f"{d['error_count']} errors")
        ids = r["ids"]
        set_row("IDs", "err" if ids["duplicates"] or ids["invalid"] else ("warn" if ids["pattern_violation_count"] else "ok"),
                f"{ids['total']:,} IDs · {len(ids['duplicates'])} duplicates")
        lk = r["links"]
        set_row("Internal Links", "err" if lk["broken"] or lk["wrong_type"] else "ok", f"{lk['valid']:,} / {lk['total']:,} resolved")
        set_row("Figures", "ok" if not lk["figures"]["unlinked"] else "warn", f"{lk['figures']['total']} · {lk['figures']['linked']} linked")
        set_row("Tables", "ok" if not lk["tables"]["unlinked"] else "warn", f"{lk['tables']['total']} · {lk['tables']['linked']} linked")
        im = r["images"]
        set_row("Images", "err" if im["missing"] or im["unreadable"] else "ok", f"{im['ok']} ok · {len(im['missing'])} missing")
        c = r.get("content")
        if c:
            set_row("Content Coverage", "ok" if c["coverage"] >= self.ctx.cfg.get("qa.content_coverage_min", 0.97) else "warn", f"{c['coverage']:.1%}")
        else:
            set_row("Content Coverage", "na", "needs the conversion output (PDF comparison)")
        self._details(r)
        try:
            self.viewer.load_file(r["file"])
            if getattr(self, "_goto", None):
                self.tabs.setCurrentWidget(self.viewer)
                self.viewer.goto_id(self._goto)
                self._goto = None
        except Exception:
            pass

    def _details(self, r):
        self.details.clear()

        def sec(name, items):
            top = QTreeWidgetItem([name, str(len(items)) if isinstance(items, list) else ""])
            for a, b in (items if isinstance(items, list) else []):
                QTreeWidgetItem(top, [str(a), str(b)])
            self.details.addTopLevelItem(top)
            return top
        d = r["dtd"]
        sec("DTD", [(f"line {e['line']}", e["message"]) for e in d["errors"][:300]] or [("✓", "valid against the BITS 1.0 DTD")])
        ids = r["ids"]
        sec("IDs", [("total", f"{ids['total']:,}"), ("unique", f"{ids['unique']:,}")] + [("duplicate", x) for x in ids["duplicates"][:100]]
            + [("pattern", x) for x in ids["pattern_violations"][:50]])
        lk = r["links"]
        sec("References / links", [("links", f"{lk['total']:,}"), ("resolved", f"{lk['valid']:,}"), ("broken", len(lk['broken']))]
            + [(f"broken {b.get('element')}", f"{b.get('rid')}  “{b.get('text')}”") for b in lk["broken"][:200]]
            + [(f"wrong target {w.get('ref-type')}", f"{w.get('rid')} -> {w.get('target')}") for w in lk["wrong_type"][:100]])
        sec("Figures", [("figures", lk["figures"]["total"]), ("linked", lk["figures"]["linked"])] + [("not cited", x) for x in lk["figures"]["unlinked"][:100]])
        sec("Tables", [("tables", lk["tables"]["total"]), ("linked", lk["tables"]["linked"])] + [("not cited", x) for x in lk["tables"]["unlinked"][:100]])
        im = r["images"]
        sec("Images", [("readable", im["ok"])] + [("missing", x) for x in im["missing"][:100]] + [("unreadable", x) for x in im["unreadable"][:50]])
        s = r["structure"]
        sec("Structure", [(k, f"{v:,}") for k, v in s["counts"].items()] + [("issue", x) for x in s["issues"][:100]]
            + [("empty paragraph", x) for x in s["empty_paragraphs"][:50]])
