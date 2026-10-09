"""Logs: dark monospace viewer with search, level filter, copy and clear."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPlainTextEdit

from .widgets.common import Card, Page, button, label

LOGS = [("Conversion Log", "conversion.log"), ("Validation Log", "validation.log"), ("Errors", "errors.log"),
        ("Link Log", "link_validation.log"), ("Placement Log", "placement.log")]
LEVELS = ["ALL", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogsPage(Page):
    def __init__(self, ctx):
        super().__init__("Logs", "Technical logs of the last conversion. Most users never need this screen.", scroll=False)
        self.ctx = ctx
        bar = Card(margins=(12, 10, 12, 10))
        h = QHBoxLayout()
        self.which = QComboBox()
        for t, _f in LOGS:
            self.which.addItem(t)
        self.which.currentIndexChanged.connect(lambda _: self.load())
        self.level = QComboBox()
        self.level.addItems(LEVELS)
        self.level.currentIndexChanged.connect(lambda _: self.render())
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter…")
        self.search.textChanged.connect(lambda _: self.render())
        copy = button("Copy", icon_name="copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.view.toPlainText()))
        clear = button("Clear", icon_name="trash")
        clear.clicked.connect(self.clear)
        for w in (self.which, self.level, self.search):
            h.addWidget(w)
        h.addWidget(copy)
        h.addWidget(clear)
        bar.lay.addLayout(h)
        self.src_lbl = label("", "faint")
        bar.lay.addWidget(self.src_lbl)
        self.add(bar)
        self.view = QPlainTextEdit()
        self.view.setObjectName("code")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(20000)
        self.add(self.view, 1)
        self.lines = []

    def focus_search(self):
        self.search.setFocus()

    def on_show(self, **kw):
        self.load()

    def _dir(self) -> Path | None:
        d = self.ctx.last_book_dir
        if d is None:
            out = self.ctx.cfg.path("output_dir")
            books = sorted((p for p in out.glob("*") if (p / "logs").exists()), key=lambda p: p.stat().st_mtime) if out.exists() else []
            d = books[-1] if books else None
        return (Path(d) / "logs") if d else None

    def load(self):
        d = self._dir()
        fname = LOGS[self.which.currentIndex()][1]
        if d is None or not (d / fname).exists():
            self.lines = []
            self.src_lbl.setText("No logs yet.")
        else:
            p = d / fname
            size = p.stat().st_size
            with open(p, encoding="utf-8", errors="replace") as fh:
                if size > 4_000_000:
                    fh.seek(size - 4_000_000)
                    fh.readline()
                self.lines = fh.read().splitlines()
            self.src_lbl.setText(str(p) + ("   (last 4 MB)" if size > 4_000_000 else ""))
        self.render()

    def render(self):
        lvl = LEVELS[self.level.currentIndex()]
        q = self.search.text().lower()
        rank = {"INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
        out = []
        for ln in self.lines:
            if lvl != "ALL":
                lv = next((k for k in rank if f" {k} " in ln), "INFO")
                if rank[lv] < rank[lvl]:
                    continue
            if q and q not in ln.lower():
                continue
            out.append(ln)
        self.view.setPlainText("\n".join(out[-20000:]))

    def clear(self):
        self.view.clear()
        self.lines = []
