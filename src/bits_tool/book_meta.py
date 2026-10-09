"""Book-level metadata: ISBN, title, short name, edition, publisher, copyright.

Evidence order: per-book override file (book.yaml) > printed title/copyright
pages > PDF bookmarks > PDF metadata > file name. Values that cannot be found
are left out (never invented) and reported.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ISBN13_RE = re.compile(r"97[89][\-‐‑ ]?\d{1,5}[\-‐‑ ]?\d{1,7}[\-‐‑ ]?\d{1,7}[\-‐‑ ]?\d")
YEAR_RE = re.compile(r"(?:©|copyright|\(c\))\s*(?:\d{4}\s*[-–,]\s*)*((?:19|20)\d{2})", re.I)
EDITION_RE = re.compile(r"\b(\d{1,2})\s*(?:\.ª|ª|\.a\.?|a\.?|\.º|º|st|nd|rd|th|e)\s*(?:edici[óo]n|edition|ed\.)", re.I)


@dataclass
class BookInfo:
    isbn: str | None = None
    isbn_display: str | None = None
    prefix: str = "book"
    author: str | None = None
    title: str | None = None
    short_name: str | None = None
    shortcode: str | None = None
    serial_code: str | None = None
    edition: str | None = None
    pub_year: str | None = None
    publisher_name: str | None = None
    publisher_loc: str | None = None
    contributors: list = field(default_factory=list)
    copyright_statement: str | None = None
    copyright_year: str | None = None
    copyright_holder: str | None = None
    lang: str = "en"
    sources: dict = field(default_factory=dict)


NAME_DEG_RE = re.compile(r"^\s*(?P<given>(?:[A-ZÁÉÍÓÚÑ][\w'’\-]*\.?\s+){1,3})(?P<sur>[A-ZÁÉÍÓÚÑ][\w'’\-]+(?:\s[A-ZÁÉÍÓÚÑ][\w'’\-]+)?)\s*,\s*(?:MD|PhD|PHD|DO|RN|DNP|MBA|MSN|BSN|MPH|MS|MA|EdD|FACS|FRCP|FACP|CNE|DDS|DMD|PT|OTR|ScD|BS|BA)\b")
SKIP_PAGE_RE = re.compile(r"cat[áa]logo|otros t[íi]tulos|also available|other titles|colaboradores|contributors|revisi[óo]n cient[íi]fica|traducci[óo]n", re.I)


def author_from_title_pages(fm_pages: list) -> str | None:
    """Surname of the first credited author/editor on the title page(s)."""
    for lines in fm_pages:
        text = " ".join(l.text for l in lines[:12])
        if SKIP_PAGE_RE.search(text):
            continue
        for l in lines:
            m = NAME_DEG_RE.match(l.text)
            if m:
                return m.group("sur").split()[-1]
    return None


ROLE_HEAD_RE = re.compile(r"^\s*(editores|editor|editors|autores|autor|authors?|edited by)\s*:?\s*$", re.I)
STOP_HEAD_RE = re.compile(r"^\s*(editor(es)? de la serie|series editors?|colaboradores|contributors)\b", re.I)
LOOSE_NAME_RE = re.compile(r"^\s*(?P<given>(?:[A-ZÁÉÍÓÚÑ][\w'’\-]*\.?\s+){1,3})(?P<sur>[A-ZÁÉÍÓÚÑ][\w'’\-]+(?:[\s-][A-ZÁÉÍÓÚÑ][\w'’\-]+)?)\s*,\s*(?P<deg>[A-Z][A-Za-z.()]*(?:.*))$")


def contributors_from_title_pages(fm_pages: list) -> list[dict]:
    """Book editors/authors listed on the title page under 'Editores' / 'Editors' (bold name lines with degrees)."""
    for lines in fm_pages:
        heads = [i for i, l in enumerate(lines) if ROLE_HEAD_RE.match(l.text)]
        if not heads:
            continue
        role = "editor" if re.match(r"\s*(edit)", lines[heads[0]].text, re.I) else "author"
        out = []
        wrap = False
        hd = lines[heads[0]]
        xs = sorted(l.x0 for l in lines)
        mid = (xs[0] + max(l.x1 for l in lines)) / 2 if xs else 0
        for l in sorted(lines, key=lambda l: (l.x0 > mid, l.y0, l.x0)):     # column by column (2-column credits)
            if l is hd or l.y0 < hd.y0 - 3:
                continue
            t = l.text.strip()
            if STOP_HEAD_RE.match(t):
                break
            bold = all(s.bold for s in l.spans if s.text.strip())
            if not bold:
                continue
            m = LOOSE_NAME_RE.match(t)
            if wrap and out and not m and len(t) < 80:
                out[-1]["degrees"] = (out[-1]["degrees"] + ", " + t.rstrip(",").strip()).strip(", ")   # degrees wrapped
                wrap = t.endswith(",")
                continue
            if m:
                out.append({"type": role, "given": m.group("given").strip(), "surname": m.group("sur").strip(),
                            "degrees": m.group("deg").strip().rstrip(",").strip()})
                wrap = t.endswith(",")
        if out:
            return out
    return []


def derive_book_info(src, overrides: dict, title_lines, copyright_text: str, bookmarks, pdf_meta: dict, cfg,
                     fm_pages: list | None = None) -> BookInfo:
    bi = BookInfo()
    bi.isbn = overrides.get("isbn") or src.isbn
    bi.author = overrides.get("author") or src.author
    if not bi.author and fm_pages:
        bi.author = author_from_title_pages(fm_pages)
        if bi.author:
            from .pdf_loader import ascii_fold
            bi.author = ascii_fold(bi.author)
            bi.sources["author"] = "title page"
    bi.prefix = (overrides.get("id_prefix") or (bi.author or "")).lower() or "book"
    bi.sources["prefix"] = "override" if overrides.get("id_prefix") else ("file name" if src.author else bi.sources.get("author", "default"))
    # ISBN as printed (with hyphens) when the copyright page shows it
    if bi.isbn:
        for m in ISBN13_RE.finditer(copyright_text or ""):
            digits = re.sub(r"\D", "", m.group(0))
            if digits == bi.isbn:
                bi.isbn_display = re.sub(r"[‐‑ ]", "-", m.group(0))
                break
    bi.isbn_display = overrides.get("isbn_display") or bi.isbn_display or bi.isbn
    bi.edition = str(overrides.get("edition") or src.edition or "") or None
    if not bi.edition and copyright_text:
        m = EDITION_RE.search(copyright_text)
        if m:
            bi.edition = m.group(1)
    # title
    title = overrides.get("title")
    if not title and title_lines:
        big = max(title_lines, key=lambda l: l.size)
        same = [l for l in title_lines if abs(l.size - big.size) < 0.6 and abs(l.y0 - big.y0) < 4 * big.size and l.page == big.page]
        same.sort(key=lambda l: l.y0)
        title = " ".join(l.text.strip() for l in same)
        bi.sources["title"] = "title page"
    if not title and bookmarks:
        title = bookmarks[0][1]
        bi.sources["title"] = "bookmark"
    if not title and pdf_meta.get("title"):
        title = pdf_meta["title"]
        bi.sources["title"] = "pdf metadata"
    bi.title = re.sub(r"\s+", " ", title).strip() if title else None
    bi.shortcode = overrides.get("shortcode")
    bi.short_name = overrides.get("short_name") or (f"{(bi.author or bi.prefix).capitalize()}{bi.edition or ''}")
    bi.serial_code = overrides.get("serial_code")
    bi.contributors = overrides.get("contributors") or (contributors_from_title_pages(fm_pages) if fm_pages else [])
    # copyright
    if copyright_text:
        years = [int(y) for y in YEAR_RE.findall(copyright_text)]
        if years:
            bi.copyright_year = str(max(years))
        m = re.search(r"(©|Copyright)[^\n.]{0,120}", copyright_text)
        if m:
            bi.copyright_statement = re.sub(r"\s+", " ", m.group(0)).strip()
            h = re.search(r"(?:19|20)\d{2}\s*(?:by\s+)?(.+)$", bi.copyright_statement)
            if h:
                bi.copyright_holder = h.group(1).strip(" .")
        pub = re.search(r"(Wolters Kluwer[^\n.,;]*|Lippincott[^\n.;]*|Elsevier[^\n.,;]*|Springer[^\n.,;]*)", copyright_text)
        if pub:
            bi.publisher_name = pub.group(1).strip()
    bi.pub_year = str(overrides.get("pub_year") or bi.copyright_year or "") or None
    bi.publisher_name = overrides.get("publisher_name") or bi.publisher_name or cfg.get("book.publisher_name")
    bi.publisher_loc = overrides.get("publisher_loc") or cfg.get("book.publisher_loc")
    bi.lang = overrides.get("xml_lang") or cfg.get("bits.xml_lang", "en")
    return bi
