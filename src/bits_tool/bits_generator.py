"""Semantic tree -> BITS XML (lxml DOM, DTD-aware).

Responsibilities:
  * assign deterministic IDs (IdGenerator) in final (post-placement) order
  * register every target (TargetRegistry) before links are resolved
  * build book-meta / front-matter / book-body / book-back per the learned mapping
  * check each parent/child against the DTD before appending
No string templating and no regex repair of XML: everything is DOM.
"""
from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from .document_tree import IssueLog, Node
from .id_generator import IdGenerator
from .index_detector import page_tokens
from .layout_analyzer import ROMAN_RE
from .reference_parser import flatten
from .structure_builder import clip_runs
from .target_registry import TargetRegistry
from .xref_resolver import author_year_key

XLINK = "http://www.w3.org/1999/xlink"
MML = "http://www.w3.org/1998/Math/MathML"
NSMAP = {"mml": MML, "xlink": XLINK, "xsi": "http://www.w3.org/2001/XMLSchema-instance", "xi": "http://www.w3.org/2001/XInclude"}
STYLE_TAG = {"bold": "bold", "italic": "italic", "sc": "sc", "monospace": "monospace", "underline": "underline",
             "sup": "sup", "sub": "sub"}
STYLE_ORDER = ["bold", "italic", "sc", "monospace", "underline", "sup", "sub"]
XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def clean(t: str) -> str:
    return XML_ILLEGAL.sub("", t or "")


