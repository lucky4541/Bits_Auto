"""Intermediate document model.

Physical layer:  Span -> Line -> (Region) on PageInfo
Semantic layer:  Node tree (book > parts > chapters > secs > blocks)
Inline content:  list of inline items (Text, PageMark, Cite, FnRef, Break)

Everything is JSON-serialisable (document_structure.json) for debugging.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

# ----------------------------------------------------------------- physical


@dataclass
class Span:
    text: str
    font: str
    size: float
    bold: bool = False
    italic: bool = False
    sup: bool = False
    sub: bool = False
    sc: bool = False
    mono: bool = False
    color: int = 0
    bbox: tuple = (0, 0, 0, 0)
    origin: tuple | None = None
    glyphs: list = field(default_factory=list)

    def styles(self) -> frozenset:
        s = set()
        if self.bold:
            s.add("bold")
        if self.italic:
            s.add("italic")
        if self.sup:
            s.add("sup")
        if self.sub:
            s.add("sub")
        if self.sc:
            s.add("sc")
        if self.mono:
            s.add("monospace")
        return frozenset(s)


@dataclass
class Line:
    spans: list[Span]
    bbox: tuple
    page: int                     # global page index in the book
    role: str = "body"            # body/header/footer/folio/slug/figure-text/table-text/caption/...
    order: int = -1               # reading order within page
    column: int = 0
    conf: float = 1.0
    ocr: bool = False
    uid: str = ""                 # stable id "page:k" assigned after layout (zoning)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def x0(self):
        return self.bbox[0]

    @property
    def y0(self):
        return self.bbox[1]

    @property
    def x1(self):
        return self.bbox[2]

    @property
    def y1(self):
        return self.bbox[3]

    @property
    def main(self) -> Span:
        best = None
        for s in self.spans:
            if s.text.strip() and (best is None or len(s.text.strip()) > len(best.text.strip())) and not s.sup and not s.sub:
                best = s
        return best or self.spans[0]

    @property
    def size(self) -> float:
        return self.main.size

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    def all_bold(self) -> bool:
        chars = [s for s in self.spans if s.text.strip()]
        return bool(chars) and sum(len(s.text) for s in chars if s.bold) >= 0.85 * sum(len(s.text) for s in chars)

    def all_italic(self) -> bool:
        chars = [s for s in self.spans if s.text.strip()]
        return bool(chars) and sum(len(s.text) for s in chars if s.italic) >= 0.85 * sum(len(s.text) for s in chars)

    def to_json(self):
        return {"t": self.text, "bbox": [round(v, 1) for v in self.bbox], "page": self.page, "role": self.role,
                "order": self.order, "col": self.column, "font": self.main.font, "size": self.size,
                "bold": self.all_bold(), "italic": self.all_italic(), "conf": self.conf, "ocr": self.ocr}


@dataclass
class Region:
    kind: str                      # figure / table / image / drawing / box
    bbox: tuple
    page: int
    conf: float = 1.0
    meta: dict = field(default_factory=dict)


@dataclass
class PageInfo:
    index: int                     # global index
    pdf: str
    pdf_page: int
    width: float
    height: float
    trim: tuple
    folio: str | None = None
    folio_conf: float = 0.0
    is_ocr: bool = False
    ocr_conf: float | None = None
    lang: str | None = None
    columns: list = field(default_factory=list)   # [(x0,x1)]
    kind: str = "body"             # body/chapter-opener/toc/index/front/blank/figure-page/...
    rot: int = 0                   # 90 / -90 when the page content is typeset rotated (landscape tables)
    orig_size: tuple | None = None
    lines: list[Line] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    drawings: list[tuple] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)

    def to_json(self, with_lines=True):
        d = {"index": self.index, "pdf": self.pdf, "pdf_page": self.pdf_page, "size": [self.width, self.height],
             "trim": self.trim, "folio": self.folio, "folio_conf": self.folio_conf, "ocr": self.is_ocr,
             "ocr_conf": self.ocr_conf, "lang": self.lang, "columns": self.columns, "kind": self.kind,
             "regions": [asdict(r) for r in self.regions], "errors": self.errors,
             "diagnostics": self.diagnostics}
        if with_lines:
            d["lines"] = [l.to_json() for l in self.lines]
        return d


# ------------------------------------------------------------------ inline
# Inline items are small tuples-as-dicts to keep serialisation trivial.

def T(text: str, styles=frozenset()) -> dict:
    return {"k": "t", "text": text, "st": sorted(styles)}


def PageMark(folio: str, page: int) -> dict:
    return {"k": "pg", "folio": folio, "page": page}


def Cite(kind: str, keys: list, text: str, styles=frozenset(), ctx: str = "body") -> dict:
    """kind: fig/table/chapter/section/bibr/app/box/eq ; keys resolved in pass 2."""
    return {"k": "cite", "kind": kind, "keys": keys, "text": text, "st": sorted(styles), "ctx": ctx}


def FnRef(label: str, styles=frozenset()) -> dict:
    return {"k": "fnref", "label": label, "st": sorted(styles)}


def Break() -> dict:
    return {"k": "br"}


def inline_text(items: list[dict]) -> str:
    return "".join(i.get("text", "") if i["k"] in ("t", "cite", "math") else (i.get("label", "") if i["k"] == "fnref" else "")
                   for i in items)


# ---------------------------------------------------------------- semantic

@dataclass
class Node:
    kind: str
    attrs: dict = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    inlines: list[dict] = field(default_factory=list)      # p / title / label / caption-title / term / cell
    page: int | None = None
    bbox: tuple | None = None
    conf: float = 1.0
    meta: dict = field(default_factory=dict)
    id: str | None = None
    flags: list[str] = field(default_factory=list)
    parent: "Node | None" = field(default=None, repr=False, compare=False)

    # tree helpers ---------------------------------------------------------
    def add(self, child: "Node") -> "Node":
        child.parent = self
        self.children.append(child)
        return child

    def insert(self, index: int, child: "Node") -> "Node":
        child.parent = self
        self.children.insert(index, child)
        return child

    def remove(self, child: "Node"):
        self.children.remove(child)
        child.parent = None

    def walk(self) -> Iterator["Node"]:
        yield self
        for c in self.children:
            yield from c.walk()

    def find_all(self, kind: str) -> list["Node"]:
        return [n for n in self.walk() if n.kind == kind]

    def ancestors(self):
        p = self.parent
        while p is not None:
            yield p
            p = p.parent

    def ancestor(self, *kinds) -> "Node | None":
        for a in self.ancestors():
            if a.kind in kinds:
                return a
        return None

    def text(self) -> str:
        s = inline_text(self.inlines)
        for c in self.children:
            t = c.text()
            if t:
                s = (s + " " + t) if s else t
        return s

    def fix_parents(self):
        for c in self.children:
            c.parent = self
            c.fix_parents()

    def to_json(self) -> dict:
        d: dict[str, Any] = {"kind": self.kind}
        if self.id:
            d["id"] = self.id
        if self.attrs:
            d["attrs"] = self.attrs
        if self.inlines:
            d["inlines"] = self.inlines
        if self.page is not None:
            d["page"] = self.page
        if self.bbox:
            d["bbox"] = [round(v, 1) for v in self.bbox]
        if self.conf != 1.0:
            d["conf"] = round(self.conf, 3)
        if self.meta:
            d["meta"] = {k: v for k, v in self.meta.items() if k not in ("lines",)}
        if self.flags:
            d["flags"] = self.flags
        if self.children:
            d["children"] = [c.to_json() for c in self.children]
        return d


@dataclass
class Issue:
    code: str
    severity: str                  # info / warning / error / critical
    message: str
    page: int | None = None
    element: str | None = None
    target: str | None = None
    conf: float | None = None

    def to_json(self):
        return {k: v for k, v in asdict(self).items() if v is not None}


class IssueLog:
    def __init__(self):
        self.items: list[Issue] = []

    def add(self, code, severity, message, **kw):
        self.items.append(Issue(code, severity, message, **kw))

    def count(self, severity=None):
        return sum(1 for i in self.items if severity is None or i.severity == severity)

    def by_code(self):
        out: dict[str, int] = {}
        for i in self.items:
            out[i.code] = out.get(i.code, 0) + 1
        return out

    def to_json(self):
        return [i.to_json() for i in self.items]
