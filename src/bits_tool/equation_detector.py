"""Spatial equation regions and conservative Presentation MathML reconstruction.

Text syntax is supporting evidence. Fraction rules, baselines and span boxes
supply relationships; unsupported layouts retain a source crop for review.
"""
from __future__ import annotations

import re
from .document_tree import Line, Region

MATH_FONT = re.compile(r"symbol|stix|cambria ?math|euclid|cmmi|cmsy|cmex", re.I)
RELATIONS = set("=≈≠≤≥→←↔⇌⇄⇆⟶⟵")
ARROWS = set("→←↔⇌⇄⇆⟶⟵")
OPS = set("=±×÷∑∫√≈≠≤≥∞∂∆∇·→←↔⇌⇄⇆⟶⟵+−*/()[]{}^_/")


def _box(objects):
    bs = [o.bbox if hasattr(o, "bbox") else o for o in objects]
    return min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)


def _overlap(a, b):
    return min(a[2], b[2]) - max(a[0], b[0])


def is_math_fragment(line):
    text = line.text.strip()
    # Ordinary words, captions and italic sentences do not become equations.
    words = re.findall(r"[^\W\d_]+", text, re.U)
    if not text or len(text) > 100:
        return False
    visible = [s for s in line.spans if s.text.strip()]
    if (len(visible) > 1 and not visible[0].italic and
            visible[0].text.rstrip().isalpha() and visible[0].text[-1:].isspace() and
            visible[1].italic and not (visible[1].sub or visible[1].sup)):
        # An upright prose prefix followed by italic math belongs to its
        # sentence (including short conjunctions), not a display identifier.
        return False
    if all(len(w) <= 3 for w in words):
        return True
    # Preserve a variable with a separately positioned script (e.g. localized
    # descriptive subscripts). Never accept long prose merely for using italics.
    base_text = "".join(s.text for s in line.spans if not (s.sub or s.sup))
    base_words = re.findall(r"[^\W\d_]+", base_text, re.U)
    return (any(s.sub or s.sup for s in line.spans) and
            all(len(w) <= 3 for w in base_words) and
            all(len(s.text.strip()) <= 8 for s in line.spans if s.sub or s.sup))


def math_score(line):
    if not is_math_fragment(line):
        return 0.0
    return (0.6 if any(c in RELATIONS for c in line.text) else 0.0) + (
        0.25 if any(MATH_FONT.search(s.font) for s in line.spans) else 0.0)


def is_display_math(line, col_left, em):
    """Compatibility predicate; actual display classification needs a region."""
    return math_score(line) >= 0.6 and line.x0 - col_left > em * 0.5


def linearize(lines):
    out = []
    for line in sorted(lines, key=lambda l: (l.y0, l.x0)):
        for s in line.spans:
            text = s.text.strip()
            if text:
                out.append(("_" if s.sub else "^" if s.sup else "") + text)
    return " ".join(out)


def _mnode(tag, text=None, children=None):
    node = {"tag": tag}
    if text is not None:
        node["text"] = text
    if children:
        node["children"] = children
    return node


def _row(nodes):
    return _mnode("mrow", children=nodes)


def _tokenize_math(text):
    # Explicit script syntax and Unicode scripts are evidence, unlike variable
    # names (Vmax must never be silently changed to V with a max subscript).
    text = re.sub(r"[\x00-\x1f\x7f\u00ad]", "", text)
    subs = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
    sups = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
    text = re.sub(r"[₀-₉]+", lambda m: "_{" + m[0].translate(subs) + "}", text)
    text = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", lambda m: "^{" + m[0].translate(sups) + "}", text)
    parts = re.findall(r"[_^](?:\{[^{}]+\}|[A-Za-z0-9]+)|[^\W\d_]+|\d+(?:\.\d+)?|[^\s]", text, re.U)
    out = []
    for p in parts:
        if p[0] in "_^" and len(p) > 1 and out:
            base = out.pop()
            script = _row(_tokenize_math(p[1:].strip("{}")))
            kind = "msub" if p[0] == "_" else "msup"
            if base["tag"] == ("msup" if kind == "msub" else "msub"):
                b, other = base["children"]
                out.append(_mnode("msubsup", children=[b, script, other] if kind == "msub" else [b, other, script]))
            else:
                out.append(_mnode(kind, children=[base, script]))
        else:
            out.append(_mnode("mn" if p[0].isdigit() else "mi" if p[0].isalpha() else "mo", p))
    return out


