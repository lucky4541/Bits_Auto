"""Heuristics for detecting display mathematics in PDF-extracted lines.

The detector intentionally accepts short, indented mathematical fragments (for
example k with a subscript, E + S, or an arrow label) because equations are
frequently extracted as several spatially separated lines. Ambiguous matches
are still flagged for review by the structure builder.
"""
from __future__ import annotations

import re
from .document_tree import Line

MATH_FONT = re.compile(r"symbol|mt-extra|mtextra|stix|cambria ?math|math|euclid|mathematicalpi|lucidamath|cmmi|cmsy|cmex|rmtmi", re.I)
OPS = set("=±×÷∑∫√≈≠≤≥∞∂∆∇·→←↔⇌⇄⟶⟵+−*/()[]{}^_/")
MATH_FRAGMENT = re.compile(r"^[\s(\[]*(?:[A-Za-zΑ-ω][A-Za-zΑ-ω0-9]*|[0-9]+|[=+−*/×÷<>≤≥≈→←↔⇌⇄⟶⟵]+)(?:[\s\[\](){}.,:+−*/×÷=<>≤≥≈→←↔⇌⇄⟶⟵^_A-Za-zΑ-ω0-9]*)\s*$")
WORD = re.compile(r"[a-záéíóúñ]{4,}", re.I)


def _has_script(spans) -> bool:
    return any(bool(getattr(s, "sub", False) or getattr(s, "sup", False)) for s in spans)


def math_score(l: Line) -> float:
    text = (l.text or "").strip()
    chars = sum(len(s.text.strip()) for s in l.spans) or max(1, len(text))
    mf = sum(len(s.text.strip()) for s in l.spans if MATH_FONT.search(getattr(s, "font", "")))
    mf += sum(1 for c in text if c == "\ufffd" or 0xE000 <= ord(c) <= 0xF8FF)
    ops = sum(1 for c in text if c in OPS)
    score = mf / chars + min(0.4, 0.15 * ops)
    if _has_script(l.spans):
        score += 0.35
    if len(l.spans) >= 3 and len(text) < 100:
        score += 0.15
    score -= 0.12 * len(WORD.findall(text))
    if len(text) <= 12 and MATH_FRAGMENT.fullmatch(text) and not WORD.search(text):
        score = max(score, 0.55)
    if re.search(r"\b(?:k|v|K|V|E|S|P)\s*[_^]?\s*[0-9]+\b", text):
        score = max(score, 0.65)
    return score


def is_math_fragment(l: Line) -> bool:
    """True for short equation pieces that may not score as a full equation."""
    text = (l.text or "").strip()
    if not text or len(text) > 48:
        return False
    if _has_script(l.spans) or any(c in text for c in "=±×÷∑∫√≈≠≤≥∞∂∆∇·→←↔⇌⇄⟶⟵"):
        return True
    if re.fullmatch(r"(?:\[[A-Za-zΑ-ω0-9]+\]|[A-Za-zΑ-ω]{1,5}\s*[_^]?\s*[0-9]{0,3}|[A-Za-z]\s*[+−*/=]\s*[A-Za-z](?:\s*[+−*/=]\s*[A-Za-z])?|\([A-Za-z0-9+−*/= ]+\))", text):
        return True
    return False


def is_display_math(l: Line, col_left: float, em: float) -> bool:
    text = (l.text or "").strip()
    if not text or len(text) > 180:
        return False
    indent = l.x0 - col_left
    # Keep the original font/indent test, but allow common equation fragments.
    if math_score(l) >= 0.45 and indent > 0.8 * em:
        return True
    if is_math_fragment(l) and indent > 0.5 * em:
        return True
    return False


def linearize(lines: list[Line]) -> str:
    spans = [s for line in lines for s in line.spans if s.text.strip()]
    if not spans:
        return " ".join((line.text or "").strip() for line in lines if (line.text or "").strip())
    spans.sort(key=lambda s: (round(((s.bbox[1] + s.bbox[3]) / 2) / 6), s.bbox[0]))
    out = []
    for span in spans:
        t = span.text.strip()
        if getattr(span, "sup", False):
            t = "^" + t
        elif getattr(span, "sub", False):
            t = "_" + t
        out.append(t)
    return re.sub(r"\s+", " ", " ".join(out)).replace(" ^", "^").replace(" _", "_").strip()

# ---------------------------------------------------------------------------
# Structured MathML reconstruction used by the BITS generator.
# The previous pipeline stored every equation as a single <mml:mtext>, which
# can only display a linear string and can never create a fraction/scripts.
def _mnode(tag: str, text: str | None = None, children: list | None = None) -> dict:
    out = {"tag": tag}
    if text is not None:
        out["text"] = text
    if children:
        out["children"] = children
    return out


