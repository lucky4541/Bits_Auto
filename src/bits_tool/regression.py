"""Golden-XML regression: compare a generated BITS XML with the approved sample.

Metrics (all computed from both XMLs, nothing estimated):
  text recall/precision · element counts · section titles & levels ·
  paragraph boundaries · figure/table presence, IDs, captions, placement ·
  cross-reference agreement · index/TOC sizes · ID agreement
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from lxml import etree

KEY_ELEMENTS = ["book-part", "sec", "p", "list", "list-item", "fig", "table-wrap", "boxed-text", "ref", "fn",
                "disp-formula", "xref", "index-entry", "nav-pointer", "toc-entry", "target"]


def _parse(p):
    return etree.parse(str(p), etree.XMLParser(load_dtd=False, huge_tree=True, recover=True))


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("­", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _words(s):
    return _norm(s).split()


def _txt(e):
    return "".join(e.itertext()) if e is not None else ""


def _local(t):
    return t.split("}", 1)[1] if isinstance(t, str) and t.startswith("{") else t


def _label_key(label: str) -> str | None:
    m = re.search(r"([A-Z]?\d+(?:[.\-–]\d+)*)", label or "")
    return m.group(1).replace(".", "-").replace("–", "-") if m else None


def _prev_block_text(el) -> str:
    prev = el.getprevious()
    while prev is not None and _local(prev.tag) in ("fig", "table-wrap", "fig-group"):
        prev = prev.getprevious()
    if prev is None:
        par = el.getparent()
        if par is not None and _local(par.tag) == "p":
            return "IN-P:" + " ".join(_words(_txt(par))[:8])
        return "START:" + (" ".join(_words(_txt(par.find("title"))))[:40] if par is not None and par.find("title") is not None else "")
    return " ".join(_words(_txt(prev))[:8])


def compare(generated: Path, golden: Path) -> dict:
    g, o = _parse(golden).getroot(), _parse(generated).getroot()
    res: dict = {"generated": str(generated), "golden": str(golden)}
    # --- text
    gw, ow = Counter(_words("".join(g.itertext()))), Counter(_words("".join(o.itertext())))
    inter = sum((gw & ow).values())
    res["text"] = {"golden_words": sum(gw.values()), "generated_words": sum(ow.values()),
                   "recall": round(inter / max(1, sum(gw.values())), 4), "precision": round(inter / max(1, sum(ow.values())), 4)}
    # --- counts
    res["counts"] = {k: {"golden": sum(1 for _ in g.iter(k)), "generated": sum(1 for _ in o.iter(k))} for k in KEY_ELEMENTS}
    # --- sections
    gs = [(_norm(_txt(s.find("title"))), s.get("disp-level")) for s in g.iter("sec") if s.find("title") is not None]
    os_ = [(_norm(_txt(s.find("title"))), s.get("disp-level")) for s in o.iter("sec") if s.find("title") is not None]
    gset, oset = Counter(t for t, _ in gs if t), Counter(t for t, _ in os_ if t)
    match = sum((gset & oset).values())
    lvl_g = {t: l for t, l in gs}
    lvl_o = {t: l for t, l in os_}
    same_lvl = sum(1 for t in (set(lvl_g) & set(lvl_o)) if lvl_g[t] == lvl_o[t])
    res["sections"] = {"golden": len(gs), "generated": len(os_), "title_recall": round(match / max(1, len(gs)), 4),
                       "title_precision": round(match / max(1, len(os_)), 4),
                       "level_agreement": round(same_lvl / max(1, len(set(lvl_g) & set(lvl_o))), 4)}
    # --- paragraphs (boundary agreement by first 8 words)
    gp = Counter(" ".join(_words(_txt(p))[:8]) for p in g.iter("p") if _txt(p).strip())
    op = Counter(" ".join(_words(_txt(p))[:8]) for p in o.iter("p") if _txt(p).strip())
    pm = sum((gp & op).values())
    res["paragraphs"] = {"golden": sum(gp.values()), "generated": sum(op.values()),
                         "start_recall": round(pm / max(1, sum(gp.values())), 4), "start_precision": round(pm / max(1, sum(op.values())), 4)}
    # --- figures / tables
    for kind in ("fig", "table-wrap"):
        gd = {}
        for e in g.iter(kind):
            k = _label_key(_txt(e.find("label")))
            if k:
                gd[k] = e
        od = {}
        for e in o.iter(kind):
            k = _label_key(_txt(e.find("label")))
            if k:
                od[k] = e
        common = set(gd) & set(od)
        same_id = sum(1 for k in common if gd[k].get("id") == od[k].get("id"))
        cap_ok = sum(1 for k in common if _norm(_txt(gd[k].find("caption")))[:60] == _norm(_txt(od[k].find("caption")))[:60])
        place_ok = sum(1 for k in common if _prev_block_text(gd[k]) == _prev_block_text(od[k]))
        rows = {}
        if kind == "table-wrap":
            rows_ok = sum(1 for k in common if len(gd[k].findall(".//tr")) == len(od[k].findall(".//tr")))
            rows = {"row_count_agreement": round(rows_ok / max(1, len(common)), 4)}
        res[kind] = {"golden_labeled": len(gd), "generated_labeled": len(od), "found": len(common),
                     "recall": round(len(common) / max(1, len(gd)), 4), "id_agreement": round(same_id / max(1, len(common)), 4),
                     "caption_agreement": round(cap_ok / max(1, len(common)), 4),
                     "placement_agreement": round(place_ok / max(1, len(common)), 4),
                     "missing": sorted(set(gd) - set(od))[:40], "extra": sorted(set(od) - set(gd))[:40], **rows}
    # --- xrefs
    def xr(root):
        return Counter((x.get("ref-type"), _norm(_txt(x))) for x in root.iter("xref"))
    gx, ox = xr(g), xr(o)
    xm = sum((gx & ox).values())
    res["xrefs"] = {"golden": sum(gx.values()), "generated": sum(ox.values()), "recall": round(xm / max(1, sum(gx.values())), 4),
                    "precision": round(xm / max(1, sum(ox.values())), 4),
                    "by_type": {t: {"golden": sum(v for (tt, _), v in gx.items() if tt == t),
                                    "generated": sum(v for (tt, _), v in ox.items() if tt == t)} for t in sorted({t for t, _ in gx} | {t for t, _ in ox}, key=str)}}
    # --- ids
    gids = {e.get("id") for e in g.iter() if isinstance(e.tag, str) and e.get("id")}
    oids = {e.get("id") for e in o.iter() if isinstance(e.tag, str) and e.get("id")}
    res["ids"] = {"golden": len(gids), "generated": len(oids), "shared": len(gids & oids),
                  "book_part_ids_equal": sorted(e.get("id") for e in g.iter("book-part")) == sorted(e.get("id") for e in o.iter("book-part"))}
    # --- index
    res["index"] = {"golden_entries": res["counts"]["index-entry"]["golden"], "generated_entries": res["counts"]["index-entry"]["generated"]}
    gi = Counter(_norm(_txt(e.find("term"))) for e in g.iter("index-entry"))
    oi = Counter(_norm(_txt(e.find("term"))) for e in o.iter("index-entry"))
    res["index"]["term_recall"] = round(sum((gi & oi).values()) / max(1, sum(gi.values())), 4)
    res["score"] = round(sum([res["text"]["recall"], res["text"]["precision"], res["sections"]["title_recall"],
                              res["sections"]["title_precision"], res["paragraphs"]["start_recall"],
                              res["fig"]["recall"], res["table-wrap"]["recall"], res["xrefs"]["recall"],
                              res["index"]["term_recall"]]) / 9, 4)
    return res


def summarize(r: dict) -> str:
    c = r["counts"]
    return (f"score {r['score']:.3f} | text R{r['text']['recall']:.3f} P{r['text']['precision']:.3f} | "
            f"sec {r['sections']['generated']}/{r['sections']['golden']} R{r['sections']['title_recall']:.2f} P{r['sections']['title_precision']:.2f} lvl{r['sections']['level_agreement']:.2f} | "
            f"p {r['paragraphs']['generated']}/{r['paragraphs']['golden']} startR{r['paragraphs']['start_recall']:.2f} | "
            f"fig {r['fig']['found']}/{r['fig']['golden_labeled']} id{r['fig']['id_agreement']:.2f} place{r['fig']['placement_agreement']:.2f} | "
            f"tbl {r['table-wrap']['found']}/{r['table-wrap']['golden_labeled']} place{r['table-wrap']['placement_agreement']:.2f} rows{r['table-wrap'].get('row_count_agreement', 0):.2f} | "
            f"xref R{r['xrefs']['recall']:.2f} P{r['xrefs']['precision']:.2f} | index {c['index-entry']['generated']}/{c['index-entry']['golden']} R{r['index']['term_recall']:.2f} | "
            f"list {c['list']['generated']}/{c['list']['golden']} box {c['boxed-text']['generated']}/{c['boxed-text']['golden']} ref {c['ref']['generated']}/{c['ref']['golden']}")


def save(r: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(r, fh, indent=1, ensure_ascii=False)
