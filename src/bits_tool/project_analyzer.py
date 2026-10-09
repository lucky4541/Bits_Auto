"""Project + sample inventory.

Walks the project root (DTD, Samples, inputs), looks *inside* zip packages
(the WK deliveries are zips, sometimes nested), records sizes / checksums /
page counts / XML roots and pairs PDFs with XMLs by ISBN.

Also extracts sample XML and PDFs from zips into a local cache so the
analyzers can read them (`materialize_samples`).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from collections import defaultdict
from pathlib import Path

ISBN_RE = re.compile(r"(97[89]\d{10})")
INTERESTING = {".pdf", ".xml", ".dtd", ".ent", ".mod", ".xsd", ".rng", ".json", ".yaml", ".yml",
               ".txt", ".ini", ".cfg", ".py", ".docx", ".xlsx", ".zip", ".gif", ".jpg", ".jpeg", ".png", ".tif"}


def sha1(path: Path, limit: int | None = None) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        read = 0
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            h.update(b)
            read += len(b)
            if limit and read >= limit:
                break
    return h.hexdigest()


def _xml_head(data: bytes) -> dict:
    head = data[:4000].decode("utf-8", "replace")
    m = re.search(r"<!DOCTYPE\s+(\S+)\s+PUBLIC\s+\"([^\"]+)\"\s+\"([^\"]+)\"", head)
    r = re.search(r"<(book|article|book-part|index)[\s>]", head)
    return {"xml_root": r.group(1) if r else None, "doctype_public": m.group(2) if m else None,
            "doctype_system": m.group(3) if m else None}


def _pdf_pages(path_or_bytes) -> int | None:
    try:
        import pymupdf
        doc = pymupdf.open(stream=path_or_bytes, filetype="pdf") if isinstance(path_or_bytes, bytes) else pymupdf.open(str(path_or_bytes))
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return None


def _kind(name: str) -> str:
    n = name.lower()
    if n.endswith(".pdf"):
        return "pdf"
    if n.endswith(".xml"):
        return "xml"
    if n.endswith((".dtd", ".ent", ".mod", ".xsd", ".rng")):
        return "schema"
    if n.endswith((".jpg", ".jpeg", ".gif", ".png", ".tif")):
        return "image"
    if n.endswith(".zip"):
        return "zip"
    return "support"


class ProjectAnalyzer:
    def __init__(self, root: Path, samples: Path, dtd: Path, inputs: Path, checksums: bool = True,
                 pdf_pages: bool = True, checksum_limit: int = 64 << 20):
        self.root, self.samples, self.dtd, self.inputs = map(Path, (root, samples, dtd, inputs))
        self.checksums = checksums
        self.pdf_pages = pdf_pages
        self.checksum_limit = checksum_limit

    # ----------------------------------------------------------- walking
    def _walk(self, base: Path):
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                p = Path(dirpath) / f
                if p.suffix.lower() not in INTERESTING:
                    continue
                yield p

    def _zip_members(self, zpath: Path, data: bytes | None = None, prefix: str = "", depth: int = 0):
        out = []
        try:
            zf = zipfile.ZipFile(io.BytesIO(data) if data is not None else str(zpath))
        except zipfile.BadZipFile:
            return [{"path": prefix or str(zpath), "error": "bad zip"}]
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                rec = {"path": f"{prefix or zpath.name}!/{name}", "name": Path(name).name, "size": info.file_size,
                       "kind": _kind(name), "in_zip": True}
                if rec["kind"] == "xml" and info.file_size:
                    rec.update(_xml_head(zf.read(name)[:4000]))
                if rec["kind"] == "zip" and depth < 3:
                    out.extend(self._zip_members(zpath, zf.read(name), rec["path"], depth + 1))
                    continue
                out.append(rec)
        return out

    def file_record(self, p: Path) -> dict:
        rec = {"path": str(p.relative_to(self.root)) if self.root in p.parents else str(p),
               "name": p.name, "ext": p.suffix.lower(), "size": p.stat().st_size, "kind": _kind(p.name)}
        if self.checksums:
            rec["sha1" if p.stat().st_size <= self.checksum_limit else "sha1_first64MB"] = sha1(p, self.checksum_limit)
        if rec["kind"] == "xml":
            with open(p, "rb") as fh:
                rec.update(_xml_head(fh.read(4000)))
        if rec["kind"] == "pdf" and self.pdf_pages:
            rec["page_count"] = _pdf_pages(p)
        return rec

    # ------------------------------------------------------------- build
    def project_inventory(self) -> dict:
        files, dirs = [], set()
        for base in (self.dtd, self.samples, self.inputs):
            if not base.exists():
                continue
            for p in self._walk(base):
                dirs.add(str(p.parent))
                files.append(self.file_record(p))
        ext = defaultdict(int)
        for f in files:
            ext[f["ext"]] += 1
        py = [f for f in files if f["ext"] == ".py"]
        return {"root": str(self.root), "directories": sorted(dirs), "file_count": len(files),
                "extensions": dict(ext), "existing_python_modules": py, "files": files}

    def sample_inventory(self) -> dict:
        recs = []
        for p in self._walk(self.samples):
            r = self.file_record(p)
            recs.append(r)
            if r["kind"] == "zip":
                r["members"] = self._zip_members(p)
        flat = []
        for r in recs:
            flat.append(r)
            flat.extend(r.get("members", []))
        by_isbn = defaultdict(lambda: {"pdf": [], "xml": [], "assets": 0})
        for r in flat:
            m = ISBN_RE.search(r["name"]) or ISBN_RE.search(r.get("path", ""))
            if not m:
                continue
            k = m.group(1)
            if r["kind"] == "pdf":
                by_isbn[k]["pdf"].append(r["path"])
            elif r["kind"] == "xml" and r.get("xml_root") == "book":
                if r["path"] not in by_isbn[k]["xml"]:
                    by_isbn[k]["xml"].append(r["path"])
            elif r["kind"] == "image":
                by_isbn[k]["assets"] += 1
        pairs = {}
        for k, v in sorted(by_isbn.items()):
            status = "matched" if v["pdf"] and v["xml"] else ("pdf_only" if v["pdf"] else ("xml_only" if v["xml"] else "assets_only"))
            pairs[k] = {**v, "status": status, "pdf_is_split": len(v["pdf"]) > 1}
        counts = {
            "TOTAL_PDF_ISBNS": sum(1 for v in pairs.values() if v["pdf"]),
            "TOTAL_XML_ISBNS": sum(1 for v in pairs.values() if v["xml"]),
            "MATCHED_PDF_XML": sum(1 for v in pairs.values() if v["status"] == "matched"),
            "PDF_WITHOUT_XML": [k for k, v in pairs.items() if v["status"] == "pdf_only"],
            "XML_WITHOUT_PDF": [k for k, v in pairs.items() if v["status"] == "xml_only"],
            "DTD_FILES": sum(1 for r in flat if r["kind"] == "schema"),
            "SUPPORT_FILES": sum(1 for r in flat if r["kind"] in ("support",)),
        }
        return {"counts": counts, "pairs": pairs, "files": recs}

    def write(self, out_dir: Path) -> dict:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        pi = self.project_inventory()
        si = self.sample_inventory()
        for name, data in (("project_inventory.json", pi), ("sample_inventory.json", si)):
            with open(out_dir / name, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, ensure_ascii=False)
        return si["counts"]


def materialize_samples(samples_dir: Path, cache_dir: Path, want_pdf: bool = True, max_member_size: int = 2 << 30) -> dict:
    """Extract sample XML (and optionally PDFs) from zip packages into cache_dir/<isbn>/.
    Returns {isbn: {"xml": path|None, "pdf": [paths]}}. Already-extracted files are reused."""
    samples_dir, cache_dir = Path(samples_dir), Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, dict] = defaultdict(lambda: {"xml": None, "pdf": []})

    def take(name: str, data_fn, size: int):
        m = ISBN_RE.search(name)
        if not m:
            return
        k = m.group(1)
        low = name.lower()
        if not (low.endswith(".xml") or (want_pdf and low.endswith(".pdf"))) or size > max_member_size:
            return
        dest = cache_dir / k / Path(name).name
        if not dest.exists() or dest.stat().st_size != size:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(data_fn())
        if low.endswith(".xml"):
            found[k]["xml"] = str(dest)
        elif str(dest) not in found[k]["pdf"]:
            found[k]["pdf"].append(str(dest))

    def scan_zip(zf: zipfile.ZipFile, depth=0):
        for info in zf.infolist():
            if info.is_dir():
                continue
            n = info.filename
            if n.lower().endswith(".zip") and depth < 3:
                try:
                    with zipfile.ZipFile(io.BytesIO(zf.read(n))) as inner:
                        scan_zip(inner, depth + 1)
                except zipfile.BadZipFile:
                    pass
                continue
            take(n, lambda n=n: zf.read(n), info.file_size)

    loose_names = set()
    for dirpath, _d, files in os.walk(samples_dir):          # 1) loose files first (no copy needed)
        for f in files:
            p = Path(dirpath) / f
            low = f.lower()
            if low.endswith((".xml", ".pdf")):
                m = ISBN_RE.search(f)
                if m:
                    k = m.group(1)
                    loose_names.add(f.lower())
                    if low.endswith(".xml"):
                        found[k]["xml"] = str(p)
                    elif want_pdf and str(p) not in found[k]["pdf"]:
                        found[k]["pdf"].append(str(p))
    _take = take

    def take(name, data_fn, size):                               # noqa: F811  skip duplicates of loose files
        if Path(name).name.lower() in loose_names:
            return
        _take(name, data_fn, size)
    for dirpath, _d, files in os.walk(samples_dir):          # 2) zip packages (nested)
        for f in files:
            if f.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(str(Path(dirpath) / f)) as zf:
                        scan_zip(zf)
                except zipfile.BadZipFile:
                    continue
    for v in found.values():
        v["pdf"].sort()
    return dict(found)