def _span_row(spans):
    """Attach scripts only to a geometrically adjacent base on their left."""
    from dataclasses import replace
    # PDF font runs may interleave: one run holds brackets, another the
    # identifiers between them. Use retained glyph coordinates in that case.
    expanded = []
    for s in spans:
        interleaved = any(o is not s and not (s.sub or s.sup or o.sub or o.sup) and
                          min(s.bbox[2], o.bbox[2]) - max(s.bbox[0], o.bbox[0]) > 1 and
                          abs((s.bbox[1]+s.bbox[3]-o.bbox[1]-o.bbox[3])/2) < .5 * s.size for o in spans)
        if interleaved and s.glyphs:
            expanded.extend(replace(s, text=g['text'], bbox=tuple(g['bbox']), origin=tuple(g['origin']), glyphs=[])
                            for g in s.glyphs)
        else:
            expanded.append(s)
    out = []
    previous = None
    for s in sorted(expanded, key=lambda s: (s.bbox[0], s.bbox[1])):
        nodes = _tokenize_math(s.text.strip())
        if not nodes:
            continue
        if (s.sub or s.sup) and out and previous is not None:
            gap = s.bbox[0] - previous.bbox[2]
            if -1 <= gap <= max(s.size, previous.size):
                base = out.pop()
                out.append(_mnode("msub" if s.sub else "msup", children=[base, _row(nodes)]))
            else:
                return None
        else:
            out.extend(nodes)
        previous = s
    return _row(out)


