"""Sample Analysis: scan approved PDF/XML samples and build the mapping knowledge base."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QMessageBox, QProgressBar

from bits_tool import services

from .widgets.common import Card, Page, PathField, StatusLine, button, label
from .widgets.progress_panel import StageList
from .widgets.status_card import StatGrid
from .workers import Worker, start

STAGES = ["Scanning sample corpus", "PDF/XML matching", "Analyzing DTD", "XML structure analysis",
          "PDF typography analysis", "Mapping generation", "Complete"]
ROWS = [("Elements detected", "elements"), ("Attributes", "attributes"), ("Parent/child rules", "parent_child_rules"),
        ("ID patterns", "id_patterns"), ("Link patterns", "link_patterns"), ("Figure patterns", "figure_patterns"),
        ("Table patterns", "table_patterns"), ("List patterns", "list_patterns"), ("Index patterns", "index_patterns"),
        ("Citation patterns", "citation_patterns"), ("Placement rules", "placement_rules"), ("Mapping conflicts", "conflicts")]


class SamplePage(Page):
    def __init__(self, ctx):
        super().__init__("Sample Analysis", "Analyze approved PDF/XML samples and build the conversion knowledge base.")
        self.ctx = ctx
        self.running = False
        top = Card()
        self.samples = PathField("Samples Directory")
        self.dtd = PathField("DTD Directory")
        top.lay.addWidget(self.samples)
        top.lay.addWidget(self.dtd)
        h = QHBoxLayout()
        self.go = button("Analyze All Samples", "primary", "play")
        self.go.clicked.connect(self.start)
        self.cancel_btn = button("Cancel", icon_name="stop")
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.hide()
        h.addWidget(self.go)
        h.addWidget(self.cancel_btn)
        h.addStretch(1)
        top.lay.addLayout(h)
        self.add(top)
        # progress
        self.prog = Card()
        self.prog.lay.addWidget(label("Analyzing sample corpus…", "sectionTitle"))
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.prog.lay.addWidget(self.bar)
        g = QGridLayout()
        self.cur = label("—")
        self.stage = label("—")
        g.addWidget(label("Current", "faint"), 0, 0)
        g.addWidget(self.cur, 1, 0)
        g.addWidget(label("Stage", "faint"), 0, 1)
        g.addWidget(self.stage, 1, 1)
        self.prog.lay.addLayout(g)
        self.stages = StageList(STAGES)
        self.prog.lay.addWidget(self.stages)
        self.prog.hide()
        self.add(self.prog)
        # results
        self.res = Card()
        self.res.lay.addWidget(label("Sample Analysis", "sectionTitle"))
        self.grid = StatGrid(["PDF Samples", "XML Samples", "Matched", "DTD Version"], cols=4)
        self.res.lay.addWidget(self.grid)
        rows = QGridLayout()
        rows.setHorizontalSpacing(30)
        rows.setVerticalSpacing(6)
        self.row_vals = {}
        for i, (t, k) in enumerate(ROWS):
            rows.addWidget(label(t, "muted"), i % 6, (i // 6) * 2)
            v = label("—")
            rows.addWidget(v, i % 6, (i // 6) * 2 + 1)
            self.row_vals[k] = v
        rows.setColumnStretch(4, 1)
        self.res.lay.addLayout(rows)
        self.cov_box = QGridLayout()
        self.cov_box.setHorizontalSpacing(10)
        self.res.lay.addWidget(label("Mapping coverage (sample objects covered by a learned rule)", "faint"))
        self.res.lay.addLayout(self.cov_box)
        rh = QHBoxLayout()
        self.map_status = StatusLine("idle", "No mapping profile found.")
        rh.addWidget(self.map_status, 1)
        vm = button("View Mapping", icon_name="mapping")
        vm.clicked.connect(lambda: ctx.navigate("mapping"))
        rh.addWidget(vm)
        self.res.lay.addLayout(rh)
        self.add(self.res)
        self.finish()

    def on_show(self, **kw):
        cfg = self.ctx.cfg
        if not self.samples.text():
            self.samples.setText(str(cfg.path("samples_dir")))
        if not self.dtd.text():
            self.dtd.setText(str(cfg.path("dtd_dir")))
        if not self.running:
            self.show_report(services.sample_report(cfg))

    def on_status(self, m, d):
        self.map_status.set("ok" if m["state"] == "ok" else "warn" if m["state"] == "stale" else "err",
                            {"ok": "Mapping profile ready", "stale": "Mapping needs rebuild (samples changed)",
                             "missing": "No mapping profile found — click Analyze All Samples"}[m["state"]])

    def show_report(self, r: dict):
        self.grid.set("PDF Samples", r.get("pdf_samples", 0))
        self.grid.set("XML Samples", r.get("xml_samples", 0))
        self.grid.set("Matched", r.get("matched", 0))
        self.grid.set("DTD Version", r.get("dtd_version", "—"))
        for _t, k in ROWS:
            v = r.get(k)
            self.row_vals[k].setText(f"{v:,}" if isinstance(v, int) else "—")
        while self.cov_box.count():
            it = self.cov_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i, (k, pct) in enumerate((r.get("coverage") or {}).items()):
            b = QProgressBar()
            b.setRange(0, 100)
            b.setValue(int(pct))
            b.setMaximumHeight(8)
            self.cov_box.addWidget(label(k, "muted"), i, 0)
            self.cov_box.addWidget(b, i, 1)
            self.cov_box.addWidget(label(f"{pct}%", "muted"), i, 2)
        self.cov_box.setColumnStretch(1, 1)

    def start(self):
        if self.running:
            return
        cfg = self.ctx.cfg
        if self.samples.text():
            cfg.set("paths.samples_dir", self.samples.text())
        if self.dtd.text():
            cfg.set("paths.dtd_dir", self.dtd.text())
        self.running = True
        self.go.setEnabled(False)
        self.cancel_btn.show()
        self.prog.show()
        self.stages.reset()
        self.ctx.status("Analyzing samples…")
        self.worker = Worker(services.analyze_samples, cfg)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._done)
        self.worker.failed.connect(self._failed)
        start(self, self.worker)

    def cancel(self):
        if self.running and getattr(self, "worker", None):
            self.worker.cancel()

    def _progress(self, info):
        self.bar.setValue(int(1000 * float(info.get("frac") or 0)))
        self.stage.setText(info.get("stage", ""))
        if info.get("current"):
            self.cur.setText(str(info["current"]))
        if info.get("stage") in STAGES:
            self.stages.update_stage(info["stage"], 1.0 if info["stage"] == "Complete" else 0.5)

    def _finish(self):
        self.running = False
        self.go.setEnabled(True)
        self.cancel_btn.hide()

    def _done(self, r):
        self._finish()
        self.stages.update_stage("Complete", 1.0)
        self.show_report(r)
        self.ctx.status(f"Sample analysis complete · {r.get('matched', 0)} matched pairs · {r.get('seconds', 0)} s")
        self.ctx.refresh_status()

    def _failed(self, msg, detail):
        self._finish()
        self.ctx.status("Sample analysis stopped")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Sample analysis")
        box.setText("Sample analysis did not complete." if msg != "Cancelled" else "Sample analysis was cancelled.")
        box.setInformativeText(msg if msg != "Cancelled" else detail)
        if msg != "Cancelled":
            box.setDetailedText(detail)
        box.exec()
