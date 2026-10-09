"""Opening a book: one PDF, or a folder of split PDFs (FM / CH / SEC / app / BM / Index).

Split deliveries are ordered by their part code and treated as a single book
with a global page index. File boundaries are kept as strong structural hints.
Book identity (ISBN, author prefix, edition) is taken from the file names
when present — the WK naming convention `Author{ed}e{ISBN}-{part}.pdf`
matches the ID prefix used in every golden sample.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

ISBN_RE = re.compile(r"(97[89]\d{10})")
NAME_RE = re.compile(r"^(?P<author>[A-Za-z][A-Za-z\-']*?)(?:(?P<ed>\d{1,2})e)?[-_ ]?(?P<isbn>97[89]\d{10})", re.I)
PART_RE = re.compile(r"[-_](?P<code>FM|BM|CH|SEC|SECT|PT|PART|UNIT|APPX|APP|INDEX|IDX|GLOSS|GL)[-_]?(?P<num>[A-Z]|\d{1,3})?(?=[-_.]|HR|$)", re.I)

ORDER = {"fm": 0, "pt": 1, "part": 1, "unit": 1, "sec": 1, "sect": 1, "ch": 1, "app": 5, "appx": 5,
         "gloss": 6, "gl": 6, "bm": 7, "index": 8, "idx": 8}


@dataclass
class PdfFile:
    path: Path
    code: str             # fm / ch / sec / app / bm / index / whole
    num: str | None
    pages: int
    sha: str
    outline: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class BookSource:
    root: Path
    files: list[PdfFile]
    isbn: str | None
    author: str | None
    edition: str | None
    total_pages: int
    page_map: list[tuple[int, int]]   # global -> (file idx, page no)

    @property
    def name(self) -> str:
        return self.isbn or self.root.stem

    def open(self, i: int):
        return pymupdf.open(str(self.files[i].path))

    def hash(self) -> str:
        h = hashlib.sha1()
        for f in self.files:
            h.update(f.sha.encode())
        return h.hexdigest()[:16]


def file_sha(path: Path, limit: int = 8 << 20) -> str:
    h = hashlib.sha1()
    h.update(str(path.stat().st_size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(limit))
    return h.hexdigest()


def ascii_fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def classify_file(path: Path) -> tuple[str, str | None]:
    stem = path.stem
    if re.search(r"[-_](IND|INDEX|IDX)(?=[-_.]|HR|$)|index", stem, re.I):
        return "index", None
    if re.search(r"[-_](GLOSS|GLOSSARY)", stem, re.I):
        return "gloss", None
    m = None
    for m in PART_RE.finditer(stem):
        pass
    if m:
        code = m.group("code").lower()
        if code in ("sect",):
            code = "sec"
        if code in ("idx",):
            code = "index"
        return code, m.group("num")
    return "whole", None


def _sort_key(pf: PdfFile):
    n = pf.num or "0"
    num = int(n) if n.isdigit() else (ord(n.upper()) - 64 if len(n) == 1 else 0)
    return (ORDER.get(pf.code, 1), num, pf.path.name.lower())


FOLIO_RE = re.compile(r"^\s*(\d{1,4})\s*$")


def first_folio(pf: PdfFile, pages: int = 4) -> int | None:
    """Printed page number of the file's first page (folio - page offset)."""
    try:
        doc = pymupdf.open(str(pf.path))
    except Exception:
        return None
    votes: dict[int, int] = {}
    try:
        for pno in range(min(pages, len(doc))):
            page = doc[pno]
            trim = page.trimbox
            zone = 0.1 * trim.height
            for b in page.get_text("blocks"):
                x0, y0, x1, y1, txt = b[:5]
                if y1 < trim.y0 + zone or y0 > trim.y1 - zone:
                    for part in txt.split():
                        if FOLIO_RE.match(part):
                            start = int(part) - pno
                            votes[start] = votes.get(start, 0) + 1
    finally:
        doc.close()
    return max(votes, key=votes.get) if votes else None


