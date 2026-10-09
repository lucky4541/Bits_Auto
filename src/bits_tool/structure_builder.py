"""Semantic document tree construction.

stream (pages, lines, regions in reading order)
  -> units   (front matter parts, parts, chapters, appendices, index)
  -> blocks  (sec / p / list / fig / table-wrap / boxed-text / disp-formula / refs / fn)

Every decision records a confidence; nothing is dropped — uncertain content
is kept as a paragraph and flagged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .caption_detector import label_match
from .document_tree import IssueLog, Line, Node, PageInfo, Region, T
from .footnote_detector import mark_footnotes
from .heading_detector import HeadingModel, caps_ratio, is_candidate, learn_headings, norm_title, style_key
from .index_detector import TITLE_RE as INDEX_TITLE_RE
from .index_detector import parse_index
from .layout_analyzer import BookStyle, family
from .list_detector import marker, ordinal, strip_marker_from_runs
from .paragraph_detector import (Hyphenation, PageGeometry, append_line, despace_runs, finish_inlines, line_runs,
                                 page_mark_for, starts_new_paragraph)
from .reading_order import box_items, page_items
from .reference_parser import is_ref_heading, parse_citation, split_entries
from .toc_detector import TOC_TITLE_RE, parse_toc

CHAPTER_LABEL_RE = re.compile(r"^\s*(?P<word>chapter|cap[íi]tulo|kapitel|chapitre|cap\.)\s*(?P<num>\d{1,3}(?:\.\d{1,3})?|[IVXL]+)\b\.?", re.I)
PART_LABEL_RE = re.compile(r"^\s*(?P<word>part|parte|secci[óo]n|section|unit|unidad|sistema|m[óo]dulo|module)\s+(?P<num>\d{1,2}|[IVXL]+|[A-Z])\b", re.I)
APP_LABEL_RE = re.compile(r"^\s*(?P<word>appendix|ap[ée]ndice|anexo)\s*(?P<num>[A-Z]|\d{1,2}|[IVXL]+)?\b", re.I)
FM_TYPES = [
    (re.compile(r"^\s*(dedicatoria|dedication|dedicated|in memoriam|a mi|a mis|to my|for my)\b", re.I), "dedication"),
    (re.compile(r"^\s*(prefacio|preface|pr[óo]logo a la|prefacio a la)\b", re.I), "preface"),
    (re.compile(r"^\s*(pr[óo]logo|foreword|presentaci[óo]n)\b", re.I), "foreword"),
    (re.compile(r"^\s*(agradecimientos?|acknowledge?ments?)\b", re.I), "ack"),
    (re.compile(r"^\s*(colaboradores|contributors|contributing authors|revisores|reviewers|autores)\b", re.I), "contributors"),
    (re.compile(r"^\s*(abreviaturas|abbreviations|siglas)\b", re.I), "abbreviations"),
]
COPYRIGHT_RE = re.compile(r"copyright|©|isbn|all rights reserved|derechos reservados|reservados todos", re.I)
NAME_SPLIT_RE = re.compile(r"\s*(?:,|;|\sy\s|\sand\s|\se\s|&)\s*")


@dataclass
class Unit:
    kind: str                  # fm / part / chapter / app / index / bm
    start: int                 # global page index
    end: int
    title: str | None = None
    label: str | None = None
    number: str | None = None
    file_code: str | None = None
    children: list = field(default_factory=list)
    conf: float = 0.9
    start_line: object = None      # first line of the unit when it starts mid-page
    end_line: object = None        # first line of the NEXT unit when that one starts mid-page (exclusive)


def _looks_like_person(t: str) -> bool:
    """'Mark Turrentine', 'Michael A. Belfort': 2-5 capitalised words / initials, nothing else."""
    words = t.strip().split()
    return 2 <= len(words) <= 5 and all(re.fullmatch(r"[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü'\-]+|[A-Z]\.(?:[A-Z]\.)?", w) for w in words) \
        and not t.isupper()


_NAME_CONNECT = {"y", "and", "e", "de", "del", "la", "las", "los", "van", "von", "da", "do", "dos", "der", "di", "et", "al", "al."}


def _author_like(t: str) -> bool:
    """An author line is mostly capitalised name words (connectors and degrees aside), not running prose."""
    t = t.strip()
    if not t or not (t[0].isupper() or t[0] in "¿¡\"'(") or t.endswith((":", ";")):
        return False
    words = [w.strip(",.;()") for w in t.split()]
    words = [w for w in words if w and w.lower() not in _NAME_CONNECT and not re.fullmatch(r"[A-Z]{2,}[a-zA-Z]*|[A-Z]\.?", w)]
    if not words:
        return True
    cap = sum(1 for w in words if w[:1].isupper())
    return cap >= 0.6 * len(words)


def _bbox(lines):
    return (min(l.x0 for l in lines), min(l.y0 for l in lines), max(l.x1 for l in lines), max(l.y1 for l in lines))


def _runs_text(runs):
    return "".join(r.get("text", "") for r in runs)


def clip_runs(runs: list[dict], start: int, end: int | None = None) -> list[dict]:
    """Sub-range of inline runs by character offsets (styles preserved)."""
    out, pos = [], 0
    for r in runs:
        if r["k"] not in ("t", "math"):
            if end is None or pos < end:
                if pos >= start:
                    out.append(r)
            continue
        t = r["text"]
        a, b = pos, pos + len(t)
        lo, hi = max(a, start), min(b, end if end is not None else b)
        if lo < hi:
            if r["k"] == "math" and (lo != a or hi != b):
                out.append(T(t[lo - a:hi - a]))
            else:
                out.append({**r, "text": t[lo - a:hi - a]})
        pos = b
    return out


class BookBuilder:
    def __init__(self, src, pages: list[PageInfo], style: BookStyle, cfg, issues: IssueLog, log=None, progress=None):
        self.src = src
        self.pages = pages
        self.style = style
        self.cfg = cfg
        self.issues = issues
        self.log = log or (lambda *a, **k: None)
        self.progress = progress or (lambda *a, **k: None)
        self.hyph = Hyphenation()
        self.page_by_index = {p.index: p for p in pages}
        self.footnotes: dict[int, list[dict]] = {}
        self.file_start = []
        acc = 0
        for f in src.files:
            self.file_start.append(acc)
            acc += f.pages

    # ================================================================ public
    def _prepare(self):
        """Book-wide learning shared by auto structuring and the zoning editor's helpers."""
        for p in self.pages:
            self.footnotes[p.index] = mark_footnotes(p, self.style)
        flow_lines = [l for p in self.pages for k, o in page_items(p) if k == "line" for l in [o]]
        self.hyph.learn(flow_lines)
        by_page: dict[int, list[Line]] = {}
        for p in self.pages:
            by_page[p.index] = [l for l in p.lines if l.role in ("body", "box-text")]
        self.geo = PageGeometry(by_page, self.style)
        self.bookmarks, self.chapter_bm_level = self._bookmarks()
        self.headings: HeadingModel = learn_headings(flow_lines, self.style, self.bookmarks, self.chapter_bm_level)
        self.log("headings", styles=len(self.headings.style_levels), bookmarks=len(self.headings.bookmark_titles))
        units = self.detect_units()
        # re-learn heading styles on chapter content only (front matter / index typography must not
        # create heading tiers of its own)
        body_pages = {gi for u in units if u.kind in ("chapter", "app", "part") for gi in range(u.start, u.end + 1)}
        body_flow = [l for l in flow_lines if l.page in body_pages]
        if len(body_flow) > 200:
            self.headings = learn_headings(body_flow, self.style, self.bookmarks, self.chapter_bm_level)
        self.units = units

    def skeleton(self) -> Node:
        """Manual zoning: book structure without content (one chapter per source PDF)."""
        self._prepare()
        self.meta = {"copyright_lines": [], "title_lines": []}
        book = Node("book")
        body = book.add(Node("book-body"))
        for fi, f in enumerate(self.src.files):
            start = self.file_start[fi]
            ch = body.add(Node("book-part", {"book-part-type": "chapter"}, page=start,
                               meta={"label": None, "title": [T(f.path.stem)], "fpage": self._folio(start),
                                     "lpage": self._folio(start + f.pages - 1), "first_page": start}))
            ch.flags.append("MANUAL_TITLE")
            ch.add(Node("body"))
        book.meta.update(self.meta)
        return book

    def build(self) -> Node:
        self._prepare()
        units = self.units
        book = Node("book")
        fm = book.add(Node("front-matter"))
        body = book.add(Node("book-body"))
        back = book.add(Node("book-back"))
        self.meta = {"copyright_lines": [], "title_lines": []}
        current_part = None
        total = len(units)
        for ui, u in enumerate(units):
            self.progress("structure", ui / max(1, total), f"{u.kind} {u.title or ''}")
            try:
                if u.kind == "fm":
                    for n in self.build_front_matter(u):
                        fm.add(n)
                elif u.kind == "part":
                    current_part = self.build_part(u)
                    body.add(current_part)
                elif u.kind == "chapter":
                    ch = self.build_chapter(u)
                    if current_part is not None:
                        next(c for c in current_part.children if c.kind == "body").add(ch)
                        current_part.meta["lpage"] = self._folio(u.end) or current_part.meta.get("lpage")
                    else:
                        body.add(ch)
                elif u.kind == "app":
                    back.add(self.build_chapter(u, appendix=True))
                elif u.kind == "index":
                    back.add(self.build_index(u))
                else:
                    back.add(self.build_chapter(u, appendix=True))
            except Exception as e:  # a failing unit never kills the book
                import traceback
                self.issues.add("UNIT_FAILED", "error", f"{u.kind} p{u.start}-{u.end}: {e}", page=u.start)
                self.log("unit_failed", unit=u.kind, start=u.start, error=repr(e), trace=traceback.format_exc())
                fallback = Node("book-part", {"book-part-type": "chapter"}, meta={"title": [T(u.title or "Untitled")], "fpage": self._folio(u.start),
                                                                                  "lpage": self._folio(u.end), "failed": True})
                b = fallback.add(Node("body"))
                for p in self.pages[u.start:u.end + 1]:
                    for l in p.lines:
                        if l.role == "body":
                            b.add(Node("p", inlines=line_runs(l), page=p.index, conf=0.3, flags=["RECOVERED_TEXT"]))
                body.add(fallback)
        if not fm.children:
            book.remove(fm)
        if not back.children:
            book.remove(back)
        book.meta.update(self.meta)
        book.meta["units"] = [{"kind": u.kind, "start": u.start, "end": u.end, "title": u.title, "label": u.label} for u in units]
        return book

    # ============================================================ helpers
    def _folio(self, gi: int) -> str | None:
        p = self.page_by_index.get(gi)
        return p.folio if p else None

    def _bookmarks(self):
        items = []
        for fi, f in enumerate(self.src.files):
            for lvl, title, page in f.outline or []:
                if page and page > 0:
                    items.append((lvl, title.strip(), self.file_start[fi] + page - 1))
        if not items:
            return [], None
        levels = {}
        for lvl, title, _ in items:
            if re.match(r"^\s*(\d{1,3}[.:\s]|cap[íi]tulo\s+\d|chapter\s+\d)", title, re.I):
                levels[lvl] = levels.get(lvl, 0) + 1
        ch_level = max(levels, key=levels.get) if levels else None
        return items, ch_level

    def _items(self, start: int, end: int):
        out = []
        for gi in range(start, end + 1):
            p = self.page_by_index.get(gi)
            if p is None:
                continue
            out.append(("page", p))
            out.extend(page_items(p))
        return out

    def _unit_items(self, u: "Unit"):
        """Items of a unit, cut at line level where a chapter starts in the middle of a page."""
        items = self._items(u.start, u.end)
        if u.start_line is not None:
            idx = next((i for i, (k, o) in enumerate(items) if k == "line" and o is u.start_line), None)
            if idx is not None:
                pg = next((i for i in range(idx, -1, -1) if items[i][0] == "page"), 0)
                items = [items[pg]] + items[idx:]
        if u.end_line is not None:
            idx = next((i for i, (k, o) in enumerate(items) if k == "line" and o is u.end_line), None)
            if idx is not None:
                items = items[:idx]
        return items

    def _right_aligned(self, l: Line) -> bool:
        """Signature lines ('Michael W. Varner, MD' set flush right under a foreword) are not headings."""
        p = self.page_by_index.get(l.page)
        if p is None:
            return False
        cols = p.columns or [(p.trim[0], p.trim[2])]
        col = next(((a, b) for a, b in cols if a - 4 <= (l.x0 + l.x1) / 2 <= b + 4), cols[0])
        body = [o for o in p.lines if o.role == "body" and col[0] - 4 <= (o.x0 + o.x1) / 2 <= col[1] + 4 and len(o.text) > 40]
        if len(body) < 4:
            return False
        left = min(o.x0 for o in body)
        right = max(o.x1 for o in body)
        return l.x0 > left + 0.3 * (right - left) and abs(l.x1 - right) < 6

    def _item_continues(self, prev: Line, cur: Line) -> bool:
        """Inside a list item a non-marker line continues the item's paragraph: the hanging indent
        under the marker and inline bold/italic runs are not paragraph breaks; only a blank gap is."""
        if abs(prev.size - cur.size) > 1.0:
            return False
        if prev.page == cur.page and cur.y0 >= prev.y0 and cur.x0 <= prev.x1:
            gap = cur.y0 - prev.y1
            lead = max(0.0, self.style.line_pitch - prev.height)
            return gap <= lead + 0.45 * self.style.line_pitch
        return True      # column / page break inside the item

    def _flow_lines(self, start, end):
        return [o for k, o in self._items(start, end) if k == "line"]

    # ============================================================== units
    def detect_units(self) -> list[Unit]:
        units: list[Unit] = []
        files = self.src.files
        bm = self.bookmarks
        for fi, f in enumerate(files):
            s = self.file_start[fi]
            e = s + f.pages - 1
            if f.code == "fm":
                units.append(Unit("fm", s, e, file_code="fm"))
            elif f.code == "index":
                units.append(Unit("index", s, e, file_code="index"))
            elif f.code in ("app", "appx"):
                units.append(Unit("app", s, e, file_code="app", number=f.num))
            elif f.code in ("gloss", "gl"):
                units.append(Unit("app", s, e, file_code="gloss", title="Glossary"))
            elif f.code == "bm":
                units.extend(self._split_backmatter(s, e))
            elif f.code == "ch":
                sub = self._openers(s, e) if f.pages > 60 else []
                if len(sub) > 1:
                    units.extend(self._units_from_openers(sub, e, "chapter"))
                else:
                    units.append(Unit("chapter", s, e, file_code="ch", number=(f.num or "").lstrip("0") or None))
            elif f.code in ("sec", "pt", "part", "unit") and f.pages > 3 and len(self._label_starts(s, e)) >= 1:
                units.extend(self._units_from_labels(s, e, f))
            elif f.code in ("sec", "pt", "part", "unit"):
                ops = self._openers(s + 1, e) if f.pages > 3 else []
                part_end = (ops[0][0] - 1) if ops else e
                units.append(Unit("part", s, part_end, file_code=f.code, number=(f.num or "").lstrip("0") or None))
                if ops:
                    units.extend(self._units_from_openers(ops, e, "chapter"))
            else:  # whole book in one PDF
                units.extend(self._units_whole(s, e))
        # merge consecutive fm units and ensure ordering
        units.sort(key=lambda u: u.start)
        return units

    def _split_backmatter(self, s, e) -> list[Unit]:
        first = self._flow_lines(s, min(e, s + 1))[:6]
        txt = " ".join(l.text for l in first)
        if INDEX_TITLE_RE.search(txt) or re.search(r"\b(index|índice)\b", txt, re.I):
            return [Unit("index", s, e, file_code="bm")]
        return [Unit("app", s, e, file_code="bm")]

    def _units_whole(self, s, e) -> list[Unit]:
        bm = [b for b in self.bookmarks if s <= b[2] <= e]
        units: list[Unit] = []
        if bm and self.chapter_bm_level is not None:
            L = self.chapter_bm_level
            tops = [b for b in bm if b[0] <= L]
            first_ch = next((b for b in tops if b[0] == L and re.match(r"^\s*(\d{1,3}[.:\s]|cap[íi]tulo|chapter)", b[1], re.I)), None)
            fm_end = (first_ch[2] - 1) if first_ch else s - 1
            # front matter = everything before the first part/chapter
            first_struct = min([b[2] for b in tops if PART_LABEL_RE.match(b[1]) or b is first_ch] or [fm_end + 1])
            if first_struct > s:
                units.append(Unit("fm", s, first_struct - 1, file_code="whole"))
            starts = []
            for b in tops:
                if b[2] < first_struct:
                    continue
                t = b[1]
                if PART_LABEL_RE.match(t) and b[0] < L:
                    kind = "part"
                elif INDEX_TITLE_RE.match(t) or re.match(r"^\s*(índice alfab|index)\b", t, re.I):
                    kind = "index"
                elif APP_LABEL_RE.match(t):
                    kind = "app"
                elif b[0] == L or re.match(r"^\s*(\d{1,3}[.:\s]|cap[íi]tulo|chapter)", t, re.I):
                    kind = "chapter"
                else:
                    kind = "app"   # other back-matter parts (glossary...) become chapter-type back parts
                starts.append((b[2], kind, t))
            dedup = []
            for st in sorted(starts):
                if dedup and dedup[-1][0] == st[0]:
                    if st[1] == "chapter" and dedup[-1][1] == "part":
                        continue
                    dedup[-1] = st
                else:
                    dedup.append(st)
            for i, (pg, kind, t) in enumerate(dedup):
                end = (dedup[i + 1][0] - 1) if i + 1 < len(dedup) else e
                if kind == "part":
                    end = min(end, pg + 1) if i + 1 < len(dedup) else end
                num = None
                m = re.match(r"^\s*(?:cap[íi]tulo\s+|chapter\s+)?(\d{1,3})", t, re.I)
                if kind == "chapter" and m:
                    num = m.group(1)
                units.append(Unit(kind, pg, max(pg, end), title=t, number=num, file_code="whole", conf=0.95))
            # part openers extend only over their opener pages; chapters follow
            return units
        ops = self._openers(s, e)
        if not ops:
            return [Unit("chapter", s, e, file_code="whole", conf=0.4)]
        if ops[0][0] > s:
            units.append(Unit("fm", s, ops[0][0] - 1, file_code="whole"))
        units.extend(self._units_from_openers(ops, e, "chapter"))
        return units

    def _label_starts(self, s, e) -> list[tuple]:
        """Chapter starts marked by a display-size printed label ('Capítulo 4.2'), anywhere on the page.

        Returns (page, first_line, label_line, title_lines) in reading order."""
        cache = getattr(self, "_label_cache", None)
        if cache is None:
            cache = self._label_cache = {}
        if (s, e) in cache:
            return cache[(s, e)]
        em = self.style.body_size
        out = []
        for gi in range(s, e + 1):
            p = self.page_by_index.get(gi)
            if p is None:
                continue
            flow = [o for k, o in page_items(p) if k == "line"]
            for i, l in enumerate(flow):
                t = l.text.strip()
                if not (CHAPTER_LABEL_RE.match(t) and l.size >= 1.4 * em and len(t) < 30):
                    continue
                # title: display lines next to / just above / below the label
                near = [x for x in flow if x is not l and x.size >= max(1.4 * em, 0.9 * l.size) and abs(x.y0 - l.y0) < 60
                        and not CHAPTER_LABEL_RE.match(x.text.strip()) and not PART_LABEL_RE.match(x.text.strip())]
                group = [l] + near
                first = min(group, key=lambda x: flow.index(x))
                out.append((gi, first, l, sorted(near, key=lambda x: (x.y0, x.x0))))
        cache[(s, e)] = out
        return out

    def _units_from_labels(self, s, e, f) -> list[Unit]:
        starts = self._label_starts(s, e)
        units = []
        num = (f.num or "").lstrip("0") or None
        g0, first0 = starts[0][0], starts[0][1]
        part_end = g0 if self._has_lines_before(g0, first0) or g0 == s else g0 - 1
        part = Unit("part", s, part_end, file_code=f.code, number=num, conf=0.85)
        if part_end == g0:
            part.end_line = first0
        units.append(part)
        for i, (gi, first, lab, title_lines) in enumerate(starts):
            nxt = starts[i + 1] if i + 1 < len(starts) else None
            end = e
            end_line = None
            if nxt is not None:
                end = nxt[0] if self._has_lines_before(nxt[0], nxt[1]) else nxt[0] - 1
                if end == nxt[0]:
                    end_line = nxt[1]
            m = CHAPTER_LABEL_RE.match(lab.text.strip())
            title = " ".join(x.text.strip() for x in title_lines)[:200] or None
            units.append(Unit("chapter", gi, max(gi, end), title=title, label=lab.text.strip(),
                              number=m.group("num") if m else None, file_code=f.code, conf=0.9,
                              start_line=first if self._has_lines_before(gi, first) or gi == part_end else None,
                              end_line=end_line))
        return units

    def _has_lines_before(self, gi, line) -> bool:
        p = self.page_by_index.get(gi)
        if p is None:
            return False
        flow = [o for k, o in page_items(p) if k == "line"]
        return line in flow and flow.index(line) > 0

    def _units_from_openers(self, ops, end, default_kind):
        units = []
        for i, (pg, kind, title, label) in enumerate(ops):
            e = (ops[i + 1][0] - 1) if i + 1 < len(ops) else end
            m = CHAPTER_LABEL_RE.match(label or "") or re.match(r"^\s*(\d{1,3})\s*$", label or "")
            units.append(Unit(kind or default_kind, pg, e, title=title, label=label,
                              number=(m.group(m.lastindex) if m else None), conf=0.75))
        return units

    def _openers(self, s, e) -> list[tuple]:
        """Pages that open a chapter/part: display-size text at the top, before any body text."""
        out = []
        for gi in range(s, e + 1):
            p = self.page_by_index.get(gi)
            if p is None:
                continue
            flow = [o for k, o in page_items(p) if k == "line"]
            if not flow:
                continue
            top = flow[:6]
            big = [l for l in top if l.size >= 1.7 * self.style.body_size]
            lab = next((l for l in top if CHAPTER_LABEL_RE.match(l.text) or PART_LABEL_RE.match(l.text)), None)
            if not big and not lab:
                continue
            first_body = next((i for i, l in enumerate(flow) if abs(l.size - self.style.body_size) < 0.6 and len(l.text) > 40), len(flow))
            big_before = [l for l in flow[:first_body] if l.size >= 1.7 * self.style.body_size]
            if not big_before and not lab:
                continue
            ph = p.trim[3] - p.trim[1]
            if big_before and min(l.y0 for l in big_before) > p.trim[1] + 0.55 * ph:
                continue
            kind = "part" if lab is not None and PART_LABEL_RE.match(lab.text) else "chapter"
            if kind == "chapter" and len([l for l in flow if len(l.text) > 40]) < 2 and not lab:
                continue
            title_lines = [l for l in big_before if not re.fullmatch(r"\s*\d{1,3}\s*", l.text)] or big_before
            title = " ".join(l.text.strip() for l in title_lines)[:200]
            label = lab.text.strip() if lab else next((l.text.strip() for l in big_before if re.fullmatch(r"\s*\d{1,3}\s*", l.text)), None)
            out.append((gi, kind, title, label))
        return out

    # ========================================================= chapter-ish
    def _opener_header(self, items):
        """Split leading display lines (label/title/subtitle/authors/outline) from the body."""
        lines = [o for k, o in items if k == "line"]
        head, i = [], 0
        em = self.style.body_size
        while i < len(lines) and i < 40:
            l = lines[i]
            if abs(l.size - em) < 0.6 and len(l.text) > 50 and not l.all_bold() and family(l.main.font) == self.style.body_family:
                break
            if l.page != lines[0].page:
                break
            head.append(l)
            i += 1
        # section headings that sit just above the first paragraph belong to the body, not to the opener
        if head:
            top = max(h.size for h in head)
            while head and head[-1].size < 0.85 * top and \
                    self.headings.level_for(head[-1], head[-1].text.strip(), self.style) is not None and \
                    not (len(head) > 1 and _looks_like_person(head[-1].text) and head[-1].y0 - head[-2].y1 < 0.8 * head[-1].size):
                head.pop()
        return head

    def _classify_head(self, head: list[Line]):
        label = title = subtitle = None
        authors, outline, rest = [], [], []
        if not head:
            return label, title, subtitle, authors, outline, rest
        sizes = sorted({round(l.size, 1) for l in head}, reverse=True)
        num_only = [l for l in head if re.fullmatch(r"\s*\d{1,3}\s*", l.text)]
        word_only = next((l for l in head if re.fullmatch(r"\s*(chapter|cap[íi]tulo|unit|unidad|part|parte|secci[óo]n|section|appendix|ap[ée]ndice)\s*", l.text, re.I)), None)
        if word_only is not None and num_only:
            n0 = num_only[0]
            label = Line(spans=[type(word_only.spans[0])(**{**word_only.spans[0].__dict__, "text": f"{word_only.text.strip()} {n0.text.strip()}"})],
                         bbox=word_only.bbox, page=word_only.page, uid=word_only.uid)
            head = [h for h in head if h is not word_only and h is not n0]
            sizes = sorted({round(l.size, 1) for l in head}, reverse=True) or [0]
        non_label = [h for h in head if not (CHAPTER_LABEL_RE.match(h.text) or PART_LABEL_RE.match(h.text) or APP_LABEL_RE.match(h.text)
                                             or re.fullmatch(r"\s*\d{1,3}\s*", h.text))]
        big = max((round(h.size, 1) for h in non_label), default=sizes[0])
        title_lines = []
        for l in head:
            t = l.text.strip()
            if label is None and (CHAPTER_LABEL_RE.match(t) or PART_LABEL_RE.match(t) or APP_LABEL_RE.match(t) or
                                  re.fullmatch(r"\d{1,3}", t)):
                label = l
                continue
            m = re.match(r"^\s*(\d{1,3})\s+(\S.*)$", t)
            if label is None and m and l.size >= 1.5 * self.style.body_size:
                # '1 Bases neurofisiológicas' : number + title on one line
                label = Line(spans=[l.spans[0]], bbox=l.bbox, page=l.page, uid=l.uid)
                label.spans = [type(l.spans[0])(**{**l.spans[0].__dict__, "text": m.group(1)})]
                title_lines.append(("cut", l, len(t) - len(m.group(2))))
                continue
            if l.size >= big - 0.5 or (title_lines and l.size >= 0.85 * big and not outline):
                title_lines.append(("full", l, 0))
            elif title_lines and not outline and l.size > self.style.body_size * 1.05 and len(t) < 120 and not marker(t):
                if (re.search(r"\b(y|and|et al)\b|,|\b(MD|PhD|RN|DO|Dr\.?)\b", t) or _looks_like_person(t)) and caps_ratio(t) < 0.6 \
                        and _author_like(t):
                    authors.append(l)
                elif subtitle is None:
                    subtitle = l
                else:
                    rest.append(l)
            elif title_lines and re.search(r"\b(y|and)\b|,", t) and not marker(t) and len(t) < 90 and caps_ratio(t) < 0.6 and not outline \
                    and _author_like(t):
                authors.append(l)
            elif re.match(r"^\s*(?:[IVXL]+|[A-Z]|\d{1,2})[.)]\s", t) or outline:
                outline.append(l)
            else:
                rest.append(l)
        if title_lines:
            title = title_lines
        return label, title, subtitle, authors, outline, rest

    def _title_inlines(self, title_lines):
        runs = []
        for kind, l, cut in title_lines:
            r = line_runs(l)
            if kind == "cut":
                r = clip_runs(r, cut)
            if runs:
                runs.append(T(" "))
            runs.extend(r)
        return finish_inlines(runs)

    def build_part(self, u: Unit) -> Node:
        items = self._unit_items(u)
        head = self._opener_header(items)
        label, title, subtitle, authors, outline, rest = self._classify_head(head)
        lab_text = label.text.strip() if label else (u.label or None)
        if not title and u.title:
            m = PART_LABEL_RE.match(u.title)
            ttxt = u.title[m.end():].lstrip(" :.-") if m else u.title
            if m and not lab_text:
                lab_text = u.title[:m.end()].strip()
            title_inl = [T(ttxt)]
        else:
            title_inl = self._title_inlines(title) if title else [T(u.title or "")]
        node = Node("book-part", {"book-part-type": "part"}, page=u.start,
                    meta={"label": lab_text, "title": title_inl, "fpage": self._folio(u.start), "lpage": self._folio(u.end),
                          "part_word": (PART_LABEL_RE.match(lab_text or "") or PART_LABEL_RE.match(u.title or "") or None) and
                          (PART_LABEL_RE.match(lab_text or "") or PART_LABEL_RE.match(u.title or "")).group("word").lower(),
                          "first_page": u.start})
        node.meta["_head_ln"] = [l.uid for l in head if l.uid]
        body = node.add(Node("body"))
        consumed = {id(l) for l in head}
        rest_items = [(k, o) for k, o in items if not (k == "line" and id(o) in consumed)]
        blocks = self.parse_blocks(rest_items, allow_secs=False)
        for b in blocks["body"]:
            body.add(b)
        return node

    def build_chapter(self, u: Unit, appendix: bool = False) -> Node:
        items = self._unit_items(u)
        head = self._opener_header(items)
        label, title, subtitle, authors, outline, rest = self._classify_head(head)
        lab_text = label.text.strip() if label else None
        if title:
            title_inl = self._title_inlines(title)
        elif u.title:
            t = u.title
            m = re.match(r"^\s*(?:cap[íi]tulo\s+|chapter\s+)?(\d{1,3})[.:\s]+(.*)$", t, re.I)
            if m:
                lab_text = lab_text or m.group(1)
                t = m.group(2)
            title_inl = [T(t)]
            self.issues.add("CHAPTER_TITLE_FROM_BOOKMARK", "info", f"title taken from bookmark: {u.title}", page=u.start)
        else:
            title_inl = [T(u.title or "")]
            self.issues.add("CHAPTER_TITLE_NOT_FOUND", "warning", "chapter title not detected", page=u.start)
        if appendix and not lab_text and u.number:
            lab_text = None
        node = Node("book-part", {"book-part-type": "chapter"}, page=u.start,
                    meta={"label": lab_text, "title": title_inl, "subtitle": line_runs(subtitle) if subtitle else None,
                          "authors": [a.text.strip() for a in authors], "fpage": self._folio(u.start), "lpage": self._folio(u.end),
                          "appendix": appendix, "number": u.number, "first_page": u.start, "conf": u.conf})
        consumed = {id(l) for l in head if l not in rest}
        node.meta["_head_ln"] = [l.uid for l in head if l not in rest and l.uid and l not in (outline or [])]
        if outline:
            node.meta["outline"] = [{"text": l.text.strip(), "runs": line_runs(l)} for l in outline]
        rest_items = [(k, o) for k, o in items if not (k == "line" and id(o) in consumed)]
        ttxt = _runs_text(title_inl)
        refs_root = bool(is_ref_heading(ttxt))
        blocks = self.parse_blocks(rest_items, allow_secs=True, refs_root=refs_root)
        body = node.add(Node("body"))
        for b in blocks["body"]:
            body.add(b)
        back_nodes = blocks["refs"]
        fns = [fn for gi in range(u.start, u.end + 1) for fn in self.footnotes.get(gi, [])]
        if back_nodes or fns:
            back = node.add(Node("back"))
            if refs_root and len(back_nodes) > 1:
                wrapper = back.add(Node("ref-list", meta={"title": []}))
                for r in back_nodes:
                    wrapper.add(r)
            else:
                for r in back_nodes:
                    back.add(r)
            if fns:
                fg = back.add(Node("fn-group"))
                for fn in fns:
                    runs = []
                    for l in fn["lines"]:
                        append_line(runs, l, self.hyph)
                    runs = finish_inlines(runs)
                    lab = fn["label"]
                    runs = [r for r in runs]
                    txt = _runs_text(runs)
                    if txt.startswith(lab):
                        runs = clip_runs(runs, len(lab))
                        runs = finish_inlines(runs)
                    fg.add(Node("fn", meta={"label": lab}, children=[], page=fn["page"])).add(Node("p", inlines=runs, page=fn["page"]))
        if not body.children:
            node.remove(body)
        return node

    # ================================================================ blocks
    def parse_blocks(self, items, allow_secs=True, in_box=False, refs_root=False) -> dict:
        """Core block parser: headings, paragraphs, lists, regions, equations, references."""
        root: list[Node] = []
        refs: list[Node] = []
        stack: list[tuple[int, Node]] = []
        state = {"para": None, "last": None, "lists": [], "floats": [], "marks": []}
        em = self.style.body_size

        def container() -> list[Node] | Node:
            return stack[-1][1] if stack else None

        def emit(n: Node):
            c = container()
            if c is None:
                root.append(n)
            else:
                c.add(n)

        def close_para():
            p = state["para"]
            if p is not None:
                p.inlines = finish_inlines(p.inlines)
                if not p.inlines and not p.children:
                    if p.parent is not None:
                        p.parent.remove(p)
                    elif p in root:
                        root.remove(p)
            state["para"] = None
            if state["floats"]:
                fl = state["floats"]
                state["floats"] = []
                for f in fl:
                    if state["lists"]:
                        # a float inside a list item is appended to the item's last paragraph (sample convention)
                        item = state["lists"][-1]["item"]
                        target = next((c for c in reversed(item.children) if c.kind == "p"), None)
                        if target is not None:
                            target.add(f)
                            continue
                    emit(f)

        def close_lists(to_depth=0):
            close_para()
            while len(state["lists"]) > to_depth:
                state["lists"].pop()

        def take_marks(p: Node):
            if state["marks"]:
                p.inlines.extend(state["marks"])
                state["marks"] = []

        lines_only = [(i, o) for i, (k, o) in enumerate(items) if k == "line"]
        next_line_of = {}
        for j, (i, o) in enumerate(lines_only):
            next_line_of[i] = lines_only[j + 1][1] if j + 1 < len(lines_only) else None
        idx = 0
        refs_level = 0 if refs_root else None
        ref_lines: list = []
        ref_title = None
        n_items = len(items)
        skip = set()
        while idx < n_items:
            k, o = items[idx]
            if idx in skip:
                idx += 1
                continue
            if k == "page":
                mk = page_mark_for(o)
                if mk:
                    if refs_level is not None:
                        ref_lines.append(("mark", mk))
                    else:
                        state["marks"].append(mk)
                idx += 1
                continue
            if k == "region":
                if o.kind == "equation":
                    close_para()
                node = self.region_node(o)
                if node is None:
                    idx += 1
                    continue
                if node.kind == "p" and node.children and node.children[0].kind == "inline-graphic":
                    if state["para"] is not None:
                        state["para"].add(node.children[0])     # icon inside the running paragraph
                    else:
                        close_lists()
                        emit(node)
                    idx += 1
                    continue
                if refs_level is not None:
                    ref_lines.append(("region", node))
                elif state["para"] is not None and node.kind != "boxed-text":
                    state["floats"].append(node)       # paragraph may continue after the float
                else:
                    close_para()
                    if state["lists"] and node.kind in ("fig", "table-wrap"):
                        item = state["lists"][-1]["item"]
                        target = next((c for c in reversed(item.children) if c.kind == "p"), None)
                        if target is not None:
                            target.add(node)
                            idx += 1
                            continue
                    close_lists()
                    emit(node)
                idx += 1
                continue
            l: Line = o
            nxt = next_line_of.get(idx)
            prev = state["last"]
            text = l.text.strip()
            # ---------------------------------------------------- headings
            lvl = None
            if allow_secs and l.size < 1.8 * self.style.body_size and is_candidate(l, nxt, prev, self.style) \
                    and not self._right_aligned(l):
                lvl = self.headings.level_for(l, text, self.style)
            if lvl is None and allow_secs and is_ref_heading(text) and (l.all_bold() or caps_ratio(text) > 0.8 or l.size > em + 0.5
                                                                          or family(l.main.font) != self.style.body_family):
                lvl = stack[-1][0] + 1 if stack else 1
                lvl = min(lvl, 2) if refs_level is None else lvl
            if lvl is not None and not in_box:
                # multi-line heading: absorb following lines of the same style that sit right below
                hl = [l]
                j = idx + 1
                while j < n_items and items[j][0] == "line":
                    c = items[j][1]
                    if style_key(c, self.style) != style_key(l, self.style) or c.page != l.page or c.y0 - hl[-1].y1 > 0.6 * self.style.line_pitch:
                        break
                    if self.headings.level_for(c, c.text.strip(), self.style) is None and not is_candidate(c, None, None, self.style):
                        break
                    hl.append(c)
                    skip.add(j)
                    j += 1
                title_runs = []
                for h in hl:
                    append_line(title_runs, h, self.hyph)
                title_runs = despace_runs(finish_inlines(title_runs))
                ttext = _runs_text(title_runs)
                if refs_level is not None and refs_root:
                    refs.extend(self.ref_list(ref_lines, ref_title))
                    ref_lines, ref_title = [], title_runs
                    state["last"] = hl[-1]
                    idx += 1
                    continue
                if refs_level is not None:
                    if lvl <= refs_level:
                        refs.extend(self.ref_list(ref_lines, ref_title))
                        refs_level, ref_lines, ref_title = None, [], None
                    else:
                        ref_lines.append(("line", l))
                        for h in hl[1:]:
                            ref_lines.append(("line", h))
                        state["last"] = hl[-1]
                        idx += 1
                        continue
                close_lists()
                if is_ref_heading(ttext):
                    refs_level = lvl
                    ref_title = title_runs
                    ref_lines = []
                    if state["marks"]:
                        ref_lines.extend(("mark", m) for m in state["marks"])
                        state["marks"] = []
                    state["last"] = hl[-1]
                    idx += 1
                    continue
                while stack and stack[-1][0] >= lvl:
                    stack.pop()
                sec = Node("sec", {"disp-level": str(lvl)}, page=l.page, bbox=_bbox(hl),
                           conf=0.9 if norm_title(ttext) in self.headings.bookmark_titles else 0.8)
                sec.meta["title"] = (state["marks"] + title_runs) if state["marks"] else title_runs
                state["marks"] = []
                sec.meta["title_text"] = ttext
                emit(sec)
                stack.append((lvl, sec))
                state["last"] = hl[-1]
                idx += 1
                continue
            if refs_level is not None:
                ref_lines.append(("line", l))
                state["last"] = l
                idx += 1
                continue
            # -------------------------------------------------------- lists
            mk = marker(l.text)
            if mk and not (lvl is None and len(text) < 3):
                ltype, mtext, rest_text = mk
                x = l.x0
                depth = None
                for d, lst in enumerate(state["lists"]):
                    if abs(lst["x"] - x) <= 0.6 * em:
                        depth = d
                        break
                if depth is None:
                    if state["lists"] and x > state["lists"][-1]["x"] + 0.6 * em:
                        depth = len(state["lists"])       # nested
                    elif state["lists"] and x < state["lists"][0]["x"] - 0.6 * em:
                        close_lists()
                        depth = 0
                    elif state["lists"]:
                        depth = len(state["lists"]) - 1
                    else:
                        depth = 0
                close_para()
                if depth < len(state["lists"]) and state["lists"][depth]["type"] != ltype and \
                        not (ltype == "alpha-lower" and state["lists"][depth]["type"] == "roman-lower"):
                    # a different marker type at the same depth starts a new list
                    del state["lists"][depth:]
                if depth < len(state["lists"]):
                    del state["lists"][depth + 1:]
                    lst = state["lists"][depth]
                else:
                    lst_node = Node("list", {"list-type": ltype}, page=l.page, conf=0.85)
                    if depth == 0:
                        emit(lst_node)
                    else:
                        parent_item = state["lists"][-1]["item"]
                        parent_item.add(lst_node)
                    lst = {"x": x, "type": ltype, "node": lst_node, "item": None}
                    state["lists"].append(lst)
                item = lst["node"].add(Node("list-item", page=l.page))
                if ltype == "simple":
                    item.meta["label"] = mtext
                lst["item"] = item
                runs = line_runs(l)
                cut = len(l.text) - len(l.text.lstrip()) + (len(l.text.lstrip()) - len(rest_text))
                runs = strip_marker_from_runs(runs, cut)
                p = item.add(Node("p", page=l.page, bbox=l.bbox))
                take_marks(p)
                p.inlines.extend(runs)
                lst["text_x"] = l.x0 + max(0.0, l.x1 - l.x0) * 0.0
                state["para"] = p
                state["last"] = l
                idx += 1
                continue
            # --------------------------------------------------- paragraphs
            if state["lists"]:
                lst = state["lists"][-1]
                broke = prev is not None and (prev.page != l.page or l.y0 < prev.y0)
                in_item = (l.x0 > lst["x"] + 0.4 * em and not broke) or \
                    (prev is not None and not starts_new_paragraph(prev, l, self.geo, self.style)[0]) or \
                    (broke and (l.text.strip()[:1].islower() or not re.search(r"[.:;!?]\s*$", prev.text.rstrip())))
                # (after a page / column break the margins move, so x positions can't be compared: a line
                #  starting in lower case or following an unfinished sentence continues the item)
                if not in_item:
                    # fell back to a shallower indentation: close lists deeper than this x
                    keep = [d for d, s_ in enumerate(state["lists"]) if l.x0 > s_["x"] + 0.4 * em]
                    close_lists(len(keep))
                if state["lists"]:
                    item = state["lists"][-1]["item"]
                    if state["para"] is not None and prev is not None and self._item_continues(prev, l):
                        marks = state["marks"]
                        state["marks"] = []
                        append_line(state["para"].inlines, l, self.hyph, None)
                        if marks:
                            # page break inside the item: the page target goes exactly where the new page starts
                            pos = len(state["para"].inlines) - len(line_runs(l))
                            for m in reversed(marks):
                                state["para"].inlines.insert(max(0, pos), m)
                        state["last"] = l
                        idx += 1
                        continue
                    close_para()
                    p = item.add(Node("p", page=l.page, bbox=l.bbox))
                    take_marks(p)
                    p.inlines.extend(line_runs(l))
                    state["para"] = p
                    state["last"] = l
                    idx += 1
                    continue
            new, conf = (True, 0.95) if state["para"] is None or prev is None else starts_new_paragraph(prev, l, self.geo, self.style)
            if new:
                close_para()
                p = Node("p", page=l.page, bbox=l.bbox, conf=conf)
                take_marks(p)
                p.inlines.extend(line_runs(l))
                emit(p)
                state["para"] = p
            else:
                marks = state["marks"]
                state["marks"] = []
                if marks:
                    # page break inside the paragraph: keep the target at the exact break position
                    append_line(state["para"].inlines, l, self.hyph, None)
                    # insert marks before the runs of this line
                    runs_len = len(line_runs(l))
                    pos = len(state["para"].inlines) - runs_len
                    for m in reversed(marks):
                        state["para"].inlines.insert(max(0, pos), m)
                else:
                    append_line(state["para"].inlines, l, self.hyph, None)
                state["para"].conf = min(state["para"].conf, conf)
            state["last"] = l
            idx += 1
        close_lists()
        close_para()
        if refs_level is not None:
            refs.extend(self.ref_list(ref_lines, ref_title))
        if state["marks"]:
            # trailing page marks with no following text: attach to the last paragraph
            tail = self._last_text_node(root)
            if tail is not None:
                tail.inlines.extend(state["marks"])
            else:
                root.append(Node("p", inlines=state["marks"], flags=["PAGE_TARGET_ONLY"]))
        return {"body": root, "refs": refs}

    @staticmethod
    def _last_text_node(nodes):
        for n in reversed(nodes):
            for m in reversed(list(n.walk())):
                if m.kind in ("p",):
                    return m
        return None

    # ============================================================ regions
    def region_node(self, r: Region) -> Node | None:
        if r.kind == "equation":
            lines = r.meta["lines"]
            meta = {**r.meta, "_head_ln": [l.uid for l in lines if l.uid]}
            meta.pop("lines", None)
            flags = []
            if not meta.get("mathml"):
                flags.append("EQUATION_NEEDS_REVIEW")
                self.issues.add("EQUATION_NEEDS_REVIEW", "warning", meta["reason"], page=r.page, conf=r.conf)
            return Node("disp-formula", page=r.page, bbox=r.bbox, conf=r.conf, meta=meta, flags=flags)
        if r.kind == "figure":
            n = Node("fig", page=r.page, bbox=r.bbox, conf=r.conf)
            n.meta.update({"label": r.meta.get("label"), "key": r.meta.get("key"), "art": r.meta.get("art"),
                           "unlabeled": r.meta.get("unlabeled", False), "direction": r.meta.get("direction"),
                           "diagram": r.meta.get("diagram"), "asset_format": r.meta.get("asset_format")})
            cap = r.meta.get("caption") or []
            if cap:
                runs = []
                for c in cap:
                    append_line(runs, c, self.hyph)
                runs = finish_inlines(runs)
                lab = r.meta.get("label") or ""
                txt = _runs_text(runs)
                cut = txt.find(lab) + len(lab) if lab and lab in txt else 0
                n.meta["caption"] = finish_inlines(clip_runs(runs, cut))
            if not r.meta.get("art"):
                n.flags.append("FIGURE_ARTWORK_NOT_FOUND")
                self.issues.add("FIGURE_ARTWORK_NOT_FOUND", "warning", f"no artwork found for {r.meta.get('label')}", page=r.page, conf=0.5)
                # No inferred crop: keep the caption and report the missing artwork.
            if r.meta.get("absorbed"):
                n.meta["art_text"] = " ".join(a.text.strip() for a in r.meta["absorbed"])
            return n
        if r.kind == "table":
            n = Node("table-wrap", {"specific-use": "inline"}, page=r.page, bbox=r.bbox, conf=r.conf)
            n.meta.update({"label": r.meta.get("label"), "key": r.meta.get("key"), "continued": r.meta.get("continued", False),
                           "body_bbox": r.meta.get("body_bbox"), "unlabeled": r.meta.get("unlabeled", False)})
            cap = r.meta.get("caption") or []
            if cap:
                runs = []
                for c in cap:
                    append_line(runs, c, self.hyph)
                runs = finish_inlines(runs)
                lab = r.meta.get("label") or ""
                txt = _runs_text(runs)
                cut = txt.find(lab) + len(lab) if lab and lab in txt else 0
                n.meta["caption"] = finish_inlines(clip_runs(runs, cut))
            grid = r.meta.get("grid")
            if grid is not None and grid.conf >= 0.5 and (grid.ncols >= 2 or len(r.meta.get("lines", [])) >= 2):
                rows = []
                for row in grid.rows:
                    cells = []
                    for c in row:
                        cl_sorted = sorted(c.lines, key=lambda x: (x.y0, x.x0))
                        has_list = sum(1 for cl in cl_sorted if marker(cl.text)) >= 2
                        if has_list or (grid.ncols == 1 and len(cl_sorted) > 1):
                            # block content inside the cell (lists / several paragraphs), as in the samples
                            inner = self.parse_blocks([("line", cl) for cl in cl_sorted], allow_secs=False, in_box=True)
                            cells.append({"runs": [], "blocks": inner["body"], "colspan": c.colspan, "rowspan": c.rowspan, "header": c.header})
                            continue
                        runs = []
                        for cl in cl_sorted:
                            append_line(runs, cl, self.hyph)
                        cells.append({"runs": finish_inlines(runs), "colspan": c.colspan, "rowspan": c.rowspan, "header": c.header})
                    rows.append(cells)
                n.meta["rows"] = rows
                n.meta["header_rows"] = grid.header_rows
                n.meta["ncols"] = grid.ncols
                if grid.conf < self.cfg.get("confidence.warn", 0.75):
                    self.issues.add("LOW_CONFIDENCE_TABLE", "warning", f"{r.meta.get('label') or 'table'} conf {grid.conf}", page=r.page, conf=grid.conf)
            else:
                n.flags.append("TABLE_STRUCTURAL_EXTRACTION_FAILED")
                n.meta["fallback_image"] = r.meta.get("body_bbox") or r.bbox
                n.meta["fallback_text"] = [l.text for l in r.meta.get("lines", [])]
                self.issues.add("TABLE_STRUCTURAL_EXTRACTION_FAILED", "warning",
                                f"{r.meta.get('label') or 'table'}: kept as image + flagged", page=r.page, conf=r.conf)
            foot = r.meta.get("foot") or []
            if foot:
                fnodes = []
                cur = None
                for fl in foot:
                    m = re.match(r"^\s*([a-z]|[*†‡§¶]+|\d{1,2})[.)]?\s+", fl.text)
                    starts_note = bool(m) and (fl.spans[0].sup or len(m.group(1)) <= 2)
                    if starts_note or cur is None:
                        cur = {"label": m.group(1) if starts_note else None, "lines": [fl]}
                        fnodes.append(cur)
                    else:
                        cur["lines"].append(fl)
                out = []
                for f in fnodes:
                    runs = []
                    for fl in f["lines"]:
                        append_line(runs, fl, self.hyph)
                    runs = finish_inlines(runs)
                    if f["label"]:
                        txt = _runs_text(runs)
                        runs = finish_inlines(clip_runs(runs, txt.find(f["label"]) + len(f["label"])))
                        runs = finish_inlines([{**runs[0], "text": runs[0]["text"].lstrip(".) ")}] + runs[1:]) if runs else runs
                    out.append({"label": f["label"], "runs": runs})
                n.meta["foot"] = out
            return n
        if r.kind == "box":
            n = Node("boxed-text", page=r.page, bbox=r.bbox, conf=r.conf)
            page = self.page_by_index[r.page]
            its = box_items(page, r)
            lines = [o for k, o in its if k == "line"]
            title_runs = None
            label = None
            if lines:
                first = lines[0]
                lk, lm = label_match(first.text)
                distinct = first.all_bold() or caps_ratio(first.text) > 0.8 or first.size > self.style.body_size + 0.5 or \
                    family(first.main.font) != family(lines[1].main.font if len(lines) > 1 else first.main.font)
                if (distinct and len(first.text) < 120) or lk == "box":
                    runs = finish_inlines(line_runs(first))
                    if lk == "box":
                        label = lm.group(0).strip()
                        runs = finish_inlines(clip_runs(runs, len(lm.group(0))))
                    title_runs = runs
                    its = [(k, o) for k, o in its if o is not first]
            from .sidebar_detector import box_type
            n.attrs["content-type"] = box_type((label or "") + " " + _runs_text(title_runs or []))
            n.meta["label"] = label
            n.meta["title"] = title_runs
            inner = self.parse_blocks(its, allow_secs=False, in_box=True)
            for b in inner["body"]:
                n.add(b)
            if not n.children:
                n.add(Node("p", inlines=[], flags=["EMPTY_BOX"]))
            return n
        if r.kind == "inline-graphic":
            p = Node("p", page=r.page, bbox=r.bbox)
            p.add(Node("inline-graphic", page=r.page, bbox=r.bbox, meta={"art": r.bbox}))
            return p
        return None

    # ========================================================= references
    def ref_list(self, ref_items, title_runs) -> list[Node]:
        lines = [o for k, o in ref_items if k == "line"]
        marks = [o for k, o in ref_items if k == "mark"]
        regions = [o for k, o in ref_items if k == "region"]
        rl = Node("ref-list", meta={"title": title_runs or []})
        entries = split_entries(lines, self.style.body_size)
        mark_pages = {m["page"]: m for m in marks}
        for ent in entries:
            runs = []
            for l in ent:
                pm = mark_pages.pop(l.page, None)
                append_line(runs, l, self.hyph, pm if runs else None)
                if pm and not runs:
                    pass
            runs = finish_inlines(runs)
            if not runs:
                continue
            txt = _runs_text(runs)
            m = re.match(r"^\s*(\[?\d{1,4}[.)\]]?)\s+", txt)
            label = None
            if m:
                label = m.group(1)
                runs = finish_inlines(clip_runs(runs, m.end()))
                txt = _runs_text(runs)
            try:
                parsed = parse_citation(txt)
            except Exception:      # never lose a reference over a parsing problem
                parsed = {"type": "other", "segments": [("text", txt)]}
            ref = Node("ref", meta={"label": label, "runs": runs, "parsed": parsed, "text": txt}, page=ent[0].page)
            rl.add(ref)
        for pm in mark_pages.values():
            if rl.children:
                rl.children[-1].meta.setdefault("marks", []).append(pm)
        out = [rl] if rl.children else []
        for rg in regions:
            out.append(rg)
        if not rl.children and lines:
            self.issues.add("REFERENCES_NOT_SPLIT", "warning", "reference section could not be split", page=lines[0].page)
        return out

    # ======================================================= front matter
    def build_front_matter(self, u: Unit) -> list[Node]:
        # group pages into parts: a new part starts at a page whose first line is a heading-like display line
        groups: list[list[int]] = []
        bm_titles = {b[2]: b[1] for b in self.bookmarks if u.start <= b[2] <= u.end}
        for gi in range(u.start, u.end + 1):
            p = self.page_by_index.get(gi)
            flow = [o for k, o in page_items(p) if k == "line"] if p else []
            if not flow and p:
                # whole page inside a frame (dedication, epigraph): its lines live in the box region
                flow = sorted((l for r in p.regions if r.kind == "box" for l in r.meta.get("lines", [])),
                              key=lambda l: (l.y0, l.x0))
            if not flow:
                continue
            first = flow[0]
            is_new = (gi in bm_titles or first.size >= 1.3 * self.style.body_size or first.all_bold() or
                      TOC_TITLE_RE.match(first.text.strip()) or any(rx.match(first.text) for rx, _ in FM_TYPES) or
                      COPYRIGHT_RE.search(" ".join(l.text for l in flow[:3])) or
                      # the copyright page often opens with the publisher's address: its ISBN line marks it
                      (any(re.search(r"\bISBN\b", l.text) for l in flow) and any(re.search(r"©|copyright", l.text, re.I) for l in flow)))
            if is_new and groups and self._continues_contributors(groups[-1], first, bm_titles.get(gi)):
                is_new = False      # contributor list running over several pages (each page opens with a bold name)
            if not groups or is_new and not self._continues_toc(groups[-1], flow):
                groups.append([gi])
            else:
                groups[-1].append(gi)
        out = []
        for gi, pages in enumerate(groups):
            items = []
            for pg in pages:
                items.append(("page", self.page_by_index[pg]))
                items.extend(page_items(self.page_by_index[pg]))
            lines = [o for k, o in items if k == "line"]
            if not lines:
                lines = sorted((l for k, o in items if k == "region" and o.kind == "box" for l in o.meta.get("lines", [])),
                               key=lambda l: (l.page, l.y0, l.x0))
            if not lines:
                continue
            head = lines[0]
            all_text = " ".join(l.text for l in lines[:40])
            bm_title = bm_titles.get(pages[0])
            if TOC_TITLE_RE.match(head.text.strip()) or (bm_title and TOC_TITLE_RE.match(bm_title)):
                out.append(self.build_toc(items, pages))
                continue
            if COPYRIGHT_RE.search(all_text) and sum(1 for l in lines if COPYRIGHT_RE.search(l.text)) >= 1 and len(lines) > 5 \
                    and gi > 0:
                self.meta["copyright_lines"].extend(items)
                continue
            book_title = " ".join(l.text.strip() for l in self.meta.get("_title_big", [])).lower()
            is_title_page = gi == 0 or (book_title and head.size >= 1.3 * self.style.body_size and
                                        head.text.strip().lower() and head.text.strip().lower() in book_title)
            if is_title_page and not any(rx.match(head.text) for rx, _ in FM_TYPES):
                # title page(s) and half-title
                self.meta["title_lines"].extend(lines)
                n = Node("front-matter-part", {"book-part-type": "title page"}, page=pages[0])
                bigl = max(lines, key=lambda l: l.size)
                # a title set over several lines: every line of the title size, in reading order
                big_lines = [l for l in lines if l.size >= bigl.size - 0.6 and l.page == bigl.page]
                if gi == 0:
                    self.meta["_title_big"] = big_lines
                runs = []
                for l in big_lines:
                    if runs and not str(runs[-1].get("text", "")).endswith((" ", "\u00a0")):
                        runs.append(T(" "))
                    runs.extend(line_runs(l))
                n.meta["title"] = finish_inlines(runs)
                big = None
                n.meta["fpage"], n.meta["lpage"] = self._folio(pages[0]), self._folio(pages[-1])
                body = [(k, o) for k, o in items if not (k == "line" and any(o is b for b in big_lines))]
                blocks = self.parse_blocks(body, allow_secs=False)
                nb = n.add(Node("named-book-part-body"))
                for b in blocks["body"]:
                    nb.add(b)
                out.append(n)
                continue
            ftype = next((t for rx, t in FM_TYPES if rx.match(head.text) or (bm_title and rx.match(bm_title))), None)
            title_line = head if (head.size >= 1.2 * self.style.body_size or head.all_bold() or caps_ratio(head.text) > 0.8) else None
            kind = {"dedication": "dedication", "preface": "preface", "foreword": "foreword", "ack": "ack"}.get(ftype, "front-matter-part")
            n = Node(kind, page=pages[0])
            if kind == "front-matter-part":
                label_txt = (bm_title or (title_line.text.strip() if title_line else "front matter")).strip()
                n.attrs["book-part-type"] = (label_txt.lower()[:40] + " page") if ftype != "contributors" else "contributors"
            n.meta["title"] = finish_inlines(line_runs(title_line)) if title_line else ([T(bm_title)] if bm_title else [])
            n.meta["fpage"], n.meta["lpage"] = self._folio(pages[0]), self._folio(pages[-1])
            body = [(k, o) for k, o in items if not (k == "line" and o is title_line)]
            blocks = self.parse_blocks(body, allow_secs=ftype in ("preface", "foreword", "ack", None))
            nb = Node("named-book-part-body") if kind != "ack" else n
            for b in blocks["body"]:
                nb.add(b)
            if nb is not n:
                n.add(nb)
            out.append(n)
        return out

    def _continues_contributors(self, group, first, bm_title) -> bool:
        if bm_title or first.size >= 1.3 * self.style.body_size or TOC_TITLE_RE.match(first.text.strip()) \
                or any(rx.match(first.text) for rx, _ in FM_TYPES):
            return False
        p0 = self.page_by_index[group[0]]
        f0 = next((o for k, o in page_items(p0) if k == "line"), None)
        return bool(f0 and any(t == "contributors" and rx.match(f0.text.strip().lstrip("\ufeff")) for rx, t in FM_TYPES))

    def _continues_toc(self, group, flow) -> bool:
        first_page = self.page_by_index[group[0]]
        f0 = next((o for k, o in page_items(first_page) if k == "line"), None)
        return bool(f0 and TOC_TITLE_RE.match(f0.text.strip())) and not TOC_TITLE_RE.match(flow[0].text.strip()) and \
            sum(1 for l in flow if re.search(r"\d{1,4}\s*$", l.text)) >= 0.3 * len(flow)

    def build_toc(self, items, pages) -> Node:
        lines = [o for k, o in items if k == "line"]
        # a contents page is read row by row (label | title / author | page), not column by column
        lines = sorted(lines, key=lambda l: (l.page, round(l.y0 / 3), l.x0))
        title = next((l for l in lines if TOC_TITLE_RE.match(l.text.strip())), None)
        entries = parse_toc([l for l in lines if l is not title], self.style.body_size)
        n = Node("toc", page=pages[0], meta={"title": finish_inlines(line_runs(title)) if title else [T("Contents")],
                                               "entries": entries, "fpage": self._folio(pages[0])})
        marks = [page_mark_for(o) for k, o in items if k == "page" and page_mark_for(o)]
        n.meta["marks"] = marks
        return n

    # ============================================================== index
    def build_index(self, u: Unit) -> Node:
        items = self._items(u.start, u.end)
        lines = [o for k, o in items if k == "line"]
        marks = [(o.index, page_mark_for(o)) for k, o in items if k == "page" and page_mark_for(o)]
        title, intro, divs = parse_index(lines, self.style.body_size)
        n = Node("index", page=u.start, meta={"title": title or "Index", "intro": [finish_inlines(line_runs(l)) for l in intro],
                                               "divs": divs, "marks": marks, "fpage": self._folio(u.start)})
        if not any(d["entries"] for d in divs):
            self.issues.add("INDEX_NOT_PARSED", "warning", "no index entries recognised", page=u.start)
        return n