def _tokenize_math(text: str) -> list[dict]:
    """Turn linear equation text into MathML token nodes, preserving scripts."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    # Match common scientific tokens before operators.  Unicode arrows are kept
    # as operators, while sub/superscripts become proper MathML nodes.
    token_re = re.compile(r"([A-Za-zΑ-ω][A-Za-zΑ-ω]?)(?:[_^]\{?([A-Za-z0-9+−-]+)\}?)|([A-Za-zΑ-ω]+)|([0-9]+(?:\.[0-9]+)?)|(⇌|⇄|⟶|→|←|↔|≤|≥|≠|≈|±|×|÷|[=+−*/<>(),\[\]{}|])|(\s+)|(.)")
    out = []
    for m in token_re.finditer(text):
        base, script, word, number, op, space, other = m.groups()
        if space:
            continue
        if base:
            script_mark = m.group(0)[len(base)]
            b = _mnode("mi", base)
            s = _mnode("mn" if script.isdigit() else "mi", script)
            out.append(_mnode("msub" if script_mark == "_" else "msup", children=[b, s]))
        elif word:
            # Keep variable names like Vmax as a single identifier unless max
            # is explicitly marked as a subscript in the source text.
            out.append(_mnode("mi", word))
        elif number:
            out.append(_mnode("mn", number))
        elif op:
            out.append(_mnode("mo", op))
        elif other:
            out.append(_mnode("mi", other))
    return out


def build_mathml_ast(lines: list[Line]) -> dict:
    """Build a MathML tree from extracted equation lines and span geometry.

    Two vertically stacked, horizontally overlapping lines are interpreted as
    numerator/denominator when the region contains no reaction arrow.  For a
    reaction scheme, arrow labels positioned above/below an arrow are attached
    with mover/munder instead of being flattened into the equation string.
    """
    spans = [s for ln in lines for s in ln.spans if (s.text or "").strip()]
    if not spans:
        return _mnode("mrow", children=_tokenize_math(linearize(lines)))
    # A real fraction is normally extracted as two baselines whose x ranges
    # overlap. Do this before generic horizontal reconstruction.
    has_arrow = any(any(ch in (s.text or "") for ch in "→←↔⇌⇄⟶") for s in spans)
    baselines = []
    for ln in sorted(lines, key=lambda x: x.y0):
        if not baselines or abs(ln.y0 - baselines[-1][0]) > max(2.5, 0.45 * max(ln.height, 1)):
            baselines.append((ln.y0, [ln]))
        else:
            baselines[-1][1].append(ln)
    if not has_arrow and len(baselines) == 2:
        top = [s for ln in baselines[0][1] for s in ln.spans if s.text.strip()]
        bot = [s for ln in baselines[1][1] for s in ln.spans if s.text.strip()]
        if top and bot:
            tx0, tx1 = min(s.bbox[0] for s in top), max(s.bbox[2] for s in top)
            bx0, bx1 = min(s.bbox[0] for s in bot), max(s.bbox[2] for s in bot)
            overlap = max(0.0, min(tx1, bx1) - max(tx0, bx0))
            if overlap / max(1.0, min(tx1 - tx0, bx1 - bx0)) >= 0.30:
                top_text = " ".join(s.text.strip() for s in sorted(top, key=lambda z: z.bbox[0]))
                bot_text = " ".join(s.text.strip() for s in sorted(bot, key=lambda z: z.bbox[0]))
                return _mnode("mfrac", children=[_mnode("mrow", children=_tokenize_math(top_text)), _mnode("mrow", children=_tokenize_math(bot_text))])

    # Spatially order tokens left-to-right. Script-marked PDF spans become msub/
    # msup even if the PDF extractor did not include a literal underscore.
    tokens = []
    for s in spans:
        txt = (s.text or "").strip()
        nodes = _tokenize_math(txt)
        if not nodes:
            continue
        if getattr(s, "sub", False) or getattr(s, "sup", False):
            nodes = [_mnode("mn" if txt.isdigit() else "mi", txt)]
            tokens.append({"x": (s.bbox[0] + s.bbox[2]) / 2, "y": (s.bbox[1] + s.bbox[3]) / 2,
                           "bbox": s.bbox, "script": "sub" if s.sub else "sup", "nodes": nodes})
        else:
            tokens.append({"x": s.bbox[0], "xc": (s.bbox[0] + s.bbox[2]) / 2,
                           "y": (s.bbox[1] + s.bbox[3]) / 2, "bbox": s.bbox, "script": None, "nodes": nodes, "text": txt})
    tokens.sort(key=lambda z: (z["x"], z["y"]))
    # Attach small k_i labels to the nearest arrow by x-coordinate and vertical
    # position, making the reaction layout meaningful rather than a word string.
    arrow_ids = [i for i, t in enumerate(tokens) if any(n.get("tag") == "mo" and n.get("text") in {"→", "←", "↔", "⇌", "⇄", "⟶"} for n in t["nodes"])]
    removed = set()
    for ai in arrow_ids:
        arrow = tokens[ai]
        above, below = [], []
        for j, t in enumerate(tokens):
            if j == ai or j in removed or t.get("script"):
                continue
            label = "".join(n.get("text", "") for n in t["nodes"])
            if not re.fullmatch(r"k\s*[_^]?\s*[0-9]+|k[₀-₉]+", label, re.I):
                continue
            if abs(t["xc"] - arrow.get("xc", arrow["x"])) < 30:
                if t["y"] < arrow["y"] - 2:
                    above.append((abs(t["xc"] - arrow.get("xc", arrow["x"])), j))
                elif t["y"] > arrow["y"] + 2:
                    below.append((abs(t["xc"] - arrow.get("xc", arrow["x"])), j))
        for arr, wrapper in ((above, "mover"), (below, "munder")):
            if arr:
                _, j = min(arr)
                label_nodes = tokens[j]["nodes"]
                arrow["nodes"] = [_mnode(wrapper, children=[arrow["nodes"][0], _mnode("mrow", children=label_nodes)])]
                removed.add(j)
    out = []
    for i, t in enumerate(tokens):
        if i in removed:
            continue
        ns = t["nodes"]
        if t.get("script"):
            # Attach script to the nearest preceding base token.
            if out:
                base = out.pop()
                out.append(_mnode("msub" if t["script"] == "sub" else "msup", children=[base, ns[0]]))
            else:
                out.extend(ns)
        else:
            out.extend(ns)
    return _mnode("mrow", children=out)
