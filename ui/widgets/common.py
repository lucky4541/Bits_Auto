"""Small building blocks shared by all pages."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .. import icons
from ..app_state import theme_tokens


def label(text: str = "", name: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    if selectable:
        l.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return l


def button(text: str, variant: str | None = None, icon_name: str | None = None, tooltip: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if icon_name:
        t = theme_tokens()
        color = "#FFFFFF" if variant == "primary" else (t["accent_text"] if variant == "ghost" else t["muted"])
        b.setIcon(icons.icon(icon_name, color, 16))
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("divider")
    f.setFrameShape(QFrame.NoFrame)
    return f


class Card(QFrame):
    def __init__(self, name: str = "card", margins=(16, 14, 16, 14), spacing=10):
        super().__init__()
        self.setObjectName(name)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(spacing)


class Page(QWidget):
    """Scrollable page with title, subtitle and a content column."""

    def __init__(self, title: str, subtitle: str = "", scroll: bool = True, max_width: int = 1100):
        super().__init__()
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        inner.setObjectName("page")
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(32, 26, 32, 26)
        self.body.setSpacing(14)
        head = QVBoxLayout()
        head.setSpacing(3)
        self.title = label(title, "pageTitle")
        head.addWidget(self.title)
        if subtitle:
            self.subtitle = label(subtitle, "pageSubtitle", wrap=True)
            head.addWidget(self.subtitle)
        self.body.addLayout(head)
        self.body.addSpacing(4)
        if scroll:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QFrame.NoFrame)
            sa.setWidget(inner)
            outer.addWidget(sa)
        else:
            outer.addWidget(inner)
        inner.setMaximumWidth(16777215)

    def add(self, w, stretch=0):
        self.body.addWidget(w, stretch)
        return w

    def finish(self):
        self.body.addStretch(1)


class PathField(QWidget):
    changed = Signal(str)

    def __init__(self, caption: str, mode: str = "dir", filter_: str = "", placeholder: str = ""):
        super().__init__()
        self.mode, self.filter = mode, filter_
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(5)
        v.addWidget(label(caption, "fieldLabel"))
        h = QHBoxLayout()
        h.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed.emit)
        h.addWidget(self.edit, 1)
        self.btn = button("Browse", icon_name="folder")
        self.btn.clicked.connect(self.browse)
        h.addWidget(self.btn)
        v.addLayout(h)

    def browse(self):
        start = self.edit.text() or str(Path.home())
        if self.mode == "dir":
            p = QFileDialog.getExistingDirectory(self, "Select folder", start)
        elif self.mode == "save":
            p, _ = QFileDialog.getSaveFileName(self, "Select file", start, self.filter)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Select file", start, self.filter)
        if p:
            self.edit.setText(str(Path(p)))

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, t: str):
        self.edit.setText(t or "")


class StatusLine(QWidget):
    """Icon + text, e.g. ✓ Mapping ready  (never colour-only)."""

    def __init__(self, state: str = "ok", text: str = ""):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.ic = QLabel()
        self.tx = QLabel()
        h.addWidget(self.ic)
        h.addWidget(self.tx)
        h.addStretch(1)
        self.set(state, text)

    def set(self, state: str, text: str):
        t = theme_tokens()
        name, col, obj = {"ok": ("check-circle", t["success"], "ok"), "warn": ("warning", t["warning"], "warn"),
                          "err": ("error", t["error"], "err"), "run": ("refresh", t["accent"], "muted"),
                          "idle": ("circle", t["faint"], "muted")}.get(state, ("circle", t["faint"], "muted"))
        self.ic.setPixmap(icons.pixmap(name, col, 16))
        self.tx.setText(text)
        self.tx.setObjectName(obj)
        self.tx.style().unpolish(self.tx)
        self.tx.style().polish(self.tx)


def hbox(*widgets, spacing=8, stretch_last=False, margins=(0, 0, 0, 0)):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(*margins)
    h.setSpacing(spacing)
    for x in widgets:
        if x is None:
            h.addStretch(1)
        elif isinstance(x, int):
            h.addSpacing(x)
        else:
            h.addWidget(x)
    if stretch_last:
        h.addStretch(1)
    return w


def expanding(w):
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w


class FitStack(QStackedWidget):
    """Stacked widget that takes the height of the *current* page only."""

    def __init__(self):
        super().__init__()
        self.currentChanged.connect(self._fit)

    def _fit(self, _i=None):
        for i in range(self.count()):
            w = self.widget(i)
            w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred if w is self.currentWidget() else QSizePolicy.Ignored)
        self.updateGeometry()

    def addWidget(self, w):
        i = super().addWidget(w)
        self._fit()
        return i

    def sizeHint(self):
        w = self.currentWidget()
        return w.sizeHint() if w else super().sizeHint()

    def minimumSizeHint(self):
        w = self.currentWidget()
        return w.minimumSizeHint() if w else super().minimumSizeHint()
