"""In-text citation detection (pass 1: keys only, no IDs).

Recognises figure / table / box / chapter / appendix / equation references
(English + Spanish + learned words), numbered bibliography citations
([1], [2,3], superscripts), and author–year citations. Each match becomes a
Cite inline item holding *keys*; the xref resolver turns keys into IDs in
pass 2. Unresolvable keys stay as text and are reported — never guessed.
"""
from __future__ import annotations

import re

from .caption_detector import number_key
from .document_tree import Cite

NUMTOK = r"(?:[A-Z]{1,2}[-.–])?\d+(?:[.\-–]\d+){0,2}[A-Za-z]?"
FIGW = r"(?:(?:[Ff]ig(?:ura|ure)?s?\.?|FIG(?:URA|URE)?S?\.?)(?:\s+t[ée]cnicas?|\s+T[ÉE]CNICAS?)?)"
TABW = r"(?:[Tt]abl[ae]s?|TABL[AE]S?)"
BOXW = r"(?:[Bb]ox(?:es)?|BOX|[Rr]ecuadros?|[Ee]ncuadres?)"
CUADW = r"(?:[Cc]uadros?|CUADROS?)"
CHAPW = r"(?:[Cc]hapters?|CHAPTERS?|[Cc]ap[íi]tulos?|CAP[ÍI]TULOS?|[Cc]ap\.)"
APPW = r"(?:[Aa]ppendi(?:x|ces)|[Aa]p[ée]ndices?)"
EQW = r"(?:[Ee]quations?|[Ee]cuaci[óo]n(?:es)?|[Ee]q\.)"
SEP = r"(?:\s*,\s*(?:and\s+|y\s+|e\s+)?|\s+(?:and|y|e|&|to|through|a|al)\s+|\s*[–\-]\s*)"

PAT = re.compile(
    rf"(?P<word>{FIGW}|{TABW}|{BOXW}|{CUADW}|{CHAPW}|{APPW}|{EQW})\s*(?P<nums>\(?{NUMTOK}\)?(?:{SEP}\(?{NUMTOK}\)?)*)(?![\w])")
APP_LETTER = re.compile(rf"(?P<word>{APPW})\s+(?P<nums>[A-Z](?:\s*(?:,|and|y)\s*[A-Z])*)\b")
PAREN = re.compile(r"(?<![\w(])\((?P<nums>\d{1,3}(?:\s*[,–\-]\s*\d{1,3}){0,8})\)")
BRACKET = re.compile(r"\[(?P<nums>\d{1,4}(?:\s*[,–\-]\s*\d{1,4})*)\]")
AUTHOR_YEAR = re.compile(
    r"(?P<name>[A-ZÁÉÍÓÚÑ][A-Za-zÀ-ÿ'’\-]+(?:\s(?:et al\.?|and|y|&)\s?(?:[A-ZÁÉÍÓÚÑ][A-Za-zÀ-ÿ'’\-]+)?)?),?\s(?P<years>(?:19|20)\d{2}[a-z]?(?:,\s?(?:19|20)\d{2}[a-z]?)*)")


def _kind(word: str) -> str:
    w = word.lower()
    if w.startswith("fig"):
        return "fig"
    if w.startswith("tab"):
        return "table"
    if w.startswith(("box", "recuadro", "encuadre")):
        return "box"
    if w.startswith("cuadro"):
        return "table-or-box"
    if w.startswith(("chap", "cap")):
        return "chapter"
    if w.startswith(("appen", "apén", "apen")):
        return "app"
    return "eq"


def _split_nums(nums: str):
    """'3-1, 3-4 y 3-6' -> [(text, key, is_range_end)] preserving separators."""
    parts = []
    pos = 0
    for m in re.finditer(NUMTOK, nums):
        if m.start() > pos:
            parts.append(("sep", nums[pos:m.start()]))
        parts.append(("num", m.group(0)))
        pos = m.end()
    if pos < len(nums):
        parts.append(("sep", nums[pos:]))
    return parts


