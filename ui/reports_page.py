"""Reports: conversion / validation / placement / content / link reports per book."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem

from bits_tool import services

from . import icons
from .app_state import theme_tokens
from .widgets.common import Card, Page, PathField, button, label


class ReportsPage(Page):
    def __init__(self, ctx):
        super().__init__("Reports", "Conversion, validation, placement, content and link reports for converted books.", scroll=False)
        self.ctx = ctx
        top = Card()
        self.out = PathField("Output folder")
        self.out.changed.connect(lambda _: self.refresh())
        top.lay.addWidget(self.out)
        self.add(top)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Book", "Report", "Date", "Status", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(lambda r, c: self._open(r))
        self.add(self.table, 1)
        self.empty = label("No reports yet. Convert a PDF to create them.", "muted")
        self.add(self.empty)
        self.rows = []

    def on_show(self, **kw):
        if not self.out.text():
            self.out.setText(str(self.ctx.cfg.path("output_dir")))
        self.refresh()

    def refresh(self):
        t = theme_tokens()
        self.rows = services.list_reports(Path(self.out.text()))
        self.empty.setVisible(not self.rows)
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            st = r.get("status") or "—"
            ic = {"PASS": ("check", t["success"]), "PASS WITH WARNINGS": ("warning", t["warning"]), "FAILED": ("error", t["error"])}.get(st, ("circle", t["faint"]))
            for c, v in enumerate((r["book"], f"{r['kind']} report", r["date"], st)):
                it = QTableWidgetItem(v)
                if c == 3:
                    it.setIcon(icons.icon(ic[0], ic[1], 14))
                self.table.setItem(i, c, it)
            b = button("Open", "ghost", "external")
            b.clicked.connect(lambda _=False, row=i: self._open(row))
            self.table.setCellWidget(i, 4, b)

    def _open(self, row):
        if 0 <= row < len(self.rows):
            self.ctx.open_path(self.rows[row]["path"])
