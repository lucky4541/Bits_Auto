"""Figure & table placement QA table with a detail panel."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import icons
from ..app_state import theme_tokens
from .common import Card, button, label

COLS = ["Type", "Label", "First Citation", "XML Position", "Link", "Status"]


class FigureTableView(QWidget):
    openXml = Signal(str)
    openPdfPage = Signal(int)

    def __init__(self):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        sp = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._sel)
        sp.addWidget(self.table)
        self.detail = Card()
        self.d_title = label("Select a row", "sectionTitle")
        self.d_img = QLabel()
        self.d_img.setAlignment(Qt.AlignCenter)
        self.d_img.setMinimumHeight(140)
        self.d_body = label("", wrap=True, selectable=True)
        self.detail.lay.addWidget(self.d_title)
        self.detail.lay.addWidget(self.d_img)
        self.detail.lay.addWidget(self.d_body, 1)
        bh = QHBoxLayout()
        self.b_pdf = button("Open PDF Page", icon_name="file")
        self.b_xml = button("Open XML Location", icon_name="code")
        bh.addWidget(self.b_pdf)
        bh.addWidget(self.b_xml)
        self.detail.lay.addLayout(bh)
        self.b_xml.clicked.connect(lambda: self._cur and self.openXml.emit(self._cur.get("target_id") or ""))
        self.b_pdf.clicked.connect(lambda: self._cur and self.openPdfPage.emit(int(self._cur.get("physical_page") or 0)))
        sp.addWidget(self.detail)
        sp.setSizes([640, 340])
        h.addWidget(sp)
        self.rows = []
        self._cur = None
        self.img_dir = None
        self.xml_text = ""

    def load(self, rows: list[dict], img_dir: Path | None = None, xml_text: str = ""):
        self.rows = rows or []
        self.img_dir = img_dir
        self.xml_text = xml_text
        t = theme_tokens()
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            status = r.get("status") or ""
            ok = status == "PLACED"
            link_ok = (r.get("incoming_links") or 0) > 0
            vals = [r.get("type", "").capitalize(), r.get("label") or "(unlabelled)",
                    f"p.{r.get('first_citation_folio') or r.get('first_citation_page')}" if r.get("first_citation_page") is not None else "—",
                    "after citation" if ok else "physical position", "", "PASS" if ok and link_ok else "REVIEW"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 4:
                    it.setIcon(icons.icon("check" if link_ok else "warning", t["success"] if link_ok else t["warning"], 14))
                    it.setText("linked" if link_ok else "no links")
                if c == 5:
                    it.setForeground(Qt.darkGreen if vals[5] == "PASS" else Qt.darkYellow)
                self.table.setItem(i, c, it)

    def _sel(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        r = self.rows[rows[0].row()]
        self._cur = r
        self.d_title.setText(f"{(r.get('label') or 'Unlabelled').rstrip('.')}")
        img = None
        if self.img_dir and r.get("target_id") and self.xml_text:
            import re
            m = re.search(rf'id="{re.escape(r["target_id"])}".*?xlink:href="([^"]+)"', self.xml_text, re.S)
            if m and len(m.group(0)) < 3000:
                p = Path(self.img_dir) / m.group(1)
                if p.exists():
                    img = QPixmap(str(p))
        if img is not None and not img.isNull():
            self.d_img.setPixmap(img.scaled(320, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.d_img.setText("(table)" if r.get("type") == "table" else "")
        lines = [
            f"<b>Physical location:</b> page {r.get('physical_folio') or r.get('physical_page')}",
            f"<b>First citation:</b> {('page ' + str(r.get('first_citation_folio') or r.get('first_citation_page'))) if r.get('first_citation_page') is not None else 'none'}",
            f"<b>Citation:</b> “{r.get('first_citation_text') or '—'}”",
            f"<b>Target ID:</b> <code>{r.get('target_id')}</code>",
            f"<b>XML position:</b> {r.get('xml_insertion_point')}",
            f"<b>Rule:</b> {r.get('placement_rule')}",
            f"<b>Links:</b> {r.get('citations')} citation(s) · {r.get('incoming_links')} resolved",
            f"<b>Status:</b> {r.get('status')}" + (f"<br><span>{r.get('note')}</span>" if r.get("note") else ""),
        ]
        self.d_body.setText("<br>".join(lines))
