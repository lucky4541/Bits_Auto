"""Page extraction: spans -> styled lines, raster images, vector drawings.

* Works inside the trim box so printer slugs / crop marks are excluded.
* Re-joins same-baseline fragments PyMuPDF splits (inline maths, kerning).
* Detects super/subscript from both font flags and geometry.
* Maps Symbol-font private-use glyphs to real Unicode.
* Page results are cached as JSON (cache/<pdf-sha>/page_NNNN.json) so a
  stopped conversion resumes without re-extracting finished pages.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf

from .document_tree import Line, PageInfo, Span

EXTRACT_VERSION = "10"
ZW_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
SLUG_RE = re.compile(r"\.indd\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\s+\d{1,2}:\d{2}(:\d{2})?\b|^\s*\d{10}\.indd", re.I)

# Adobe Symbol encoding (PUA F0xx as produced by many PDFs) -> Unicode
_SYMBOL = {
    0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x2D: "−", 0x40: "≅",
    0x41: "Α", 0x42: "Β", 0x43: "Χ", 0x44: "Δ", 0x45: "Ε", 0x46: "Φ", 0x47: "Γ", 0x48: "Η", 0x49: "Ι",
    0x4B: "Κ", 0x4C: "Λ", 0x4D: "Μ", 0x4E: "Ν", 0x4F: "Ο", 0x50: "Π", 0x51: "Θ", 0x52: "Ρ", 0x53: "Σ",
    0x54: "Τ", 0x55: "Υ", 0x57: "Ω", 0x58: "Ξ", 0x59: "Ψ", 0x5A: "Ζ", 0x5E: "⊥",
    0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε", 0x66: "φ", 0x67: "γ", 0x68: "η", 0x69: "ι",
    0x6A: "ϕ", 0x6B: "κ", 0x6C: "λ", 0x6D: "μ", 0x6E: "ν", 0x6F: "ο", 0x70: "π", 0x71: "θ", 0x72: "ρ",
    0x73: "σ", 0x74: "τ", 0x75: "υ", 0x76: "ϖ", 0x77: "ω", 0x78: "ξ", 0x79: "ψ", 0x7A: "ζ",
    0xA3: "≤", 0xA5: "∞", 0xAB: "↔", 0xAC: "←", 0xAD: "↑", 0xAE: "→",
    0xAF: "↓", 0xB0: "°", 0xB1: "±", 0xB3: "≥", 0xB4: "×", 0xB5: "∝", 0xB6: "∂",
    0xB7: "•", 0xB8: "÷", 0xB9: "≠", 0xBA: "≡", 0xBB: "≈", 0xBC: "…",
    0xD6: "√", 0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨", 0xDB: "⇔",
    0xDE: "⇒", 0xE5: "∑", 0x3D: "=", 0x2B: "+", 0x3C: "<", 0x3E: ">", 0x28: "(", 0x29: ")",
    0x5B: "[", 0x5D: "]", 0x7C: "|", 0x2F: "/", 0x2E: ".", 0x2C: ",", 0x20: " ", 0xA2: "′",
    0xB2: "″", 0xC5: "⊕", 0xC4: "⊗", 0xC6: "∅", 0xCE: "∈", 0xCF: "∉",
    0xC7: "∩", 0xC8: "∪", 0xCC: "⊂", 0xCA: "⊇", 0xF2: "∫",
}
BOLD_RE = re.compile(r"bold|black|heavy|semibold|demi|[-_](bd|bdcn|hv|sb|blk)\b", re.I)
ITAL_RE = re.compile(r"italic|oblique|[-_](it|ital|obl|bdit|cnobl|lightit|mediumit)\b|It$|Obl$", re.I)
SC_RE = re.compile(r"smallcaps|[-_]sc\b|SC$|Caps", re.I)


def clean_font(name: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", name or "")


def map_symbol_text(text: str, font: str) -> tuple[str, int]:
    """Return text with PUA glyphs mapped; second value = unmapped PUA count."""
    out, unmapped = [], 0
    symbolic = "symbol" in font.lower()
    for ch in text:
        o = ord(ch)
        if 0xF000 <= o <= 0xF0FF:
            m = _SYMBOL.get(o - 0xF000)
            if m is None:
                unmapped += 1
                out.append(ch)
            else:
                out.append(m)
        elif symbolic and o < 0x100 and o in _SYMBOL and ch not in "0123456789":
            out.append(_SYMBOL[o])
        else:
            out.append(ch)
    return "".join(out), unmapped


def _inside(b, clip, tol=2.0) -> bool:
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return clip[0] - tol <= cx <= clip[2] + tol and clip[1] - tol <= cy <= clip[3] + tol


def _make_span(s: dict) -> Span:
    font = clean_font(s.get("font", ""))
    flags = s.get("flags", 0)
    text, _ = map_symbol_text(s.get("text", ""), font)
    # zero-width characters set inside labels ('Figura 1\u200b.2.4') break label / citation matching
    text = ZW_RE.sub("", text)
    return Span(text=text, font=font, size=round(float(s.get("size", 0)), 2),
                bold=bool(flags & 16) or bool(BOLD_RE.search(font)),
                italic=bool(flags & 2) or bool(ITAL_RE.search(font)),
                sup=bool(flags & 1), sc=bool(SC_RE.search(font)), mono=bool(flags & 8),
                color=int(s.get("color", 0) or 0), bbox=tuple(round(v, 2) for v in s["bbox"]))


def _finish_line(spans: list[Span], page: int) -> Line:
    spans = sorted(spans, key=lambda s: s.bbox[0])
    x0 = min(s.bbox[0] for s in spans)
    y0 = min(s.bbox[1] for s in spans)
    x1 = max(s.bbox[2] for s in spans)
    y1 = max(s.bbox[3] for s in spans)
    line = Line(spans=spans, bbox=(x0, y0, x1, y1), page=page)
    main = line.main
    base = main.bbox[3]
    for s in spans:
        if s is main or not s.text.strip():
            continue
        small = s.size <= main.size * 0.8
        if small and s.bbox[3] < base - main.size * 0.18:
            s.sup, s.sub = True, False
        elif small and s.bbox[1] > main.bbox[1] + main.size * 0.3:
            s.sub, s.sup = True, False
        elif not small:
            s.sup = s.sub = False
    # insert spaces between spans separated by a visible gap
    fixed: list[Span] = []
    for s in spans:
        if fixed:
            prev = fixed[-1]
            gap = s.bbox[0] - prev.bbox[2]
            need = 0.22 * max(prev.size, s.size)
            if s.sup or s.sub or prev.sup or prev.sub:
                need = 0.5 * main.size
            if gap > need and not prev.text.endswith(" ") and not s.text.startswith(" "):
                s = Span(**{**s.__dict__, "text": " " + s.text})
        fixed.append(s)
    line.spans = fixed
    return line


def rot_bbox(b, rot: int, W: float, H: float):
    """Map a bbox into the upright frame of a page whose content is rotated by `rot`."""
    x0, y0, x1, y1 = b
    if rot == 90:        # text runs upward: turn the page clockwise
        return (H - y1, x0, H - y0, x1)
    if rot == -90:       # text runs downward: turn counter-clockwise
        return (y0, W - x1, y1, W - x0)
    return tuple(b)


def unrot_bbox(b, rot: int, W: float, H: float):
    """Inverse of rot_bbox (upright frame -> original page coordinates)."""
    x0, y0, x1, y1 = b
    if rot == 90:
        return (y0, H - x1, y1, H - x0)
    if rot == -90:
        return (W - y1, x0, W - y0, x1)
    return tuple(b)


def dominant_rotation(raw: dict) -> int:
    c = {0: 0, 90: 0, -90: 0}
    for b in raw["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            d = l.get("dir", (1, 0))
            n = sum(len(s.get("text", "")) for s in l["spans"])
            if d[1] < -0.9:
                c[90] += n
            elif d[1] > 0.9:
                c[-90] += n
            else:
                c[0] += n
    tot = sum(c.values()) or 1
    best = max(c, key=c.get)
    return best if best != 0 and c[best] >= 0.5 * tot else 0


def _is_side_tab(l: dict, clip) -> bool:
    """A short vertical line sitting in the outer 12% of the trim width (thumb tab, side running head)."""
    x0, y0, x1, y1 = l["bbox"]
    cx0, cy0, cx1, cy1 = clip
    w = cx1 - cx0
    txt = "".join(s.get("text", "") for s in l["spans"]).strip()
    if not txt or len(txt) > 80:
        return False
    return x1 <= cx0 + 0.12 * w or x0 >= cx1 - 0.12 * w


def build_lines(raw: dict, page_index: int, clip, rot: int = 0, W: float = 0, H: float = 0) -> tuple[list[Line], int]:
    """Lines from PyMuPDF 'dict' output, merged across same-baseline fragments.
    With rot != 0 the rotated text is mapped to an upright frame first; horizontal
    text on such a page (running head, folio) is returned separately as margin lines."""
    frags: list[list[Span]] = []
    margin: list[Line] = []
    unmapped = 0
    for b in raw["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            d = l.get("dir", (1, 0))
            is_rot = abs(d[1]) > 0.9
            if rot and not is_rot:
                sp = [_make_span(s) for s in l["spans"] if s.get("text", "").strip()]
                if sp:
                    ml = _finish_line(sp, page_index)
                    ml.role = "rotated-margin"
                    margin.append(ml)
                continue
            if not rot and is_rot and _is_side_tab(l, clip):
                # vertical thumb-tab / side running head in the outer margin of an upright page
                sp = [_make_span(s) for s in l["spans"] if s.get("text", "").strip() and _inside(s["bbox"], clip)]
                if sp:
                    ml = _finish_line(sp, page_index)
                    ml.role = "rotated-margin"
                    margin.append(ml)
                continue
            spans = []
            for s in l["spans"]:
                if not s.get("text"):
                    continue
                if not rot and not _inside(s["bbox"], clip):
                    continue
                sp = _make_span(s)
                if rot:
                    sp.bbox = tuple(round(v, 2) for v in rot_bbox(sp.bbox, rot, W, H))
                unmapped += sum(1 for ch in sp.text if 0xF000 <= ord(ch) <= 0xF0FF)
                spans.append(sp)
            if spans and any(sp.text.strip() for sp in spans):
                frags.append(spans)
    # merge fragments sharing a baseline and nearly touching
    frags.sort(key=lambda f: (round(min(s.bbox[3] for s in f)), min(s.bbox[0] for s in f)))
    merged: list[list[Span]] = []
    marker_re = re.compile(r"\s*(?:[•●■□▪◆◇◦○‣∙·►▶✓✔➢➤❑❖–—]|\(?\d{1,3}[.)]|\(?[A-Za-z][.)]|\(?[ivxIVX]{1,4}[.)])\s*")
    for f in frags:
        fb = (min(s.bbox[0] for s in f), min(s.bbox[1] for s in f), max(s.bbox[2] for s in f), max(s.bbox[3] for s in f))
        fsize = max(s.size for s in f)
        target = None
        for m in merged[-6:]:
            mb = (min(s.bbox[0] for s in m), min(s.bbox[1] for s in m), max(s.bbox[2] for s in m), max(s.bbox[3] for s in m))
            msize = max(s.size for s in m)
            size = max(fsize, msize)
            vover = min(mb[3], fb[3]) - max(mb[1], fb[1])
            if vover < 0.45 * min(mb[3] - mb[1], fb[3] - fb[1]):
                continue
            hover = min(mb[2], fb[2]) - max(mb[0], fb[0])        # >0: horizontal overlap
            gap = -hover
            limit = 0.9 * size
            left_txt = "".join(x.text for x in (m if mb[0] <= fb[0] else f))
            if marker_re.fullmatch(left_txt) and abs(mb[3] - fb[3]) < 0.6 * size:
                limit = 3.5 * size          # list marker set apart from its text (hanging bullet)
            if gap < limit and hover <= 0.6 * size:
                target = m
                break
        if target is not None:
            target.extend(f)
        else:
            merged.append(list(f))
    lines = [_finish_line(m, page_index) for m in merged]
    return lines + margin, unmapped


def page_drawings(page) -> list[dict]:
    out = []
    try:
        drs = page.get_drawings()
    except Exception:
        return out
    for d in drs:
        r = d.get("rect")
        if r is None:
            continue
        items = d.get("items", [])
        kinds = "".join(sorted({it[0] for it in items}))
        w, h = r.width, r.height
        out.append({"bbox": (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)),
                    "n": len(items), "kinds": kinds, "fill": d.get("fill") is not None,
                    "hline": h < 2.5 and w > 8, "vline": w < 2.5 and h > 8})
    return out


def page_images(page, clip) -> list[dict]:
    out = []
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return out
    for i in infos:
        b = i["bbox"]
        bb = (max(b[0], clip[0]), max(b[1], clip[1]), min(b[2], clip[2]), min(b[3], clip[3]))
        if bb[2] - bb[0] < 2 or bb[3] - bb[1] < 2:
            continue
        out.append({"bbox": tuple(round(v, 1) for v in bb), "xref": i.get("xref", 0),
                    "w": i.get("width"), "h": i.get("height"), "full": tuple(round(v, 1) for v in b)})
    return out


def trim_clip(page) -> tuple:
    """Trim box expressed in page (cropbox-relative) coordinates, clipped to the page."""
    r = page.rect
    try:
        t, c = page.trimbox, page.cropbox
        if t.width > 50 and t.height > 50:
            x0, y0, x1, y1 = t.x0 - c.x0, t.y0 - c.y0, t.x1 - c.x0, t.y1 - c.y0
            x0, y0, x1, y1 = max(x0, r.x0), max(y0, r.y0), min(x1, r.x1), min(y1, r.y1)
            if x1 - x0 > 50 and y1 - y0 > 50:
                return (x0, y0, x1, y1)
    except Exception:
        pass
    return (r.x0, r.y0, r.x1, r.y1)


def image_flatness(doc, xref: int) -> float | None:
    """Std-dev of a downscaled copy of the image: ~0 for tints/backgrounds."""
    try:
        pix = pymupdf.Pixmap(doc, xref)
        if pix.n - pix.alpha >= 4:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        while pix.width * pix.height > 40000:
            pix.shrink(1)
        data = pix.samples
        step = pix.n
        vals = [data[i] for i in range(0, len(data), step)]
        if not vals:
            return None
        m = sum(vals) / len(vals)
        return round((sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5, 2)
    except Exception:
        return None


def extract_page(doc, pno: int, gindex: int, pdf_name: str, cache_dir: Path | None = None) -> PageInfo:
    cache_file = None
    if cache_dir is not None:
        cache_file = Path(cache_dir) / f"page_{pno + 1:04d}.json"
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("v") == EXTRACT_VERSION:
                    return page_from_json(data, gindex)
            except Exception:
                pass
    page = doc[pno]
    clip = trim_clip(page)
    info = PageInfo(index=gindex, pdf=pdf_name, pdf_page=pno, width=page.rect.width, height=page.rect.height, trim=clip)
    try:
        raw = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES)
        rot = dominant_rotation(raw)
        W, H = page.rect.width, page.rect.height
        if rot:
            info.rot, info.orig_size = rot, (W, H)
            clip = rot_bbox(clip, rot, W, H)
            info.trim = clip
            info.width, info.height = (H, W)
        lines, unmapped = build_lines(raw, gindex, clip, rot, W, H)
        kept = []
        for l in lines:
            if SLUG_RE.search(l.text):
                l.role = "slug"
            kept.append(l)
        info.lines = kept
        if unmapped:
            info.errors.append(f"UNMAPPED_PUA_GLYPHS:{unmapped}")
        info.images = page_images(page, unrot_bbox(clip, rot, W, H) if rot else clip)
        if rot:
            for im in info.images:
                im["bbox"] = tuple(round(v, 1) for v in rot_bbox(im["bbox"], rot, W, H))
        seen = {}
        for im in info.images:
            x = im.get("xref") or 0
            if x and x not in seen:
                seen[x] = image_flatness(doc, x)
            im["std"] = seen.get(x)
        info.drawings = page_drawings(page)
        if rot:
            for dr in info.drawings:
                dr["bbox"] = tuple(round(v, 1) for v in rot_bbox(dr["bbox"], rot, W, H))
                dr["hline"], dr["vline"] = dr["vline"], dr["hline"]
    except Exception as e:  # recorded, never fatal for the book
        info.errors.append(f"EXTRACTION_ERROR:{type(e).__name__}:{e}")
    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(page_to_json(info), fh, ensure_ascii=False)
        tmp.replace(cache_file)
    return info


# ---------------------------------------------------------------- caching

def page_to_json(p: PageInfo) -> dict:
    return {"v": EXTRACT_VERSION, "pdf": p.pdf, "pdf_page": p.pdf_page, "w": p.width, "h": p.height, "trim": p.trim,
            "rot": p.rot, "orig": p.orig_size,
            "ocr": p.is_ocr, "ocr_conf": p.ocr_conf, "errors": p.errors, "images": p.images, "drawings": p.drawings,
            "lines": [{"b": l.bbox, "r": l.role, "c": l.conf, "o": l.ocr,
                       "s": [[s.text, s.font, s.size, int(s.bold), int(s.italic), int(s.sup), int(s.sub), int(s.sc),
                              int(s.mono), s.color, s.bbox] for s in l.spans]} for l in p.lines]}


def page_from_json(d: dict, gindex: int) -> PageInfo:
    p = PageInfo(index=gindex, pdf=d["pdf"], pdf_page=d["pdf_page"], width=d["w"], height=d["h"], trim=tuple(d["trim"]),
                 is_ocr=d.get("ocr", False), ocr_conf=d.get("ocr_conf"), errors=d.get("errors", []),
                 images=[{**i, "bbox": tuple(i["bbox"])} for i in d.get("images", [])],
                 drawings=[{**x, "bbox": tuple(x["bbox"])} for x in d.get("drawings", [])],
                 rot=d.get("rot", 0), orig_size=tuple(d["orig"]) if d.get("orig") else None)
    for l in d["lines"]:
        spans = [Span(text=s[0], font=s[1], size=s[2], bold=bool(s[3]), italic=bool(s[4]), sup=bool(s[5]),
                      sub=bool(s[6]), sc=bool(s[7]), mono=bool(s[8]), color=s[9], bbox=tuple(s[10])) for s in l["s"]]
        p.lines.append(Line(spans=spans, bbox=tuple(l["b"]), page=gindex, role=l.get("r", "body"),
                            conf=l.get("c", 1.0), ocr=l.get("o", False)))
    return p
