"""Sample corpus analyzer.

Parses every approved (golden) BITS XML in the Samples directory with lxml and
derives the production conventions the converter must reproduce:
element / attribute inventories, parent-child rules, ID patterns, link
patterns, figure/table placement relative to first citation, list, boxed-text,
reference, index and TOC structures.

Nothing here is hard-coded to a particular book: every rule is counted from
the corpus and written to analysis/*.json with evidence counts and examples.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from lxml import etree

XLINK = "{http://www.w3.org/1999/xlink}href"
MML_NS = "http://www.w3.org/1998/Math/MathML"

BLOCK_TAGS = {"p", "list", "fig", "fig-group", "table-wrap", "boxed-text", "disp-formula",
              "disp-quote", "def-list", "question-wrap", "sec", "ref-list", "speech",
              "verse-group", "statement", "preformat", "graphic", "media", "array"}


def local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ("mml:" + name) if ns == MML_NS else name
    return tag


def sha1_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def parse_xml(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(load_dtd=False, resolve_entities=False, huge_tree=True,
                             remove_blank_text=False, recover=False)
    return etree.parse(str(path), parser)


# ---------------------------------------------------------------- ID patterns
_DIGITS = re.compile(r"\d+")


def id_shape(value: str, prefix: str | None) -> str:
    """Generalise an id into a pattern: prefix -> {P}, digit runs -> {N:width}."""
    v = value
    if prefix and v.lower().startswith(prefix.lower() + "-"):
        v = "{P}" + v[len(prefix):]
    return _DIGITS.sub(lambda m: "{N%d}" % len(m.group()), v)


def detect_prefix(ids: Iterable[str]) -> str | None:
    c = collections.Counter(i.split("-", 1)[0] for i in ids if "-" in i)
    if not c:
        return None
    prefix, n = c.most_common(1)[0]
    return prefix


@dataclass
class SampleResult:
    isbn: str
    path: str
    root: str = ""
    doctype_public: str = ""
    doctype_system: str = ""
    lang: str = ""
    prefix: str | None = None
    element_counts: collections.Counter = field(default_factory=collections.Counter)
    stats: dict = field(default_factory=dict)


class SampleAnalyzer:
    def __init__(self, samples_dir: Path, out_dir: Path, progress: Callable[[str, float], None] | None = None):
        self.samples_dir = Path(samples_dir)
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.progress = progress or (lambda msg, frac: None)
        # corpus-wide accumulators
        self.elements = collections.Counter()
        self.element_docs = collections.defaultdict(set)
        self.attributes = collections.defaultdict(collections.Counter)      # el@attr -> values
        self.parent_child = collections.defaultdict(collections.Counter)    # parent -> child
        self.child_seq = collections.defaultdict(collections.Counter)       # el -> child sequence
        self.id_patterns = collections.defaultdict(collections.Counter)     # el -> shape
        self.id_examples = collections.defaultdict(dict)
        self.id_conflicts = []
        self.link_patterns = collections.defaultdict(collections.Counter)   # xref ref-type -> target el
        self.link_text = collections.defaultdict(collections.Counter)
        self.link_stats = collections.Counter()
        self.placement = collections.defaultdict(collections.Counter)       # fig/table -> relation
        self.placement_examples = collections.defaultdict(list)
        self.placement_by_sample = collections.defaultdict(collections.Counter)
        self.placement_context = collections.defaultdict(collections.Counter)
        self.structure = collections.defaultdict(collections.Counter)
        self.samples: list[SampleResult] = []

    # ------------------------------------------------------------------ run
    def find_xml(self) -> list[Path]:
        out = []
        for root, _dirs, files in os.walk(self.samples_dir):
            for f in files:
                if f.lower().endswith(".xml") and re.match(r"97[89]\d{10}", f):
                    out.append(Path(root) / f)
        # dedupe identical files (same ISBN appears in several zips)
        seen, uniq = {}, []
        for p in sorted(out):
            h = sha1_file(p)
            if h in seen:
                continue
            seen[h] = p
            uniq.append(p)
        return uniq

    def run(self) -> dict:
        files = self.find_xml()
        for i, p in enumerate(files):
            self.progress(f"Analyzing XML structure: {p.name}", i / max(1, len(files)))
            self.samples.append(self.analyze_file(p))
        self.progress("Writing analysis", 1.0)
        return self.write()

    # ------------------------------------------------------------ per file
    def analyze_file(self, path: Path) -> SampleResult:
        tree = parse_xml(path)
        root = tree.getroot()
        di = tree.docinfo
        res = SampleResult(isbn=path.stem, path=str(path), root=local(root.tag),
                           doctype_public=di.public_id or "", doctype_system=di.system_url or "",
                           lang=root.get("{http://www.w3.org/XML/1998/namespace}lang", ""))
        all_ids = {}
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            i = el.get("id")
            if i:
                all_ids[i] = el
        res.prefix = detect_prefix(all_ids)
        prefix = res.prefix

        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            name = local(el.tag)
            self.elements[name] += 1
            self.element_docs[name].add(res.isbn)
            res.element_counts[name] += 1
            for a, v in el.attrib.items():
                an = a.replace(XLINK, "xlink:href").replace("{http://www.w3.org/XML/1998/namespace}", "xml:")
                key = f"{name}@{an}"
                if an in ("id", "rid", "xlink:href", "alttext") or len(v) > 60:
                    self.attributes[key]["<value>"] += 1
                else:
                    self.attributes[key][v] += 1
            par = el.getparent()
            if par is not None:
                self.parent_child[local(par.tag)][name] += 1
            kids = [local(k.tag) for k in el if isinstance(k.tag, str)]
            if name not in ("p", "td", "th", "title", "mixed-citation", "term", "italic", "bold") and kids:
                seq = []
                for k in kids:  # collapse repeats
                    if not seq or seq[-1] != k:
                        seq.append(k)
                self.child_seq[name][" ".join(seq[:12])] += 1
            i = el.get("id")
            if i:
                shape = id_shape(i, prefix)
                self.id_patterns[name][shape] += 1
                self.id_examples[name].setdefault(shape, i)

        self._links(root, all_ids, res)
        self._placement(root, all_ids, res)
        self._structure(root, res)
        return res

    # ---------------------------------------------------------------- links
    def _links(self, root, ids, res):
        for el in root.iter("xref", "nav-pointer", "ext-link", "answer", "question"):
            name = local(el.tag)
            rid = el.get("rid")
            if name == "ext-link":
                t = el.get("ext-link-type", "")
                href = el.get(XLINK, "")
                self.link_patterns[f"ext-link[{t}]"]["internal" if href in ids else ("uri" if "://" in href or href.startswith("www") else "unresolved")] += 1
                continue
            if rid is None:
                if name in ("xref", "nav-pointer"):
                    self.link_stats[f"{name}:no-rid"] += 1
                continue
            rt = el.get("ref-type") or el.get("specific-use") or ""
            tgt = ids.get(rid)
            key = f"{name}[{rt}]"
            if tgt is None:
                self.link_patterns[key]["<BROKEN>"] += 1
                self.link_stats["broken"] += 1
            else:
                tname = local(tgt.tag)
                if tname == "target":
                    tname = f"target[{tgt.get('target-type','')}]"
                self.link_patterns[key][tname] += 1
                self.link_stats["resolved"] += 1
            txt = "".join(el.itertext()).strip()
            self.link_text[key][re.sub(r"\d+", "#", txt)[:40]] += 1

    # ------------------------------------------------------------ placement
    @staticmethod
    def _block_of(el):
        """nearest ancestor that is a block child of a sec/body-like container."""
        cur = el
        while cur is not None:
            par = cur.getparent()
            if par is None:
                return cur
            if local(par.tag) in ("sec", "body", "named-book-part-body", "boxed-text", "app", "ack") and local(cur.tag) in BLOCK_TAGS:
                return cur
            cur = par
        return el

    def _placement(self, root, ids, res):
        order = {}
        for n, el in enumerate(root.iter()):
            order[el] = n
        first_cite = {}
        cite_count = collections.Counter()
        for x in root.iter("xref"):
            rid = x.get("rid")
            tgt = ids.get(rid)
            if tgt is None or local(tgt.tag) not in ("fig", "table-wrap", "fig-group", "boxed-text"):
                continue
            # ignore xrefs located inside the target itself (caption self-references)
            anc = x
            inside = False
            while anc is not None:
                if anc is tgt:
                    inside = True
                    break
                anc = anc.getparent()
            if inside:
                continue
            if any(local(a.tag) in ("toc", "toc-entry", "index", "index-entry", "nav-pointer-group")
                   for a in x.iterancestors()):
                continue  # TOC / index links are navigation, not body citations
            cite_count[rid] += 1
            if rid not in first_cite:
                first_cite[rid] = x
        for kind in ("fig", "table-wrap", "fig-group", "boxed-text"):
            for t in root.iter(kind):
                tid = t.get("id")
                if not tid:
                    continue
                if kind == "fig" and local(t.getparent().tag) == "fig-group":
                    continue
                has_label = t.find("label") is not None
                bucket = f"{kind}{'' if has_label or kind=='boxed-text' else '(unlabeled)'}"
                x = first_cite.get(tid)
                if x is None:
                    self.placement[bucket]["no_citation"] += 1
                    self.placement_by_sample[res.isbn][f"{bucket}:no_citation"] += 1
                    continue
                cp = x.getparent()
                while cp is not None and local(cp.tag) not in ("p", "title", "td", "th"):
                    cp = cp.getparent()
                if cp is not None and local(cp.tag) == "p" and t.getparent() is cp:
                    tail_is_end = t.getnext() is None and not (t.tail or "").strip()
                    rel = "inside_citing_paragraph_" + ("end" if tail_is_end else "mid")
                    ctx = "list-item" if any(local(a.tag) == "list-item" for a in cp.iterancestors()) else local(cp.getparent().tag)
                    self.placement[bucket][rel] += 1
                    self.placement_by_sample[res.isbn][f"{bucket}:{rel}"] += 1
                    self.placement_context[f"{bucket}:{rel}"][ctx] += 1
                    continue
                blk = self._block_of(x)
                tblk = self._block_of(t) if local(t.getparent().tag) not in ("sec", "body", "named-book-part-body", "boxed-text") else t
                rel = self._relation(blk, tblk, order)
                self.placement[bucket][rel] += 1
                self.placement_by_sample[res.isbn][f"{bucket}:{rel}"] += 1
                ctx = "list-item" if any(local(a.tag) == "list-item" for a in x.iterancestors()) else local(blk.getparent().tag) if blk.getparent() is not None else ""
                self.placement_context[f"{bucket}:{rel}"][ctx] += 1
                if len(self.placement_examples[bucket + ":" + rel]) < 4:
                    self.placement_examples[bucket + ":" + rel].append(
                        {"sample": res.isbn, "target": tid, "first_citation_text": "".join(x.itertext())[:60],
                         "citing_block_id": blk.get("id"), "target_parent": local(t.getparent().tag)})

    @staticmethod
    def _relation(blk, tblk, order):
        if blk is tblk:
            return "citation_inside_target_block"
        if order.get(tblk, 0) < order.get(blk, 0):
            return "before_first_citation"
        bp, tp = blk.getparent(), tblk.getparent()
        if bp is tp:
            sib = blk.getnext()
            hops = 0
            while sib is not None and sib is not tblk:
                if local(sib.tag) not in ("fig", "table-wrap", "fig-group", "boxed-text"):
                    hops += 1
                sib = sib.getnext()
            if hops == 0:
                return "immediately_after_citing_block"
            return f"after_citing_block_+{min(hops,5)}blocks"
        # different containers: is target in same sec ancestry?
        def secs(e):
            s = []
            while e is not None:
                if local(e.tag) in ("sec", "book-part"):
                    s.append(e)
                e = e.getparent()
            return s
        bs, ts = secs(blk), secs(tblk)
        if bs and ts and bs[0] in ts:
            return "later_in_descendant_sec"
        if bs and ts and ts[0] in bs:
            return "after_in_ancestor_sec"
        common = [s for s in bs if s in ts]
        if common and local(common[0].tag) == "book-part":
            return "later_in_other_sec_same_chapter"
        return "different_book_part"

    # ------------------------------------------------------------ structure
    def _structure(self, root, res):
        S = self.structure
        for bp in root.iter("book-part"):
            S["book-part@book-part-type"][bp.get("book-part-type", "")] += 1
            lab = bp.find("book-part-meta/title-group/label")
            if lab is not None:
                S["book-part label shape"][re.sub(r"\d+", "#", "".join(lab.itertext()).strip())[:30]] += 1
            S["book-part parent"][local(bp.getparent().tag)] += 1
            has_pg = bp.find("book-part-meta/fpage") is not None
            S["book-part has fpage/lpage"][str(has_pg)] += 1
        for fm in root.iter("front-matter-part", "preface", "foreword", "dedication", "toc", "ack"):
            S["front-matter children"][local(fm.tag) + ("[" + fm.get("book-part-type", "") + "]" if fm.get("book-part-type") else "")] += 1
        for s in root.iter("sec"):
            S["sec@disp-level"][s.get("disp-level", "<none>")] += 1
            S["sec@sec-type"][s.get("sec-type", "<none>")] += 1
            depth = sum(1 for a in s.iterancestors("sec"))
            S["sec nesting depth"][str(depth)] += 1
            t = s.find("title")
            if t is not None and t.find("target") is not None:
                S["pagenum target inside sec/title"]["yes"] += 1
        for l in root.iter("list"):
            S["list@list-type"][l.get("list-type", "<none>")] += 1
            if any(True for _ in l.iterancestors("list")):
                S["nested list@list-type"][l.get("list-type", "<none>")] += 1
            li = l.find("list-item")
            if li is not None:
                S["list-item has label"][str(li.find("label") is not None) + ":" + l.get("list-type", "")] += 1
        for b in root.iter("boxed-text"):
            S["boxed-text@content-type"][b.get("content-type", "<none>")] += 1
            S["boxed-text@specific-use"][b.get("specific-use", "<none>")] += 1
        for tw in root.iter("table-wrap"):
            S["table-wrap@specific-use"][tw.get("specific-use", "<none>")] += 1
            S["table-wrap has label"][str(tw.find("label") is not None)] += 1
            S["table-wrap has graphic"][str(tw.find(".//graphic") is not None)] += 1
            S["table-wrap has table"][str(tw.find(".//table") is not None)] += 1
            lab = tw.find("label")
            if lab is not None:
                S["table label shape"][re.sub(r"\d+", "#", "".join(lab.itertext()).strip())] += 1
            S["table-wrap-foot present"][str(tw.find("table-wrap-foot") is not None)] += 1
        for f in root.iter("fig"):
            lab = f.find("label")
            S["fig label shape"][re.sub(r"\d+", "#", "".join(lab.itertext()).strip()) if lab is not None else "<none>"] += 1
            S["fig has attrib"][str(f.find("attrib") is not None)] += 1
            g = f.find("graphic")
            if g is not None:
                S["fig graphic href shape"][id_shape(g.get(XLINK, ""), None)] += 1
        for r in root.iter("ref"):
            mc = r.find("mixed-citation")
            S["ref has label"][str(r.find("label") is not None)] += 1
            if mc is not None:
                S["mixed-citation@publication-type"][mc.get("publication-type", "<none>")] += 1
                S["mixed-citation children"][" ".join(sorted({local(c.tag) for c in mc if isinstance(c.tag, str)}))[:80]] += 1
            S["ref-list parent"][local(r.getparent().getparent().tag) if r.getparent() is not None and r.getparent().getparent() is not None else ""] += 1
        for ie in root.iter("index-entry"):
            depth = sum(1 for a in ie.iterancestors("index-entry"))
            S["index-entry depth"][str(depth)] += 1
            np_ = ie.find("nav-pointer-group")
            S["index-entry has nav-pointer-group"][str(np_ is not None)] += 1
            if ie.find("see-entry") is not None:
                S["index see/see-also"]["see-entry"] += 1
            if ie.find("see-also-entry") is not None:
                S["index see/see-also"]["see-also-entry"] += 1
        for np_ in root.iter("nav-pointer"):
            S["nav-pointer@specific-use"][np_.get("specific-use", "<none>")] += 1
            S["nav-pointer text shape"][re.sub(r"\d+", "#", "".join(np_.itertext()).strip())[:12]] += 1
        for t in root.iter("target"):
            S["target@target-type"][t.get("target-type", "<none>")] += 1
            S["target parent"][local(t.getparent().tag)] += 1
            S["target id shape"][id_shape(t.get("id", ""), res.prefix)] += 1
        for g in root.iter("graphic", "inline-graphic"):
            S[local(g.tag) + " href ext"][os.path.splitext(g.get(XLINK, ""))[1].lower()] += 1
        for f in root.iter("fn"):
            S["fn parent"][local(f.getparent().tag)] += 1
        for d in root.iter("disp-formula", "inline-formula"):
            S[local(d.tag) + " content"][" ".join(sorted({local(c.tag) for c in d if isinstance(c.tag, str)}))] += 1
        for qw in root.iter("question-wrap"):
            S["question-wrap@content-type"][qw.get("content-type", "<none>")] += 1
        for st in ("sup", "sub", "italic", "bold", "sc", "underline", "monospace", "bold-italic"):
            pass
        res.stats = {k: v for k, v in res.element_counts.items() if k in (
            "book-part", "sec", "p", "list", "fig", "table-wrap", "ref", "xref", "index-entry", "toc-entry",
            "boxed-text", "fn", "disp-formula", "target", "graphic", "question-wrap")}

    # ---------------------------------------------------------------- write
    def write(self) -> dict:
        o = self.out_dir

        def dump(name, data):
            with open(o / name, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=False)

        def top(counter: collections.Counter, n=40):
            return dict(counter.most_common(n))

        dump("xml_element_inventory.json", {
            e: {"count": c, "documents": len(self.element_docs[e])} for e, c in self.elements.most_common()})
        dump("xml_attribute_inventory.json", {k: top(v, 30) for k, v in sorted(self.attributes.items())})
        dump("xml_parent_child_rules.json", {p: dict(c.most_common()) for p, c in sorted(self.parent_child.items())})
        dump("xml_structure_patterns.json", {
            "child_sequences": {e: top(c, 8) for e, c in sorted(self.child_seq.items())},
            "structures": {k: top(v, 40) for k, v in self.structure.items()}})
        dump("xml_id_patterns.json", {
            e: [{"pattern": s, "count": n, "example": self.id_examples[e][s]} for s, n in c.most_common(12)]
            for e, c in sorted(self.id_patterns.items())})
        dump("xml_link_patterns.json", {
            "xref_targets": {k: dict(v) for k, v in sorted(self.link_patterns.items())},
            "link_text_shapes": {k: top(v, 10) for k, v in sorted(self.link_text.items())},
            "stats": dict(self.link_stats)})
        dump("placement_patterns.json", {
            "relations": {k: dict(v) for k, v in self.placement.items()},
            "by_sample": {k: dict(v) for k, v in self.placement_by_sample.items()},
            "citing_context": {k: dict(v) for k, v in self.placement_context.items()},
            "examples": dict(self.placement_examples)})
        summary = {
            "samples": [{"isbn": s.isbn, "path": s.path, "root": s.root, "doctype_public": s.doctype_public,
                         "doctype_system": s.doctype_system, "lang": s.lang, "id_prefix": s.prefix,
                         "counts": s.stats} for s in self.samples],
            "unique_elements": len(self.elements),
            "unique_attributes": len(self.attributes),
            "parent_child_rules": sum(len(v) for v in self.parent_child.values()),
        }
        dump("xml_sample_summary.json", summary)
        return summary


if __name__ == "__main__":  # pragma: no cover
    import sys
    s = SampleAnalyzer(Path(sys.argv[1]), Path(sys.argv[2]), progress=lambda m, f: print(f"[{f:4.0%}] {m}"))
    out = s.run()
    print(json.dumps({k: v for k, v in out.items() if k != "samples"}, indent=1))
    for smp in out["samples"]:
        print(smp["isbn"], smp["lang"], smp["id_prefix"], smp["counts"])
