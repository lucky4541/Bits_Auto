"""PDF <-> golden XML alignment for paired samples.

For every sample that has both PDF(s) and approved XML, the text of known
XML structures (chapter titles, section titles by disp-level, figure/table
labels, captions, list items, references, index terms) is located in the PDF
and the *typographic signature* of the matching PDF lines is recorded.

Signatures are stored relative to the body-text size of that book, so they
generalise to books typeset with different fonts:
    size_ratio, bold, italic, all_caps, indent_from_column, gap_before ...
"""
from __future__ import annotations

import collections
import re
import statistics
import unicodedata
from pathlib import Path

import pymupdf

from .sample_analyzer import local, parse_xml

SLUG_RE = re.compile(r"\.indd\b|\d{2}-\d{2}-\d{4}\s+\d{1,2}:\d{2}", re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("­", "").replace("‑", "-")
    s = re.sub(r"[^\w]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def pdf_lines(pdf: Path, max_pages: int | None = None):
    doc = pymupdf.open(str(pdf))
    out = []
    for pno, page in enumerate(doc):
        if max_pages and pno >= max_pages:
            break
        d = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            for l in b["lines"]:
                spans = [s for s in l["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in l["spans"]).strip()
                if SLUG_RE.search(text):
                    continue
                main = max(spans, key=lambda s: len(s["text"]))
                out.append({
                    "page": pno, "text": text, "x0": l["bbox"][0], "y0": l["bbox"][1], "x1": l["bbox"][2],
                    "y1": l["bbox"][3], "size": round(main["size"], 1), "font": main["font"],
                    "bold": bool(main["flags"] & 16) or "bold" in main["font"].lower() or "black" in main["font"].lower(),
                    "italic": bool(main["flags"] & 2) or "italic" in main["font"].lower() or "-it" in main["font"].lower(),
                    "color": main.get("color", 0), "page_w": page.rect.width, "page_h": page.rect.height,
                })
    doc.close()
    return out


def body_size(lines) -> float:
    c = collections.Counter()
    for l in lines:
        c[l["size"]] += len(l["text"])
    return c.most_common(1)[0][0] if c else 10.0


def xml_probes(xml_path: Path):
    """(category, text) pairs from the golden XML."""
    root = parse_xml(xml_path).getroot()
    probes = []

    def txt(e):
        return re.sub(r"\s+", " ", "".join(e.itertext())).strip() if e is not None else ""

    for bp in root.iter("book-part"):
        tg = bp.find("book-part-meta/title-group")
        if tg is None:
            continue
        probes.append((f"{bp.get('book-part-type','part')}_title", txt(tg.find("title"))))
        if tg.find("label") is not None:
            probes.append((f"{bp.get('book-part-type','part')}_label", txt(tg.find("label"))))
    for s in root.iter("sec"):
        t = s.find("title")
        if t is not None:
            probes.append((f"sec_level{s.get('disp-level', '?')}", txt(t)))
    for f in root.iter("fig"):
        if f.find("label") is not None:
            probes.append(("fig_label", txt(f.find("label"))))
    for tw in root.iter("table-wrap"):
        if tw.find("label") is not None:
            probes.append(("table_label", txt(tw.find("label"))))
        for td in list(tw.iter("th"))[:3]:
            probes.append(("table_head_cell", txt(td)))
    for bx in root.iter("boxed-text"):
        t = bx.find("caption/title")
        if t is not None:
            probes.append((f"box_title[{bx.get('content-type')}]", txt(t)))
    for li in root.iter("list-item"):
        p = li.find("p")
        if p is not None:
            probes.append((f"list_item[{li.getparent().get('list-type')}]", txt(p)))
    for r in root.iter("ref"):
        probes.append(("reference", txt(r)))
    for p in root.iter("p"):
        par = p.getparent()
        if par is not None and local(par.tag) == "sec":
            probes.append(("body_p", txt(p)))
    for ie in root.iter("index-entry"):
        depth = sum(1 for _ in ie.iterancestors("index-entry"))
        probes.append((f"index_entry_level{depth}", txt(ie.find("term"))))
    for fn in root.iter("fn"):
        probes.append(("footnote", txt(fn)))
    return [(c, t) for c, t in probes if len(norm(t)) >= 6]


def align(pdfs: list[Path], xml_path: Path, per_category: int = 400):
    lines = []
    for p in pdfs:
        for l in pdf_lines(p):
            l["pdf"] = p.name
            lines.append(l)
    if not lines:
        return {}
    bsize = body_size(lines)
    index = collections.defaultdict(list)
    for i, l in enumerate(lines):
        k = norm(l["text"])[:24]
        if len(k) >= 6:
            index[k[:12]].append(i)
    feats = collections.defaultdict(list)
    seen_cat = collections.Counter()
    for cat, text in xml_probes(xml_path):
        if seen_cat[cat] >= per_category:
            continue
        key = norm(text)
        cands = index.get(key[:12], [])
        hit = None
        for i in cands:
            nl = norm(lines[i]["text"])
            if key.startswith(nl[:min(len(nl), 40)]) or nl.startswith(key[:40]):
                hit = i
                break
        if hit is None:
            continue
        seen_cat[cat] += 1
        l = lines[hit]
        prev = lines[hit - 1] if hit > 0 and lines[hit - 1]["page"] == l["page"] else None
        letters = [c for c in l["text"] if c.isalpha()]
        feats[cat].append({
            "size_ratio": round(l["size"] / bsize, 2),
            "bold": l["bold"], "italic": l["italic"],
            "all_caps": bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.85,
            "gap_before": round((l["y0"] - prev["y1"]) / bsize, 2) if prev else None,
            "x0_rel": round(l["x0"] / l["page_w"], 3),
            "font": re.sub(r"^[A-Z]{6}\+", "", l["font"]),
            "color": l["color"],
        })
    sig = {"body_size": bsize, "categories": {}}
    for cat, fl in feats.items():
        def frac(k):
            return round(sum(1 for f in fl if f[k]) / len(fl), 2)
        gaps = [f["gap_before"] for f in fl if f["gap_before"] is not None]
        sig["categories"][cat] = {
            "matched": len(fl),
            "size_ratio_median": statistics.median(f["size_ratio"] for f in fl),
            "size_ratio_range": [min(f["size_ratio"] for f in fl), max(f["size_ratio"] for f in fl)],
            "bold": frac("bold"), "italic": frac("italic"), "all_caps": frac("all_caps"),
            "gap_before_median": statistics.median(gaps) if gaps else None,
            "colored": round(sum(1 for f in fl if f["color"] not in (0,)) / len(fl), 2),
            "fonts": dict(collections.Counter(f["font"] for f in fl).most_common(3)),
        }
    return sig
