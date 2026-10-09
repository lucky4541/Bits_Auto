"""Pipeline stage list (✓ done · ● current · ○ waiting) + overall / current progress bars."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from .. import icons
from ..app_state import theme_tokens
from .common import Card, label

STAGES = ["PDF Loaded", "Text Extraction", "OCR", "Layout Analysis", "Reading Order", "Structure Detection",
          "Figure/Table Detection", "Placement", "ID Generation", "Link Resolution", "XML Generation",
          "DTD Validation", "QA"]
ALIASES = {"Citation Detection": "Placement", "Images": "XML Generation"}


class StageList(QWidget):
    def __init__(self, stages=STAGES):
        super().__init__()
        self.stages = stages
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.rows = {}
        for s in stages:
            h = QHBoxLayout()
            h.setSpacing(8)
            ic = QLabel()
            tx = label(s, "muted")
            det = label("", "faint")
            h.addWidget(ic)
            h.addWidget(tx)
            h.addStretch(1)
            h.addWidget(det)
            v.addLayout(h)
            self.rows[s] = (ic, tx, det)
        self.reset()

    def reset(self):
        for s in self.stages:
            self._set(s, "wait")

    def _set(self, s, state, detail=""):
        t = theme_tokens()
        ic, tx, det = self.rows[s]
        name, col, obj = {"done": ("check", t["success"], None), "run": ("dot", t["accent"], "sectionTitle"),
                          "wait": ("circle", t["faint"], "muted"), "fail": ("error", t["error"], "err")}[state]
        ic.setPixmap(icons.pixmap(name, col, 14))
        tx.setObjectName(obj or "")
        tx.style().unpolish(tx)
        tx.style().polish(tx)
        det.setText(detail)

    def update_stage(self, stage: str, frac: float, detail: str = ""):
        stage = ALIASES.get(stage, stage)
        if stage not in self.rows:
            return
        idx = self.stages.index(stage)
        for i, s in enumerate(self.stages):
            if i < idx:
                self._set(s, "done", self.rows[s][2].text())
            elif i == idx:
                self._set(s, "done" if frac >= 1.0 else "run", detail[:40])

    def fail(self, stage: str):
        stage = ALIASES.get(stage, stage)
        if stage in self.rows:
            self._set(stage, "fail")


class ProgressPanel(Card):
    """Overall + current-file progress, current file / page / stage, live counts."""

    def __init__(self):
        super().__init__()
        top = QGridLayout()
        top.setHorizontalSpacing(14)
        top.setVerticalSpacing(6)
        self.overall_lbl = label("Overall", "fieldLabel")
        self.overall = QProgressBar()
        self.overall.setRange(0, 1000)
        self.overall_pct = label("0%", "muted")
        self.current_lbl = label("Current PDF", "fieldLabel")
        self.current = QProgressBar()
        self.current.setRange(0, 1000)
        self.current_pct = label("0%", "muted")
        top.addWidget(self.overall_lbl, 0, 0)
        top.addWidget(self.overall, 0, 1)
        top.addWidget(self.overall_pct, 0, 2)
        top.addWidget(self.current_lbl, 1, 0)
        top.addWidget(self.current, 1, 1)
        top.addWidget(self.current_pct, 1, 2)
        top.setColumnStretch(1, 1)
        self.lay.addLayout(top)
        info = QGridLayout()
        info.setHorizontalSpacing(18)
        self.file_v = label("—", selectable=True)
        self.page_v = label("—")
        self.stage_v = label("—")
        self.files_v = label("—")
        for i, (k, w) in enumerate((("Current PDF", self.file_v), ("Page", self.page_v), ("Stage", self.stage_v), ("Files", self.files_v))):
            info.addWidget(label(k, "faint"), 0, i)
            info.addWidget(w, 1, i)
        self.lay.addLayout(info)

    def reset(self):
        self.overall.setValue(0)
        self.current.setValue(0)
        self.overall_pct.setText("0%")
        self.current_pct.setText("0%")
        for w in (self.file_v, self.page_v, self.stage_v, self.files_v):
            w.setText("—")

    def update(self, info: dict):
        stage = info.get("stage", "")
        frac = float(info.get("frac") or 0)
        bi, bt = info.get("book_index"), info.get("book_total")
        if info.get("current_file"):
            self.file_v.setText(info["current_file"])
        if bt:
            self.files_v.setText(f"{bi or 0} of {bt}")
        if info.get("page") and info.get("total_pages"):
            self.page_v.setText(f"{info['page']:,} / {info['total_pages']:,}")
        if stage and stage != "Batch":
            self.stage_v.setText(stage)
        order = STAGES
        st = ALIASES.get(stage, stage)
        if st in order:
            cur = (order.index(st) + min(frac, 1.0)) / len(order)
            self.current.setValue(int(cur * 1000))
            self.current_pct.setText(f"{cur:.0%}")
            if bt:
                ov = ((bi or 1) - 1 + cur) / bt
                self.overall.setValue(int(ov * 1000))
                self.overall_pct.setText(f"{ov:.0%}")
        elif stage == "Batch" and bt:
            ov = frac
            self.overall.setValue(int(ov * 1000))
            self.overall_pct.setText(f"{ov:.0%}")
