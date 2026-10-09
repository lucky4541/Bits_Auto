"""Design tokens + Qt stylesheet (light default, proper dark theme — not inverted)."""
from __future__ import annotations

LIGHT = {
    "bg": "#F6F7F9", "surface": "#FFFFFF", "surface2": "#F1F3F6", "sidebar": "#FBFBFC", "border": "#E3E6EB",
    "border_strong": "#CFD4DC", "text": "#1C2230", "muted": "#5E6878", "faint": "#8A93A3",
    "accent": "#2563EB", "accent_hover": "#1D4ED8", "accent_soft": "#E8EFFD", "accent_text": "#1E4FC2",
    "success": "#15803D", "success_soft": "#E7F5EC", "warning": "#B45309", "warning_soft": "#FDF3E4",
    "error": "#B91C1C", "error_soft": "#FCEBEB", "focus": "#93B4F5", "code_bg": "#1E2230", "code_text": "#D7DCE6",
    "selection": "#DCE7FC",
}
DARK = {
    "bg": "#15181E", "surface": "#1C2027", "surface2": "#232833", "sidebar": "#181B21", "border": "#2B313C",
    "border_strong": "#3A4250", "text": "#E6E9EF", "muted": "#A0A8B7", "faint": "#7B8494",
    "accent": "#4C8BF5", "accent_hover": "#6A9EF7", "accent_soft": "#1F2C45", "accent_text": "#8DB3FA",
    "success": "#3FB36B", "success_soft": "#16291F", "warning": "#E0A33B", "warning_soft": "#2D2415",
    "error": "#EF6B6B", "error_soft": "#2E1A1C", "focus": "#3B6FD1", "code_bg": "#11141A", "code_text": "#D7DCE6",
    "selection": "#2A3B5E",
}
FONT = '"Segoe UI", "Segoe UI Variable", "SF Pro Text", "Inter", "Helvetica Neue", Arial, sans-serif'
MONO = '"Cascadia Mono", Consolas, "SF Mono", Menlo, monospace'


