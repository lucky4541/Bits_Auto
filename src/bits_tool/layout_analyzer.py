"""Book-level layout analysis.

* body typography (family/size/line pitch) from character-weighted statistics
* running heads / feet via zone + local page-frequency analysis
* printed page numbers (folios) with sequence fitting per PDF file
* per-page column detection (vertical white gutters)
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .document_tree import Line, PageInfo

ROMAN_RE = re.compile(r"^(?=[ivxlcdm]+$)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.I)
NUM_RE = re.compile(r"^\d{1,4}$")


def family(font: str) -> str:
    f = re.sub(r"^[A-Z]{6}\+", "", font or "")
    return re.split(r"[-,]", f)[0].lower()


def roman_to_int(s: str) -> int:
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    s = s.lower()
    total = 0
    for i, ch in enumerate(s):
        v = vals[ch]
        total += -v if i + 1 < len(s) and vals[s[i + 1]] > v else v
    return total


def int_to_roman(n: int) -> str:
    out = ""
    for v, r in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
                 (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out += r
            n -= v
    return out


def norm_repeat(text: str) -> str:
    t = re.sub(r"\d+", "#", text.lower())
    t = re.sub(r"\b[ivxlc]+\b", "#", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class BookStyle:
    body_size: float = 10.0
    body_family: str = ""
    line_pitch: float = 12.0
    body_color: int = 0
    size_hist: dict = field(default_factory=dict)
    family_hist: dict = field(default_factory=dict)

    def ratio(self, line: Line) -> float:
        return line.size / self.body_size if self.body_size else 1.0

    def is_body_like(self, line: Line) -> bool:
        return abs(line.size - self.body_size) <= 0.6 and family(line.main.font) == self.body_family


def body_style(pages: list[PageInfo]) -> BookStyle:
    sizes, fams = Counter(), Counter()
    fam_size = Counter()
    colors = Counter()
    for p in pages:
        for l in p.lines:
            if l.role not in ("body",):
                continue
            for s in l.spans:
                n = len(s.text.strip())
                if not n or s.sup or s.sub:
                    continue
                sz = round(s.size * 2) / 2
                sizes[sz] += n
                fams[family(s.font)] += n
                fam_size[(family(s.font), sz)] += n
                colors[s.color] += n
    if not fam_size:
        return BookStyle()
    (fam, size), _ = fam_size.most_common(1)[0]
    # line pitch: median baseline distance between consecutive body lines of that style
    gaps = []
    for p in pages[: min(len(pages), 400)]:
        prev = None
        for l in sorted((l for l in p.lines if l.role == "body"), key=lambda l: (round(l.x0 / 40), l.y1)):
            if prev is not None and abs(l.x0 - prev.x0) < 30 and abs(l.size - size) < 0.6 and abs(prev.size - size) < 0.6:
                d = l.y1 - prev.y1
                if 0.8 * size < d < 2.2 * size:
                    gaps.append(d)
            prev = l
    gaps.sort()
    pitch = gaps[len(gaps) // 2] if gaps else size * 1.2
    return BookStyle(size, fam, round(pitch, 2), colors.most_common(1)[0][0] if colors else 0,
                     dict(sizes.most_common(12)), dict(fams.most_common(8)))


# ------------------------------------------------------------ running heads

def _rh_sig(l: Line, where: str):
    s = l.spans[0] if l.spans else None
    return (where, (s.font if s else ""), round(l.size, 1), round(l.y0 / 2))


def mark_headers_footers(pages: list[PageInfo], zone_frac: float = 0.085, window: int = 12, min_repeat: int = 3):
    """Furniture needs repeated position/style AND separation from body text.

    An isolated boundary line on a single page remains content. Numeric folios
    need corroborating page progression, so a diagram label 'I' is not erased.
    """
    from .caption_detector import label_match
    candidates = []
    keyed = defaultdict(set)
    folios = defaultdict(set)
    for page in pages:
        x0, y0, x1, y1 = page.trim
        height = max(y1 - y0, 1)
        for line in page.lines:
            if line.role not in ("body", "rotated-margin"):
                continue
            where = "header" if line.y1 <= y0 + zone_frac * height else "footer" if line.y0 >= y1 - zone_frac * height else None
            text = line.text.strip()
            if not where or label_match(text)[0] or len(text) > 160:
                continue
            neighbors = [l for l in page.lines if l is not line and l.role == "body"
                         and min(l.x1, line.x1) > max(l.x0, line.x0)
                         and (l.y0 >= line.y1 if where == "header" else l.y1 <= line.y0)]
            gap = min((l.y0 - line.y1 if where == "header" else line.y0 - l.y1 for l in neighbors), default=height if (NUM_RE.fullmatch(text) or ROMAN_RE.fullmatch(text)) else 0)
            if gap < 0.7 * line.size:
                continue
            sig = (page.pdf, where, family(line.main.font), round(line.size),
                   round((line.y0 - y0) / height * 100),
                   round(((line.x0 + line.x1) / 2 - x0) / max(x1 - x0, 1) * 4))
            key = sig + (norm_repeat(text),)
            keyed[key].add(page.index)
            numeric = int(text) if NUM_RE.fullmatch(text) else roman_to_int(text) if ROMAN_RE.fullmatch(text) else None
            fkey = sig + ("roman" if text.isalpha() else "arabic", numeric - page.pdf_page) if numeric is not None else None
            if fkey:
                folios[fkey].add(page.index)
            candidates.append((page, line, where, key, fkey))
    for page, line, where, key, fkey in candidates:
        repeats = sum(abs(i - page.index) <= window for i in keyed[key])
        if fkey and len(folios[fkey]) >= 2:
            line.role = "folio"
        elif not fkey and repeats >= min_repeat:
            line.role = where
        else:
            page.diagnostics.append({"action": "include", "bbox": line.bbox,
                                     "reason": "boundary text lacks corroborated furniture evidence"})
            continue
        page.diagnostics.append({"action": "exclude-from-body", "bbox": line.bbox,
                                 "role": line.role, "reason": "repeated position, typography and isolated margin context"})


def _folio_from_line(l: Line) -> str | None:
    t = l.text.strip()
    if NUM_RE.match(t) or ROMAN_RE.match(t):
        return t.lower()
    m = re.match(r"^(\d{1,4})\s{1,}\S", t) or re.search(r"\S\s{1,}(\d{1,4})$", t)
    if m and l.role in ("header", "footer", "header-iso", "footer-iso"):
        return m.group(1)
    for s in (l.spans[0], l.spans[-1]):
        st = s.text.strip()
        if NUM_RE.match(st) and l.role in ("header", "footer", "header-iso", "footer-iso"):
            return st
    return None


def assign_folios(pages: list[PageInfo]):
    """Read printed page numbers and fit them to a consistent sequence per PDF file."""
    by_pdf = defaultdict(list)
    for p in pages:
        by_pdf[p.pdf].append(p)
        cands = [f for l in p.lines if l.role in ("folio", "header", "footer", "header-iso", "footer-iso") for f in [_folio_from_line(l)] if f]
        if cands:
            p.folio = cands[0]
            p.folio_conf = 0.9 if len(set(cands)) == 1 else 0.6
    for pdf, plist in by_pdf.items():
        offs = Counter()
        roman_offs = Counter()
        for p in plist:
            if p.folio and p.folio.isdigit():
                offs[int(p.folio) - p.pdf_page] += 1
            elif p.folio and ROMAN_RE.match(p.folio):
                roman_offs[roman_to_int(p.folio) - p.pdf_page] += 1
        if offs:
            off, n = offs.most_common(1)[0]
            numeric_pages = [p for p in plist if p.folio and p.folio.isdigit()]
            first_num = min((p.pdf_page for p in numeric_pages if int(p.folio) - p.pdf_page == off), default=0)
            for p in plist:
                expect = p.pdf_page + off
                if p.pdf_page < first_num and roman_offs:
                    continue
                if expect < 1:
                    continue
                if p.folio is None or not p.folio.isdigit():
                    # a roman folio is only plausible before the arabic sequence starts; later it is a
                    # stray letter in the margin zone (figure part 'c', 'i', 'v'...)
                    if not (p.folio and ROMAN_RE.match(p.folio)) or p.pdf_page >= first_num:
                        p.folio, p.folio_conf = str(expect), 0.7
                elif int(p.folio) != expect:
                    # disagreeing folio: trust sequence if the majority is strong
                    if n >= 3:
                        p.folio, p.folio_conf = str(expect), 0.6
        if roman_offs:
            roff, _ = roman_offs.most_common(1)[0]
            for p in plist:
                if p.folio is None and p.pdf_page + roff >= 1:
                    later_num = [q for q in plist if q.folio and q.folio.isdigit() and q.pdf_page <= p.pdf_page]
                    if not later_num:
                        p.folio, p.folio_conf = int_to_roman(p.pdf_page + roff), 0.6
    # uniqueness: a folio may appear only once per book
    seen = {}
    for p in pages:
        if p.folio is None:
            continue
        if p.folio in seen:
            p.folio_conf = min(p.folio_conf, 0.3)
            p.errors.append(f"DUPLICATE_FOLIO:{p.folio}")
            p.folio = None
        else:
            seen[p.folio] = p.index


# ---------------------------------------------------------------- columns

def detect_columns(page: PageInfo, lines: list[Line], min_gutter: float = 6.0, _narrow: bool = True) -> list[tuple[float, float]]:
    body = [l for l in lines if l.role in ("body",) and len(l.text.strip()) > 1]
    tx0, _, tx1, _ = page.trim
    if len(body) < 6:
        return [(tx0, tx1)]
    # display lines (chapter/section titles, author lines) span the gutter on opener pages:
    # find the gutter from text-size lines only
    sizes = Counter(round(l.size) for l in body)
    common = sizes.most_common(1)[0][0]
    text_lines = [l for l in body if abs(l.size - common) <= 1.0]
    if len(text_lines) >= 6:
        body = text_lines
    # full-width blocks (a boxed 'Procedimientos' panel, a wide table) cross the gutter of an otherwise
    # 2-column page: find the gutter from the narrow lines when they are the majority
    span = max(l.x1 for l in body) - min(l.x0 for l in body)
    narrow = [l for l in body if l.x1 - l.x0 < 0.6 * span]
    if len(narrow) >= 12 and len(narrow) >= 0.5 * len(body):
        # only when the wide lines form their own band (above/below the columns), not interleaved
        # with short lines of a single-column page (lists, short paragraph ends)
        wide = [l for l in body if l.x1 - l.x0 >= 0.6 * span]
        ys = sorted(l.y0 for l in narrow)
        lo, hi = ys[len(ys) // 10], ys[(9 * len(ys)) // 10]
        if _narrow and sum(1 for l in wide if lo < l.y0 < hi) <= 0.2 * max(1, len(wide)):
            cols = detect_columns(page, narrow, min_gutter, _narrow=False)
            tx0_, _, tx1_, _ = page.trim
            # accept only a classic 2-column split with the gutter near the middle of the page
            mid = ((cols[0][1] + cols[1][0]) / 2 - tx0_) / max(1.0, tx1_ - tx0_) if len(cols) == 2 else 0
            if len(cols) == 2 and 0.42 <= mid <= 0.58:
                return cols
    width = tx1 - tx0
    res = 1.0
    nb = int(width / res) + 1
    cov = [0] * nb
    for l in body:
        a = int(max(0, l.x0 - tx0) / res)
        b = int(min(width, l.x1 - tx0) / res)
        for i in range(a, min(b + 1, nb)):
            cov[i] += 1
    # candidate gutters in the central 80%
    gutters = []
    i = int(0.12 * nb)
    end = int(0.88 * nb)
    thresh = max(1, int(0.03 * len(body)))
    while i < end:
        if cov[i] <= thresh:
            j = i
            while j < end and cov[j] <= thresh:
                j += 1
            if (j - i) * res >= min_gutter:
                left = sum(1 for l in body if l.x1 <= tx0 + i * res + 1)
                right = sum(1 for l in body if l.x0 >= tx0 + j * res - 1)
                if left >= 3 and right >= 3:
                    gutters.append((tx0 + i * res, tx0 + j * res))
            i = j
        i += 1
    if not gutters:
        return _banded_two_columns(page, lines) or [(tx0, tx1)]
    cols = []
    start = tx0
    for g0, g1 in gutters:
        cols.append((start, g0))
        start = g1
    cols.append((start, tx1))
    return cols


def _banded_two_columns(page: PageInfo, lines: list[Line]):
    """2-column page where a band of gutter-crossing text (an unruled full-width table, a panel) sits above or
    below the columns: the crossing lines form at most two contiguous bands in reading height."""
    body = [l for l in lines if l.role == "body" and len(l.text.strip()) > 1]
    if len(body) < 20:
        return None
    tx0, _, tx1, _ = page.trim
    w = tx1 - tx0
    best = None
    for k in range(40, 61):
        x = tx0 + w * k / 100
        cross = [l for l in body if l.x0 < x - 3 and l.x1 > x + 3]
        left = [l for l in body if l.x1 <= x + 3]
        right = [l for l in body if l.x0 >= x - 3]
        if len(left) < 8 or len(right) < 8 or len(cross) > 0.4 * len(body):
            continue
        if best is None or len(cross) < len(best[1]):
            best = (x, cross, left, right)
    if best is None:
        return None
    x, cross, left, right = best
    if cross:
        # the crossing text must be a band of its own: most column lines lie outside its height
        b0, b1 = min(l.y0 for l in cross), max(l.y1 for l in cross)
        out_l = [l for l in left if l.y1 < b0 or l.y0 > b1]
        out_r = [l for l in right if l.y1 < b0 or l.y0 > b1]
        if len(out_l) < 0.6 * len(left) or len(out_r) < 0.6 * len(right) or len(out_l) < 6 or len(out_r) < 6:
            return None
        left, right = out_l, out_r
    # both columns must be filled beside each other (not one column above the other)
    ly = sorted(l.y0 for l in left)
    ry = sorted(l.y0 for l in right)
    if min(ly[-1], ry[-1]) - max(ly[0], ry[0]) < 0.3 * min(ly[-1] - ly[0], ry[-1] - ry[0]) or \
            min(ly[-1], ry[-1]) - max(ly[0], ry[0]) < 40:
        return None
    g0 = max(l.x1 for l in left if l.x1 <= x + 3)
    g1 = min(l.x0 for l in right if l.x0 >= x - 3)
    if g1 - g0 < 6:
        return None
    return [(tx0, g0), (g1, tx1)]


def column_of(line_or_bbox, cols) -> int:
    b = line_or_bbox.bbox if hasattr(line_or_bbox, "bbox") else line_or_bbox
    if len(cols) <= 1:
        return 0
    x0, x1 = b[0], b[2]
    spans = [i for i, (c0, c1) in enumerate(cols) if x0 < c1 - 2 and x1 > c0 + 2]
    if len(spans) > 1:
        return -1      # spanner
    cx = (x0 + x1) / 2
    for i, (c0, c1) in enumerate(cols):
        if c0 - 4 <= cx <= c1 + 4:
            return i
    return min(range(len(cols)), key=lambda i: min(abs(cx - cols[i][0]), abs(cx - cols[i][1])))
