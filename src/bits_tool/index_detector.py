"""Back-of-book index parsing: entries, sub-entries, page references, see / see also."""
from __future__ import annotations

import re
from collections import Counter

from .document_tree import Line
from .paragraph_detector import line_runs

PAGE = r"(?:[ivxlc]+|\d{1,4})[a-z]{0,2}(?:\s*[–\-]\s*(?:\d{1,4}|[ivxlc]+)[a-z]{0,2})?"
REFS_TAIL = re.compile(rf"(?P<sep>,\s*|\s+)(?P<refs>{PAGE}(?:\s*,\s*{PAGE})*)\s*\.?\s*$")
SEE_RE = re.compile(r"(?P<pre>[.,;]?\s*\(?)(?P<kind>see also|See also|see|See|v[ée]ase tambi[ée]n|V[ée]ase tambi[ée]n|v[ée]ase|V[ée]ase|V\. tambi[ée]n|V\.|ver tambi[ée]n|Ver tambi[ée]n|ver|Ver)\s+(?P<target>[^()]+?)\)?\.?\s*$")
LETTER_RE = re.compile(r"^\s*[A-ZÁÉÍÓÚÑ]\s*$|^\s*[A-Z]\s*[-–]\s*[A-Z]\s*$")
TITLE_RE = re.compile(r"^\s*(index|índice|indice|subject index|author index|índice alfab[ée]tico|índice de materias|índice de pacientes)\b", re.I)


def split_refs(text: str):
    """term, [page tokens], see[], see_also[]"""
    see, see_also = [], []
    t = text.rstrip()
    m = SEE_RE.search(t)
    if m and m.start() > 0:
        kind = m.group("kind").lower()
        targets = [x.strip() for x in re.split(r";", m.group("target")) if x.strip()]
        (see_also if "also" in kind or "tambi" in kind else see).extend(targets)
        t = t[: m.start()].rstrip(" ,.;(")
    refs = []
    m = REFS_TAIL.search(t)
    if m and m.start() > 0:
        refs = [r.strip() for r in m.group("refs").split(",") if r.strip()]
        t = t[: m.start()].rstrip(" ,")
    return t, refs, see, see_also


def page_tokens(ref: str):
    """'123–125f' -> [('123', 'start'), ('125f', 'end')] ; '105b' -> [('105b', None)]"""
    m = re.match(r"^(?P<a>[ivxlc]+|\d{1,4})(?P<as>[a-z]{0,2})\s*[–\-]\s*(?P<b>[ivxlc]+|\d{1,4})(?P<bs>[a-z]{0,2})$", ref)
    if m:
        a, b = m.group("a"), m.group("b")
        if a.isdigit() and b.isdigit() and len(b) < len(a):      # 123–25 -> 125
            b = a[: len(a) - len(b)] + b
        return [(a, m.group("as"), "start-of-range"), (b, m.group("bs"), "end-of-range")]
    m = re.match(r"^(?P<a>[ivxlc]+|\d{1,4})(?P<as>[a-z]{0,2})$", ref)
    if m:
        return [(m.group("a"), m.group("as"), None)]
    return []


def parse_index(lines: list[Line], body_size: float):
    """Return (title, intro_lines, divs) where divs = [{'title': str|None, 'entries': [entry]}],
    entry = {'level', 'runs', 'term', 'refs', 'see', 'see_also', 'page'}"""
    title = None
    intro: list[Line] = []
    divs = [{"title": None, "entries": []}]
    # indentation steps per page/column
    by_col: dict[tuple, list[Line]] = {}
    for l in lines:
        by_col.setdefault((l.page, round(l.x0 / 60)), []).append(l)
    col_left = {}
    for k, ls in by_col.items():
        base = min(x.x0 for x in ls)
        for x in ls:
            col_left[id(x)] = base
    col_right = {}
    for k, ls in by_col.items():
        r = sorted(x.x1 for x in ls)
        for x in ls:
            col_right[id(x)] = r[int(0.9 * (len(r) - 1))]
    indents = Counter()
    for l in lines:
        d = l.x0 - col_left[id(l)]
        if d > 1:
            indents[round(d)] += 1
    step = min((d for d, n in indents.items() if n >= 3), default=body_size)
    step = max(step, 0.6 * body_size)
    started = False
    prev_entry = None
    prev_line = None
    for l in lines:
        text = l.text.strip()
        if not text:
            continue
        if not started and title is None and TITLE_RE.match(text) and l.size >= body_size:
            title = text
            continue
        if LETTER_RE.match(text):
            started = True
            divs.append({"title": text, "entries": []})
            prev_entry = None
            prev_line = l
            continue
        term, refs, see, see_also = split_refs(text)
        if not started and not refs and not see and not see_also and len(text) > 60:
            intro.append(l)
            continue
        started = True
        indent = l.x0 - col_left[id(l)]
        level = max(0, int(round(indent / step)))
        turnover = False
        if prev_entry is not None and prev_line is not None:
            prev_no_refs = not prev_entry["refs"] and not prev_entry["see"] and not prev_entry["see_also"]
            prev_full = prev_line.x1 >= col_right.get(id(prev_line), prev_line.x1) - 1.5 * body_size
            if prev_line.text.rstrip().endswith((",", "-", "–", "(", ";")) or (prev_full and level > prev_entry["level"] and prev_no_refs is False):
                turnover = True
            elif level > prev_entry["level"] + 1:
                turnover = True
        if turnover:
            e = prev_entry
            joined = (e["raw"] + (" " if not e["raw"].endswith("-") else "") + text)
            t2, r2, s2, sa2 = split_refs(joined)
            e.update({"raw": joined, "term": t2, "refs": r2, "see": s2, "see_also": sa2})
            e["runs"] += [{"k": "t", "text": " ", "st": []}] + line_runs(l)
            prev_line = l
            continue
        entry = {"level": level, "raw": text, "term": term, "refs": refs, "see": see, "see_also": see_also,
                 "runs": line_runs(l), "page": l.page}
        divs[-1]["entries"].append(entry)
        prev_entry = entry
        prev_line = l
    divs = [d for d in divs if d["entries"] or d["title"]]
    # normalise levels so the first level is 0 and steps are at most +1
    for d in divs:
        stack = []
        for e in d["entries"]:
            lv = e["level"]
            if not stack:
                lv = 0
            else:
                lv = min(lv, stack[-1] + 1)
            e["level"] = lv
            stack = stack[:lv] + [lv]
    return title, intro, divs
