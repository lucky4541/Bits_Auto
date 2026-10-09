"""Independent validation of the written XML (re-parsed from disk).

  well-formedness · DTD · ID uniqueness / syntax / pattern · IDREF + xref +
  nav-pointer targets (and target *type*) · image references · figure / table
  checks · TOC / index links · content completeness (PDF words vs XML words)
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
ID_SYNTAX = re.compile(r"^[A-Za-z_][\w.\-]*$")
EXPECTED_TARGET = {"fig": {"fig", "fig-group"}, "table": {"table-wrap"}, "bibr": {"ref"}, "fn": {"fn"},
                   "table-fn": {"fn"}, "chapter": {"book-part"}, "section": {"sec"}, "sec": {"sec"},
                   "app": {"book-part", "app"}, "boxed-text": {"boxed-text"}, "disp-formula": {"disp-formula"}}


def local(el) -> str:
    t = el.tag
    return t.split("}", 1)[1] if isinstance(t, str) and t.startswith("{") else (t if isinstance(t, str) else "")


def parse(path: Path):
    parser = etree.XMLParser(load_dtd=False, resolve_entities=False, huge_tree=True)
    return etree.parse(str(path), parser)


def validate_dtd(tree, rules) -> dict:
    ok, errs = rules.validate(tree)
    return {"valid": ok, "errors": errs[:500], "error_count": len(errs)}


def validate_ids(tree, prefix: str) -> dict:
    ids = Counter()
    bad_syntax, pattern = [], []
    pat = re.compile(rf"^(?:{re.escape(prefix)}-[a-z]+\d{{3,}}(?:-[a-z\-]+\d{{3,}})*(?:-fn\d{{3,}})?|{re.escape(prefix)}-(?:cover|thumbnail|per|license)|{re.escape(prefix)}-[a-z]+\d{{3,}}|page[0-9a-z]+|{re.escape(prefix)}-fm001-cap000)$")
    for el in tree.getroot().iter():
        i = el.get("id") if isinstance(el.tag, str) else None
        if i is None:
            continue
        ids[i] += 1
        if not ID_SYNTAX.match(i):
            bad_syntax.append(i)
        elif not pat.match(i):
            pattern.append(i)
    dups = [i for i, n in ids.items() if n > 1]
    return {"total": sum(ids.values()), "unique": len(ids), "duplicates": dups, "invalid": bad_syntax,
            "pattern_violations": pattern[:200], "pattern_violation_count": len(pattern)}


def validate_links(tree) -> dict:
    root = tree.getroot()
    by_id = {}
    for el in root.iter():
        if isinstance(el.tag, str) and el.get("id"):
            by_id[el.get("id")] = el
    total = 0
    broken, wrong_type = [], []
    by_kind = defaultdict(lambda: {"total": 0, "valid": 0})
    incoming = defaultdict(list)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        name = local(el)
        rid = el.get("rid")
        if name in ("xref", "nav-pointer") or (rid and name in ("answer", "question")):
            if rid is None:
                continue
            total += 1
            kind = el.get("ref-type") or ("page" if name == "nav-pointer" and (rid or "").startswith("page") else name)
            by_kind[kind]["total"] += 1
            tgt = by_id.get(rid)
            txt = "".join(el.itertext())[:60]
            if tgt is None:
                broken.append({"element": name, "rid": rid, "ref-type": el.get("ref-type"), "text": txt})
                continue
            exp = EXPECTED_TARGET.get(el.get("ref-type") or "")
            if exp and local(tgt) not in exp:
                wrong_type.append({"rid": rid, "ref-type": el.get("ref-type"), "target": local(tgt), "text": txt})
                continue
            by_kind[kind]["valid"] += 1
            incoming[rid].append(txt)
        elif name == "ext-link" and el.get("ext-link-type") in ("fig", "table") and el.get(XLINK_HREF) not in by_id:
            total += 1
            broken.append({"element": name, "rid": el.get(XLINK_HREF), "text": "".join(el.itertext())[:60]})
    figs = [e.get("id") for e in root.iter("fig")]
    tabs = [e.get("id") for e in root.iter("table-wrap")]
    return {"total": total, "valid": total - len(broken) - len(wrong_type), "broken": broken, "wrong_type": wrong_type,
            "by_kind": dict(by_kind),
            "figures": {"total": len(figs), "linked": sum(1 for f in figs if incoming.get(f)),
                        "unlinked": [f for f in figs if not incoming.get(f)]},
            "tables": {"total": len(tabs), "linked": sum(1 for t in tabs if incoming.get(t)),
                       "unlinked": [t for t in tabs if not incoming.get(t)]},
            "incoming": {k: len(v) for k, v in incoming.items()}}


def validate_images(tree, xml_dir: Path) -> dict:
    missing, unreadable, ok = [], [], 0
    for el in tree.getroot().iter():
        if not isinstance(el.tag, str):
            continue
        href = el.get(XLINK_HREF)
        if href is None or local(el) not in ("graphic", "inline-graphic", "supplementary-material"):
            continue
        cands = [xml_dir / href, xml_dir.parent / "Assets" / href, xml_dir / "Assets" / href, xml_dir.parent / "images" / href,
                 xml_dir / "images" / href]
        f = next((c for c in cands if c.exists()), None)
        if f is None:
            missing.append(href)
            continue
        try:
            if f.suffix.lower() == '.svg':
                svg = etree.parse(str(f), etree.XMLParser(resolve_entities=False, no_network=True))
                if svg.getroot().tag != '{http://www.w3.org/2000/svg}svg':
                    raise ValueError('not an SVG')
                import pymupdf
                with pymupdf.open(f) as vector:
                    vector[0].get_pixmap(matrix=pymupdf.Matrix(.5, .5))
            else:
                from PIL import Image
                with Image.open(f) as im:
                    im.verify()
            ok += 1
        except Exception:
            unreadable.append(href)
    return {"ok": ok, "missing": missing, "unreadable": unreadable}


def validate_structure(tree) -> dict:
    root = tree.getroot()
    issues = []
    for tw in root.iter("table-wrap"):
        if tw.find("table") is None and tw.find("graphic") is None:
            issues.append(f"table-wrap {tw.get('id')} has neither table nor graphic")
        if tw.get("specific-use") != "inline":
            issues.append(f"table-wrap {tw.get('id')} missing specific-use=inline")
    for f in root.iter("fig"):
        if f.find("graphic") is None:
            issues.append(f"fig {f.get('id')} has no graphic")
    empty_p = [p.get("id") for p in root.iter("p") if not "".join(p.itertext()).strip() and len(p) == 0]
    for bp in root.iter("book-part"):
        if bp.find("book-part-meta/book-part-id") is None:
            issues.append(f"book-part {bp.get('id')} lacks book-part-id")
    return {"issues": issues, "empty_paragraphs": empty_p,
            "counts": {k: sum(1 for _ in root.iter(k)) for k in ("book-part", "sec", "p", "list", "fig", "table-wrap",
                                                                    "boxed-text", "ref", "fn", "disp-formula", "index-entry",
                                                                    "toc-entry", "target", "xref", "nav-pointer")}}


# ------------------------------------------------------------ completeness

def _words(s: str) -> list[str]:
    s = unicodedata.normalize("NFKC", s).replace("­", "").lower()
    return re.findall(r"\w+", s)


def content_coverage(pages, xml_tree, exclude_roles=("header", "footer", "folio", "slug", "figure-text")) -> dict:
    def coverage_text(el):
        # Presentation MathML stores identifiers/scripts in separate token
        # elements. Joining without boundaries turns V + max into "Vmax" and
        # reports retained text as missing. Preserve normal inline text joins.
        if el.tag == "{http://www.w3.org/1998/Math/MathML}math":
            return " " + " ".join(el.itertext()) + " "
        text = el.text or ""
        for child in el:
            boundary = " " if local(child) in {"td", "th", "tr", "p", "label", "list-item",
                                                     "caption", "title", "fig", "sec", "table-wrap",
                                                     "disp-formula", "boxed-text"} else ""
            text += boundary + coverage_text(child) + boundary + (child.tail or "")
        return text
    xml_words = Counter(_words(coverage_text(xml_tree.getroot())))
    total = matched = 0
    per_page = []
    remaining = Counter(xml_words)
    missing_samples = []
    for p in pages:
        pw = []
        prefixes = {}
        lines = [l for l in p.lines if l.role not in exclude_roles]
        for l in sorted(lines, key=lambda line: (line.y0, line.x0)):
            ws = _words(l.text)
            if id(l) in prefixes and ws:
                ws[0] = prefixes.pop(id(l)) + ws[0]
            t = l.text.rstrip()
            if ws and t.endswith(("-", "\u00ad")) and re.search(r"[^\W\d_][-\u00ad]$", t):
                # Only join the next line in the same text flow. Extraction
                # order interleaves columns and table cells on real pages.
                candidates = [o for o in lines if o.y0 > l.y0 + .5 * l.size
                              and -.5 * l.size <= o.y0 - l.y1 <= 1.8 * l.size
                              and abs(o.x0 - l.x0) <= 2 * l.size
                              and abs(o.size - l.size) < 1 and o.role == l.role
                              and _words(o.text)]
                if candidates:
                    next_line = min(candidates, key=lambda o: (o.y0, abs(o.x0 - l.x0)))
                    joined = ws[-1] + _words(next_line.text)[0]
                    if t.endswith("\u00ad") or joined in xml_words:
                        prefixes[id(next_line)] = ws.pop()
            pw.extend(ws)
        pw.extend(prefixes.values())
        pm = 0
        for w in pw:
            if remaining[w] > 0:
                remaining[w] -= 1
                pm += 1
            elif len(missing_samples) < 300:
                missing_samples.append({"page": p.index, "folio": p.folio, "word": w})
        # hyphenation joins: words split at line end count as found if the joined word exists
        total += len(pw)
        matched += pm
        per_page.append({"page": p.index, "folio": p.folio, "words": len(pw), "matched": pm,
                         "coverage": round(pm / len(pw), 4) if pw else 1.0, "ocr": p.is_ocr, "ocr_conf": p.ocr_conf})
    extra = sum(remaining.values())
    fig_words = sum(len(_words(l.text)) for p in pages for l in p.lines if l.role == "figure-text")
    return {"pdf_words": total, "matched": matched, "coverage": round(matched / total, 4) if total else 1.0,
            "xml_extra_words": extra, "figure_internal_words": fig_words, "pages": per_page, "missing_samples": missing_samples}
