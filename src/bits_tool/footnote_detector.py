"""Page-bottom footnotes: small text below the body, starting with a marker that
also appears as a superscript in the page body."""
from __future__ import annotations

import re

from .document_tree import PageInfo
from .layout_analyzer import BookStyle

FN_START = re.compile(r"^\s*(?P<m>[*†‡§¶]+|\d{1,3}|[a-z])(?:[.)])?\s+\S")


def mark_footnotes(page: PageInfo, style: BookStyle) -> list[dict]:
    body = [l for l in page.lines if l.role == "body"]
    if len(body) < 3:
        return []
    sup_marks = {s.text.strip() for l in body for s in l.spans if s.sup and s.text.strip()}
    main = [l for l in body if abs(l.size - style.body_size) < 0.6]
    if not main:
        return []
    main_bottom = max(l.y1 for l in main)
    cands = sorted((l for l in body if l.y0 >= main_bottom - 1 or l.size <= 0.92 * style.body_size), key=lambda l: l.y0)
    notes = []
    zone_top = page.trim[1] + 0.6 * (page.trim[3] - page.trim[1])
    cur = None
    for l in cands:
        if l.y0 < zone_top or l.size > 0.92 * style.body_size:
            continue
        m = FN_START.match(l.text)
        first_is_sup = bool(l.spans and l.spans[0].sup)
        if (m and (m.group("m") in sup_marks or first_is_sup)):
            cur = {"label": m.group("m"), "lines": [l], "page": page.index}
            notes.append(cur)
            l.role = "footnote"
        elif cur is not None and abs(l.size - cur["lines"][-1].size) < 0.4 and 0 <= l.y0 - cur["lines"][-1].y1 < 0.8 * style.line_pitch:
            cur["lines"].append(l)
            l.role = "footnote"
    return notes
