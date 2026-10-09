"""Configuration: defaults < config/config.yaml < per-book overrides < GUI/CLI.

No absolute paths are hard-coded in core modules; everything path-like is
resolved relative to the project root (the folder that contains config/).
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

TOOL_VERSION = "1.0.0"

DEFAULTS: dict[str, Any] = {
    "paths": {
        "dtd_dir": "DTD",
        "samples_dir": "Samples",
        "input_dir": "inputs",
        "output_dir": "output",
        "analysis_dir": "analysis",
        "mapping_dir": "mapping",
        "cache_dir": "cache",
    },
    "bits": {
        "version": "1.0",
        "public_id": "-//NLM//DTD BITS Book Interchange DTD v1.0 20130520//EN",
        "system_id": "..\\..\\BITS-book1.dtd",
        "xml_lang": "en",
        "include_serial_code": True,
    },
    "ocr": {
        "engine": "auto",          # auto | tesseract | paddle | none
        "languages": ["spa", "eng"],
        "dpi": 300,
        "min_text_chars": 40,       # page with fewer chars + big image -> OCR
        "confidence_threshold": 60,
        "tesseract_cmd": "",       # optional explicit path (Windows)
    },
    "images": {"dpi": 300, "format": "jpg", "jpeg_quality": 90, "min_figure_pt": 36,
               "include_unlabeled_images": True, "cover_width_px": 300},
    "layout": {"column_detection": True, "table_detection": True, "figure_detection": True,
               "header_footer_zone": 0.085, "repeat_ratio": 0.3},
    "links": {"citation_detection": True, "cross_reference_detection": True,
              "bibliography_citations": True, "link_validation": "strict"},
    "placement": {"policy": "learned", "fallback": "physical"},
    "confidence": {"auto": 0.90, "warn": 0.75, "review": 0.50},
    "qa": {"content_coverage_min": 0.97, "page_coverage_warn": 0.90, "visual_overlays": False},
    "performance": {"workers": 1, "memory_mode": "normal", "resume": True},
    "mode": "production",          # production | development
    "debug": False,
    "save_intermediate_json": True,
    "auto_open_report": False,
}


def deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def project_root() -> Path:
    """Folder containing config/ (two levels above this file: src/bits_tool/)."""
    return Path(__file__).resolve().parents[2]


@dataclass
class Config:
    data: dict
    root: Path

    @classmethod
    def load(cls, path: Path | None = None, overrides: dict | None = None) -> "Config":
        root = project_root()
        path = Path(path) if path else root / "config" / "config.yaml"
        data = copy.deepcopy(DEFAULTS)
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                user = yaml.safe_load(fh) or {}
            data = deep_merge(data, user)
        if overrides:
            data = deep_merge(data, overrides)
        return cls(data, root)

    def save(self, path: Path | None = None):
        path = Path(path) if path else self.root / "config" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.data, fh, sort_keys=False, allow_unicode=True)

    def get(self, dotted: str, default=None):
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, dotted: str, value):
        cur = self.data
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value

    def path(self, key: str) -> Path:
        p = Path(self.get(f"paths.{key}"))
        return p if p.is_absolute() else (self.root / p)

    def fingerprint(self) -> str:
        import hashlib
        return hashlib.sha1(json.dumps(self.data, sort_keys=True, default=str).encode()).hexdigest()[:12]


def load_book_overrides(pdf_or_dir: Path) -> dict:
    """Optional per-book metadata: book.json / book.yaml next to the PDF(s)."""
    base = Path(pdf_or_dir)
    base = base if base.is_dir() else base.parent
    for name in ("book.yaml", "book.yml", "book.json"):
        p = base / name
        if p.exists():
            with open(p, encoding="utf-8") as fh:
                return (yaml.safe_load(fh) if p.suffix != ".json" else json.load(fh)) or {}
    return {}
