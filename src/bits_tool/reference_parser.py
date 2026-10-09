"""Bibliography: section detection, entry splitting and conservative citation tagging.

Only metadata that is unambiguously identifiable is tagged; everything else
stays as text inside <mixed-citation>. The concatenation of all segments is
always identical to the printed reference (nothing invented, nothing lost).
"""
from __future__ import annotations

import re

from .document_tree import Line

REF_TITLE_RE = re.compile(r"^\s*(references?|bibliograf[íi]a|bibliography|referencias(?: bibliogr[áa]ficas| clave| seleccionadas| recomendadas)?|key references|lecturas? recomendadas?|lectura recomendada|suggested readings?|further reading|works cited|literature cited|references and notes|selected references|recommended readings?|bibliograf[íi]a recomendada|fuentes|sources)\s*:?\s*$", re.I)
NUM_LABEL_RE = re.compile(r"^\s*(?P<lab>\[?\d{1,4}[.)\]]?)\s+(?=\S)")
AUTHOR_START_RE = re.compile(r"^[A-ZÁÉÍÓÚÑÄÖÜ][\w'’\-]+(?:\s[A-Z][\w'’\-]+)?,?\s(?:[A-Z]{1,3}\b|[A-Z]\.)")

VANCOUVER = re.compile(
    r"^(?P<authors>.+?(?:et al|[A-Z]{1,3}))\.\s+(?P<title>.+?[.?!])\s+(?P<source>[^.;]+?(?:\.\s?[^.;\d]+?)*?)\.?\s+"
    r"(?P<year>(?:19|20)\d{2})[a-z]?(?:\s+[A-Z][a-z]{2}(?:\s+\d{1,2})?)?;\s*(?P<vol>[\w]+)?(?:\s*\((?P<iss>[^)]+)\))?"
    r"(?::\s*(?P<fp>[A-Za-z]?\d+)(?:\s*[-–]\s*(?P<lp>[A-Za-z]?\d+))?)?")
BOOK = re.compile(
    r"^(?P<authors>.+?)\.\s+(?P<title>.+?)\.\s+(?:(?P<ed>\d+(?:st|nd|rd|th|a|ª)?\s*ed)\.\s+)?"
    r"(?P<loc>[A-Z][\w .,'’\-]+?):\s*(?P<pub>[^;.]+?)[;,.]\s*(?P<year>(?:19|20)\d{2})")
APA_BOOK = re.compile(
    r"^(?P<authors>.+?)\s\((?P<year>(?:19|20)\d{2})[a-z]?\)\.\s+(?P<title>[^.]+?(?:\([^)]*\))?)\.\s+(?P<pub>[A-Z][^.]+?)\.\s*$")
APA = re.compile(
    r"^(?P<authors>.+?)\s\((?P<year>(?:19|20)\d{2})[a-z]?\)\.\s+(?P<title>.+?[.?!])\s+(?P<source>[^,]+?),\s*"
    r"(?P<vol>\d+)(?:\((?P<iss>[^)]+)\))?,\s*(?P<fp>\d+)(?:\s*[-–]\s*(?P<lp>\d+))?")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
DOI_RE = re.compile(r"(?:doi:\s*|https?://(?:dx\.)?doi\.org/)(?P<doi>10\.\d{4,9}/\S+?)(?=[.,;]?\s|[.,;]?$)", re.I)


def is_ref_heading(text: str) -> bool:
    t = text.strip()
    if re.fullmatch(r"(?:\w\s){2,}\w", t):
        t = t.replace(" ", "")          # letter-spaced display heading 'R E F E R E N C E S'
    return bool(REF_TITLE_RE.match(t))


def split_entries(lines: list[Line], body_size: float) -> list[list[Line]]:
    """Group reference lines into entries (numbered, hanging-indent or author-start)."""
    if not lines:
        return []
    numbered = sum(1 for l in lines if NUM_LABEL_RE.match(l.text)) >= max(2, 0.3 * len(lines) / 2.5)
    lefts: dict[tuple, float] = {}
    for l in lines:
        k = (l.page, round(l.x0 / 80))
        lefts[k] = min(lefts.get(k, l.x0), l.x0)
    entries: list[list[Line]] = []
    for l in lines:
        t = l.text.strip()
        left = lefts[(l.page, round(l.x0 / 80))]
        prev = entries[-1][-1] if entries else None
        if numbered:
            new = bool(NUM_LABEL_RE.match(t))
        else:
            hanging = l.x0 <= left + 1.0
            new = hanging and (prev is None or prev.text.rstrip().endswith((".", ")")) or AUTHOR_START_RE.match(t) is not None)
            if prev is not None and prev.page == l.page and prev.y1 <= l.y0 and l.y0 - prev.y1 > 0.5 * body_size:
                new = True
        if new or not entries:
            entries.append([l])
        else:
            entries[-1].append(l)
    return entries


