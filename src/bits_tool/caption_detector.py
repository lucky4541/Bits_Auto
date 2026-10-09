"""Figure / table / box labels and caption blocks (multilingual, sample-derived)."""
from __future__ import annotations

import re

from .document_tree import Line
from .layout_analyzer import BookStyle, family

NUM = r"(?:[A-Z]{1,2}[-.–]?\s?)?\d+(?:\s?[.\-–]\s?\d+){0,2}[A-Za-z]?|[IVX]+[-.–]\d+|[A-Z][-.–]\d+"
FIG_WORDS = r"FIGURA\s+T[ÉE]CNICA|Figura\s+t[ée]cnica|Fig\.\s+t[ée]cnica|FIGURA(?:\s+DE\s+REVISI[ÓO]N)?|Figura(?:\s+de\s+revisi[óo]n)?|FIGURES?|Figures?|FIG\.|Fig\.|FIGURE|Ilustraci[óo]n|L[áa]mina|Plate|PLATE|Image|IMAGEN|Imagen|Abbildung|Abb\.|Gráfico|Gráfica"
TAB_WORDS = r"TABLA|Tabla|TABLE|Table|TABELLE|Tabelle|Tableau|TABLEAU|Tabela|TABELA"
BOX_WORDS = r"BOX|Box|RECUADRO|Recuadro|CUADRO|Cuadro|ENCUADRE|Encuadre|Communication in Action|Evidence-Based Practice|Clinical Problem"
FIG_LABEL_RE = re.compile(rf"^\s*(?P<word>{FIG_WORDS})\s*(?P<num>{NUM})(?P<punct>\s*[.:]?)", re.U)
TAB_LABEL_RE = re.compile(rf"^\s*(?P<word>{TAB_WORDS})\s*(?P<num>{NUM})(?P<punct>\s*[.:]?)", re.U)
BOX_LABEL_RE = re.compile(rf"^\s*(?P<word>{BOX_WORDS})\s*(?P<num>{NUM})(?P<punct>\s*[.:]?)", re.U)
CONT_RE = re.compile(r"\((?:cont\.?|continued|continuaci[óo]n|contin[úu]a|suite|fortsetzung)\)|\bcontinued\b|\bcontinuaci[óo]n\b", re.I)


def number_key(num: str) -> str:
    """'3.7' '3-7' '3–7' '3 -7' -> '3-7'; keeps letters of appendix numbers; drops trailing panel letter."""
    n = re.sub(r"\s+", "", num).replace("–", "-").replace(".", "-")
    n = n.rstrip("-")
    m = re.match(r"^(.*\d)([a-z])$", n)
    if m:
        n = m.group(1)
    m = re.match(r"^(.*-\d+)([A-Z])$", n)       # 3-72A -> 3-72 (panel)
    if m:
        n = m.group(1)
    return n


def label_match(line_text: str):
    for kind, rx in (("fig", FIG_LABEL_RE), ("table", TAB_LABEL_RE), ("box", BOX_LABEL_RE)):
        m = rx.match(line_text)
        if m:
            return kind, m
    return None, None


def is_caption_start(line: Line, style: BookStyle, near_graphic: bool) -> tuple[str | None, dict | None]:
    """Decide whether a line starts a figure/table/box caption."""
    kind, m = label_match(line.text)
    if not kind:
        return None, None
    if re.search(r"/\s*\d{1,4}\s*$", line.text):
        return None, None      # 'TABLA 1-1. Tipos de músculos / 29': an outline / contents entry, not a caption
    label_text = m.group(0).strip()
    # styling of the label part
    lab_len = len(m.group(0))
    acc, lab_spans = 0, []
    for s in line.spans:
        if acc >= lab_len:
            break
        if s.text.strip():
            lab_spans.append(s)
        acc += len(s.text)
    distinct = any(s.bold or family(s.font) != style.body_family or abs(s.size - style.body_size) > 0.6 or s.color != style.body_color
                   for s in lab_spans)
    rest = line.text[lab_len:].strip()
    # a sentence that merely starts with "Figura 3-1 muestra ..." in body style is a citation, not a caption
    if not distinct and not near_graphic:
        return None, None      # 'Figura 3-1 muestra ...' in running-text style is a citation, not a caption
    rest_spans = []
    acc = 0
    core = len(m.group(0).rstrip())
    for s in line.spans:
        if acc >= core and s.text.strip():
            rest_spans.append(s)
        acc += len(s.text)
    if rest_spans and not near_graphic and all(family(s.font) == style.body_family and abs(s.size - style.body_size) < 0.6
                                               for s in rest_spans):
        return None, None      # styled call-out followed by running text (e.g. bold coloured 'Figure 15.5 denotes ...')
    conf = 0.95 if distinct and near_graphic else 0.85 if distinct or near_graphic else 0.6
    return kind, {"label": label_text.rstrip(), "word": m.group("word"), "num": m.group("num"),
                  "key": ("T" if "cnica" in m.group("word").lower() else "") + number_key(m.group("num")), "rest": rest, "continued": bool(CONT_RE.search(line.text)),
                  "conf": conf}


def caption_block(start: Line, following: list[Line], pitch: float) -> list[Line]:
    """The caption = start line + following lines of the same style that are close below."""
    out = [start]
    fam = family(start.spans[-1].font)
    size = start.spans[-1].size
    prev = start
    for l in following:
        if l.page != start.page:
            break
        if min(l.x1, start.x1) - max(l.x0, start.x0) <= 0:
            continue          # a line of the other column at the same height (lines are y-sorted page-wide)
        if l.role not in ("body", "footer-iso"):
            break
        gap = l.y0 - prev.y1
        if gap > 1.1 * pitch or gap < -pitch:
            break
        if label_match(l.text)[0]:
            break
        if abs(l.size - size) > 0.8 or family(l.main.font) != fam:
            break
        if l.x0 > prev.x1 or l.x1 < prev.x0 - 5:        # not horizontally aligned
            break
        out.append(l)
        prev = l
    return out