def stylesheet(t: dict) -> str:
    return f"""
* {{ font-family: {FONT}; font-size: 13px; color: {t['text']}; }}
QMainWindow, QWidget#root {{ background: {t['bg']}; }}
QWidget#page {{ background: {t['bg']}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QToolTip {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']}; padding: 5px 7px; border-radius: 6px; }}

/* top bar */
QWidget#topbar {{ background: {t['surface']}; border-bottom: 1px solid {t['border']}; }}
QLabel#appTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#appSubtitle {{ color: {t['muted']}; font-size: 12px; }}

/* sidebar */
QWidget#sidebar {{ background: {t['sidebar']}; border-right: 1px solid {t['border']}; }}
QLabel#navSection {{ color: {t['faint']}; font-size: 11px; font-weight: 600; letter-spacing: 0.6px; padding: 14px 14px 4px 16px; }}
QPushButton#navItem {{ text-align: left; padding: 7px 10px; margin: 1px 8px; border: none; border-radius: 7px;
    color: {t['muted']}; background: transparent; font-size: 13px; }}
QPushButton#navItem:hover {{ background: {t['surface2']}; color: {t['text']}; }}
QPushButton#navItem:checked {{ background: {t['accent_soft']}; color: {t['accent_text']}; font-weight: 600; }}
QPushButton#navItem:focus {{ outline: none; border: 1px solid {t['focus']}; }}

/* status bar */
QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['border']}; color: {t['muted']}; }}
QStatusBar QLabel {{ color: {t['muted']}; font-size: 12px; padding: 0 8px; }}
QPushButton#statusChip {{ border: none; background: transparent; color: {t['muted']}; font-size: 12px; padding: 2px 8px; border-radius: 6px; }}
QPushButton#statusChip:hover {{ background: {t['surface2']}; color: {t['text']}; }}

/* typography */
QLabel#pageTitle {{ font-size: 20px; font-weight: 600; }}
QLabel#pageSubtitle {{ color: {t['muted']}; font-size: 13px; }}
QLabel#sectionTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#fieldLabel {{ color: {t['muted']}; font-size: 12px; font-weight: 600; }}
QLabel#muted {{ color: {t['muted']}; }}
QLabel#faint {{ color: {t['faint']}; font-size: 12px; }}
QLabel#statValue {{ font-size: 22px; font-weight: 600; }}
QLabel#statLabel {{ color: {t['muted']}; font-size: 12px; }}
QLabel#hero {{ font-size: 24px; font-weight: 600; }}
QLabel#ok {{ color: {t['success']}; font-weight: 600; }}
QLabel#warn {{ color: {t['warning']}; font-weight: 600; }}
QLabel#err {{ color: {t['error']}; font-weight: 600; }}
QLabel#bigIcon {{ font-size: 34px; }}

/* cards */
QFrame#card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; }}
QFrame#cardFlat {{ background: {t['surface2']}; border: none; border-radius: 8px; }}
QFrame#banner_ok {{ background: {t['success_soft']}; border: 1px solid {t['success']}; border-radius: 8px; }}
QFrame#banner_warn {{ background: {t['warning_soft']}; border: 1px solid {t['warning']}; border-radius: 8px; }}
QFrame#banner_err {{ background: {t['error_soft']}; border: 1px solid {t['error']}; border-radius: 8px; }}
QFrame#banner_info {{ background: {t['accent_soft']}; border: 1px solid {t['accent']}; border-radius: 8px; }}
QFrame#divider {{ background: {t['border']}; max-height: 1px; min-height: 1px; border: none; }}

/* buttons */
QPushButton {{ background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 7px; padding: 6px 13px; }}
QPushButton:hover {{ background: {t['surface2']}; }}
QPushButton:pressed {{ background: {t['border']}; }}
QPushButton:disabled {{ color: {t['faint']}; background: {t['surface2']}; border-color: {t['border']}; }}
QPushButton:focus {{ border: 1px solid {t['focus']}; }}
QPushButton[variant="primary"] {{ background: {t['accent']}; border: 1px solid {t['accent']}; color: white; font-weight: 600; padding: 7px 18px; }}
QPushButton[variant="primary"]:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton[variant="primary"]:disabled {{ background: {t['border_strong']}; border-color: {t['border_strong']}; color: {t['surface']}; }}
QPushButton[variant="danger"] {{ color: {t['error']}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: none; color: {t['accent_text']}; padding: 4px 6px; }}
QPushButton[variant="ghost"]:hover {{ background: {t['accent_soft']}; }}
QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 5px; }}
QToolButton:hover {{ background: {t['surface2']}; }}

/* inputs */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{ background: {t['surface']}; border: 1px solid {t['border_strong']};
    border-radius: 7px; padding: 5px 8px; selection-background-color: {t['selection']}; selection-color: {t['text']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {t['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']}; selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}
QCheckBox {{ spacing: 8px; }}

/* progress */
QProgressBar {{ background: {t['surface2']}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
QProgressBar[state="ok"]::chunk {{ background: {t['success']}; }}

/* tables / lists */
QTableWidget, QTableView, QTreeWidget, QListWidget {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px;
    gridline-color: {t['border']}; selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; alternate-background-color: {t['surface2']}; }}
QHeaderView::section {{ background: {t['surface2']}; color: {t['muted']}; border: none; border-bottom: 1px solid {t['border']};
    padding: 6px 8px; font-size: 12px; font-weight: 600; }}
QTableWidget::item, QTreeWidget::item, QListWidget::item {{ padding: 4px 6px; }}
QListWidget#categoryList::item {{ padding: 7px 10px; border-radius: 6px; margin: 1px 4px; }}
QListWidget#categoryList::item:selected {{ background: {t['accent_soft']}; color: {t['accent_text']}; }}

/* tabs */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {t['muted']}; padding: 7px 14px; border: none; border-bottom: 2px solid transparent; margin-right: 4px; }}
QTabBar::tab:selected {{ color: {t['accent_text']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {t['text']}; }}

/* code / logs */
QPlainTextEdit#code, QTextEdit#code {{ font-family: {MONO}; font-size: 12px; background: {t['code_bg']}; color: {t['code_text']};
    border: 1px solid {t['border']}; border-radius: 8px; }}
QPlainTextEdit#xml {{ font-family: {MONO}; font-size: 12px; background: {t['surface']}; }}

/* drop zone */
QFrame#dropZone {{ background: {t['surface']}; border: 1.5px dashed {t['border_strong']}; border-radius: 10px; }}
QFrame#dropZone[hover="true"] {{ border-color: {t['accent']}; background: {t['accent_soft']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['border_strong']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['border_strong']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QSplitter::handle {{ background: {t['border']}; }}
"""
