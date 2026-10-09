"""Paragraph segmentation, line joining, hyphenation and inline runs.

A new paragraph starts on: first-line indent, extra vertical space, a style
change, a list marker, or when the previous line ended short with sentence
punctuation. Separate paragraphs are never merged.
"""
from __future__ import annotations

import re
from collections import Counter

from .document_tree import Line, PageMark, T
from .layout_analyzer import BookStyle, family

SENT_END = re.compile(r"[.!?:;»”\")\]]\s*$")
SOFT_HYPHEN = "­"
WORD_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.U)


class Hyphenation:
    """Decides whether a line-final hyphen is a typesetting break, using the book itself as lexicon."""

    def __init__(self):
        self.words = Counter()
        self.compounds = Counter()

    def learn(self, lines):
        for l in lines:
            t = l.text.replace(SOFT_HYPHEN, "")
            core = t[:-1] if t.rstrip().endswith("-") else t
            for w in WORD_RE.findall(core.lower()):
                if "-" in w:
                    self.compounds[w] += 1
                    for part in w.split("-"):
                        self.words[part] += 1
                else:
                    self.words[w] += 1

    def join(self, left_word: str, right_word: str) -> bool:
        """True -> remove the hyphen ('conver-' + 'sion'); False -> keep ('well-' + 'known')."""
        lw, rw = left_word.lower(), right_word.lower()
        if not lw or not rw or not rw[0].isalpha() or not lw[-1].isalpha():
            return False
        if right_word[0].isupper():
            return False
        compound = f"{lw}-{rw}"
        joined = lw + rw
        if self.compounds.get(compound, 0) > self.words.get(joined, 0):
            return False
        if self.words.get(joined, 0) > 0:
            return True
        if len(lw) >= 3 and len(rw) >= 3 and self.words.get(lw, 0) >= 2 and self.words.get(rw, 0) >= 2:
            return False     # both halves are words of this book: a real compound (hemoglobin-bound)
        return True  # line-end hyphen in running text is a typesetting break


def split_left_edges(lines: list[Line], tol: float = 3.0) -> list[float]:
    xs = sorted(round(l.x0) for l in lines)
    edges = []
    for x in xs:
        if not edges or x - edges[-1][-1] > tol:
            edges.append([x])
        else:
            edges[-1].append(x)
    return [min(e) for e in edges if len(e) >= 2]


class PageGeometry:
    """Column left/right edges per page, for indent / short-line tests."""

    def __init__(self, page_lines: dict[int, list[Line]], style: BookStyle):
        self.style = style
        self.left: dict[int, list[float]] = {}
        self.right: dict[int, list[tuple[float, float]]] = {}
        for p, ls in page_lines.items():
            body = [l for l in ls if abs(l.size - style.body_size) < 1.2]
            edges = split_left_edges(body) or split_left_edges(ls) or [min((l.x0 for l in ls), default=0)]
            self.left[p] = edges
            rights = []
            for e in edges:
                grp = sorted(l.x1 for l in body if e - 1 <= l.x0 <= e + 3 * style.body_size)
                if grp:
                    rights.append((e, grp[int(0.9 * (len(grp) - 1))]))
            self.right[p] = rights

    def left_edge(self, l: Line) -> float:
        edges = [e for e in self.left.get(l.page, []) if e <= l.x0 + 1.5]
        return max(edges) if edges else l.x0

    def column_left(self, l: Line) -> float:
        """Column start: the leftmost edge not farther than ~4 em left of the line."""
        edges = [e for e in self.left.get(l.page, []) if l.x0 - 4 * self.style.body_size <= e <= l.x0 + 1.5]
        return min(edges) if edges else l.x0

    def right_edge(self, l: Line) -> float:
        cl = self.column_left(l)
        cands = [r for e, r in self.right.get(l.page, []) if abs(e - cl) < 4 * self.style.body_size]
        return max(cands) if cands else l.x1

    def indent(self, l: Line) -> float:
        return l.x0 - self.column_left(l)

    def short(self, l: Line) -> bool:
        return l.x1 < self.right_edge(l) - 1.5 * self.style.body_size


def same_style(a: Line, b: Line) -> bool:
    return family(a.main.font) == family(b.main.font) and abs(a.size - b.size) < 0.6


def starts_new_paragraph(prev: Line, cur: Line, geo: PageGeometry, style: BookStyle) -> tuple[bool, float]:
    """(new?, confidence)."""
    em = style.body_size
    ptxt = prev.text.rstrip()
    if not same_style(prev, cur):
        return True, 0.85
    if prev.page == cur.page and cur.y0 >= prev.y0:
        gap = cur.y0 - prev.y1
        lead = max(0.0, style.line_pitch - prev.height)
        if gap > lead + 0.45 * style.line_pitch:
            return True, 0.93
        if cur.x0 > prev.x1 and cur.y0 < prev.y1:      # same row, different column
            return True, 0.6
    ind_cur = geo.indent(cur)
    ind_prev = geo.indent(prev)
    if ind_cur > 0.6 * em and ind_cur > ind_prev + 0.5 * em:
        return True, 0.9 if SENT_END.search(ptxt) else 0.75
    if geo.short(prev) and SENT_END.search(ptxt):
        return True, 0.8
    if prev.page != cur.page or cur.y0 < prev.y0:
        # page/column break: continue unless the previous line closed a sentence and the new one is indented
        if not SENT_END.search(ptxt):
            return False, 0.9
        if cur.text[:1].islower():
            return False, 0.85
        return (ind_cur > 0.6 * em), 0.7
    return False, 0.9