class BitsGenerator:
    def __init__(self, rules, idgen: IdGenerator, registry: TargetRegistry, issues: IssueLog, cfg, book_info, image_namer=None):
        self.rules = rules
        self.ids = idgen
        self.reg = registry
        self.issues = issues
        self.cfg = cfg
        self.bi = book_info
        self.image_namer = image_namer
        self.emitted_targets: set[str] = set()
        self.part_ids: dict[int, str] = {}      # id(node) -> part code (for scopes)
        self.unit_ids: dict[int, str] = {}
        self.dtd_skips: list[str] = []
        self.pending_marks: list[dict] = []
        self.seq_mode: dict[tuple, bool] = {}
        self.cur_part = "fm001"

    # ============================================================ IDs pass
    def assign_ids(self, root: Node):
        P = self.ids.prefix
        chapter_n = app_n = 0
        for top in root.children:
            if top.kind == "front-matter":
                for part in top.children:
                    self._ids_in(part, "fm001", top_level=True)
            elif top.kind == "book-body":
                for bp in top.children:
                    if bp.kind != "book-part":
                        continue
                    if bp.attrs.get("book-part-type") == "part":
                        code = self.ids.generate_part_id("part", bp.meta.get("part_word"))
                        bp.id = self.ids.generate_chapter_id(code)
                        self.part_ids[id(bp)] = code
                        num = self._label_num(bp.meta.get("label"))
                        self.reg.register("part", None, num, bp.id, "book-part", bp.page, bp.meta.get("label"))
                        for c in bp.children:
                            if c.kind == "body":
                                for ch in c.children:
                                    if ch.kind == "book-part":
                                        chapter_n += 1
                                        self._chapter_ids(ch, f"ch{chapter_n:03d}")
                                    else:
                                        self._ids_in(ch, code)
                    else:
                        chapter_n += 1
                        self._chapter_ids(bp, f"ch{chapter_n:03d}")
            elif top.kind == "book-back":
                for bp in top.children:
                    if bp.kind == "book-part":
                        app_n += 1
                        self._chapter_ids(bp, f"app{app_n:03d}", appendix=True)
                    elif bp.kind == "index":
                        code = self.ids.generate_part_id("index")
                        bp.id = f"{P}-{code}"
                        self.ids.used.add(bp.id)
                        self.part_ids[id(bp)] = code
                        self._index_ids(bp, code)

    @staticmethod
    def _label_num(label: str | None):
        if not label:
            return None
        m = re.search(r"(\d+|[IVXL]+|[A-Z])\s*\.?\s*$", label.strip())
        return m.group(1) if m else None

    def _chapter_ids(self, bp: Node, code: str, appendix=False):
        bp.id = self.ids.generate_chapter_id(code)
        # learned conditional rule (analysis/mapping_conflicts.json): label numbers when every object of the
        # kind is labelled; one running sequence when the part mixes labelled and unlabelled objects
        for kind in ("fig", "table-wrap"):
            objs = [n for n in bp.walk() if n.kind == kind]
            self.seq_mode[(code, kind)] = any(not n.meta.get("key") for n in objs)
        self.part_ids[id(bp)] = code
        num = self._label_num(bp.meta.get("label")) or bp.meta.get("number")
        if appendix:
            self.reg.register("app", None, num or code[3:].lstrip("0"), bp.id, "book-part", bp.page, bp.meta.get("label"))
        else:
            self.reg.register("chapter", None, num or code[2:].lstrip("0"), bp.id, "book-part", bp.page, bp.meta.get("label"))
        for c in bp.children:
            self._ids_in(c, code)

    def _ids_in(self, n: Node, part: str, top_level: bool = False):
        k = n.kind
        P = self.ids.prefix
        if top_level:
            if k == "front-matter-part":
                n.id = self.ids.next(part, "front-matter-part")
            elif k in ("dedication", "preface", "foreword", "toc"):
                n.id = self.ids.next(part, k)
            elif k == "ack":
                n.id = self.ids.next(part, "ack")
            if n.id:
                self.reg.register("fm", None, n.id, n.id, k, n.page)
        if k == "sec":
            n.id = self.ids.next(part, "sec")
            from .heading_detector import norm_title
            self.reg.register("sec", part, norm_title(n.meta.get("title_text", "")), n.id, "sec", n.page)
        elif k == "p":
            n.id = self.ids.next(part, "p")
        elif k == "list":
            n.id = self.ids.next(part, "list")
        elif k == "fig":
            num = self.ids.label_number(n.meta.get("key"))
            n.id = self.ids.next(part, "fig") if self.seq_mode.get((part, "fig")) else self.ids.generate_figure_id(part, num)
            if n.meta.get("caption") is not None or n.meta.get("label"):
                n.meta["caption_id"] = self.ids.next(part, "caption")
            n.meta["graphic_id"] = self.ids.next(part, "graphic")
            if n.meta.get("key"):
                self.reg.register("fig", part, n.meta["key"], n.id, "fig", n.page, n.meta.get("label"))
        elif k == "table-wrap":
            num = self.ids.label_number(n.meta.get("key"))
            n.id = self.ids.next(part, "table-wrap") if self.seq_mode.get((part, "table-wrap")) else self.ids.generate_table_id(part, num)
            if n.meta.get("caption") is not None or n.meta.get("label"):
                n.meta["caption_id"] = self.ids.next(part, "caption")
            if n.meta.get("key"):
                self.reg.register("table", part, n.meta["key"], n.id, "table-wrap", n.page, n.meta.get("label"))
            for f in n.meta.get("foot") or []:
                if f.get("label"):
                    f["id"] = self.ids.generate_footnote_id(part, n.id)
                    f["p_id"] = self.ids.next(part, "p")
                    self.reg.register("tfn", n.id, f["label"], f["id"], "fn", n.page)
                else:
                    f["id"] = self.ids.next(part, "p")
            if "TABLE_STRUCTURAL_EXTRACTION_FAILED" in n.flags:
                n.meta["graphic_id"] = self.ids.next(part, "graphic")
            for row in n.meta.get("rows") or []:
                for cell in row:
                    for b in cell.get("blocks") or []:
                        self._ids_in(b, part)
        elif k == "boxed-text":
            code = "case-study" if n.attrs.get("content-type") == "case-study" else "boxed-text"
            n.id = self.ids.next(part, code)
            if n.meta.get("title"):
                n.meta["caption_id"] = self.ids.next(part, "caption")
            lab = n.meta.get("label")
            if lab:
                from .caption_detector import BOX_LABEL_RE, number_key
                m = BOX_LABEL_RE.match(lab)
                if m:
                    self.reg.register("box", part, number_key(m.group("num")), n.id, "boxed-text", n.page, lab)
        elif k == "disp-formula":
            n.id = self.ids.next(part, "disp-formula")
        elif k == "inline-graphic":
            n.id = self.ids.next(part, "inline-graphic")
        elif k == "ref-list":
            n.id = self.ids.next(part, "ref-list")
        elif k == "ref":
            n.id = self.ids.next(part, "ref")
            lab = n.meta.get("label")
            if lab:
                self.reg.register("bibr", part, re.sub(r"\D", "", lab), n.id, "ref", n.page, lab)
            for key in author_year_key(n.meta.get("text", "")):
                if self.reg.lookup("bibr-ay", part, key) is None:
                    self.reg.register("bibr-ay", part, key, n.id, "ref", n.page)
        elif k == "fn":
            n.id = self.ids.next(part, "fn")
            self.reg.register("fn", part, n.meta.get("label"), n.id, "fn", n.page)
        for c in n.children:
            self._ids_in(c, part)

    def _index_ids(self, idx: Node, code: str):
        for d in idx.meta.get("divs", []):
            for e in d["entries"]:
                e["id"] = self.ids.next(code, "index-entry")
        idx.meta["intro_ids"] = [self.ids.next(code, "p") for _ in idx.meta.get("intro", [])]

    def register_pages(self, root: Node):
        """Page targets: every PageMark in the tree becomes <target id="page{folio}">."""
        seen = set()

        def visit(items):
            for it in items or []:
                if isinstance(it, dict) and it.get("k") == "pg":
                    tid = self.ids.page_target(it["folio"])
                    if tid not in seen:
                        seen.add(tid)
                        self.reg.register("page", None, str(it["folio"]).lower(), tid, "target", it.get("page"))
        for n in root.walk():
            visit(n.inlines)
            for key in ("title", "caption", "title_marks", "marks"):
                v = n.meta.get(key)
                if isinstance(v, list):
                    visit(v)
            if n.kind == "ref":
                visit(n.meta.get("runs"))
            if n.kind == "index":
                for _gi, m in n.meta.get("marks", []):
                    visit([m])
            if n.kind == "toc":
                visit(n.meta.get("marks"))
            for row in n.meta.get("rows") or []:
                for c in row:
                    visit(c.get("runs"))
        self.ids.used.update(seen)

    # ======================================================= XML building
    def E(self, parent, tag, attrs=None, text=None):
        ptag = self._tagname(parent)
        ctag = tag
        if ptag and not self.rules.allows_child(ptag, ctag.replace("mml:", "")) and not self.rules.allows_child(ptag, ctag):
            self.dtd_skips.append(f"{ptag}>{ctag}")
        qn = tag
        if tag.startswith("mml:"):
            qn = f"{{{MML}}}{tag[4:]}"
        el = etree.SubElement(parent, qn)
        for k, v in (attrs or {}).items():
            if v is None:
                continue
            if k.startswith("xlink:"):
                el.set(f"{{{XLINK}}}{k[6:]}", clean(str(v)))
            elif k == "xml:lang":
                el.set("{http://www.w3.org/XML/1998/namespace}lang", str(v))
            else:
                el.set(k, clean(str(v)))
        if text is not None:
            el.text = clean(text)
        return el

    @staticmethod
    def _tagname(el):
        if el is None:
            return None
        t = el.tag
        if t.startswith("{"):
            ns, _, name = t[1:].partition("}")
            return ("mml:" + name) if ns == MML else name
        return t

    # ------------------------------------------------------------ inlines
    def _append_text(self, parent, text: str):
        text = clean(text)
        if not text:
            return
        if len(parent):
            last = parent[-1]
            last.tail = (last.tail or "") + text
        else:
            parent.text = (parent.text or "") + text

    def _styled(self, parent, text, styles, wrapper=None, wattrs=None):
        tgt = parent
        if wrapper:
            if self.rules.allows_child(self._tagname(tgt), wrapper):
                tgt = self.E(tgt, wrapper, wattrs)
        for s in STYLE_ORDER:
            if s in styles and self.rules.allows_child(self._tagname(tgt), STYLE_TAG[s]):
                tgt = self.E(tgt, STYLE_TAG[s])
        self._append_text(tgt, text)

    def _emit_target(self, parent, it) -> bool:
        tid = self.ids.page_target(it["folio"])
        if tid in self.emitted_targets:
            return True
        if not self.rules.allows_child(self._tagname(parent), "target"):
            return False
        self.emitted_targets.add(tid)
        self.E(parent, "target", {"target-type": "pagenum", "id": tid})
        return True

    def append_mathml(self, parent_el, spec):
        if not isinstance(spec, dict):
            node = etree.SubElement(parent_el, f"{{{MML}}}mtext")
            node.text = clean(str(spec or ""))
            return node
        tag = spec.get("tag", "mrow")
        if tag not in {"mrow", "mi", "mn", "mo", "mtext", "msub", "msup", "msubsup", "mfrac", "msqrt", "mroot", "mover", "munder", "munderover", "mfenced"}:
            tag = "mrow"
        node = etree.SubElement(parent_el, f"{{{MML}}}{tag}")
        if spec.get("text") is not None:
            node.text = clean(str(spec["text"]))
        for child in spec.get("children", []):
            self.append_mathml(node, child)
        return node


    def add_inlines(self, parent, items, allow_targets=True):
        ptag = self._tagname(parent)
        if self.pending_marks and allow_targets and self.rules.allows_child(ptag, "target"):
            pend, self.pending_marks = self.pending_marks, []
            for m in pend:
                self._emit_target(parent, m)
        for it in items or []:
            k = it.get("k")
            if k == "t":
                st = [s for s in it["st"] if s in STYLE_TAG]
                if st:
                    self._styled(parent, it["text"], st)
                else:
                    self._append_text(parent, it["text"])
            elif k == "math":
                formula = self.E(parent, "inline-formula")
                math = etree.SubElement(formula, f"{{{MML}}}math", display="inline", alttext=clean(it["text"]))
                self.append_mathml(math, it["mathml"])
            elif k == "xref":
                self._styled(parent, it["text"], [s for s in it["st"] if s in STYLE_TAG], "xref",
                             {"ref-type": it["ref-type"], "rid": it["rid"]})
            elif k == "cite":        # should not survive pass 2, but never lose text
                self._append_text(parent, it["text"])
            elif k == "fnref":
                self._styled(parent, it["label"], ["sup"])
            elif k == "pg":
                if not allow_targets or not self._emit_target(parent, it):
                    self.pending_marks.append(it)     # emitted at the next text position (never lost)
            elif k == "br":
                if self.rules.allows_child(ptag, "break"):
                    self.E(parent, "break")

    # --------------------------------------------------------------- book
    def build(self, root: Node, license_paras: list[Node]) -> etree._Element:
        book = etree.Element("book", nsmap=NSMAP)
        book.set("dtd-version", "1.0")
        book.set("{http://www.w3.org/XML/1998/namespace}lang", self.bi.lang or "en")
        self.book_meta(book, license_paras)
        for top in root.children:
            if top.kind == "front-matter":
                fm = self.E(book, "front-matter")
                for part in top.children:
                    self.front_part(fm, part)
            elif top.kind == "book-body":
                bb = self.E(book, "book-body")
                seq = [0]
                for bp in top.children:
                    self.book_part(bb, bp, seq)
            elif top.kind == "book-back":
                bk = self.E(book, "book-back")
                seq = [1000]
                for bp in top.children:
                    if bp.kind == "index":
                        continue
                    self.book_part(bk, bp, seq)
                self._flush_pending(book)
                for bp in top.children:
                    if bp.kind == "index":
                        self.index(bk, bp)
        self._flush_pending(book)
        return book

    def _flush_pending(self, book):
        if not self.pending_marks:
            return
        ps = [e for e in book.iter("p")]
        if ps:
            self.add_inlines(ps[-1], [])

    def book_meta(self, book, license_paras):
        bi = self.bi
        bm = self.E(book, "book-meta")
        if bi.isbn:
            self.E(bm, "book-id", {"book-id-type": "publisher-id"}, bi.isbn)
        if bi.serial_code:
            self.E(bm, "book-id", {"book-id-type": "serial-code"}, bi.serial_code)
        tg = self.E(bm, "book-title-group")
        title = bi.title or "Untitled"
        # English WK titles end with the edition ('…, 6e'); the approved Spanish samples do not
        if bi.edition and not re.search(r",\s*\d+e$", title) and getattr(bi, "content_lang", "en") in ("en", None, ""):
            title = f"{title}, {bi.edition}e"
        self.E(tg, "book-title", text=title)
        self.E(tg, "alt-title", {"alt-title-type": "short-name"}, bi.shortcode or bi.short_name)
        if bi.contributors:
            cg = self.E(bm, "contrib-group")
            for c in bi.contributors:
                ct = self.E(cg, "contrib", {"contrib-type": c.get("type", "author")})
                nm = self.E(ct, "name")
                self.E(nm, "surname", text=c.get("surname", ""))
                if c.get("given"):
                    self.E(nm, "given-names", text=c["given"])
                if c.get("degrees"):
                    self.E(ct, "degrees", text=c["degrees"])
        if bi.pub_year:
            pd = self.E(bm, "pub-date")
            self.E(pd, "year", text=bi.pub_year)
        if bi.isbn_display:
            self.E(bm, "isbn", {"publication-format": "print", "content-type": "ISBN13"}, bi.isbn_display)
        if bi.publisher_name:
            pub = self.E(bm, "publisher")
            self.E(pub, "publisher-name", text=bi.publisher_name)
            if bi.publisher_loc:
                self.E(pub, "publisher-loc", text=bi.publisher_loc)
        if bi.edition:
            self.E(bm, "edition", text=str(bi.edition))
        P = self.ids.prefix
        sm = self.E(bm, "supplementary-material", {"id": f"{P}-cover", "content-type": "book-cover", "xlink:href": "cover.jpg"})
        self.E(sm, "label", text="Cover Title")
        cap = self.E(sm, "caption", {"id": f"{P}-fm001-cap000"})
        self.E(cap, "title", text=bi.title or "Cover")
        self.E(bm, "supplementary-material", {"id": f"{P}-thumbnail", "content-type": "book-thumbnail",
                                              "xlink:href": f"{(bi.shortcode or bi.prefix).lower()}.gif"})
        if bi.copyright_statement or license_paras:
            perm = self.E(bm, "permissions", {"id": f"{P}-per"})
            if bi.copyright_statement:
                self.E(perm, "copyright-statement", text=bi.copyright_statement)
            if bi.copyright_year:
                self.E(perm, "copyright-year", {"content-type": "domestic"}, bi.copyright_year)
            if bi.copyright_holder:
                self.E(perm, "copyright-holder", text=bi.copyright_holder)
            if license_paras:
                lic = self.E(perm, "license", {"license-type": "ccc", "id": f"{P}-license"})
                for p in license_paras:
                    lp = self.E(lic, "license-p", {"id": p.id})
                    self.add_inlines(lp, p.inlines)

    # ------------------------------------------------------- front matter
    def _part_meta(self, parent, n: Node, title_items=None, label=None):
        meta = self.E(parent, "book-part-meta")
        tg = self.E(meta, "title-group")
        if label:
            lab = self.E(tg, "label")
            self.add_inlines(lab, n.meta.get("title_marks") or [])
            self._append_text(lab, label)
            t = self.E(tg, "title")
            self.add_inlines(t, title_items or [])
        else:
            t = self.E(tg, "title")
            self.add_inlines(t, (n.meta.get("title_marks") or []) + (title_items or []))
        if n.meta.get("subtitle"):
            st = self.E(tg, "subtitle")
            self.add_inlines(st, n.meta["subtitle"])
        return meta

    def _pages(self, meta, n: Node):
        if n.meta.get("fpage"):
            self.E(meta, "fpage", text=str(n.meta["fpage"]))
            self.E(meta, "lpage", text=str(n.meta.get("lpage") or n.meta["fpage"]))

    def front_part(self, fm, n: Node):
        self.cur_part = "fm001"
        if n.kind == "toc":
            return self.toc(fm, n)
        if n.kind == "ack":
            el = self.E(fm, "ack", {"id": n.id})
            t = self.E(el, "title")
            self.add_inlines(t, (n.meta.get("title_marks") or []) + (n.meta.get("title") or []))
            for c in n.children:
                self.block(el, c)
            return el
        attrs = {"id": n.id}
        if n.kind == "front-matter-part":
            attrs["book-part-type"] = n.attrs.get("book-part-type", "front matter page")
        el = self.E(fm, n.kind, attrs)
        meta = self._part_meta(el, n, n.meta.get("title"))
        self._pages(meta, n)
        body_nodes = [c for c in n.children if c.kind == "named-book-part-body"]
        if body_nodes and body_nodes[0].children:
            nb = self.E(el, "named-book-part-body")
            for c in body_nodes[0].children:
                self.block(nb, c)
        return el

    def toc(self, parent, n: Node):
        el = self.E(parent, "toc", {"id": n.id})
        tg = self.E(el, "title-group")
        t = self.E(tg, "title")
        self.add_inlines(t, (n.meta.get("marks") or [])[:1] + (n.meta.get("title") or []))
        entries = n.meta.get("entries") or []
        stack = [(-1, el)]
        extra_marks = (n.meta.get("marks") or [])[1:]
        for e in entries:
            lvl = e.get("level", 0)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            par = stack[-1][1] if stack else el
            te = self.E(par, "toc-entry")
            if e.get("label"):
                self.E(te, "label", text=e["label"])
            tt = self.E(te, "title")
            if extra_marks and e.get("lines") and e["lines"][0].page == extra_marks[0].get("page"):
                self.add_inlines(tt, [extra_marks.pop(0)])
            self._append_text(tt, e.get("title") or "")
            rid = e.get("rid")
            if rid and e.get("page"):
                self.E(te, "nav-pointer", {"rid": rid, "specific-use": "pagenum"}, str(e["page"]))
            elif e.get("page"):
                self.issues.add("UNRESOLVED_TOC_ENTRY", "warning", f"TOC entry '{e.get('title', '')[:50]}' p.{e['page']} has no target")
                self._append_text(tt, "")
            stack.append((lvl, te))
        for m in extra_marks:    # pages of the TOC without an entry start: keep their targets
            last = el.findall(".//toc-entry/title")
            if last:
                self.add_inlines(last[-1], [m])
        return el

    # ------------------------------------------------------------ parts
    def book_part(self, parent, n: Node, seq):
        if n.kind != "book-part":
            return self.block(parent, n)
        seq[0] += 1
        self.cur_part = self.part_ids.get(id(n), self.cur_part)
        typ = n.attrs.get("book-part-type", "chapter")
        el = self.E(parent, "book-part", {"id": n.id, "book-part-type": typ, "seq": str(seq[0] if seq[0] < 1000 else seq[0] - 1000)})
        meta = self.E(el, "book-part-meta")
        self.E(meta, "book-part-id", {"book-part-id-type": "publisher-id"}, n.id)
        tg = self.E(meta, "title-group")
        marks = n.meta.get("title_marks") or []
        if n.meta.get("label"):
            lab = self.E(tg, "label")
            self.add_inlines(lab, marks)
            self._append_text(lab, n.meta["label"])
            marks = []
        t = self.E(tg, "title")
        self.add_inlines(t, marks + (n.meta.get("title") or []))
        if n.meta.get("subtitle"):
            st = self.E(tg, "subtitle")
            self.add_inlines(st, n.meta["subtitle"])
        if n.meta.get("authors"):
            cg = self.E(meta, "contrib-group")
            for a in n.meta["authors"]:
                for name in [x for x in re.split(r"\s*(?:,|;|\sy\s|\sand\s|&)\s*", a) if x.strip()]:
                    parts = name.strip().split()
                    ct = self.E(cg, "contrib", {"contrib-type": "author"})
                    nm = self.E(ct, "name")
                    self.E(nm, "surname", text=parts[-1])
                    if len(parts) > 1:
                        self.E(nm, "given-names", text=" ".join(parts[:-1]))
        self._pages(meta, n)
        if n.meta.get("outline_toc"):
            fmx = self.E(el, "front-matter")
            tc = self.E(fmx, "toc")
            for e in n.meta["outline_toc"]:
                te = self.E(tc, "toc-entry")
                if e.get("label"):
                    lab = self.E(te, "label")
                    self._styled(lab, e["label"], [], "xref", {"ref-type": "section", "rid": e["rid"]})
                tt = self.E(te, "title")
                self._styled(tt, e["title"], [], "xref", {"ref-type": "section", "rid": e["rid"]})
        for c in n.children:
            if c.kind == "body":
                b = self.E(el, "body")
                for x in c.children:
                    if x.kind == "book-part":
                        self.book_part(b, x, seq)
                    else:
                        self.block(b, x)
                if not len(b):
                    self.E(b, "p", {"id": self.ids.next(self.cur_part, "p")})
            elif c.kind == "back":
                bk = self.E(el, "back")
                for x in c.children:
                    self.back_item(bk, x)
        return el

    # ----------------------------------------------------------- blocks
    def block(self, parent, n: Node):
        k = n.kind
        if k == "sec":
            el = self.E(parent, "sec", {"id": n.id, "disp-level": n.attrs.get("disp-level")})
            t = self.E(el, "title")
            self.add_inlines(t, n.meta.get("title") or [])
            for c in n.children:
                self.block(el, c)
            if len(el) == 1:
                pass
            return el
        if k == "p":
            el = self.E(parent, "p", {"id": n.id})
            self.add_inlines(el, n.inlines)
            for c in n.children:
                self.block(el, c)
            return el
        if k == "list":
            el = self.E(parent, "list", {"list-type": n.attrs.get("list-type", "bullet"), "id": n.id})
            for it in n.children:
                li = self.E(el, "list-item")
                if it.meta.get("label") and n.attrs.get("list-type") == "simple":
                    self.E(li, "label", text=it.meta["label"])
                for c in it.children:
                    self.block(li, c)
                if not len([x for x in li if self._tagname(x) != "label"]):
                    self.E(li, "p", {"id": self.ids.next(self.cur_part, "p")})
            return el
        if k == "fig":
            return self.fig(parent, n)
        if k == "table-wrap":
            return self.table(parent, n)
        if k == "boxed-text":
            attrs = {"id": n.id, "content-type": n.attrs.get("content-type", "sidebar")}
            el = self.E(parent, "boxed-text", attrs)
            if n.meta.get("label"):
                self.E(el, "label", text=n.meta["label"])
            if n.meta.get("title"):
                cap = self.E(el, "caption", {"id": n.meta.get("caption_id")})
                t = self.E(cap, "title")
                self.add_inlines(t, n.meta["title"])
            for c in n.children:
                self.block(el, c)
            return el
        if k == "disp-formula":
            el = self.E(parent, "disp-formula", {"id": n.id})
            txt = n.meta.get("text") or ""
            if n.meta.get("href") and n.meta.get("fallback_image"):
                graphic = self.E(el, "graphic", {"xlink:href": n.meta["href"]})
                self.E(graphic, "alt-text", text=txt)
                return el
            math = etree.SubElement(el, f"{{{MML}}}math")
            math.set("display", "block")
            math.set("alttext", clean(txt) or "This is an equation")

            ast = n.meta.get("mathml")
            if ast:
                self.append_mathml(math, ast)
            else:
                # Backward compatibility for saved zoning projects created before
                # structured equation ASTs were added.
                mt = etree.SubElement(math, f"{{{MML}}}mtext")
                mt.text = clean(txt)
            return el
        if k == "inline-graphic":
            href = n.meta.get("href")
            if href:
                return self.E(parent, "inline-graphic", {"id": n.id, "xlink:href": href})
            return None
        if k in ("named-book-part-body",):
            for c in n.children:
                self.block(parent, c)
            return None
        if k == "ref-list":
            return self.back_item(parent, n)
        # unknown kinds: keep text as a paragraph (never lose content)
        txt = n.text()
        if txt:
            el = self.E(parent, "p", {"id": n.id or self.ids.next(self.cur_part, "p")})
            self._append_text(el, txt)
            self.issues.add("UNMAPPED_NODE", "warning", f"node kind {k} emitted as p", page=n.page)
            return el
        return None

    def fig(self, parent, n: Node):
        el = self.E(parent, "fig", {"id": n.id})
        if n.meta.get("label"):
            self.E(el, "label", text=n.meta["label"])
        if n.meta.get("caption_id") and (n.meta.get("caption") or n.meta.get("label")):
            cap = self.E(el, "caption", {"id": n.meta["caption_id"]})
            t = self.E(cap, "title")
            self.add_inlines(t, n.meta.get("caption") or [])
        href = n.meta.get("href")
        if href:
            self.E(el, "graphic", {"id": n.meta.get("graphic_id"), "xlink:href": href})
        else:
            self.issues.add("FIGURE_WITHOUT_GRAPHIC", "error", f"{n.meta.get('label')} has no exported graphic", page=n.page)
        return el

    def table(self, parent, n: Node):
        el = self.E(parent, "table-wrap", {"id": n.id, "specific-use": "inline"})
        if n.meta.get("label"):
            self.E(el, "label", text=n.meta["label"])
        if n.meta.get("caption_id") and n.meta.get("caption"):
            cap = self.E(el, "caption", {"id": n.meta["caption_id"]})
            t = self.E(cap, "title")
            self.add_inlines(t, n.meta["caption"])
        rows = n.meta.get("rows")
        if rows:
            tb = self.E(el, "table", {"frame": "void"})
            hr = n.meta.get("header_rows", 0)
            if hr:
                th = self.E(tb, "thead")
                for row in rows[:hr]:
                    tr = self.E(th, "tr")
                    for c in row:
                        cell = self.E(tr, "th", {"align": "left", "valign": "top", "colspan": str(c["colspan"]) if c["colspan"] > 1 else None,
                                                       "rowspan": str(c["rowspan"]) if c.get("rowspan", 1) > 1 else None})
                        self._cell(cell, c)
            body = self.E(tb, "tbody")
            for row in rows[hr:] or rows[-1:]:
                tr = self.E(body, "tr")
                for c in row:
                    cell = self.E(tr, "td", {"align": "left", "valign": "top", "colspan": str(c["colspan"]) if c["colspan"] > 1 else None,
                                                       "rowspan": str(c["rowspan"]) if c.get("rowspan", 1) > 1 else None})
                    self._cell(cell, c)
        else:
            href = n.meta.get("href")
            if href:
                self.E(el, "graphic", {"id": n.meta.get("graphic_id"), "xlink:href": href})
            text_rows = [t for t in (n.meta.get("fallback_text") or []) if t.strip()]
            if text_rows:
                # keep the table's text as well (one row per printed line) so no content is lost
                tb = self.E(el, "table", {"frame": "void", "content-type": "fallback-text"})
                body = self.E(tb, "tbody")
                for tline in text_rows:
                    tr = self.E(body, "tr")
                    self.E(tr, "td", {"align": "left", "valign": "top"}, tline.strip())
            if not href and not text_rows:
                self.issues.add("TABLE_WITHOUT_CONTENT", "error", f"{n.meta.get('label')} has no rows and no image", page=n.page)
        foot = n.meta.get("foot") or []
        if foot:
            tf = self.E(el, "table-wrap-foot")
            fg = None
            for f in foot:
                if f.get("label"):
                    if fg is None:
                        fg = self.E(tf, "fn-group")
                    fn = self.E(fg, "fn", {"id": f["id"]})
                    self.E(fn, "label", text=f["label"])
                    p = self.E(fn, "p", {"id": f.get("p_id")})
                    self.add_inlines(p, f["runs"])
                else:
                    p = self.E(tf, "p", {"id": f["id"]})
                    self.add_inlines(p, f["runs"])
        return el

    def _cell(self, cell_el, c: dict):
        if c.get("blocks"):
            for b in c["blocks"]:
                self.block(cell_el, b)
        else:
            self.add_inlines(cell_el, c["runs"])

    # ------------------------------------------------------------- back
    def back_item(self, bk, n: Node):
        if n.kind == "ref-list":
            rl = self.E(bk, "ref-list", {"id": n.id})
            if n.meta.get("title"):
                t = self.E(rl, "title")
                self.add_inlines(t, n.meta["title"])
            for r in n.children:
                if r.kind == "ref-list":
                    self.back_item(rl, r)
                else:
                    self.ref(rl, r)
            return rl
        if n.kind == "fn-group":
            fg = self.E(bk, "fn-group")
            for fn in n.children:
                f = self.E(fg, "fn", {"id": fn.id})
                if fn.meta.get("label"):
                    self.E(f, "label", text=fn.meta["label"])
                for c in fn.children:
                    self.block(f, c)
            return fg
        if n.kind in ("fig", "table-wrap"):
            sec = self.E(bk, "sec", {"id": self.ids.next(self.cur_part, "sec")})
            self.E(sec, "title")
            return self.block(sec, n)
        return self.block(bk, n)

    def ref(self, rl, r: Node):
        el = self.E(rl, "ref", {"id": r.id})
        if r.meta.get("label"):
            self.E(el, "label", text=r.meta["label"])
        parsed = r.meta.get("parsed") or {"type": "other", "segments": [("text", r.meta.get("text", ""))]}
        mc = self.E(el, "mixed-citation", {"publication-type": parsed["type"]})
        runs = [x for x in r.meta.get("runs") or [] if x.get("k") in ("t", "xref", "cite")]
        marks = [x for x in r.meta.get("runs") or [] if x.get("k") == "pg"] + (r.meta.get("marks") or [])
        self.add_inlines(mc, marks)
        segs = parsed["segments"]
        text = "".join(x["text"] for x in runs)
        if flatten(segs) != text:
            self.add_inlines(mc, runs)
            return el
        pos = 0
        for s in segs:
            if s[0] == "person-group":
                pg = self.E(mc, "person-group", {"person-group-type": "author"})
                for nm in s[1]:
                    if nm[0] == "text":
                        self.add_inlines(pg, clip_runs(runs, pos, pos + len(nm[1])))
                        pos += len(nm[1])
                    elif nm[0] == "collab":
                        c = self.E(pg, "collab")
                        self.add_inlines(c, clip_runs(runs, pos, pos + len(nm[1])))
                        pos += len(nm[1])
                    else:
                        sn = self.E(pg, "string-name")
                        su = self.E(sn, "surname")
                        self.add_inlines(su, clip_runs(runs, pos, pos + len(nm[1])))
                        pos += len(nm[1])
                        self._append_text(sn, nm[2])
                        pos += len(nm[2])
                        gv = self.E(sn, "given-names")
                        self.add_inlines(gv, clip_runs(runs, pos, pos + len(nm[3])))
                        pos += len(nm[3])
                continue
            val = s[1]
            seg_runs = clip_runs(runs, pos, pos + len(val))
            pos += len(val)
            if s[0] == "text":
                self.add_inlines(mc, seg_runs)
            elif s[0] == "uri":
                href = val if val.startswith("http") else "http://" + val
                x = self.E(mc, "ext-link", {"ext-link-type": "uri", "xlink:href": href})
                self.add_inlines(x, seg_runs)
            elif s[0] == "doi":
                x = self.E(mc, "pub-id", {"pub-id-type": "doi"})
                self.add_inlines(x, seg_runs)
            else:
                x = self.E(mc, s[0])
                self.add_inlines(x, seg_runs)
        return el

    # ------------------------------------------------------------ index
    def index(self, bk, n: Node):
        el = self.E(bk, "index", {"id": n.id})
        tg = self.E(el, "title-group")
        t = self.E(tg, "title")
        marks = dict(n.meta.get("marks") or [])
        first_page = min(marks) if marks else None
        if first_page is not None:
            self.add_inlines(t, [marks.pop(first_page)])
        self._append_text(t, n.meta.get("title") or "Index")
        for ids, runs in zip(n.meta.get("intro_ids", []), n.meta.get("intro", [])):
            p = self.E(el, "p", {"id": ids})
            self.add_inlines(p, runs)
        for d in n.meta.get("divs", []):
            div = self.E(el, "index-div")
            if d.get("title"):
                dtg = self.E(div, "title-group")
                self.E(dtg, "title", text=d["title"])
            stack = [(-1, div)]
            for e in d["entries"]:
                while stack and stack[-1][0] >= e["level"]:
                    stack.pop()
                par = stack[-1][1] if stack else div
                ie = self.E(par, "index-entry", {"id": e["id"]})
                term = self.E(ie, "term")
                if e.get("page") in marks:
                    self.add_inlines(term, [marks.pop(e["page"])])
                term_runs = clip_runs(e["runs"], 0, len(e["term"])) if e["term"] else []
                self.add_inlines(term, term_runs or [{"k": "t", "text": e["term"], "st": []}])
                if e["refs"]:
                    npg = self.E(ie, "nav-pointer-group")
                    first = True
                    for ref in e["refs"]:
                        toks = page_tokens(ref)
                        if not first:
                            self._append_text(npg, ", ")
                        first = False
                        if not toks:
                            self._append_text(npg, ref)
                            continue
                        for ti, (num, suf, rtype) in enumerate(toks):
                            if ti == 1:
                                self._append_text(npg, "–")
                            rec = self.reg.lookup("page", None, num.lower())
                            if rec is not None and rec["id"] not in self.emitted_targets:
                                rec = None
                            if rec is None:
                                self._append_text(npg, num + suf)
                                self.issues.add("UNRESOLVED_INDEX_PAGE", "warning", f"index '{e['term'][:40]}' -> page {num} not in book")
                                continue
                            attrs = {"rid": rec["id"]}
                            if rtype:
                                attrs["nav-pointer-type"] = rtype
                            self.E(npg, "nav-pointer", attrs, num + suf)
                            self.reg.add_incoming(rec["id"], {"text": num, "ctx": "index", "source": e["id"]})
                    if not len(npg):
                        # no resolvable page: keep the text, drop the empty group element
                        txt = npg.text or ""
                        ie.remove(npg)
                        self._append_text(term, " " + txt.strip()) if txt.strip() else None
                for s in e.get("see") or []:
                    self.E(ie, "see-entry", text=s)
                for s in e.get("see_also") or []:
                    self.E(ie, "see-also-entry", text=s)
                stack.append((e["level"], ie))
        return el
