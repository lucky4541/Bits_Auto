"""Heading detection and level assignment for one book.

Evidence combined (never font size alone):
  * style distinct from body (family / size / weight / caps / colour)
  * the line stands alone (short, followed by body text, space before)
  * repetition of the style across the book (a heading *style*)
  * section numbering (I., A., 1., 1.2.3)
  * PDF bookmarks (exact titles + levels) when present
Levels: heading styles ranked by prominence; bookmark-matched headings
override the ranking style-by-style.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .document_tree import Line
from .layout_analyzer import BookStyle, family

LIST_MARK = re.compile(r"^\s*(?:[•●■□▪◆◦○‣∙·►▶✓✔➢–—]|\(?\d{1,3}[.)](?!\d)|\(?[a-z][.)]\s|\(?[ivx]{1,4}[.)]\s)\s*")
NUMBERING = [
    (re.compile(r"^\s*\d+\.\d+\.\d+\.?\s"), 3),
    (re.compile(r"^\s*\d+\.\d+\.?\s"), 2),
    (re.compile(r"^\s*[IVXL]+\.\s"), 1),
    (re.compile(r"^\s*[A-Z]\.\s"), 2),
]


SCHEME_RX = [
    ("roman", re.compile(r"^(?:[IVXL]+)\.\s")),
    ("upper", re.compile(r"^[A-Z]\.\s")),
    ("decimal2", re.compile(r"^\d+\.\d+\.?\s")),
    ("arabic", re.compile(r"^\d+\.\s")),
    ("lower", re.compile(r"^[a-z]\.\s")),
]


def norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("­", "")
    s = re.sub(r"^(?:\d+(?:\.\d+)*|[ivxlc]+|[a-z])[.:)]\s+", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def caps_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(c.isupper() for c in letters) / len(letters) if letters else 0.0


def style_key(l: Line, style: BookStyle) -> tuple:
    m = l.main
    return (family(m.font), round(m.size * 2) / 2, l.all_bold(), l.all_italic(), caps_ratio(l.text) > 0.85,
            m.color != style.body_color)


@dataclass
class HeadingModel:
    body_key: tuple
    style_levels: dict = field(default_factory=dict)     # style key -> disp-level
    style_counts: dict = field(default_factory=dict)
    bookmark_titles: dict = field(default_factory=dict)  # norm title -> level (relative to chapter)
    chapter_styles: set = field(default_factory=set)

    def bookmark_level(self, text: str) -> int | None:
        nt = norm_title(text)
        if not nt:
            return None
        if nt in self.bookmark_titles:
            return self.bookmark_titles[nt]
        if len(nt) >= 10:
            for bt in self._by_prefix().get(nt[:10], []):
                if bt.startswith(nt):
                    return self.bookmark_titles[bt]
        return None

    def _by_prefix(self):
        if not hasattr(self, "_prefix_idx"):
            idx = {}
            for bt in self.bookmark_titles:
                idx.setdefault(bt[:10], []).append(bt)
            self._prefix_idx = idx
        return self._prefix_idx

    def level_for(self, l: Line, text: str, style: BookStyle) -> int | None:
        k = style_key(l, style)
        bl = self.bookmark_level(text)
        if bl is not None and k in self.style_levels:
            return bl
        return self.style_levels.get(k)


def prominence(k: tuple, style: BookStyle) -> float:
    fam, size, bold, italic, caps, color = k
    return (size / style.body_size) * 10 + bold * 2 + caps * 2 + (fam != style.body_family) * 1 + color * 1.5 - italic * 0.5


def is_candidate(l: Line, nxt: Line | None, prev: Line | None, style: BookStyle) -> bool:
    from .equation_detector import math_score
    t = l.text.strip()
    if len(t) < 2 or len(t) > 160:
        return False
    if LIST_MARK.match(t) and not any(rx.match(t) for rx, _ in NUMBERING):
        # '1. Muerte de las interneuronas' can be a numbered heading: only when the whole line
        # is set in one distinct style, short, and not a sentence
        spans = [s_ for s_ in l.spans if s_.text.strip()]
        uniform = len({(family(s_.font), round(s_.size), s_.bold, s_.italic) for s_ in spans}) == 1
        m0 = l.main
        distinct0 = family(m0.font) != style.body_family or m0.bold or m0.color != style.body_color or abs(m0.size - style.body_size) > 0.6
        if not (uniform and distinct0 and len(t) < 80 and not re.search(r"[.;:,]$", t)):
            return False
    if re.fullmatch(r"[\d\s.,;:\-–—()%/=+<>×·]+", t) or sum(c.isalpha() for c in t) < 2:
        return False
    if math_score(l) >= 0.3 or any(ch in t for ch in "=±×÷∑√≈≠≤≥\ufffd"):
        return False
    words = re.findall(r"[^\W\d_]{3,}", t)
    if not words or sum(c.isalpha() for c in t) < 4:
        return False
    if t.endswith((",", ";", "-", "–")):
        return False
    m = l.main
    ratio = m.size / style.body_size if style.body_size else 1.0
    bold, caps = l.all_bold(), caps_ratio(t) > 0.85 and sum(c.isalpha() for c in t) > 3
    fam_diff = family(m.font) != style.body_family
    colored = m.color != style.body_color
    italic = l.all_italic()
    if ratio >= 1.15:
        pass
    elif ratio >= 0.93:
        if not (bold or caps or colored or (fam_diff and italic)):
            return False
        if italic and not bold and not fam_diff and not colored:
            return False
    else:
        if not (bold and caps):
            return False
    # run-in heading: styled start then regular text on the same line -> not a heading
    if not bold and any(s.bold for s in l.spans[:1]) and not fam_diff and ratio < 1.15:
        return False
    # headings are short lines, not justified full lines of running text
    if len(t) > 90 and ratio < 1.15:
        return False
    for o in (prev, nxt):
        if o is not None and o.page == l.page:
            ov = min(o.y1, l.y1) - max(o.y0, l.y0)
            if ov > 0.5 * min(o.y1 - o.y0, l.y1 - l.y0) and (o.x0 >= l.x1 - 1 or o.x1 <= l.x0 + 1) and abs(o.x0 - l.x0) < 200:
                return False     # a fragment sharing its row with other text (formula, table row)
    if t.endswith("?") and len(t) > 45:
        return False
    if nxt is not None and nxt.page == l.page and nxt.y0 >= l.y1 - 1:
        if style_key(nxt, style) == style_key(l, style) and len(nxt.text.strip()) > 90:
            return False     # first line of a styled paragraph
    if prev is not None and prev.page == l.page and prev.y1 <= l.y0 + 1:
        gap = l.y0 - prev.y1
        ptxt = prev.text.rstrip()
        if gap < 0.15 * style.line_pitch and ptxt and not re.search(r"[.!?:»”)]$", ptxt) and style_key(prev, style)[:2] == (style.body_family, round(style.body_size * 2) / 2):
            return False     # glued to an unfinished body line
    return True


def learn_headings(flow: list[Line], style: BookStyle, bookmarks: list[tuple[int, str, int]] | None = None,
                   chapter_level: int | None = None) -> HeadingModel:
    body_key = (style.body_family, round(style.body_size * 2) / 2, False, False, False, False)
    counts = Counter()
    by_key = defaultdict(list)
    lens = defaultdict(list)
    ends = Counter()
    for i, l in enumerate(flow):
        nxt = flow[i + 1] if i + 1 < len(flow) else None
        prev = flow[i - 1] if i else None
        if l.size >= 1.8 * style.body_size:
            continue            # display titles (chapter / part openers) are not section headings
        if is_candidate(l, nxt, prev, style):
            k = style_key(l, style)
            counts[k] += 1
            by_key[k].append(l)
            t = l.text.strip()
            lens[k].append(len(t))
            if re.search(r"[.?!]$", t):
                ends[k] += 1
    # a heading *style* behaves like headings: short, rarely sentence-final punctuation
    for k in list(counts):
        avg = sum(lens[k]) / len(lens[k])
        if k[1] < 1.3 * style.body_size and (avg > 70 or ends[k] / counts[k] > 0.35):
            del counts[k]
    model = HeadingModel(body_key=body_key, style_counts=dict(counts))
    # bookmarks deeper than chapter level give exact titles and levels
    if bookmarks and chapter_level is not None:
        for lvl, title, _page in bookmarks:
            if lvl > chapter_level:
                model.bookmark_titles[norm_title(title)] = min(6, lvl - chapter_level)
    styles = [k for k, n in counts.items() if k != body_key and (n >= 3 or (n >= 1 and k[1] >= 1.3 * style.body_size))]
    # styles learnt from bookmark matches
    by_style = defaultdict(Counter)
    if model.bookmark_titles:
        for l in flow:
            bl = model.bookmark_level(l.text)
            if bl is not None:
                by_style[style_key(l, style)][bl] += 1
    # a style inherits a bookmark level only with real evidence (not one accidental title match)
    by_style = {k: c for k, c in by_style.items() if sum(c.values()) >= max(3, 0.3 * counts.get(k, 0))}
    ranked = sorted(styles, key=lambda k: -prominence(k, style))
    # 1) outline numbering (I. / A. / 1. / a.) is the strongest level signal when present
    schemes = {}
    for k in ranked:
        starts = Counter()
        for l in by_key.get(k, []):
            t = l.text.strip()
            for name, rx in SCHEME_RX:
                if rx.match(t):
                    starts[name] += 1
                    break
        if starts:
            name, n = starts.most_common(1)[0]
            if n >= 0.6 * len(by_key.get(k, [])) and n >= 3:
                schemes[k] = name
    present = [n for n, _ in SCHEME_RX if n in schemes.values()]
    if len(present) >= 2:
        order = {n: i + 1 for i, n in enumerate(present)}
        top_prom = max(prominence(k, style) for k in schemes)
        for k in ranked:
            if k in schemes:
                model.style_levels[k] = order[schemes[k]]
            elif k in by_style:
                model.style_levels[k] = by_style[k].most_common(1)[0][0]
            elif prominence(k, style) < top_prom and counts[k] >= 5:
                model.style_levels[k] = len(present) + 1
        model.scheme = {str(k): v for k, v in schemes.items()}
    else:
        # 2) prominence tiers; bookmarked styles keep their level; rare styles never open a tier
        level = 0
        last_p = None
        for k in ranked:
            p = prominence(k, style)
            if k in by_style:
                level = by_style[k].most_common(1)[0][0]
                model.style_levels[k] = level
                last_p = p
                continue
            if counts[k] < 5 and level > 0 and k[1] < 1.3 * style.body_size:
                model.style_levels[k] = level
                continue
            if last_p is None or last_p - p > 1.2:
                level += 1
                last_p = p
            model.style_levels[k] = min(level, 5)
    # re-number so used levels are contiguous starting at 1
    used = sorted(set(model.style_levels.values()))
    remap = {v: i + 1 for i, v in enumerate(used)}
    model.style_levels = {k: remap[v] for k, v in model.style_levels.items()}
    return model
