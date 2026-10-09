"""Printed table of contents: entries with labels, titles, levels and page numbers."""
from __future__ import annotations

import re

from .document_tree import Line

TOC_TITLE_RE = re.compile(r"^\s*(contents|table of contents|contenido|contenidos|índice|indice|sumario|inhalt|sommaire|table des mati[èe]res|brief contents|contents in brief)\s*$", re.I)
TRAIL_RE = re.compile(r"(?:\s*\.{2,}\s*|\s+|\t)(?P<page>\d{1,4}|[ivxlc]{1,6})\s*$", re.I)
LABEL_RE = re.compile(r"^\s*(?P<label>(?:chapter|cap[íi]tulo|part|parte|secci[óo]n|section|unit|unidad|ap[ée]ndice|appendix)\s+(?:\d+(?:\.\d+)*|[IVXLA-Z]+)[.:]?|\d{1,3}[.:]?|[IVXL]+[.:])\s+", re.I)


def _authors_line(t: str) -> bool:
    t = t.strip()
    if not t or TRAIL_RE.search(t) or LABEL_RE.match(t + " ") or t.isupper():
        return False
    words = [w.strip(",.;") for w in t.split()]
    words = [w for w in words if w and w.lower() not in ("y", "and", "e", "de", "del", "la", "van", "von", "et", "al")]
    return bool(words) and sum(1 for w in words if w[:1].isupper()) >= 0.8 * len(words)


def parse_toc(lines: list[Line], body_size: float):
    """entries: {'label', 'title', 'page', 'x0', 'size', 'bold', 'lines'}"""
    entries = []
    buf: list[Line] = []
    for l in lines:
        t = l.text.strip()
        if not t or TOC_TITLE_RE.match(t):
            continue
        m = TRAIL_RE.search(t)
        if m and m.start() > 0:
            # chapter authors printed under the previous entry's title (no page number of their own)
            while buf and entries and entries[-1]["page"] and _authors_line(buf[0].text):
                a = buf.pop(0)
                entries[-1]["title"] += " " + a.text.strip()
                entries[-1]["lines"].append(a)
            # a section heading line without page number ('SECCIÓN I. ...') is an entry of its own
            if buf and re.match(r"^\s*(secci[óo]n|section|part|parte|unit|unidad)\b", buf[0].text, re.I) and buf[0].text.strip().isupper():
                b0 = buf.pop(0)
                lm0 = LABEL_RE.match(b0.text.strip() + " ")
                entries.append({"label": None, "title": b0.text.strip(), "page": None, "x0": b0.x0, "size": b0.size,
                                "bold": b0.all_bold(), "lines": [b0]})
            parts = buf + [l]
            text = " ".join(p.text.strip() for p in buf) + (" " if buf else "") + t[: m.start()].strip()
            text = re.sub(r"\s*\.{2,}\s*$", "", text).strip()
            lm = LABEL_RE.match(text)
            label = lm.group("label").strip() if lm else None
            title = text[lm.end():].strip() if lm else text
            entries.append({"label": label, "title": title, "page": m.group("page"), "x0": parts[0].x0,
                            "size": parts[0].size, "bold": parts[0].all_bold(), "lines": parts})
            buf = []
        else:
            buf.append(l)
            if len(buf) > 4:          # not a wrapped title: header line without page number
                entries.append({"label": None, "title": " ".join(b.text.strip() for b in buf[:-1]), "page": None,
                                "x0": buf[0].x0, "size": buf[0].size, "bold": buf[0].all_bold(), "lines": buf[:-1]})
                buf = buf[-1:]
    for b in buf:
        entries.append({"label": None, "title": b.text.strip(), "page": None, "x0": b.x0, "size": b.size,
                        "bold": b.all_bold(), "lines": [b]})
    # levels from indentation
    xs = sorted({round(e["x0"]) for e in entries})
    clusters = []
    for x in xs:
        if not clusters or x - clusters[-1] > 4:
            clusters.append(x)
    for e in entries:
        e["level"] = min(range(len(clusters)), key=lambda i: abs(clusters[i] - e["x0"])) if clusters else 0
    return entries
