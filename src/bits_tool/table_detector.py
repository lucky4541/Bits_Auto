"""Table detection and reconstruction from text geometry + ruling lines.

Region: anchored on a table caption (label above) or on a stack of horizontal
rules with gridded text (unlabelled tables). Grid: column gutters from the
x-coverage profile, rows from whitespace/rules/first-column starts, header
rows from rules and bold styling, spanning cells (colspan) from cells that
cross gutters. Tables that cannot be rebuilt are *never* turned into
paragraphs: they fall back to an image of the region and are flagged
TABLE_STRUCTURAL_EXTRACTION_FAILED.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .document_tree import Line, PageInfo
from .layout_analyzer import BookStyle, family

FOOT_RE = re.compile(r"^\s*(?:[a-z]\s|[*†‡§¶]+\s?|\d\s|Fuente|Source|Adaptado|Adapted|Modificado|Modified|Datos|Data from|Reproducido|Reprinted|Nota|Note|Abreviaturas|Abbreviations)", re.I)


@dataclass
class Cell:
    lines: list[Line] = field(default_factory=list)
    row: int = 0
    col: int = 0
    colspan: int = 1
    rowspan: int = 1
    header: bool = False

    @property
    def bbox(self):
        if not self.lines:
            return None
        return (min(l.x0 for l in self.lines), min(l.y0 for l in self.lines), max(l.x1 for l in self.lines), max(l.y1 for l in self.lines))


@dataclass
class TableGrid:
    rows: list[list[Cell]]
    ncols: int
    header_rows: int
    conf: float
    issues: list[str] = field(default_factory=list)


def merged_hlines(page: PageInfo) -> list[tuple]:
    """Horizontal rules with collinear touching segments joined (cell-by-cell rules)."""
    segs = sorted(((d["bbox"][0], (d["bbox"][1] + d["bbox"][3]) / 2, d["bbox"][2]) for d in page.drawings
                   if d["hline"] or (d["kinds"] == "re" and d["bbox"][3] - d["bbox"][1] < 2.5 and d["bbox"][2] - d["bbox"][0] > 8)),
                  key=lambda s: (round(s[1]), s[0]))
    # shaded / framed cells drawn as rectangles: their top and bottom edges are row boundaries
    cell_edges = [(d["bbox"][0], y, d["bbox"][2]) for d in page.drawings
                  if d["kinds"] == "re" and 2.5 <= d["bbox"][3] - d["bbox"][1] <= 120 and d["bbox"][2] - d["bbox"][0] > 8
                  for y in (d["bbox"][1], d["bbox"][3])]
    segs = sorted(segs + cell_edges, key=lambda s: (round(s[1]), s[0]))
    out: list[list[float]] = []
    for x0, y, x1 in segs:
        if out and abs(out[-1][1] - y) < 1.2 and x0 <= out[-1][2] + 3:
            out[-1][2] = max(out[-1][2], x1)
        else:
            out.append([x0, y, x1])
    return [(a, y - 0.5, b, y + 0.5) for a, y, b in out]


def _hrules(page: PageInfo, x0, x1, y0, y1):
    out = []
    for b in merged_hlines(page):
        if b[1] >= y0 - 2 and b[3] <= y1 + 2:
            if min(b[2], x1) - max(b[0], x0) > 0.5 * (x1 - x0) and b[0] >= x0 - 12:
                # (a rule reaching far left of the column belongs to a wider frame, e.g. a full-width box)
                out.append((b[1] + b[3]) / 2)
    out.sort()
    merged = []
    for y in out:
        if not merged or y - merged[-1] > 2:
            merged.append(y)
    return merged


def table_lines_below(caption_bottom: float, page: PageInfo, lines: list[Line], col_range, style: BookStyle,
                      stop_y: float) -> tuple[list[Line], list[Line]]:
    """Collect table body lines (and footnote lines) below a caption."""
    c0, c1 = col_range
    cand = [l for l in lines if l.role == "body" and l.y0 >= caption_bottom - 1 and l.y1 <= stop_y + 1
            and l.x1 > c0 - 2 and l.x0 < c1 + 2]
    cand.sort(key=lambda l: (l.y0, l.x0))
    rules = _hrules(page, c0, c1, caption_bottom, stop_y)
    last_rule = rules[-1] if len(rules) >= 2 else None
    body, foot = [], []
    width = c1 - c0
    in_foot = False
    for l in cand:
        if last_rule is not None and l.y0 > last_rule + 1:
            # below the closing rule: footnotes / sources, or the text resumes
            if FOOT_RE.match(l.text) or l.size < style.body_size - 0.4 or in_foot and abs(l.size - foot[-1].size) < 0.3:
                if style.is_body_like(l) and (l.x1 - l.x0) > 0.8 * width and not FOOT_RE.match(l.text) and not in_foot:
                    break
                foot.append(l)
                in_foot = True
                continue
            break
        full_body = style.is_body_like(l) and (l.x1 - l.x0) > 0.82 * width
        if full_body and last_rule is None and body:
            # a full-width body-style line ends an unruled table
            break
        body.append(l)
    return body, foot


def _gutters(lines: list[Line], x0: float, x1: float, size: float) -> list[tuple[float, float]]:
    if not lines:
        return []
    width = int(x1 - x0) + 2
    cov = [0] * width
    for l in lines:
        a = max(0, int(l.x0 - x0))
        b = min(width - 1, int(l.x1 - x0))
        for i in range(a, b + 1):
            cov[i] += 1
    gut = []
    i = 0
    min_w = max(4.0, 0.55 * size)
    # skip leading/trailing uncovered margins
    first = next((k for k, v in enumerate(cov) if v), 0)
    last = max((k for k, v in enumerate(cov) if v), default=width - 1)
    i = first
    while i <= last:
        if cov[i] == 0:
            j = i
            while j <= last and cov[j] == 0:
                j += 1
            if j - i >= min_w:
                gut.append((x0 + i, x0 + j))
            i = j
        i += 1
    return gut


def build_grid(lines: list[Line], page: PageInfo, style: BookStyle, bounds: tuple) -> TableGrid | None:
    if len(lines) < 2:
        return None
    x0, y0, x1, y1 = bounds
    size = sorted(l.size for l in lines)[len(lines) // 2]
    # spanning lines (titles across columns) must not hide gutters: try all, then without the widest
    gut = _gutters(lines, x0, x1, size)
    if not gut:
        widest = sorted(lines, key=lambda l: -(l.x1 - l.x0))[: max(1, len(lines) // 8)]
        rest = [l for l in lines if l not in widest]
        gut = _gutters(rest, x0, x1, size)
    if not gut:
        # one-column table (e.g. a boxed bullet list set as a table): a single cell holding block content
        cell = Cell(lines=sorted(lines, key=lambda l: (l.y0, l.x0)), row=0, col=0)
        return TableGrid([[cell]], 1, 0, 0.7, ["SINGLE_COLUMN"])
    edges = [min(l.x0 for l in lines) - 1] + [(a + b) / 2 for a, b in gut] + [max(l.x1 for l in lines) + 1]
    ncols = len(edges) - 1

    def col_span(l):
        cs = [i for i in range(ncols) if l.x0 < edges[i + 1] - 1 and l.x1 > edges[i] + 1]
        return (cs[0], cs[-1]) if cs else (0, 0)

    # bands of lines sharing a baseline
    ls = sorted(lines, key=lambda l: (l.y0, l.x0))
    bands: list[list[Line]] = []
    for l in ls:
        if bands and abs(((l.y0 + l.y1) / 2) - ((bands[-1][0].y0 + bands[-1][0].y1) / 2)) < 0.45 * size:
            bands[-1].append(l)
        else:
            bands.append([l])
    rules = _hrules(page, x0, x1, y0, y1)
    pitch = style.line_pitch or size * 1.2
    col0_bands = sum(1 for b in bands if any(col_span(l)[0] == 0 for l in b))
    single_line_rows = col0_bands >= 0.8 * len(bands)
    col_left: dict[int, float] = {}
    for l in lines:
        c = col_span(l)[0]
        col_left[c] = min(col_left.get(c, l.x0), l.x0)
    rows: list[list[Line]] = []
    prev = None
    for b in bands:
        top = min(l.y0 for l in b)
        new = prev is None
        if not new:
            gap = top - max(l.y1 for l in prev)
            ruled = any(max(l.y1 for l in prev) - 1 <= r <= top + 1 for r in rules)
            has_col0 = any(col_span(l)[0] == 0 for l in b)
            if ruled or gap > 0.45 * pitch:
                new = True
            elif all(l.x0 > col_left.get(col_span(l)[0], l.x0) + 0.5 * size for l in b) and \
                    (any(l.text.strip()[:1].islower() for l in b) or any(l.text.rstrip().endswith(("-", "\u00ad")) for l in prev)):
                new = False          # every line hangs indented in its column: wrapped text of the row above
            elif all(l.text.strip()[:1].islower() for l in b) and \
                    len({col_span(l)[0] for l in b}) < len({col_span(l)[0] for l in prev}):
                new = False          # 'Tamaño de' / 'la USP': a wrapped cell, the other cells of the row are done
            elif single_line_rows and has_col0:
                new = True
            elif has_col0 and b[0].text[:1].isupper() and prev and not any(col_span(l)[0] == 0 for l in prev):
                new = True
        if new:
            rows.append(list(b))
        else:
            rows[-1].extend(b)
        prev = b
    grid: list[list[Cell]] = []
    filled = 0
    for ri, rl in enumerate(rows):
        cells: dict[int, Cell] = {}
        for l in sorted(rl, key=lambda l: (l.y0, l.x0)):
            a, bcol = col_span(l)
            c = cells.get(a)
            if c is None:
                c = cells[a] = Cell(row=ri, col=a, colspan=bcol - a + 1)
            else:
                c.colspan = max(c.colspan, bcol - a + 1)
            c.lines.append(l)
        row = []
        ci = 0
        while ci < ncols:
            if ci in cells:
                c = cells[ci]
                row.append(c)
                filled += 1
                ci += c.colspan
            else:
                row.append(Cell(row=ri, col=ci))
                ci += 1
        grid.append(row)
    # header rows: above the first inner rule, or leading bold rows
    header_rows = 0
    inner = [r for r in rules if min(l.y0 for l in lines) + 2 < r < max(l.y1 for l in lines) - 2]
    if inner:
        first_rule = inner[0]
        for r in grid:
            bb = [c.bbox for c in r if c.bbox]
            if bb and max(b[3] for b in bb) <= first_rule + 1:
                header_rows += 1
            else:
                break
    if header_rows == 0:
        for r in grid[:3]:
            ls_ = [l for c in r for l in c.lines]
            if ls_ and all(l.all_bold() for l in ls_) and len(grid) > 2:
                header_rows += 1
            else:
                break
    if header_rows >= len(grid):
        header_rows = 0
    for r in grid[:header_rows]:
        for c in r:
            c.header = True
    total = len(grid) * ncols
    fill_ratio = filled / total if total else 0
    conf = 0.55 + 0.4 * fill_ratio if ncols >= 2 else 0.3
    issues = []
    if fill_ratio < 0.45:
        issues.append("SPARSE_TABLE")
    if ncols > 12:
        conf -= 0.2
        issues.append("TOO_MANY_COLUMNS")
    return TableGrid(grid, ncols, header_rows, round(min(conf, 0.97), 3), issues)


def ruled_table_regions(page: PageInfo, style: BookStyle, taken: list[tuple]) -> list[tuple]:
    """Unlabelled tables: >=3 stacked horizontal rules with gridded text between."""
    hl = merged_hlines(page)
    hl.sort(key=lambda b: (round(b[0]), round(b[2]), b[1]))
    groups: list[list[tuple]] = []
    for b in hl:
        for g in groups:
            if abs(g[0][0] - b[0]) < 4 and abs(g[0][2] - b[2]) < 4 and b[1] - g[-1][3] < 120:
                g.append(b)
                break
        else:
            groups.append([b])
    out = []
    for g in groups:
        if len(g) < 3:
            continue
        box = (g[0][0], g[0][1], g[0][2], g[-1][3])
        if box[2] - box[0] < 80 or any(not (box[2] < t[0] or t[2] < box[0] or box[3] < t[1] or t[3] < box[1]) for t in taken):
            continue
        inside = [l for l in page.lines if l.role == "body" and box[1] - 2 <= (l.y0 + l.y1) / 2 <= box[3] + 2
                  and l.x0 >= box[0] - 4 and l.x1 <= box[2] + 4]
        if len(inside) < 4:
            continue
        if _gutters(inside, box[0], box[2], style.body_size):
            out.append(box)
    return out
