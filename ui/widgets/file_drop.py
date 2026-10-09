"""Drop zone + selected-file list (PDF / XML / folders)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from .. import icons
from ..app_state import theme_tokens
from .common import button, label


def pdf_pages(path: Path) -> int | None:
    try:
        import pymupdf
        with pymupdf.open(str(path)) as d:
            return len(d)
    except Exception:
        return None


class DropZone(QFrame):
    dropped = Signal(list)

    def __init__(self, text="Drop PDF files or a folder here", exts=(".pdf",), browse_files=True, browse_dir=True):
        super().__init__()
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.exts = exts
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 18, 16, 18)
        v.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("upload", theme_tokens()["faint"], 26))
        ic.setAlignment(Qt.AlignCenter)
        v.addWidget(ic)
        t = label(text, "muted")
        t.setAlignment(Qt.AlignCenter)
        v.addWidget(t)
        h = QHBoxLayout()
        h.addStretch(1)
        if browse_files:
            b = button("Browse Files", icon_name="file")
            b.clicked.connect(self._files)
            h.addWidget(b)
        if browse_dir:
            b2 = button("Browse Folder", icon_name="folder")
            b2.clicked.connect(self._dir)
            h.addWidget(b2)
        h.addStretch(1)
        v.addLayout(h)

    def _files(self):
        flt = "Documents (" + " ".join(f"*{e}" for e in self.exts) + ")"
        ps, _ = QFileDialog.getOpenFileNames(self, "Select files", "", flt)
        if ps:
            self.dropped.emit([Path(p) for p in ps])

    def _dir(self):
        p = QFileDialog.getExistingDirectory(self, "Select folder")
        if p:
            self.dropped.emit([Path(p)])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setProperty("hover", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, e):
        self.setProperty("hover", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, e):
        self.dragLeaveEvent(e)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        ok = [p for p in paths if p.is_dir() or p.suffix.lower() in self.exts]
        if ok:
            self.dropped.emit(ok)


class FileList(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        h = QHBoxLayout()
        self.title = label("Selected Files", "sectionTitle")
        h.addWidget(self.title)
        h.addStretch(1)
        self.clear_btn = button("Clear", "ghost")
        self.clear_btn.clicked.connect(self.clear)
        h.addWidget(self.clear_btn)
        v.addLayout(h)
        self.list = QListWidget()
        self.list.setMaximumHeight(170)
        v.addWidget(self.list)
        self.paths: list[Path] = []

    def add(self, paths: list[Path]):
        t = theme_tokens()
        for p in paths:
            p = Path(p)
            if p in self.paths:
                continue
            self.paths.append(p)
            if p.is_dir():
                n = len(list(p.rglob("*.pdf")))
                meta = f"folder · {n} PDF{'s' if n != 1 else ''}"
                ic = "folder"
            else:
                pg = pdf_pages(p) if p.suffix.lower() == ".pdf" else None
                meta = f"{pg:,} pages" if pg else p.suffix.lower()[1:].upper()
                ic = "file"
            it = QListWidgetItem(icons.icon(ic, t["muted"], 16), f"{p.name}    ·    {meta}")
            it.setData(Qt.UserRole, str(p))
            it.setToolTip(str(p))
            self.list.addItem(it)
            rm = QPushButton()
            rm.setIcon(icons.icon("x", t["faint"], 14))
            rm.setFlat(True)
            rm.setToolTip("Remove")
            rm.setFixedSize(24, 24)
            rm.clicked.connect(lambda _=False, path=p: self.remove(path))
            w = QWidget()
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 0, 4, 0)
            hl.addStretch(1)
            hl.addWidget(rm)
            self.list.setItemWidget(it, w)
        self.changed.emit()

    def remove(self, path: Path):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == str(path):
                self.list.takeItem(i)
                break
        self.paths = [p for p in self.paths if p != path]
        self.changed.emit()

    def clear(self):
        self.list.clear()
        self.paths = []
        self.changed.emit()
