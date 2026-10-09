"""Home: welcome, primary actions, readiness, recent activity (only real state)."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout

from . import app_state
from .widgets.common import Card, Page, StatusLine, button, label


class HomePage(Page):
    def __init__(self, ctx):
        super().__init__("BITS Conversion Tool", "Convert complex PDF documents into validated BITS XML.")
        self.ctx = ctx
        # first run
        self.first = Card("banner_info")
        self.first.lay.addWidget(label("Welcome to BITS Conversion Tool", "sectionTitle"))
        self.first.lay.addWidget(label("Before converting a PDF, analyze your approved Samples folder so the tool can learn "
                                       "the production conventions (IDs, links, figure placement, structures).", wrap=True))
        fh = QHBoxLayout()
        b1 = button("Analyze Samples", "primary", "samples")
        b1.clicked.connect(lambda: (app_state.mark_first_run_done(), self.first.hide(), ctx.navigate("samples")))
        b2 = button("Configure First", icon_name="settings")
        b2.clicked.connect(lambda: (app_state.mark_first_run_done(), self.first.hide(), ctx.navigate("settings")))
        fh.addWidget(b1)
        fh.addWidget(b2)
        fh.addStretch(1)
        self.first.lay.addLayout(fh)
        self.add(self.first)
        self.first.setVisible(app_state.first_run())
        # actions
        acts = Card()
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        pa = button("Analyze Samples", "primary", "samples")
        pa.clicked.connect(lambda: ctx.navigate("samples"))
        pc = button("Convert PDF", "primary", "convert")
        pc.clicked.connect(lambda: ctx.navigate("convert"))
        sv = button("Validate XML", icon_name="validate")
        sv.clicked.connect(lambda: ctx.navigate("validate"))
        sc = button("Compare PDF && XML", icon_name="compare")
        sc.clicked.connect(lambda: ctx.navigate("compare"))
        for i, b in enumerate((pa, pc, sv, sc)):
            g.addWidget(b, 0, i)
        g.setColumnStretch(4, 1)
        acts.lay.addWidget(label("Workflow:  Analyze samples → Convert → Review zones → Validate → Compare", "muted"))
        acts.lay.addLayout(g)
        self.add(acts)
        # readiness
        ready = Card()
        ready.lay.addWidget(label("Status", "sectionTitle"))
        self.st_samples = StatusLine("idle", "Samples not analyzed")
        self.st_dtd = StatusLine("idle", "DTD not checked")
        self.st_out = StatusLine("idle", "No conversions yet")
        self.st_review = StatusLine("idle", "")
        for w in (self.st_samples, self.st_dtd, self.st_out, self.st_review):
            ready.lay.addWidget(w)
        self.add(ready)
        # recent
        rec = Card()
        rec.lay.addWidget(label("Recent", "sectionTitle"))
        self.recent_box = QVBoxLayout()
        rec.lay.addLayout(self.recent_box)
        self.add(rec)
        self.finish()

    def on_status(self, m, d):
        self.st_samples.set("ok" if m["state"] == "ok" else "warn" if m["state"] == "stale" else "err",
                            {"ok": f"Samples analyzed · mapping profile ready ({m.get('detail', '')})",
                             "stale": "Samples changed — mapping needs rebuild", "missing": "No mapping profile — run Sample Analysis"}[m["state"]])
        self.st_dtd.set("ok" if d["state"] == "ok" else "err", f"DTD {d['label'].lower()}")
        self.on_show()

    def on_show(self, **kw):
        out = self.ctx.cfg.path("output_dir")
        state = out / "batch_state.json"
        books = {}
        if state.exists():
            try:
                books = json.loads(state.read_text(encoding="utf-8")).get("books", {})
            except Exception:
                books = {}
        done = [b for b in books.values() if b.get("status") in ("PASS", "PASS WITH WARNINGS", "FAILED")]
        review = [b for b in books.values() if b.get("status") in ("PASS WITH WARNINGS", "FAILED")]
        if done:
            self.st_out.set("ok", f"{len(done)} PDF{'s' if len(done) != 1 else ''} converted")
            self.st_review.setVisible(bool(review))
            self.st_review.set("warn", f"{len(review)} file{'s' if len(review) != 1 else ''} require review")
        else:
            self.st_out.set("idle", "No conversions yet")
            self.st_review.setVisible(False)
        while self.recent_box.count():
            it = self.recent_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        items = app_state.recent("pdf")[:5]
        if not items:
            self.recent_box.addWidget(label("No recent files.", "faint"))
        for p in items:
            b = button(Path(p).name, "ghost", "file", tooltip=p)
            b.clicked.connect(lambda _=False, path=p: self.ctx.navigate("convert", paths=[Path(path)]))
            row = QHBoxLayout()
            row.addWidget(b)
            row.addStretch(1)
            self.recent_box.addLayout(row)
