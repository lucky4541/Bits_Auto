"""Per-page region detection (figures, tables, boxes) and reading order.

Order of operations on a page:
  1. captions (figure/table labels)          -> caption_detector
  2. figure artwork for each figure caption  -> figure_detector
  3. table body for each table caption       -> table_detector
  4. unlabelled ruled tables / large images
  5. boxed text frames                        -> sidebar_detector
  6. columns + reading order of the remaining lines and the regions
Lines inside regions change role (figure-text, caption, table-text, ...) and
leave the running text flow — they are carried by their region instead.
"""
from __future__ import annotations

from .caption_detector import caption_block, is_caption_start
import re

from .document_tree import Line, PageInfo, Region, Span
from .figure_detector import absorb_text, figure_region_for_caption, graphic_blocks, text_art_region
from .layout_analyzer import BookStyle, column_of, detect_columns
from .sidebar_detector import detect_boxes
from .table_detector import build_grid, ruled_table_regions, table_lines_below


def _bbox(lines):
    return (min(l.x0 for l in lines), min(l.y0 for l in lines), max(l.x1 for l in lines), max(l.y1 for l in lines))


def _union_box(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _sorted_lines(lines):
    return sorted(lines, key=lambda l: (round(l.y1, 0), l.x0))


LABEL_WORD_RE = re.compile(r"^(FIGURA|Figura|FIGURE|Figure|FIG\.|Fig\.|TABLA|Tabla|TABLE|Table|CUADRO|Cuadro|BOX|Box|RECUADRO|Recuadro)$")
NUMBER_RE = re.compile(r"^\s*[A-Z]?[IVX]*\d*[-.–]?\d+[A-Za-z]?\.?(?=\s|$)")


def collapse_spaced(text: str) -> str:
    """'T A B L A' -> 'TABLA' (letter-spaced display labels)."""
    t = text.strip()
    if re.fullmatch(r"(?:\w ){2,}\w", t):
        return t.replace(" ", "")
    return t


def assemble_labels(page: PageInfo, style: BookStyle):
    """Join display labels split over several lines ('TABLA' / '1-1' / title) into one caption line."""
    lines = [l for l in page.lines if l.role == "body"]
    consumed = set()
    new_lines = []
    for l in lines:
        if id(l) in consumed:
            continue
        word = collapse_spaced(l.text)
        if not LABEL_WORD_RE.match(word):
            continue
        near = [o for o in lines if o is not l and id(o) not in consumed and NUMBER_RE.match(o.text)
                and abs(o.y0 - l.y0) < 3 * max(l.size, 6) and o.x0 < l.x1 + 3 * l.size and o.x1 > l.x0 - 3 * l.size]
        if not near:
            continue
        num = min(near, key=lambda o: abs(o.y0 - l.y1) + abs(o.x0 - l.x0))
        band = (min(l.y0, num.y0), max(l.y1, num.y1))
        right = max(l.x1, num.x1)
        title = [o for o in lines if o is not l and o is not num and id(o) not in consumed and o.x0 >= right - 2
                 and o.y0 < band[1] and o.y1 > band[0] and o.x0 - right < 12 * style.body_size]
        title.sort(key=lambda o: (o.y0, o.x0))
        spans = [Span(text=word, font=l.spans[0].font, size=l.spans[0].size, bold=True, color=l.spans[0].color, bbox=l.bbox)]
        for i, sp in enumerate(num.spans):
            spans.append(Span(**{**sp.__dict__, "text": (" " if i == 0 else "") + sp.text.strip(" ") if i == 0 else sp.text,
                                 "bold": sp.bold or i == 0}))
        for t in title:
            for i, sp in enumerate(t.spans):
                spans.append(Span(**{**sp.__dict__, "text": (" " if i == 0 else "") + sp.text}))
        pieces = [l, num] + title
        bb = (min(p.x0 for p in pieces), min(p.y0 for p in pieces), max(p.x1 for p in pieces), max(p.y1 for p in pieces))
        synth = Line(spans=spans, bbox=bb, page=page.index)
        synth.assembled = True
        for p_ in pieces:
            consumed.add(id(p_))
        new_lines.append(synth)
    if consumed:
        page.lines = [o for o in page.lines if id(o) not in consumed] + new_lines


TIP_HEADER_RE = re.compile(r"^\s*(CONSEJOS?\s+O\s+ALERTAS?|CONSEJO\s*/\s*ALERTA|TIPS?\s+(?:AND|&)\s+ALERTS?|PEARLS?\s+(?:AND|&)\s+PITFALLS?)\b")


def _tip_tables(page: PageInfo, style: BookStyle, used: list) -> list[Region]:
    """Header row 'CONSEJO O ALERTA   DESCRIPCIÓN' followed by two-column rows, up to the text that resumes."""
    out = []
    body = [l for l in page.lines if l.role == "body"]
    for h in body:
        if not TIP_HEADER_RE.match(h.text) or h.role != "body":
            continue
        if any(u[0] - 2 <= (h.x0 + h.x1) / 2 <= u[2] + 2 and u[1] - 2 <= (h.y0 + h.y1) / 2 <= u[3] + 2 for u in used):
            continue
        band = [o for o in body if abs(o.y0 - h.y0) < 3 and o.x0 >= h.x0 - 2]
        x0 = min(o.x0 for o in band)
        top, hb = min(o.y0 for o in band), max(o.y1 for o in band)
        below = sorted((o for o in body if o.y0 > hb - 1 and o not in band), key=lambda o: (o.y0, o.x0))
        resume = [o.y0 for o in below if o.x0 < x0 - 12 and len(o.text.strip()) > 3]
        stop = min(resume + [page.trim[3]])
        rows, last = [], hb
        for o in below:
            if o.y0 >= stop - 1:
                break
            if o.x0 < x0 - 12:
                continue
            if o.size >= style.body_size + 1.2 or o.y0 - last > 2.5 * style.line_pitch:
                break
            rows.append(o)
            last = max(last, o.y1)
        if len(rows) < 2:
            continue
        tl = band + rows
        bb = _bbox(tl)
        grid = build_grid(tl, page, style, (bb[0], bb[1], max(bb[2], page.trim[2] - 30), bb[3]))
        for o in tl:
            o.role = "table-text"
        out.append(Region("table", bb, page.index, grid.conf if grid else 0.5,
                          {"label": None, "key": None, "caption": [], "lines": tl, "foot": [], "grid": grid,
                           "body_bbox": bb, "unlabeled": True}))
    return out


def analyze_regions(page: PageInfo, style: BookStyle, cfg) -> None:
    assemble_labels(page, style)
    body = [l for l in page.lines if l.role == "body"]
    if not body and not page.images:
        page.kind = "blank"
        return
    cols = detect_columns(page, body)
    page.columns = cols
    graphics = graphic_blocks(page, min_pt=cfg.get("images.min_figure_pt", 36) * 0.5) if cfg.get("layout.figure_detection", True) else []
    # chapter-opener outline page ('… / 29' entries): no captions and no figures on it, only its text
    if sum(1 for o in page.lines if re.search(r"/\s*\d{1,4}\s*$", o.text)) >= 4:
        graphics = []
        page.errors.append("OUTLINE_PAGE")
    # Caption association considers all graphic candidates, including small components.
    art_graphics = graphics
    used: list[tuple] = []
    ordered = _sorted_lines(body)
    pitch = style.line_pitch
    regions: list[Region] = []
    # 1-3: caption-anchored figures and tables
    for i, l in enumerate(ordered):
        if l.role != "body":
            continue
        near = any(abs(g["bbox"][3] - l.y0) < 3 * pitch or abs(l.y1 - g["bbox"][1]) < 3 * pitch for g in graphics)
        kind, info = is_caption_start(l, style, near)
        if not kind or "OUTLINE_PAGE" in page.errors:
            continue
        cap = [l] if getattr(l, "assembled", False) else caption_block(l, ordered[i + 1:], pitch)
        cap_ids = {id(x) for x in cap}
        if kind == "fig":
            found = figure_region_for_caption(page, cap, art_graphics, cols, used)
            if found is None:
                ta = text_art_region(page, cap, style)
                if ta is not None and (near or any(s.bold for s in l.spans[:2]) or info["conf"] >= 0.85):
                    found = (ta, "above")
                    info = {**info, "conf": min(info["conf"], 0.7), "text_art": True}
            if found is None:
                if not near and not any(s.bold for s in l.spans[:2]):
                    continue           # not convincing: leave as text
                art, direction, conf = None, None, 0.5
            else:
                art, direction = found
                conf = info["conf"]
            for c in cap:
                c.role = "caption"
            absorbed = absorb_text(art, page.lines, cap_ids) if art else []
            if art and absorbed:
                expanded = _union_box(art, _bbox(absorbed))
                absorbed = absorb_text(expanded, page.lines, cap_ids)
            for a in absorbed:
                a.role = "figure-text"
            if art and absorbed:
                # callout labels set beside the drawing are part of the figure image
                art = _union_box(art, _bbox(absorbed))
            full = _bbox(cap)
            if art:
                full = (min(full[0], art[0]), min(full[1], art[1]), max(full[2], art[2]), max(full[3], art[3]))
                used.append(art)
            regions.append(Region("figure", full, page.index, conf,
                                  {**info, "art": art, "direction": direction, "caption": cap, "absorbed": absorbed}))
        elif kind == "table" and cfg.get("layout.table_detection", True):
            col = next(((a, b) for a, b in cols if a - 4 <= (l.x0 + l.x1) / 2 <= b + 4), (page.trim[0], page.trim[2]))
            if l.x1 - l.x0 > (col[1] - col[0]) * 1.05 or len(cols) == 1:
                col = (page.trim[0], page.trim[2])
            below_caps = [o for o in ordered if o.y0 > _bbox(cap)[3] + 2 and o.role == "body" and o is not l
                          and is_caption_start(o, style, False)[0] and o.x1 > col[0] and o.x0 < col[1]]
            stop = min([o.y0 - 1 for o in below_caps] + [page.trim[3]])
            # artwork below the table (the next figure) ends the table too
            cap_bot = _bbox(cap)[3]
            art_tops = [g["bbox"][1] - 1 for g in art_graphics
                        if g["bbox"][1] > cap_bot + 12 and g["bbox"][1] < stop and g["bbox"][2] > col[0] and g["bbox"][0] < col[1]
                        and (g["kind"] == "image" or g.get("curves", 0) >= 2 or g.get("n", 0) >= 30)
                        and (g["bbox"][3] - g["bbox"][1]) >= 20]
            if art_tops:
                stop = min(stop, min(art_tops))
            for c in cap:
                c.role = "caption"
            tlines, foot = table_lines_below(_bbox(cap)[3], page, [o for o in page.lines if id(o) not in cap_ids], col, style, stop)
            bounds = _bbox(tlines) if tlines else None
            grid = build_grid(tlines, page, style, (col[0], bounds[1], col[1], bounds[3])) if tlines else None
            for t in tlines:
                t.role = "table-text"
            for f in foot:
                f.role = "table-foot"
            full = _bbox(cap + tlines + foot)
            used.append(full)
            regions.append(Region("table", full, page.index, grid.conf if grid else 0.4,
                                  {**info, "caption": cap, "lines": tlines, "foot": foot, "grid": grid,
                                   "body_bbox": bounds}))
        elif kind == "box":
            pass   # box labels are handled with frames below / as headings
    # 4-: uncaptioned 'CONSEJO O ALERTA | DESCRIPCIÓN' tables (header row, no caption, often no rules)
    if cfg.get("layout.table_detection", True):
        for reg in _tip_tables(page, style, used):
            used.append(reg.bbox)
            regions.append(reg)
    # 4a: unlabelled ruled tables
    if cfg.get("layout.table_detection", True):
        for box in ruled_table_regions(page, style, used):
            tl = [o for o in page.lines if o.role == "body" and box[1] - 2 <= (o.y0 + o.y1) / 2 <= box[3] + 2
                  and o.x0 >= box[0] - 4 and o.x1 <= box[2] + 4]
            grid = build_grid(tl, page, style, box)
            if grid is None or grid.ncols < 2:
                continue
            for t in tl:
                t.role = "table-text"
            used.append(box)
            regions.append(Region("table", box, page.index, grid.conf * 0.95,
                                  {"label": None, "key": None, "caption": [], "lines": tl, "foot": [], "grid": grid,
                                   "body_bbox": box, "unlabeled": True}))
    # Molecular diagrams need their short bond strokes, which are deliberately
    # excluded from ordinary figure/table candidates.
    from .chemical_detector import chemical_regions
    for region in chemical_regions(page, style, used):
        regions.append(region)
        used.append(region.bbox)
    # 4b: unlabelled graphics (large enough to be figures); small ones become inline graphics
    min_pt = cfg.get("images.min_figure_pt", 36)
    for g in graphics:
        b = g["bbox"]
        if any(not (b[2] < u[0] or u[2] < b[0] or b[3] < u[1] or u[3] < b[1]) for u in used):
            continue
        w, h = b[2] - b[0], b[3] - b[1]
        if g["kind"] == "vector" and (g.get("curves", 0) < 2 and g["n"] < 30):
            continue            # decorative rules / simple frames
        inside_text = [o for o in page.lines if o.role == "body" and b[0] - 2 <= (o.x0 + o.x1) / 2 <= b[2] + 2
                       and b[1] - 2 <= (o.y0 + o.y1) / 2 <= b[3] + 2]
        if g["kind"] == "vector" and inside_text and g["n"] < 60:
            continue            # a frame around text (box), not artwork
        if w >= min_pt and h >= min_pt:
            if not cfg.get("images.include_unlabeled_images", True):
                continue
            pw = page.trim[2] - page.trim[0]
            text_area = sum((o.x1 - o.x0) * (o.y1 - o.y0) for o in inside_text)
            if (w >= 0.5 * pw and w / max(h, 1) >= 5) or (len(inside_text) >= 3 and text_area > 0.25 * w * h):
                page.errors.append("DECORATIVE_PANEL_SKIPPED")
                continue        # banner strip / tinted panel behind running text: page design, not a figure
            # a small unlabelled graphic (box icon) keeps no text: the words next to it belong to the box
            absorbed = [] if max(w, h) < 90 else \
                [o for o in absorb_text(b, page.lines, set()) if o.size < 1.25 * style.body_size]
            for a in absorbed:
                a.role = "figure-text"
            if absorbed:
                b = _union_box(b, _bbox(absorbed))
            used.append(b)
            regions.append(Region("figure", b, page.index, 0.6, {"label": None, "key": None, "art": b, "caption": [],
                                                                 "absorbed": absorbed, "unlabeled": True}))
        elif g["kind"] == "image" and w >= 6 and h >= 6:
            regions.append(Region("inline-graphic", b, page.index, 0.7, {"art": b}))
            used.append(b)
    # 5: boxed text
    for bx in detect_boxes(page, pitch, [r.bbox for r in regions if r.kind in ("figure", "table")]):
        inside = [o for o in page.lines if o.role == "body" and bx.bbox[0] - 2 <= (o.x0 + o.x1) / 2 <= bx.bbox[2] + 2
                  and bx.bbox[1] - 2 <= (o.y0 + o.y1) / 2 <= bx.bbox[3] + 2]
        inner_regions = [r for r in regions if bx.bbox[0] - 2 <= (r.bbox[0] + r.bbox[2]) / 2 <= bx.bbox[2] + 2
                         and bx.bbox[1] - 2 <= (r.bbox[1] + r.bbox[3]) / 2 <= bx.bbox[3] + 2]
        for o in inside:
            o.role = "box-text"
        bx.meta["lines"] = inside
        bx.meta["regions"] = inner_regions
        for r in inner_regions:
            r.meta["in_box"] = True
        regions.append(bx)
    from .equation_detector import equation_regions
    regions.extend(equation_regions(page, style, regions))
    page.regions = regions
    for region in regions:
        page.diagnostics.append({"action": "include-region", "kind": region.kind,
                                 "bbox": region.bbox, "confidence": region.conf,
                                 "reason": region.meta.get("reason", "spatial region classification"),
                                 "label": region.meta.get("label")})
    if len(page.columns) <= 1 and regions:
        # tables/figures spanning the gutter hid it from the first pass: look again without their text
        rest = [l for l in page.lines if l.role == "body"]
        cols2 = detect_columns(page, rest)
        tw = page.trim[2] - page.trim[0]
        if len(cols2) == 2 and 0.42 <= ((cols2[0][1] + cols2[1][0]) / 2 - page.trim[0]) / tw <= 0.58:
            page.columns = cols2


def _gutter_columns(els, min_gutter=2.0):
    """Column intervals for a zone of elements from the x-coverage profile."""
    if len(els) < 2:
        return None
    x0 = min(e[0][0] for e in els)
    x1 = max(e[0][2] for e in els)
    iv = sorted((e[0][0], e[0][2]) for e in els)
    gaps = []
    reach = iv[0][1]
    for a, b in iv[1:]:
        if a > reach + min_gutter:
            gaps.append((reach, a))
        reach = max(reach, b)
    if not gaps:
        return None
    cols = []
    start = x0
    for g0, g1 in gaps:
        cols.append((start, g0))
        start = g1
    cols.append((start, x1))
    # every column must hold >= 2 elements, otherwise it is not a real column layout
    counts = [sum(2 if e[1] == "region" else 1 for e in els
                  if c0 - 1 <= (e[0][0] + e[0][2]) / 2 <= c1 + 1) for c0, c1 in cols]
    if min(counts) < 2:
        return None
    return cols


def _xy_order(elements, depth=0):
    """Recursive whitespace partition for bands with changing column layouts.

    A vertical gutter has priority. Horizontal cuts are used only when a
    spanning region prevents that cut. The partition tree is a layout graph.
    """
    if len(elements) < 2 or depth > 64:
        return sorted(elements, key=lambda e: (e[0][1], e[0][0]))
    columns = _gutter_columns(elements, min_gutter=8)
    if columns:
        return [e for a, b in columns for e in _xy_order(
            [item for item in elements if a - 1 <= (item[0][0] + item[0][2]) / 2 <= b + 1], depth + 1)]
    ordered = sorted(elements, key=lambda e: (e[0][1], e[0][0]))
    bottom = ordered[0][0][3]
    gaps = []
    for i, element in enumerate(ordered[1:], 1):
        if element[0][1] > bottom + 1:
            gaps.append((element[0][1] - bottom, i))
        bottom = max(bottom, element[0][3])
    if gaps:
        _, cut = max(gaps, key=lambda g: (g[0], -g[1]))
        return _xy_order(ordered[:cut], depth + 1) + _xy_order(ordered[cut:], depth + 1)
    return ordered


def order_items(page: PageInfo, lines: list[Line], regions: list[Region]) -> list[tuple[str, object]]:
    """Reading order: the page is cut into horizontal zones at full-width elements
    (spanners); inside a zone, columns are found from white gutters and read
    left→right, each top→bottom. Works for 1/2/3/n columns and mixed pages."""
    els = [(l.bbox, "line", l) for l in lines] + [(r.bbox, "region", r) for r in regions]
    if not els:
        return []
    tx0, _, tx1, _ = page.trim
    line_els = [e for e in els if e[1] == "line"]
    # width of the text block (leftmost to rightmost line), not of the widest line: on a 2-column page
    # that the column detector missed, a column line must not count as full width
    text_w = (max(e[0][2] for e in line_els) - min(e[0][0] for e in line_els)) if line_els else (tx1 - tx0)
    text_w = max(text_w, 0.5 * (tx1 - tx0))
    els.sort(key=lambda e: (e[0][1], e[0][0]))

    cols = getattr(page, "columns", None) or []
    tw = page.trim[2] - page.trim[0]
    multi = len(cols) > 1
    if not multi and len(cols) <= 1:
        # text column + side-note column (or a 2-column page the detector missed): a white gutter
        # running through all text lines of the page separates columns, whatever their widths
        g = _gutter_columns(els, min_gutter=8.0)
        if g and len(g) > 1:
            cols, multi = g, True

    def is_spanner(e):
        x0, x1 = e[0][0], e[0][2]
        if multi:
            # on a multi-column page a spanner is what crosses a gutter, not what is merely wide:
            # a column of a 2-column page is wider than 0.62 * half the page
            hit = sum(1 for c0, c1 in cols if min(x1, c1) - max(x0, c0) > 6)
            return hit > 1
        return (x1 - x0) >= 0.62 * text_w

    page_cols = cols
    zones: list[tuple[str, list]] = []
    for e in els:
        if is_spanner(e):
            zones.append(("span", [e]))
        else:
            if zones and zones[-1][0] == "zone":
                zones[-1][1].append(e)
            else:
                zones.append(("zone", [e]))
    # a zone element that starts above a preceding spanner's bottom belongs beside it; keep y-order there
    out = []
    for kind, zl in zones:
        if kind == "span" or len(zl) == 1:
            out.extend(zl)
            continue
        zcols = _gutter_columns(zl)
        if multi and not zcols:
            # the page-level columns are known: a figure/strip in the margin must not hide the gutter
            zcols = [(c0, c1) for c0, c1 in page_cols]
            zcols[0] = (min(zcols[0][0], min(e[0][0] for e in zl)), zcols[0][1])
            zcols[-1] = (zcols[-1][0], max(zcols[-1][1], max(e[0][2] for e in zl)))
        if not zcols:
            out.extend(_xy_order(zl))
            continue
        placed = set()
        for c0, c1 in zcols:
            col_els = [e for e in zl if c0 - 1 <= (e[0][0] + e[0][2]) / 2 <= c1 + 1 and id(e) not in placed]
            placed.update(id(e) for e in col_els)
            col_els.sort(key=lambda e: (round(e[0][3] if e[1] == "line" else e[0][1], 0), e[0][0]))
            out.extend(col_els)
        rest = [e for e in zl if id(e) not in placed]      # nothing may be dropped: centre in a gutter
        rest.sort(key=lambda e: (round(e[0][3] if e[1] == "line" else e[0][1], 0), e[0][0]))
        out.extend(rest)
    result = []
    for i, e in enumerate(out):
        if e[1] == "line":
            e[2].order = i
            e[2].column = column_of(e[0], cols)
        else:
            e[2].meta.update(order=i, column=column_of(e[0], cols))
        result.append((e[1], e[2]))
    page.diagnostics.append({"action": "reading-order", "reason": "gutter columns and spanning bands",
                             "columns": cols, "items": [
                                 {"order": i, "kind": e[1], "bbox": e[0],
                                  "predecessor": i - 1 if i else None} for i, e in enumerate(out)]})
    return result


def page_items(page: PageInfo) -> list[tuple[str, object]]:
    lines = [l for l in page.lines if l.role in ("body",)]
    top = [r for r in page.regions if not r.meta.get("in_box")]
    return order_items(page, lines, top)


def box_items(page: PageInfo, box: Region) -> list[tuple[str, object]]:
    return order_items(page, box.meta.get("lines", []), box.meta.get("regions", []))
