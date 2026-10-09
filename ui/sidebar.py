"""Compact left navigation: WORKFLOW / TOOLS / SETTINGS."""
from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QButtonGroup, QPushButton, QVBoxLayout, QWidget

from . import icons
from .app_state import theme_tokens
from .widgets.common import label

NAV = [
    ("WORKFLOW", [("home", "Home", "home"), ("samples", "01  Sample Analysis", "samples"), ("convert", "02  Convert", "convert"),
                  ("zoning", "03  Zoning Review", "zoning"), ("validate", "04  Validate", "validate"),
                  ("compare", "05  Compare", "compare")]),
    ("TOOLS", [("mapping", "Mapping", "mapping"), ("logs", "Logs", "logs"), ("reports", "Reports", "reports")]),
    ("SETTINGS", [("settings", "Configuration", "settings")]),
]


class Sidebar(QWidget):
    navigate = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(208)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 10)
        v.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for section, items in NAV:
            v.addWidget(label(section, "navSection"))
            for key, text, ic in items:
                b = QPushButton(text)
                b.setObjectName("navItem")
                b.setCheckable(True)
                b.setIconSize(QSize(16, 16))
                b.setProperty("iconName", ic)
                b.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
                self.group.addButton(b)
                self.buttons[key] = b
                v.addWidget(b)
        v.addStretch(1)
        self.refresh_icons()

    def refresh_icons(self):
        t = theme_tokens()
        for b in self.buttons.values():
            b.setIcon(icons.icon(b.property("iconName"), t["muted"], 16, t["accent"]))

    def select(self, key: str):
        if key in self.buttons:
            self.buttons[key].setChecked(True)
