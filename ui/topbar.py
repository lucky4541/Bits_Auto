"""Top bar: title, subtitle, theme toggle, settings, help."""
from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QVBoxLayout, QWidget

from . import icons
from .app_state import theme_name, theme_tokens
from .widgets.common import label


class TopBar(QWidget):
    settingsClicked = Signal()
    helpClicked = Signal()
    themeClicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("topbar")
        self.setFixedHeight(54)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 6, 12, 6)
        box = QVBoxLayout()
        box.setSpacing(0)
        box.addWidget(label("BITS Conversion Tool", "appTitle"))
        box.addWidget(label("PDF → BITS XML", "appSubtitle"))
        h.addLayout(box)
        h.addStretch(1)
        self.theme_btn = QToolButton()
        self.theme_btn.setToolTip("Switch light / dark theme")
        self.theme_btn.clicked.connect(self.themeClicked.emit)
        self.settings_btn = QToolButton()
        self.settings_btn.setToolTip("Settings  (Ctrl+,)")
        self.settings_btn.clicked.connect(self.settingsClicked.emit)
        self.help_btn = QToolButton()
        self.help_btn.setToolTip("Help")
        self.help_btn.clicked.connect(self.helpClicked.emit)
        for b in (self.theme_btn, self.settings_btn, self.help_btn):
            b.setIconSize(QSize(18, 18))
            h.addWidget(b)
        self.refresh_icons()

    def refresh_icons(self):
        t = theme_tokens()
        self.theme_btn.setIcon(icons.icon("moon" if theme_name() == "light" else "sun", t["muted"], 18))
        self.settings_btn.setIcon(icons.icon("settings", t["muted"], 18))
        self.help_btn.setIcon(icons.icon("help", t["muted"], 18))