NAME_VANC = re.compile(r"(?P<sur>[A-ZÁÉÍÓÚÑÄÖÜ][\w'’\-]*(?:\s(?:[Vv]an|[Dd]e|[Dd]el|[Ll]a|[Vv]on|[Dd]a)\s[\w'’\-]+)?(?:\s[A-Z][\w'’\-]{2,})?)(?P<sep>\s)(?P<giv>[A-Z]{1,3})(?=,|$|\s|\.)")
NAME_APA = re.compile(r"(?P<sur>[A-ZÁÉÍÓÚÑÄÖÜ][\w'’\-]+(?:\s[A-ZÁÉÍÓÚÑ][\w'’\-]+)?)(?P<sep>,\s)(?P<giv>[A-Z]\.(?:\s?-?[A-Z]\.)*)")


def _names(authors: str, offset: int = 0):
    """Segments for an author list (Vancouver 'Hanks SK, Hunter T' or APA 'Benner, P., & Wrubel, J.')."""
    rx = NAME_APA if NAME_APA.search(authors) else NAME_VANC
    segs = []
    pos = 0
    for m in rx.finditer(authors):
        if m.start() > pos:
            segs.append(("text", authors[pos:m.start()]))
        segs.append(("name", m.group("sur"), m.group("sep"), m.group("giv")))
        pos = m.end()
    rest = authors[pos:]
    if rest:
        segs.append(("text", rest))
    if not any(s[0] == "name" for s in segs):
        if re.search(r"\b(association|society|organization|organización|asociación|sociedad|college|institute|instituto|academy|centers|committee|comité|group|grupo|council|WHO|OMS|CDC|NANDA|international)\b", authors, re.I):
            return [("collab", authors)]
        return None
    return segs


def parse_citation(text: str) -> dict:
    """{'type': publication-type, 'segments': [(kind, text...)]} — segments cover the text exactly."""
    ptype = "other"
    segs = None
    m = VANCOUVER.match(text)
    if m:
        ptype = "journal"
        order = ["authors", "title", "source", "year", "vol", "iss", "fp", "lp"]
        tagmap = {"title": "article-title", "source": "source", "year": "year", "vol": "volume", "iss": "issue",
                  "fp": "fpage", "lp": "lpage"}
    else:
        m = APA.match(text)
        if m:
            ptype = "journal"
            order = ["authors", "year", "title", "source", "vol", "iss", "fp", "lp"]
            tagmap = {"title": "article-title", "source": "source", "year": "year", "vol": "volume", "iss": "issue",
                      "fp": "fpage", "lp": "lpage"}
        else:
            m = BOOK.match(text)
            if m:
                ptype = "book"
                order = ["authors", "title", "ed", "loc", "pub", "year"]
                tagmap = {"title": "source", "ed": "edition", "loc": "publisher-loc", "pub": "publisher-name", "year": "year"}
            else:
                m = APA_BOOK.match(text)
                if m and not URL_RE.search(text):
                    ptype = "book"
                    order = ["authors", "year", "title", "pub"]
                    tagmap = {"title": "source", "pub": "publisher-name", "year": "year"}
                else:
                    m = None
    if m:
        segs = []
        pos = 0
        spans = sorted(((g, m.start(g), m.end(g)) for g in order if m.group(g) is not None and m.start(g) >= 0), key=lambda x: x[1])
        for g, a, b in spans:
            if a < pos:
                continue
            if a > pos:
                segs.append(("text", text[pos:a]))
            if g == "authors":
                names = _names(text[a:b], a)
                if names:
                    segs.append(("person-group", names))
                else:
                    segs.append(("text", text[a:b]))
            else:
                val = text[a:b]
                if g == "title":
                    core = val.rstrip(".?! ")
                    segs.append((tagmap[g], core))
                    if len(core) < len(val):
                        segs.append(("text", val[len(core):]))
                else:
                    segs.append((tagmap[g], val))
            pos = b
        if pos < len(text):
            segs.append(("text", text[pos:]))
    else:
        segs = [("text", text)]
    # URIs / DOIs in remaining text segments
    out = []
    for s in segs:
        if s[0] != "text":
            out.append(s)
            continue
        t = s[1]
        pos = 0
        for mm in re.finditer(rf"{DOI_RE.pattern}|{URL_RE.pattern}", t, re.I):
            if mm.start() > pos:
                out.append(("text", t[pos:mm.start()]))
            val = mm.group(0).rstrip(".,;")
            out.append(("doi", val) if mm.group("doi") else ("uri", val))
            pos = mm.start() + len(val)
        if pos < len(t):
            out.append(("text", t[pos:]))
    if ptype == "other" and any(s[0] == "uri" for s in out):
        ptype = "web"
    return {"type": ptype, "segments": out}


def flatten(segs) -> str:
    out = []
    for s in segs:
        if s[0] == "person-group":
            for n in s[1]:
                out.append(n[1] if n[0] in ("text", "collab") else f"{n[1]}{n[2]}{n[3]}")
        else:
            out.append(s[1])
    return "".join(out)
