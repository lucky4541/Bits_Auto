"""Main window: top bar · sidebar · stacked pages · status bar.

The window only wires pages together; engine work happens in services
through background workers.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QStackedWidget, QStatusBar, QTextEdit, QVBoxLayout, QWidget)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bits_tool import services  # noqa: E402
from bits_tool.config import TOOL_VERSION, Config  # noqa: E402

from . import app_state, icons  # noqa: E402
from .sidebar import Sidebar  # noqa: E402
from .theme import stylesheet  # noqa: E402
from .topbar import TopBar  # noqa: E402


class MainThreadGC:
    """Run Python's cycle collector only on the UI thread: a collection triggered inside a worker
    thread would destroy Qt objects there ('Timers cannot be started from another thread')."""

    def __init__(self, interval_ms: int = 1500):
        import gc
        self.gc = gc
        gc.disable()
        self.timer = QTimer()
        self.timer.timeout.connect(self.check)
        self.timer.start(interval_ms)
        self.threshold = gc.get_threshold()

    def check(self):
        c0, c1, c2 = self.gc.get_count()
        if c0 > self.threshold[0]:
            self.gc.collect(0)
            if c1 > self.threshold[1]:
                self.gc.collect(1)
                if c2 > self.threshold[2]:
                    self.gc.collect(2)


class AppContext:
    """Shared state handed to every page."""

    def __init__(self, window):
        self.window = window
        self.cfg = Config.load()
        self.last_book_dir: Path | None = None
        self.last_results: list[dict] = []

    def navigate(self, key: str, **kw):
        self.window.navigate(key, **kw)

    def status(self, text: str):
        self.window.status_left.setText(text)

    def refresh_status(self):
        self.window.refresh_status()

    def open_path(self, p):
        if p:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        app_state.load_theme()
        self._gc = MainThreadGC()
        self.setWindowTitle("BITS Conversion Tool")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 800)
        self.setWindowIcon(self._app_icon())
        self.ctx = AppContext(self)
        root = QWidget()
        root.setObjectName("root")
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.topbar = TopBar()
        v.addWidget(self.topbar)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.sidebar = Sidebar()
        h.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)
        v.addLayout(h, 1)
        self.setCentralWidget(root)
        self.pages = {}
        self._build_pages()
        self._build_status()
        self.sidebar.navigate.connect(self.navigate)
        self.topbar.settingsClicked.connect(lambda: self.navigate("settings"))
        self.topbar.helpClicked.connect(self.show_help)
        self.topbar.themeClicked.connect(self.toggle_theme)
        self._shortcuts()
        self.setAcceptDrops(True)
        self.apply_theme()
        self._restore()
        self.navigate("home")
        QTimer.singleShot(50, self.refresh_status)

    # ------------------------------------------------------------- pages
    def _build_pages(self):
        from .compare_page import ComparePage
        from .convert_page import ConvertPage
        from .home_page import HomePage
        from .logs_page import LogsPage
        from .mapping_page import MappingPage
        from .reports_page import ReportsPage
        from .sample_page import SamplePage
        from .settings_page import SettingsPage
        from .validation_page import ValidationPage
        from .zoning_page import ZoningPage
        for key, cls in (("home", HomePage), ("samples", SamplePage), ("convert", ConvertPage), ("zoning", ZoningPage),
                         ("validate", ValidationPage),
                         ("compare", ComparePage), ("mapping", MappingPage), ("logs", LogsPage), ("reports", ReportsPage),
                         ("settings", SettingsPage)):
            w = cls(self.ctx)
            self.pages[key] = w
            self.stack.addWidget(w)

    def navigate(self, key: str, **kw):
        page = self.pages.get(key)
        if page is None:
            return
        self.sidebar.select(key)
        self.stack.setCurrentWidget(page)
        if hasattr(page, "on_show"):
            page.on_show(**kw)

    # ------------------------------------------------------------ status
    def _build_status(self):
        sb = QStatusBar()
        sb.setSizeGripEnabled(False)
        self.status_left = QLabel("Ready")
        sb.addWidget(self.status_left, 1)
        self.mapping_chip = QPushButton("Mapping: —")
        self.mapping_chip.setObjectName("statusChip")
        self.mapping_chip.setCursor(Qt.PointingHandCursor)
        self.mapping_chip.setToolTip("Mapping profile status — click for details")
        self.mapping_chip.clicked.connect(lambda: self.navigate("mapping"))
        self.dtd_chip = QPushButton("DTD: —")
        self.dtd_chip.setObjectName("statusChip")
        self.dtd_chip.setCursor(Qt.PointingHandCursor)
        self.dtd_chip.setToolTip("DTD status — click for DTD settings")
        self.dtd_chip.clicked.connect(lambda: self.navigate("settings"))
        self.version_lbl = QLabel(f"BITS {self.ctx.cfg.get('bits.version', '1.0')}  ·  v{TOOL_VERSION}")
        self.ready_chip = QLabel("● System Ready")
        sb.addPermanentWidget(self.mapping_chip)
        sb.addPermanentWidget(self.dtd_chip)
        sb.addPermanentWidget(self.version_lbl)
        sb.addPermanentWidget(self.ready_chip)
        self.setStatusBar(sb)

    def refresh_status(self):
        t = app_state.theme_tokens()
        m = services.mapping_status(self.ctx.cfg)
        d = services.dtd_status(self.ctx.cfg)
        mi = {"ok": ("check-circle", t["success"]), "stale": ("warning", t["warning"]), "missing": ("error", t["error"])}[m["state"]]
        di = {"ok": ("check-circle", t["success"]), "missing": ("warning", t["warning"]), "invalid": ("error", t["error"])}[d["state"]]
        self.mapping_chip.setIcon(icons.icon(mi[0], mi[1], 14))
        self.mapping_chip.setText(m["label"])
        self.mapping_chip.setToolTip(m.get("detail", ""))
        self.dtd_chip.setIcon(icons.icon(di[0], di[1], 14))
        self.dtd_chip.setText(f"DTD {d['label']}")
        self.dtd_chip.setToolTip(d.get("detail", ""))
        self.ctx.mapping_state, self.ctx.dtd_state = m, d
        for p in self.pages.values():
            if hasattr(p, "on_status"):
                p.on_status(m, d)

    # -------------------------------------------------------------- theme
    def apply_theme(self):
        QApplication.instance().setStyleSheet(stylesheet(app_state.theme_tokens()))
        self.topbar.refresh_icons()
        self.sidebar.refresh_icons()

    def toggle_theme(self):
        app_state.set_theme_name("dark" if app_state.theme_name() == "light" else "light")
        self.apply_theme()
        self.refresh_status()

    @staticmethod
    def _app_icon() -> QIcon:
        return icons.icon("convert", "#2563EB", 64)

    # ---------------------------------------------------------- shortcuts
    def _shortcuts(self):
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(fn)
            return s
        sc("Ctrl+O", lambda: (self.navigate("convert"), self.pages["convert"].browse_files()))
        sc("Ctrl+Return", lambda: (self.navigate("convert"), self.pages["convert"].start()))
        sc("Ctrl+Enter", lambda: (self.navigate("convert"), self.pages["convert"].start()))
        sc("Ctrl+R", self.refresh_current)
        sc("Ctrl+F", self.focus_search)
        sc("Ctrl+L", lambda: self.navigate("logs"))
        sc("Ctrl+,", lambda: self.navigate("settings"))
        sc("Esc", self.cancel_current)
        self._paste_sc = sc("Ctrl+V", self._ctrl_v)

    def _ctrl_v(self):
        w = QApplication.focusWidget()
        if isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit)) and not w.isReadOnly():
            w.paste()                     # keep normal paste inside text fields
        else:
            self.navigate("validate")

    def refresh_current(self):
        self.refresh_status()
        page = self.stack.currentWidget()
        if hasattr(page, "on_show"):
            page.on_show()

    def focus_search(self):
        page = self.stack.currentWidget()
        if hasattr(page, "focus_search"):
            page.focus_search()

    def cancel_current(self):
        for p in self.pages.values():
            if hasattr(p, "cancel") and getattr(p, "running", False):
                p.cancel()

    # ------------------------------------------------------------ drag/drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        pdfs = [p for p in paths if p.suffix.lower() == ".pdf" or p.is_dir()]
        xmls = [p for p in paths if p.suffix.lower() == ".xml"]
        if pdfs and xmls:
            self.navigate("compare", pdf=pdfs[0], xml=xmls[0])
        elif xmls:
            self.navigate("validate", paths=xmls)
        elif pdfs:
            self.navigate("convert", paths=pdfs)

    # ------------------------------------------------------------ help
    def show_help(self):
        QMessageBox.information(self, "BITS Conversion Tool", (
            "<b>Workflow</b><br>1. <b>Sample Analysis</b> — learn the production conventions from approved PDF/XML samples.<br>"
            "2. <b>Convert</b> — PDF(s) or folders to BITS XML with images and QA reports.<br>"
            "3. <b>Validate</b> — DTD, IDs, links, images and structure checks for any BITS XML.<br>"
            "4. <b>Compare</b> — PDF page next to its XML content; missing text in red.<br><br>"
            "<b>Shortcuts</b><br>Ctrl+O open · Ctrl+Enter convert · Ctrl+V validate (outside text fields) · Ctrl+R refresh · "
            "Ctrl+F search · Ctrl+L logs · Ctrl+, settings · Esc cancel<br><b>Zoning</b> V select · D draw · P/1-4/L/E/R/N/F/T/B/C/K tag · "
            "M merge · S split · U unwrap · Del delete · [ ] heading level · PgUp/PgDn page · F7 next flagged · Ctrl+Z/Y undo/redo · "
            "Ctrl+S save · Ctrl+G generate XML"))

    # ------------------------------------------------------------ persist
    def _restore(self):
        s = app_state.settings()
        g = s.value("geometry")
        if isinstance(g, QByteArray) and not g.isEmpty():
            self.restoreGeometry(g)

    def closeEvent(self, e):
        running = [p for p in self.pages.values() if getattr(p, "running", False)]
        if running:
            r = QMessageBox.question(self, "Work in progress", "A job is still running. Cancel it and quit? Completed work is kept and can be resumed.")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            for p in running:
                p.cancel()
        app_state.settings().setValue("geometry", self.saveGeometry())
        super().closeEvent(e)


def main():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("BITS Conversion Tool")
    app.setOrganizationName("BITSConversionTool")
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
