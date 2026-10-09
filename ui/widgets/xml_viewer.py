"""Lightweight XML viewer: highlighting, line numbers, search, jump to ID / xref target.
Large files are loaded lazily in blocks so a 20 MB book stays responsive."""
from __future__ import annotations

import re

from PySide6.QtCore import QRect, QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from ..app_state import theme_name
from .common import button, label


class XmlHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        dark = theme_name() == "dark"

        def fmt(c, bold=False):
            f = QTextCharFormat()
            f.setForeground(QColor(c))
            if bold:
                f.setFontWeight(QFont.DemiBold)
            return f
        self.rules = [
            (QRegularExpression(r"</?[\w:\-]+"), fmt("#7AA2F7" if dark else "#1D4ED8", True)),
            (QRegularExpression(r"/?>"), fmt("#7AA2F7" if dark else "#1D4ED8")),
            (QRegularExpression(r"\s[\w:\-]+(?==)"), fmt("#E0AF68" if dark else "#9A3412")),
            (QRegularExpression(r"\"[^\"]*\""), fmt("#9ECE6A" if dark else "#15803D")),
            (QRegularExpression(r"<!--.*-->"), fmt("#737AA2" if dark else "#6B7280")),
            (QRegularExpression(r"<[!?][^>]*>"), fmt("#BB9AF7" if dark else "#7C3AED")),
        ]

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


class _Gutter(QWidget):
    def __init__(self, ed):
        super().__init__(ed)
        self.ed = ed

    def sizeHint(self):
        return QSize(self.ed.gutter_width(), 0)

    def paintEvent(self, e):
        self.ed.paint_gutter(e)


class CodeEdit(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setObjectName("xml")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.gutter = _Gutter(self)
        self.blockCountChanged.connect(lambda _: self.setViewportMargins(self.gutter_width(), 0, 0, 0))
        self.updateRequest.connect(self._upd)
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def gutter_width(self):
        return 14 + self.fontMetrics().horizontalAdvance("9") * max(4, len(str(self.blockCount())))

    def _upd(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, e):
        p = QPainter(self.gutter)
        dark = theme_name() == "dark"
        p.fillRect(e.rect(), QColor("#1C2027" if dark else "#F4F5F7"))
        p.setPen(QColor("#6B7280"))
        b = self.firstVisibleBlock()
        top = int(self.blockBoundingGeometry(b).translated(self.contentOffset()).top())
        h = self.fontMetrics().height()
        while b.isValid() and top <= e.rect().bottom():
            if b.isVisible():
                p.drawText(0, top, self.gutter.width() - 6, h, Qt.AlignRight, str(b.blockNumber() + 1))
            top += int(self.blockBoundingRect(b).height())
            b = b.next()


class XmlViewer(QWidget):
    idClicked = Signal(str)

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search XML…  (text, id or rid)")
        self.search.returnPressed.connect(self.find_next)
        bar.addWidget(self.search, 1)
        nxt = button("Find", icon_name="search")
        nxt.clicked.connect(self.find_next)
        bar.addWidget(nxt)
        self.jump = QLineEdit()
        self.jump.setPlaceholderText("Jump to ID…")
        self.jump.setMaximumWidth(220)
        self.jump.returnPressed.connect(lambda: self.goto_id(self.jump.text().strip()))
        bar.addWidget(self.jump)
        self.more = button("Load more", "ghost")
        self.more.clicked.connect(self.load_more)
        self.more.hide()
        bar.addWidget(self.more)
        v.addLayout(bar)
        self.info = label("", "faint")
        v.addWidget(self.info)
        self.ed = CodeEdit()
        self.hl = XmlHighlighter(self.ed.document())
        v.addWidget(self.ed, 1)
        self.ed.mouseDoubleClickEvent = self._dbl
        self._full = ""
        self._shown = 0

    CHUNK = 600_000

    def load_file(self, path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            self._full = fh.read()
        self._shown = 0
        self.ed.setPlainText("")
        self.load_more()

    def set_text(self, text):
        self._full = text
        self._shown = 0
        self.ed.setPlainText("")
        self.load_more()

    def load_more(self):
        end = min(len(self._full), self._shown + self.CHUNK)
        nl = self._full.find("\n", end)
        end = len(self._full) if nl < 0 else nl
        chunk = self._full[self._shown:end]
        cur = self.ed.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(chunk if not self._shown else "\n" + chunk.lstrip("\n"))
        self._shown = end
        self.more.setVisible(self._shown < len(self._full))
        self.info.setText(f"{self._shown / max(1, len(self._full)):.0%} of file loaded" if self._shown < len(self._full) else "")

    def _ensure(self, pos: int):
        while pos >= self._shown and self._shown < len(self._full):
            self.load_more()

    def find_next(self):
        q = self.search.text()
        if not q:
            return
        if not self.ed.find(q):
            idx = self._full.find(q, self._shown)
            if idx >= 0:
                self._ensure(idx + len(q))
                self.ed.find(q)
            else:
                self.ed.moveCursor(QTextCursor.Start)
                self.ed.find(q)

    def goto_id(self, ident: str):
        if not ident:
            return
        needle = f'id="{ident}"'
        idx = self._full.find(needle)
        if idx < 0:
            self.info.setText(f"ID '{ident}' not found")
            return
        self._ensure(idx + len(needle))
        self.ed.moveCursor(QTextCursor.Start)
        self.ed.find(needle)
        self.ed.centerCursor()
        self.info.setText(f"Jumped to {ident}")

    def _dbl(self, e):
        QPlainTextEdit.mouseDoubleClickEvent(self.ed, e)
        cur = self.ed.textCursor()
        block = cur.block().text()
        m = re.search(r'(?:rid|id)="([^"]+)"', block)
        if m:
            ident = m.group(1)
            if 'rid="' in block:
                self.goto_id(ident)
            self.idClicked.emit(ident)