def detect_in_text(text: str, styles, ctx: str, chapter_no: str | None, allow_bib: bool) -> list[dict]:
    """Split one text run into text and Cite items."""
    out: list[dict] = []
    pos = 0
    matches = []
    for m in PAT.finditer(text):
        matches.append(("obj", m))
    for m in APP_LETTER.finditer(text):
        matches.append(("app", m))
    if allow_bib:
        for m in BRACKET.finditer(text):
            matches.append(("bib", m))
        for m in PAREN.finditer(text):
            matches.append(("bibp", m))     # '(1,4-6)': linked only when the chapter has those references
    matches.sort(key=lambda x: x[1].start())
    last_end = 0
    for kind, m in matches:
        if m.start() < last_end:
            continue
        last_end = m.end()
        if m.start() > pos:
            out.append({"k": "t", "text": text[pos:m.start()], "st": sorted(styles)})
        if kind == "obj":
            k = _kind(m.group("word"))
            parts = _split_nums(m.group("nums"))
            first = True
            prev_num = None
            i = 0
            while i < len(parts):
                typ, val = parts[i]
                if typ == "sep":
                    out.append({"k": "t", "text": val, "st": sorted(styles)})
                    i += 1
                    continue
                key = number_key(val.strip("()"))
                if k == "fig" and "cnica" in m.group("word").lower():
                    key = "T" + key          # 'fig. técnica 1.4.3' -> technique figure, own numbering
                if k == "chapter":
                    key = val.strip("().")
                # range "3-1 a 3-4" -> one xref (first rid) covering the range text
                rng_text = val
                if i + 2 < len(parts) and parts[i + 1][0] == "sep" and re.fullmatch(r"\s*(?:[–\-]|a|al|to|through)\s*", parts[i + 1][1]) \
                        and k != "chapter" and "-" in key:
                    rng_text = val + parts[i + 1][1] + parts[i + 2][1]
                    i += 2
                label_text = (m.group("word") + text[m.start("word") + len(m.group("word")):m.start("nums")] if first else "") + rng_text
                out.append(Cite(k, [key], label_text, styles, ctx))
                first = False
                prev_num = key
                i += 1
        elif kind == "app":
            letters = re.findall(r"[A-Z]", m.group("nums"))
            out.append(Cite("app", [letters[0]], m.group(0), styles, ctx))
        else:
            nums = m.group("nums")
            o, c = ("(", ")") if kind == "bibp" else ("[", "]")
            out.append({"k": "t", "text": o, "st": sorted(styles)})
            for typ, val in _split_nums(nums):
                if typ == "sep":
                    out.append({"k": "t", "text": val, "st": sorted(styles)})
                else:
                    out.append(Cite("bibr-paren" if kind == "bibp" else "bibr", [val], val, styles, ctx))
            out.append({"k": "t", "text": c, "st": sorted(styles)})
        pos = m.end()
    if pos < len(text):
        out.append({"k": "t", "text": text[pos:], "st": sorted(styles)})
    return out


def detect_citations(inlines: list[dict], ctx: str = "body", chapter_no: str | None = None,
                     allow_bib: bool = True, author_year: bool = False) -> list[dict]:
    out: list[dict] = []
    for it in inlines:
        if it["k"] != "t":
            out.append(it)
            continue
        st = set(it["st"])
        text = it["text"]
        if "sup" in st and re.fullmatch(r"\s*\d{1,4}(?:\s*[,–\-]\s*\d{1,4})*\s*", text) and allow_bib:
            for typ, val in _split_nums(text):
                if typ == "sep":
                    out.append({"k": "t", "text": val, "st": it["st"]})
                else:
                    out.append(Cite("bibr-or-fn", [val], val, st, ctx))
            continue
        if "sup" in st and re.fullmatch(r"\s*[*†‡§¶a-z]\s*", text):
            out.append(Cite("fn", [text.strip()], text, st, ctx))
            continue
        pieces = detect_in_text(text, st, ctx, chapter_no, allow_bib)
        if author_year:
            nxt = []
            for p in pieces:
                if p["k"] == "t":
                    nxt.extend(_author_year(p["text"], set(p["st"]), ctx))
                else:
                    nxt.append(p)
            pieces = nxt
        out.extend(pieces)
    return out


def _author_year(text: str, styles, ctx) -> list[dict]:
    out = []
    pos = 0
    # only inside parentheses to avoid prose false positives
    for par in re.finditer(r"\(([^()]{4,200})\)", text):
        inner_start = par.start(1)
        for m in AUTHOR_YEAR.finditer(par.group(1)):
            a = inner_start + m.start()
            if a < pos:
                continue
            if a > pos:
                out.append({"k": "t", "text": text[pos:a], "st": sorted(styles)})
            name = m.group("name").split()[0]
            years = re.findall(r"(?:19|20)\d{2}[a-z]?", m.group("years"))
            ystart = inner_start + m.start("years")
            out.append(Cite("bibr-ay", [f"{name}|{years[0]}"], text[a:ystart] + years[0], styles, ctx))
            p2 = ystart + len(years[0])
            for y in years[1:]:
                idx = text.index(y, p2)
                out.append({"k": "t", "text": text[p2:idx], "st": sorted(styles)})
                out.append(Cite("bibr-ay", [f"{name}|{y}"], y, styles, ctx))
                p2 = idx + len(y)
            pos = p2
    if pos < len(text):
        out.append({"k": "t", "text": text[pos:], "st": sorted(styles)})
    return out