def reconstruct_math(lines, drawings=()):
    """Return (AST or None, reason). None means retain a source crop, not guess."""
    spans = [s for l in lines for s in l.spans if s.text.strip()]
    if not spans:
        return None, "no extractable math text"
    em = max(s.size for s in spans)
    if any(c in linearize(lines) for c in "√∑∫"):
        return None, "radical or large operator extent requires review"
    # Independent fractions may share one relation chain. Each rule owns its
    # numerator and denominator; ownership conflicts require review.
    fractions, assigned = [], set()
    for d in drawings:
        if not d.get("hline"):
            continue
        bar = d["bbox"]
        y = (bar[1] + bar[3]) / 2
        inside = [s for s in spans if s.bbox[0] >= bar[0] - 2 and s.bbox[2] <= bar[2] + 2]
        top = [s for s in inside if (s.bbox[1]+s.bbox[3])/2 < y-1 and -.15*em <= y-s.bbox[3] <= 2*em]
        bot = [s for s in inside if (s.bbox[1]+s.bbox[3])/2 > y+1 and -2 <= s.bbox[1]-y <= 2*em]
        if not top or not bot or _overlap(_box(top), _box(bot)) <= 0:
            continue
        owned = {id(s) for s in top + bot}
        if owned & assigned:
            return None, "nested or overlapping fraction rules require review"
        for side in (top, bot):
            centers = [(s.bbox[1]+s.bbox[3])/2 for s in side if not s.sup and not s.sub]
            if centers and max(centers)-min(centers) > .6*em:
                return None, "multiple rows within fraction require review"
        numerator, denominator = _span_row(top), _span_row(bot)
        if numerator is None or denominator is None:
            return None, "ambiguous fraction scripts"
        fractions.append((bar, y, _mnode("mfrac", children=[numerator, denominator])))
        assigned.update(owned)
    if fractions:
        bbox = _box(lines)
        if any(not d.get("hline") and
               d["bbox"][0] >= bbox[0] - em and d["bbox"][2] <= bbox[2] + em and
               d["bbox"][1] >= bbox[1] - em and d["bbox"][3] <= bbox[3] + em for d in drawings):
            return None, "unrecognized vector components within fraction"
        rest = [s for s in spans if id(s) not in assigned]
        if any(s.bbox[0] < b[2] and s.bbox[2] > b[0] for s in rest for b, _, _ in fractions):
            return None, "overlapping fraction components"
        ys = [y for _, y, _ in fractions]
        if max(ys)-min(ys) > .5*em or any(abs((s.bbox[1]+s.bbox[3])/2-sum(ys)/len(ys)) > em for s in rest):
            return None, "multiple mathematical rows"
        nodes = []
        for bar, _, frac in sorted(fractions, key=lambda f:f[0][0]):
            left = [s for s in rest if s.bbox[2] <= bar[0]]
            row = _span_row(left)
            if row is None:
                return None, "ambiguous scripts beside fraction"
            nodes.extend(row.get("children", [])); nodes.append(frac)
            rest = [s for s in rest if s not in left]
        row = _span_row(rest)
        if row is None:
            return None, "ambiguous scripts beside fraction"
        return _row(nodes + row.get("children", [])), "fraction rules with disjoint numerator and denominator ownership"

    from .math_geometry import vector_arrows
    inferred, recognized_paths = vector_arrows(drawings, _box(lines), em)
    spans.extend(inferred)
    # Attach generic labels above/below separately extracted arrow glyphs.
    arrows = [s for s in spans if s.text.strip() in ARROWS]
    if arrows:
        bbox = _box(lines)
        if any(d not in recognized_paths and not d.get('hline') and not d.get('vline') and
               d['bbox'][0] >= bbox[0] and d['bbox'][2] <= bbox[2] and
               d['bbox'][1] >= bbox[1] and d['bbox'][3] <= bbox[3] for d in drawings):
            return None, "unrecognized paths within reaction"
        labels = {}
        used = set()
        for s in spans:
            if s in arrows:
                continue
            matches = []
            for arrow in arrows:
                ay = (arrow.bbox[1] + arrow.bbox[3]) / 2
                sy = (s.bbox[1] + s.bbox[3]) / 2
                sx = (s.bbox[0] + s.bbox[2]) / 2
                if arrow.bbox[0] - 2 <= sx <= arrow.bbox[2] + 2 and 0.22 * em < abs(sy - ay) < 3 * em:
                    matches.append((abs(sx - (arrow.bbox[0] + arrow.bbox[2]) / 2), arrow, "above" if sy < ay else "below"))
            if matches:
                _, arrow, side = min(matches, key=lambda t: t[0])
                labels.setdefault((id(arrow), side), []).append(s)
                used.add(id(s))
        baseline = [s for s in spans if id(s) not in used]
        centers = [(s.bbox[1] + s.bbox[3]) / 2 for s in baseline if not s.sub and not s.sup]
        if centers and max(centers) - min(centers) > 0.6 * em:
            return None, "reaction has unresolved baselines"
        out = []
        for s in sorted(baseline, key=lambda s: s.bbox[0]):
            if s not in arrows:
                if s.sub or s.sup:
                    return None, "reaction script needs spatial review"
                out.extend(_tokenize_math(s.text))
                continue
            node = _mnode("mo", s.text.strip())
            above = _span_row(labels.get((id(s), "above"), []))
            below = _span_row(labels.get((id(s), "below"), []))
            if above is None or below is None:
                return None, "ambiguous arrow labels"
            if above.get("children") and below.get("children"):
                node = _mnode("munderover", children=[node, below, above])
            elif above.get("children"):
                node = _mnode("mover", children=[node, above])
            elif below.get("children"):
                node = _mnode("munder", children=[node, below])
            out.append(node)
        return _row(out), "arrow shafts and heads with spatial labels" if inferred else "arrow glyphs with spatially associated labels"

    # Vector operators cannot be replaced with inferred Unicode operators.
    bbox = _box(lines)
    if any(not d.get("hline") and not d.get("vline") and
           d["bbox"][0] >= bbox[0] - em and d["bbox"][2] <= bbox[2] + em and
           d["bbox"][1] >= bbox[1] - em and d["bbox"][3] <= bbox[3] + em for d in drawings):
        return None, "vector mathematical symbols require review"
    centers = [(s.bbox[1] + s.bbox[3]) / 2 for s in spans if not s.sub and not s.sup]
    if centers and max(centers) - min(centers) > 0.6 * em:
        return None, "multiple baselines without supported structural evidence"
    if any(c in linearize(lines) for c in "√∑∫"):
        return None, "radical or large operator extent requires review"
    return _span_row(spans), "one baseline with explicit scripts and operators"


