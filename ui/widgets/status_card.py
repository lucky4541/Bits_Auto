"""Stat card and a compact grid of stat cards."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QWidget

from .common import Card, label


class StatCard(Card):
    def __init__(self, title: str, value="—"):
        super().__init__(margins=(14, 10, 14, 10), spacing=2)
        self.k = label(title, "statLabel")
        self.v = label(str(value), "statValue")
        self.lay.addWidget(self.k)
        self.lay.addWidget(self.v)

    def set(self, value):
        self.v.setText(f"{value:,}" if isinstance(value, int) else str(value))


class StatGrid(QWidget):
    def __init__(self, titles: list[str], cols: int = 4):
        super().__init__()
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(10)
        self.cards = {}
        for i, t in enumerate(titles):
            c = StatCard(t)
            g.addWidget(c, i // cols, i % cols)
            self.cards[t] = c

    def set(self, title, value):
        if title in self.cards:
            self.cards[title].set(value)
