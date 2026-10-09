"""Batch conversion: single PDF, split-PDF folder, directory, recursive directory.

Resumable: output/batch_state.json records every book (status, input hash);
finished books are skipped on resume, an interrupted book restarts from its
page cache (extraction/OCR are cached per page), so no completed work is lost.
One failing book never stops the batch.
"""
from __future__ import annotations

import traceback
from pathlib import Path

from .checkpoint_manager import BatchState
from .config import Config
from .converter import BookConverter, Cancelled
from .pdf_loader import discover_books, load_book


class BatchProcessor:
    def __init__(self, cfg: Config, progress=None, cancel=None):
        self.cfg = cfg
        self.progress = progress or (lambda **kw: None)
        self.cancel = cancel or (lambda: False)

    def run(self, inputs: list[Path], out_dir: Path, resume: bool = True, book_overrides: dict | None = None) -> list[dict]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        books: list[Path] = []
        for i in inputs:
            for b in discover_books(Path(i)):
                if b not in books:
                    books.append(b)
        state = BatchState(out_dir)
        hashes = {}
        for b in books:
            try:
                hashes[str(b)] = load_book(b).hash()
            except Exception:
                hashes[str(b)] = None
        todo = state.pending([str(b) for b in books], hashes) if resume else [str(b) for b in books]
        state.set_queue(todo)
        results = []
        done_before = [b for b in books if str(b) not in todo]
        for b in done_before:
            rec = state.data["books"].get(str(b), {})
            results.append({"book": str(b), "status": rec.get("status"), "xml": rec.get("xml"), "skipped": True,
                            "out_dir": rec.get("out_dir"), "stats": rec.get("stats", {})})
        total = len(books)
        for n, b in enumerate(todo):
            book_no = len(done_before) + n + 1
            self.progress(stage="Batch", frac=(book_no - 1) / max(1, total), detail=Path(b).name, book_index=book_no,
                          book_total=total, current_file=Path(b).name)
            state.mark(b, status="RUNNING", input_hash=hashes.get(b))

            def prog(**kw):
                kw.setdefault("book_index", book_no)
                kw["book_total"] = total
                kw["current_file"] = Path(b).name
                self.progress(**kw)
            conv = BookConverter(self.cfg, progress=prog, cancel_flag=self.cancel)
            if book_overrides:
                self.cfg.set("book_overrides", book_overrides.get(b) or book_overrides.get("*") or {})
            try:
                res = conv.convert(Path(b), out_dir)
                state.mark(b, status=res.status, xml=res.xml_path, out_dir=res.out_dir, input_hash=hashes.get(b),
                           stats=res.stats, report=res.report_path)
                results.append({"book": b, "status": res.status, "xml": res.xml_path, "out_dir": res.out_dir,
                                "stats": res.stats, "report": res.report_path, "validation": _brief(res.validation),
                                "issues": _issue_counts(res.issues), "failed_pages": len(res.failed_pages)})
            except Cancelled:
                state.mark(b, status="RUNNING", cancelled=True)
                results.append({"book": b, "status": "CANCELLED"})
                break
            except Exception as e:
                state.mark(b, status="FAILED", error=repr(e), trace=traceback.format_exc())
                results.append({"book": b, "status": "FAILED", "error": repr(e)})
        self.progress(stage="Batch", frac=1.0, detail="done", book_total=total)
        return results


def _brief(v: dict) -> dict:
    return {"dtd_valid": v["dtd"]["valid"], "dtd_errors": v["dtd"]["error_count"], "ids": v["ids"]["total"],
            "duplicate_ids": len(v["ids"]["duplicates"]), "links": v["links"]["total"], "links_valid": v["links"]["valid"],
            "broken_links": len(v["links"]["broken"]) + len(v["links"]["wrong_type"]),
            "figures": v["links"]["figures"], "tables": v["links"]["tables"],
            "images_missing": len(v["images"]["missing"]), "coverage": v["content"]["coverage"],
            "critical": v.get("gate", {}).get("critical", [])}


def _issue_counts(issues: list[dict]) -> dict:
    out: dict[str, int] = {}
    for i in issues:
        out[i["code"]] = out.get(i["code"], 0) + 1
    return out
