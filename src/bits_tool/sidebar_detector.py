"""Boxed text (sidebars, case studies, alerts...) from tinted / stroked frames."""
from __future__ import annotations

import re

from .document_tree import PageInfo, Region

TYPE_WORDS = [
    (re.compile(r"caso|case|clinical problem|problema cl[íi]nico|patient|paciente", re.I), "case-study"),
    (re.compile(r"alert|alerta|precauci[óo]n|caution|warning|advertencia", re.I), "alert"),
    (re.compile(r"pearl|perla|tip|consejo|pitfall", re.I), "pearl"),
    (re.compile(r"procedure|procedimiento", re.I), "procedure"),
    (re.compile(r"guideline|gu[íi]a|recomendaci[óo]n", re.I), "guideline"),
]


def box_type(title: str) -> str:
    for rx, t in TYPE_WORDS:
        if rx.search(title or ""):
            return t
    return "sidebar"


def detect_boxes(page: PageInfo, pitch: float, taken: list[tuple]) -> list[Region]:
    tx0, ty0, tx1, ty1 = page.trim
    tw, th = tx1 - tx0, ty1 - ty0
    frames = []
    for d in page.drawings:
        b = d["bbox"]
        w, h = b[2] - b[0], b[3] - b[1]
        if w < 0.28 * tw or h < 2.2 * pitch or w * h > 0.9 * tw * th:
            continue
        if d["hline"] or d["vline"]:
            continue
        if "re" not in d["kinds"] and "qu" not in d["kinds"] and "c" not in d["kinds"]:
            continue
        if not d["fill"] and d["n"] > 12:
            continue          # line art, not a frame
        frames.append(b)
    # merge stacked frames of equal width (title bar + body)
    frames.sort(key=lambda b: (b[1], b[0]))
    merged: list[list[float]] = []
    for b in frames:
        for m in merged:
            if abs(m[0] - b[0]) < 4 and abs(m[2] - b[2]) < 4 and b[1] - m[3] < 4:
                m[3] = max(m[3], b[3])
                break
            if m[0] - 2 <= b[0] and m[2] + 2 >= b[2] and m[1] - 2 <= b[1] and m[3] + 2 >= b[3]:
                break         # nested frame: keep the outer one
        else:
            merged.append(list(b))
    out = []
    for m in merged:
        box = tuple(m)
        if any(t[0] - 2 <= box[0] and t[2] + 2 >= box[2] and t[1] - 2 <= box[1] and t[3] + 2 >= box[3] for t in taken):
            continue  # frame inside a figure/table
        inside = [l for l in page.lines if l.role == "body" and box[0] - 2 <= (l.x0 + l.x1) / 2 <= box[2] + 2
                  and box[1] - 2 <= (l.y0 + l.y1) / 2 <= box[3] + 2]
        if len(inside) < 2:
            continue
        body_size = sorted(l.size for l in page.lines)[len(page.lines) // 2] if page.lines else 10
        if any(l.size >= 1.5 * body_size for l in inside):
            continue          # display title panel (chapter/part opener), not a sidebar
        text_w = max(l.x1 for l in inside) - min(l.x0 for l in inside)
        if text_w < 0.5 * (box[2] - box[0]):
            continue
        out.append(Region("box", box, page.index, 0.8, {"n_lines": len(inside)}))
    return out
