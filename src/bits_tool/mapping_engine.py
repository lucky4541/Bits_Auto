"""Mapping knowledge base: build from samples (SampleMapper) and load for conversion."""
from __future__ import annotations

import json
from pathlib import Path

from .sample_mapper import SampleMapper  # noqa: F401


def load_mapping(mapping_dir: Path) -> dict[str, dict]:
    """All mapping/*.json files keyed by file stem (empty dict if not built yet)."""
    out = {}
    for p in sorted(Path(mapping_dir).glob("*.json")):
        try:
            out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            out[p.stem] = {}
    return out