def _order_by_folio(files: list[PdfFile]):
    """Interleave part openers (SEC/PT) and chapters by their printed page numbers."""
    body = [f for f in files if ORDER.get(f.code, 1) == 1]
    if len(body) < 2 or len({f.code for f in body}) < 2:
        return
    starts = {id(f): first_folio(f) for f in body}
    if sum(1 for v in starts.values() if v is not None) < 0.8 * len(body):
        return
    keyed = sorted(body, key=lambda f: (starts[id(f)] if starts[id(f)] is not None else 10 ** 6,
                                        0 if f.code in ("pt", "part", "unit", "sec") else 1, _sort_key(f)))
    it = iter(keyed)
    for i, f in enumerate(files):
        if ORDER.get(f.code, 1) == 1:
            files[i] = next(it)


def load_book(path: Path) -> BookSource:
    path = Path(path)
    if path.is_dir():
        pdfs = sorted(p for p in path.glob("*.pdf") if not p.name.startswith("~"))
        if not pdfs:      # PDFs one level down (inputs/<ISBN>/PDF_HiRes_<ISBN>/*.pdf)
            pdfs = sorted(p for p in path.rglob("*.pdf") if not p.name.startswith("~"))
        root = path
    else:
        pdfs = [path]
        root = path
    files = []
    for p in pdfs:
        code, num = classify_file(p)
        try:
            doc = pymupdf.open(str(p))
        except Exception as e:  # pragma: no cover - broken pdf
            raise RuntimeError(f"Cannot open PDF {p.name}: {e}") from e
        files.append(PdfFile(p, code, num, len(doc), file_sha(p), doc.get_toc(simple=True), dict(doc.metadata or {})))
        doc.close()
    files.sort(key=_sort_key)
    _order_by_folio(files)
    isbn = author = edition = None
    for f in files:
        m = NAME_RE.match(f.path.stem)
        if m:
            author, edition, isbn = ascii_fold(m.group("author")), m.group("ed"), m.group("isbn")
            break
    if not isbn:
        m = ISBN_RE.search(root.name) or next((ISBN_RE.search(f.path.name) for f in files if ISBN_RE.search(f.path.name)), None)
        isbn = m.group(1) if m else None
    # the delivery folder names the ISBN being produced (e.g. a translated edition whose PDFs
    # still carry the original edition's ISBN in their file names): the folder wins
    folder_isbn = _folder_isbn(root if root.is_dir() else root.parent)
    if folder_isbn and folder_isbn != isbn:
        isbn = folder_isbn
    page_map = []
    for fi, f in enumerate(files):
        for pno in range(f.pages):
            page_map.append((fi, pno))
    return BookSource(root, files, isbn, author, edition, len(page_map), page_map)


def _folder_isbn(folder: Path) -> str | None:
    """ISBN in the name of the delivery folder or its parent (inputs/<ISBN>/PDF_HiRes_<ISBN>/...)."""
    for d in (folder, folder.parent):
        m = ISBN_RE.search(d.name)
        if m:
            return m.group(1)
    return None


def _is_split_delivery(pdfs: list[Path]) -> bool:
    codes = {classify_file(p)[0] for p in pdfs}
    isbns = {m.group(1) for p in pdfs for m in [ISBN_RE.search(p.name)] if m}
    return len(pdfs) > 1 and bool(codes - {"whole"}) and len(isbns) <= 1


def discover_books(path: Path, recursive: bool = True) -> list[Path]:
    """A 'book' is a PDF file, or a folder whose PDFs are split parts of one ISBN.

    A single part file (FM, SEC01, CH09, IND...) of a split delivery is not a book on its own:
    alone it can never give a valid BITS book (no book-body / front matter) and every part would
    overwrite the same <ISBN>.xml. It resolves to its folder, so the whole book is converted once."""
    path = Path(path)
    if path.is_file():
        if classify_file(path)[0] != "whole":
            sibs = sorted(p for p in path.parent.glob("*.pdf") if not p.name.startswith("~"))
            if path in sibs and _is_split_delivery(sibs):
                return [path.parent]
        return [path]
    books: list[Path] = []
    groups: dict[Path, list[Path]] = {}
    it = path.rglob("*.pdf") if recursive else path.glob("*.pdf")
    for p in sorted(it):
        groups.setdefault(p.parent, []).append(p)
    for folder, pdfs in sorted(groups.items()):
        if _is_split_delivery(pdfs):
            books.append(folder)          # split delivery -> one book
        else:
            books.extend(pdfs)
    return books
