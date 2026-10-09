"""Page classification: text / scanned (needs OCR) / blank / image-only."""
from __future__ import annotations

from .ocr_engine import needs_ocr  # noqa: F401


def classify_page(page_info) -> str:
    lines = getattr(page_info, "lines", [])
    if getattr(page_info, "is_ocr", False):
        return "scanned"
    if not lines:
        return "image-only" if getattr(page_info, "images", None) else "blank"
    return "text"