def build_mathml_ast(lines, drawings=()):
    return reconstruct_math(lines, drawings)[0]


def equation_regions(page, style, occupied=()):
    """Group math before reading order, bounded by columns and prose barriers."""
    from .layout_analyzer import column_of
    em = style.body_size
    candidates = [l for l in page.lines if l.role == "body" and is_math_fragment(l)]
    seeds = [l for l in candidates if any(c in RELATIONS | set("√∑∫") for c in l.text)]
    # A drawn fraction is a seed only when both sides contain short math text.
    for d in page.drawings:
        if not d.get("hline"):
            continue
        b = d["bbox"]
        near = [l for l in candidates if _overlap(l.bbox, b) > 0 and l.y0 < b[3] + 2 * em and l.y1 > b[1] - 2 * em]
        if any(l.y1 <= b[1] + 2 for l in near) and any(l.y0 >= b[3] - 2 for l in near):
            seeds.extend(near)
    # Vector reaction schemes: recognize the region, retain the vector symbols.
    for l in candidates:
        if "+" in l.text and any(d["n"] >= 2 and not d.get("hline") and
                                 abs(d["bbox"][1] - l.y0) < 2 * em and
                                 0 <= d["bbox"][0] - l.x1 < 4 * em for d in page.drawings):
            seeds.append(l)
    used = set()
    regions = []
    for seed in sorted(seeds, key=lambda l: (l.y0, l.x0)):
        if id(seed) in used:
            continue
        col = column_of(seed, page.columns)
        group = [seed]
        changed = True
        while changed:
            changed = False
            for line in candidates:
                if id(line) in used or line in group or column_of(line, page.columns) != col:
                    continue
                for other in group:
                    dx = max(line.x0 - other.x1, other.x0 - line.x1, 0)
                    dy = max(line.y0 - other.y1, other.y0 - line.y1, 0)
                    overlap_y = min(line.y1, other.y1) - max(line.y0, other.y0)
                    union = _box([line, other])
                    connector = any(d["n"] >= 2 and not d.get("hline") and
                                    d["bbox"][0] >= union[0] and d["bbox"][2] <= union[2] and
                                    d["bbox"][1] >= union[1] - em and d["bbox"][3] <= union[3] + em
                                    and d["bbox"][2] >= max(line.x0, other.x0) - em and
                                    d["bbox"][0] <= min(line.x1, other.x1) + em for d in page.drawings)
                    if not ((overlap_y > 0 and (dx <= 4 * em or connector)) or (dx == 0 and dy <= 1.5 * em)):
                        continue
                    barriers = [l for l in page.lines if l.role == "body" and not is_math_fragment(l)
                                and l.y0 < union[3] and l.y1 > union[1] and _overlap(l.bbox, union) > 0]
                    if not barriers:
                        group.append(line)
                        changed = True
                        break
        bbox = _box(group)
        # Inline equations inside a prose baseline stay in that sentence.
        if any(l.role == "body" and l not in group and not is_math_fragment(l) and
               min(l.y1, bbox[3]) - max(l.y0, bbox[1]) > 0 and
               max(l.x0 - bbox[2], bbox[0] - l.x1, 0) < em for l in page.lines):
            continue
        used.update(id(l) for l in group)
        ast, reason = reconstruct_math(group, page.drawings)
        for l in group:
            l.role = "equation-text"
        # Include nearby vector components in the fallback crop, within the
        # equation's own extent. They are never interpreted as invented arrows.
        drawings = [d["bbox"] for d in page.drawings if d["bbox"][0] >= bbox[0] - em and
                    d["bbox"][2] <= bbox[2] + em and d["bbox"][1] >= bbox[1] - em and d["bbox"][3] <= bbox[3] + em]
        art = _box([bbox] + drawings)
        regions.append(Region("equation", art, page.index, 0.85 if ast else 0.4,
                              {"lines": group, "mathml": ast, "text": linearize(group), "reason": reason,
                               "fallback_image": art if ast is None else None}))
    return regions
