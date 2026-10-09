"""Checkpoints for resumable single-book and batch processing.

Page extraction/OCR results are cached per page (see text_extractor / ocr_engine),
so a restarted conversion skips finished pages; this module records stage
progress per book and the batch queue state (which book is next).
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _atomic_write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    tmp.replace(path)


class Checkpoint:
    def __init__(self, path: Path, input_hash: str, config_fp: str, tool_version: str):
        self.path = Path(path)
        self.data = {"input_hash": input_hash, "config": config_fp, "tool_version": tool_version, "stage": None,
                     "page": None, "status": None, "updated": None}
        old = self.load(self.path)
        if old and old.get("input_hash") == input_hash and old.get("config") == config_fp:
            self.data.update({k: old.get(k) for k in ("stage", "page", "status")})
        self._last = 0.0

    @staticmethod
    def load(path: Path) -> dict | None:
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def update(self, **kw):
        self.data.update(kw)
        now = time.time()
        if kw.get("stage") == "extraction" and now - self._last < 2.0:
            return            # throttle writes during per-page loops
        self._last = now
        self.data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _atomic_write(self.path, self.data)


class BatchState:
    """output/batch_state.json : {book_path: {status, xml, input_hash}}"""

    def __init__(self, out_root: Path):
        self.path = Path(out_root) / "batch_state.json"
        self.data = Checkpoint.load(self.path) or {"books": {}, "queue": []}

    def pending(self, books: list[str], input_hashes: dict[str, str]) -> list[str]:
        out = []
        for b in books:
            rec = self.data["books"].get(b)
            if rec and rec.get("status") in ("PASS", "PASS WITH WARNINGS", "FAILED") and rec.get("input_hash") == input_hashes.get(b):
                continue
            out.append(b)
        return out

    def interrupted(self) -> dict | None:
        for b, rec in self.data["books"].items():
            if rec.get("status") == "RUNNING":
                return {"book": b, **rec}
        return None

    def mark(self, book: str, **kw):
        self.data["books"].setdefault(book, {}).update(kw)
        _atomic_write(self.path, self.data)

    def set_queue(self, queue: list[str]):
        self.data["queue"] = queue
        _atomic_write(self.path, self.data)