def despace_runs(runs: list[dict]) -> list[dict]:
    """'R E F E R E N C E S' -> 'REFERENCES' when a whole heading is letter-spaced."""
    txt = "".join(r.get("text", "") for r in runs).strip()
    if not re.fullmatch(r"(?:\w\s){2,}\w\s*", txt):
        return runs
    out = []
    for r in runs:
        if r.get("k") == "t":
            out.append({**r, "text": re.sub(r"(?<=\w)\s(?=\w)", "", r["text"])})
        else:
            out.append(r)
    return out


def merge_ln(a, b):
    out = list(a or [])
    for x in b or []:
        if x not in out:
            out.append(x)
    return out


def line_runs(line: Line) -> list[dict]:
    from .equation_detector import MATH_FONT, RELATIONS, reconstruct_math, is_math_fragment
    def math_span(span):
        t = span.text.strip()
        return bool(t) and len(t) < 80 and (span.italic or span.sub or span.sup or MATH_FONT.search(span.font)
                    or not re.search(r"[^\W\d_]", t, re.U))

    runs = []
    i = 0
    while i < len(line.spans):
        end = i
        while end < len(line.spans) and math_span(line.spans[end]):
            end += 1
        group = line.spans[i:end]
        text = "".join(s.text for s in group)
        if group and any(c in RELATIONS for c in text) and any(s.italic or s.sub or s.sup or MATH_FONT.search(s.font) for s in group):
            box = (min(s.bbox[0] for s in group), min(s.bbox[1] for s in group),
                   max(s.bbox[2] for s in group), max(s.bbox[3] for s in group))
            candidate = Line(group, box, line.page)
            ast = reconstruct_math([candidate])[0] if is_math_fragment(candidate) else None
            if ast:
                if text[:1].isspace():
                    runs.append(T(" "))
                runs.append({"k": "math", "text": text.strip(), "mathml": ast, "st": [],
                             "ln": [line.uid] if line.uid else []})
                if text[-1:].isspace():
                    runs.append(T(" "))
                i = end
                continue
        span = line.spans[i]
        text = re.sub(r"[ \t\r\n]*[\r\n][ \t\r\n]*", " ", span.text)
        text = re.sub(r"(?<=\S) {2,}(?=\S)", " ", text)
        if text:
            st = span.styles()
            if runs and runs[-1]["k"] == "t" and set(runs[-1]["st"]) == set(st):
                runs[-1]["text"] += text
            else:
                runs.append(T(text, st))
            if line.uid:
                runs[-1]["ln"] = [line.uid]
        i += 1
    return runs


def append_line(inlines: list[dict], line: Line, hyph: Hyphenation, pending_mark: dict | None = None) -> None:
    """Append a line's runs to a paragraph with a space or de-hyphenation."""
    runs = line_runs(line)
    if not runs:
        return
    # strip leading whitespace of the new line
    runs[0]["text"] = runs[0]["text"].lstrip()
    if inlines:
        last = next((i for i in reversed(inlines) if i["k"] in ("t", "cite", "math")), None)
        if last is not None:
            txt = last["text"].rstrip()
            last["text"] = txt
            first_word = re.match(r"[^\W\d_]+", runs[0]["text"])
            if txt.endswith(SOFT_HYPHEN) and first_word:
                last["text"] = txt[:-1]
            elif txt.endswith("-") and not txt.endswith("--") and first_word:
                left_word = re.search(r"([^\W\d_]+)-$", txt)
                if left_word and hyph.join(left_word.group(1), first_word.group(0)):
                    last["text"] = txt[:-1]
                else:
                    pass  # keep hyphen, no space
            elif txt.endswith(("–", "—", "/")):
                pass
            else:
                if last["k"] == "math":
                    inlines.append(T(" "))
                else:
                    last["text"] = txt + " "
    if pending_mark is not None:
        inlines.append(pending_mark)
    for r in runs:
        if r["k"] == "t" and inlines and inlines[-1]["k"] == "t" and inlines[-1]["st"] == r["st"]:
            inlines[-1]["text"] += r["text"]
            if r.get("ln"):
                inlines[-1]["ln"] = merge_ln(inlines[-1].get("ln"), r["ln"])
        else:
            inlines.append(r)


def finish_inlines(inlines: list[dict]) -> list[dict]:
    """Trim, collapse whitespace, drop empty runs."""
    out = []
    for i in inlines:
        if i["k"] == "t":
            i = {**i, "text": re.sub(r"[ \t ]{2,}", " ", i["text"].replace("\t", " ").replace(SOFT_HYPHEN, ""))}
            if not i["text"]:
                continue
        out.append(i)
    # trim ends
    for idx in range(len(out)):
        if out[idx]["k"] == "t":
            out[idx]["text"] = out[idx]["text"].lstrip()
            if out[idx]["text"]:
                break
    for idx in range(len(out) - 1, -1, -1):
        if out[idx]["k"] == "t":
            out[idx]["text"] = out[idx]["text"].rstrip()
            if out[idx]["text"]:
                break
    return [i for i in out if not (i["k"] == "t" and not i["text"])]


def page_mark_for(page_info) -> dict | None:
    if page_info is None or not page_info.folio:
        return None
    return PageMark(page_info.folio, page_info.index)
