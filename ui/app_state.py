"""Persistent UI state (window geometry, recent files, first-run, theme) via QSettings."""
from __future__ import annotations

from PySide6.QtCore import QSettings

from .theme import DARK, LIGHT

_THEME = {"name": "light"}


def settings() -> QSettings:
    return QSettings("BITSConversionTool", "BITS Conversion Tool")


def theme_name() -> str:
    return _THEME["name"]


def set_theme_name(n: str):
    _THEME["name"] = n
    settings().setValue("theme", n)


def theme_tokens() -> dict:
    return DARK if _THEME["name"] == "dark" else LIGHT


def load_theme():
    _THEME["name"] = settings().value("theme", "light")


def recent(kind: str) -> list[str]:
    v = settings().value(f"recent/{kind}", [])
    if isinstance(v, str):
        v = [v]
    return list(v or [])


def add_recent(kind: str, path: str, keep: int = 6):
    items = [p for p in recent(kind) if p != path]
    items.insert(0, path)
    settings().setValue(f"recent/{kind}", items[:keep])


def first_run() -> bool:
    return settings().value("first_run_done", "0") != "1"


def mark_first_run_done():
    settings().setValue("first_run_done", "1")
