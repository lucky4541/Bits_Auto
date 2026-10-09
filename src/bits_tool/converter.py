"""Conversion pipeline for one book (PDF or folder of split PDFs).

PDF -> extraction (cached per page) -> OCR when needed (cached) -> running heads
/ folios -> regions (figures, tables, boxes) -> reading order -> semantic tree
-> citations (pass 1) -> placement -> IDs + target registry -> link
resolution (pass 2) -> images -> BITS XML -> validation -> QA reports.

The UI talks to this module only through `convert_book()` / services.
"""
from __future__ import annotations

import json
import logging
import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .bits_generator import BitsGenerator
from .book_meta import derive_book_info
from .checkpoint_manager import Checkpoint
from .config import TOOL_VERSION, Config, load_book_overrides
from .document_tree import IssueLog, Node
from .dtd_analyzer import find_dtd, load_rules
from .figure_detector import export_cover, export_region
from .id_generator import IdGenerator
from .language_detector import detect as detect_lang
from .layout_analyzer import assign_folios, body_style, mark_headers_footers
from .ocr_engine import OcrCache, make_engine, needs_ocr, ocr_page
from .pdf_loader import load_book
from .placement_engine import PlacementEngine
from .reading_order import analyze_regions
from .report_generator import write_reports
from .structure_builder import BookBuilder
from .target_registry import TargetRegistry
from .text_extractor import extract_page
from .validators import (content_coverage, parse, validate_dtd, validate_ids, validate_images, validate_links,
                         validate_structure)
from .xml_generator import serialize
from .xref_resolver import XrefResolver, part_of

STAGES = ["PDF Loaded", "Text Extraction", "OCR", "Layout Analysis", "Reading Order", "Structure Detection",
          "Figure/Table Detection", "Citation Detection", "Placement", "ID Generation", "Link Resolution",
          "Images", "XML Generation", "DTD Validation", "QA"]


class Cancelled(Exception):
    pass


@dataclass
class AnalysisState:
    """Everything the second half of the pipeline needs (also pickled into the zoning project)."""
    t0: float
    book_path: Path
    src: object
    name: str
    out_dir: Path
    logs: dict
    issues: IssueLog
    pages: list
    failed_pages: list
    ocr_pages: int
    style: object
    lang: str | None
    lang_conf: float
    builder: object
    root: Node


@dataclass
class ConversionResult:
    book: str
    status: str
    xml_path: str | None = None
    out_dir: str | None = None
    stats: dict = field(default_factory=dict)
    validation: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    report_path: str | None = None
    seconds: float = 0.0
    failed_pages: list = field(default_factory=list)


def _logger(out_dir: Path, name: str) -> logging.Logger:
    lg = logging.getLogger(f"bits.{name}.{out_dir.name}")
    lg.setLevel(logging.DEBUG)
    lg.handlers.clear()
    lg.propagate = False
    out_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(out_dir / f"{name}.log", mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    lg.addHandler(fh)
    return lg


class BookConverter:
    def __init__(self, cfg: Config, progress=None, cancel_flag=None):
        self.cfg = cfg
        self.progress_cb = progress or (lambda **kw: None)
        self.cancel_flag = cancel_flag or (lambda: False)

    def progress(self, stage: str, frac: float = 0.0, detail: str = "", **kw):
        self.progress_cb(stage=stage, frac=frac, detail=detail, **kw)
        if self.cancel_flag():
            raise Cancelled()

    # ------------------------------------------------------------------ run
    def convert(self, book_path: Path, out_root: Path) -> ConversionResult:
        st = self.analyze(book_path, out_root)
        root = st.root
        try:
            from .zoning import ZoneEditor, ZoneProject
            zp = ZoneProject.create(st, mode="auto")
            if self.cfg.get("zoning.save_project", True):
                bk = zp.backup_existing()
                if bk is not None:
                    st.issues.add("ZONING_EDITS_BACKED_UP", "info", f"previous hand-edited zones kept in {bk}")
                zp.save()
            # the same zone -> XML path as the Zoning screen, so auto and reviewed output never differ
            root, _ = ZoneEditor(zp, self.cfg).prepare_for_xml()
        except Exception as e:   # zoning is a convenience; never fail the conversion over it
            st.logs["errors"].error("zoning project not saved: %r", e)
            root = st.root
            from .zoning import _strip_zone_meta
            _strip_zone_meta(root)
        return self.finish(st, root)

    def analyze(self, book_path: Path, out_root: Path, manual: bool = False) -> "AnalysisState":
        """PDF -> pages (extraction, OCR, layout) -> semantic tree (auto) or an empty skeleton (manual)."""
        t0 = time.time()
        src = load_book(Path(book_path))
        name = src.isbn or Path(book_path).stem
        out_dir = Path(out_root) / name
        for sub in ("images", "intermediate", "validation", "qa", "logs"):
            (out_dir / sub).mkdir(parents=True, exist_ok=True)
        logs = {k: _logger(out_dir / "logs", k) for k in ("conversion", "errors", "validation", "placement", "link_validation")}
        clog = logs["conversion"]
        issues = IssueLog()
        ckpt = Checkpoint(out_dir / "intermediate" / "checkpoint.json", src.hash(), self.cfg.fingerprint(), TOOL_VERSION)
        self.progress("PDF Loaded", 1.0, f"{len(src.files)} file(s), {src.total_pages} pages", total_pages=src.total_pages, book=name)
        clog.info("book=%s files=%d pages=%d isbn=%s author=%s", name, len(src.files), src.total_pages, src.isbn, src.author)

        # ---- extraction (resumable per page)
        cache_root = self.cfg.path("cache_dir")
        pages = []
        failed_pages = []
        ocr_engine = make_engine(self.cfg.get("ocr.engine", "auto"), self.cfg.get("ocr.tesseract_cmd", ""))
        ocr_pages = 0
        doc = None
        cur_fi = None
        for gi, (fi, pno) in enumerate(src.page_map):
            if fi != cur_fi:
                if doc is not None:
                    doc.close()
                doc = src.open(fi)
                cur_fi = fi
            f = src.files[fi]
            cdir = cache_root / f.sha[:16]
            try:
                info = extract_page(doc, pno, gi, f.path.name, cdir)
                if self.cfg.get("ocr.engine", "auto") != "none" and needs_ocr(info, self.cfg.get("ocr.min_text_chars", 40)):
                    conf = ocr_page(doc, pno, info, ocr_engine, OcrCache(cdir), self.cfg.get("ocr.languages", ["eng"]),
                                    self.cfg.get("ocr.dpi", 300))
                    ocr_pages += 1
                    if conf is not None and conf < self.cfg.get("ocr.confidence_threshold", 60) / 100:
                        issues.add("LOW_OCR_CONFIDENCE", "warning", f"OCR confidence {conf:.2f}", page=gi, conf=conf)
            except Exception as e:
                failed_pages.append({"page": gi, "pdf": f.path.name, "pdf_page": pno, "error": repr(e), "trace": traceback.format_exc()})
                logs["errors"].error("[PAGE %d] extraction failed: %s", gi, e)
                from .document_tree import PageInfo
                info = PageInfo(index=gi, pdf=f.path.name, pdf_page=pno, width=0, height=0, trim=(0, 0, 1, 1), errors=[repr(e)])
            for err in info.errors:
                if err.startswith("EXTRACTION_ERROR"):
                    failed_pages.append({"page": gi, "pdf": f.path.name, "pdf_page": pno, "error": err})
            pages.append(info)
            if gi % 5 == 0 or gi == src.total_pages - 1:
                self.progress("Text Extraction", (gi + 1) / src.total_pages, f"page {gi + 1}/{src.total_pages}",
                              page=gi + 1, total_pages=src.total_pages)
            ckpt.update(stage="extraction", page=gi)
        if doc is not None:
            doc.close()
        self.progress("OCR", 1.0, f"{ocr_pages} OCR page(s)", ocr_pages=ocr_pages)

        # ---- layout
        mark_headers_footers(pages, self.cfg.get("layout.header_footer_zone", 0.085))
        assign_folios(pages)
        style = body_style(pages)
        clog.info("body style: %s", style)
        # sample the body of the book, spread over all pages (front matter is often English credits / addresses)
        from .pdf_loader import classify_file as _cf
        body_pages = [p for p in pages if _cf(Path(p.pdf or "x.pdf"))[0] not in ("fm", "index")] or pages
        step = max(1, len(body_pages) // 60)
        lang_sample = " ".join(l.text for p in body_pages[::step] for l in p.lines if l.role == "body")[:40000]
        lang, lang_conf = detect_lang(lang_sample)
        for i, p in enumerate(pages):
            try:
                analyze_regions(p, style, self.cfg)
            except Exception as e:
                failed_pages.append({"page": p.index, "error": f"layout: {e!r}", "trace": traceback.format_exc()})
                logs["errors"].error("[PAGE %d] layout failed: %s", p.index, e)
            if i % 10 == 0:
                self.progress("Layout Analysis", (i + 1) / len(pages), f"page {i + 1}/{len(pages)}", page=i + 1, total_pages=len(pages))
        n_fig = sum(1 for p in pages for r in p.regions if r.kind == "figure")
        n_tab = sum(1 for p in pages for r in p.regions if r.kind == "table")
        self.progress("Reading Order", 1.0, "", figures=n_fig, tables=n_tab)
        for p in pages:
            for r in p.regions:
                clog.info("[PAGE %s] Detected %s %s conf=%.2f", p.folio or p.index, r.kind, r.meta.get("label") or "", r.conf)

        from .zoning import assign_line_uids
        assign_line_uids(pages)

        # ---- semantic tree
        builder = BookBuilder(src, pages, style, self.cfg, issues, log=lambda ev, **kw: clog.info("%s %s", ev, kw),
                              progress=lambda st, fr, d="": self.progress("Structure Detection", fr, d))
        if manual:
            root = builder.skeleton()
        else:
            root = builder.build()
        self.progress("Figure/Table Detection", 1.0, "", figures=len(root.find_all("fig")), tables=len(root.find_all("table-wrap")))
        merge_table_continuations(root, issues)
        from .zoning import attach_ownership
        attach_ownership(root, pages)
        return AnalysisState(t0=t0, book_path=Path(book_path), src=src, name=name, out_dir=out_dir, logs=logs, issues=issues,
                             pages=pages, failed_pages=failed_pages, ocr_pages=ocr_pages, style=style, lang=lang,
                             lang_conf=lang_conf, builder=builder, root=root)

    def finish(self, st: "AnalysisState", root: Node, structural_issues: list | None = None) -> ConversionResult:
        """Semantic tree (auto or edited zones) -> placement, IDs, links, images, XML, validation, reports."""
        t0, src, name, out_dir, pages, failed_pages = st.t0, st.src, st.name, st.out_dir, st.pages, st.failed_pages
        ocr_pages, style, lang, lang_conf, builder, book_path = st.ocr_pages, st.style, st.lang, st.lang_conf, st.builder, st.book_path
        if not st.logs:
            st.logs = {k: _logger(out_dir / "logs", k) for k in ("conversion", "errors", "validation", "placement", "link_validation")}
        logs = st.logs
        clog = logs["conversion"]
        issues = st.issues
        if structural_issues is not None:
            issues = IssueLog()
            issues.items = list(structural_issues)
        for sub in ("images", "intermediate", "validation", "qa", "logs"):
            (out_dir / sub).mkdir(parents=True, exist_ok=True)
        ckpt = Checkpoint(out_dir / "intermediate" / "checkpoint.json", src.hash(), self.cfg.fingerprint(), TOOL_VERSION)
        # ---- book metadata
        overrides = load_book_overrides(Path(book_path))
        cr_lines = [o for k, o in builder.meta.get("copyright_lines", []) if k == "line"]
        cr_text = "\n".join(l.text for l in cr_lines)
        overrides = {**overrides, **(self.cfg.get("book_overrides") or {})}
        fm_pages = [[l for l in p.lines if l.role == "body"] for p in pages[: min(8, len(pages))]]
        bi = derive_book_info(src, overrides, builder.meta.get("title_lines", []), cr_text, builder.bookmarks,
                              src.files[0].metadata if src.files else {}, self.cfg, fm_pages)
        if bi.prefix == "book":
            issues.add("ID_PREFIX_NOT_FOUND", "warning", "author surname for IDs not found; set it in the Convert screen or book.yaml")
        bi.content_lang = lang
        if lang and not overrides.get("xml_lang") and self.cfg.get("bits.xml_lang_from_content", False):
            bi.lang = lang

        # ---- citations pass 1 + placement
        resolver = XrefResolver(issues, self.cfg)
        cites = resolver.collect(root, author_year=True)
        placement = PlacementEngine(issues, self.cfg.get("placement.policy", "learned"))

        def key_of(obj: Node):
            part = part_of(obj)
            return ("fig" if obj.kind == "fig" else "table", id(part) if part is not None else None, obj.meta.get("key"))
        prep = placement.run(root, cites, key_of)
        self.progress("Placement", 1.0, f"{len(prep)} objects")
        for r in prep:
            logs["placement"].info("[PAGE %s] %s %s -> %s (%s)", r.get("physical_page"), r["type"], r.get("label"), r.get("status"), r.get("rule"))

        # ---- IDs + registry
        rules = load_rules(str(find_dtd(self.cfg.path("dtd_dir"))))
        idgen = IdGenerator(bi.prefix, self.cfg.path("mapping_dir") / "id_mapping.json")
        registry = TargetRegistry()
        gen = BitsGenerator(rules, idgen, registry, issues, self.cfg, bi)
        root.fix_parents()
        gen.assign_ids(root)
        # book-part scopes for citation resolution
        part_ids = {}
        for n in root.walk():
            if id(n) in gen.part_ids:
                part_ids[id(n)] = gen.part_ids[id(n)]
        license_nodes = self._license_paragraphs(builder, idgen)
        gen.register_pages(root)
        for n in license_nodes:
            for it in n.inlines:
                if it.get("k") == "pg":
                    tid = idgen.page_target(it["folio"])
                    if tid not in registry.by_id:
                        registry.register("page", None, str(it["folio"]).lower(), tid, "target", it.get("page"))
        resolve_toc(root, registry, gen, issues)
        resolve_outline(root, registry)
        self.progress("ID Generation", 1.0, f"{len(idgen.used)} ids")

        # ---- links pass 2
        resolver.resolve(root, registry, part_ids)
        n_links = sum(1 for l in resolver.links if l["status"] == "resolved")
        self.progress("Link Resolution", 1.0, f"{n_links} links", citations=len(resolver.links))
        for l in resolver.links:
            logs["link_validation"].info("%s %s -> %s", l["status"], l["text"], l.get("rid"))

        # ---- images
        self.export_images(src, root, pages, out_dir, bi, gen, issues)
        self.progress("Images", 1.0, "")

        # ---- XML
        xml_root = gen.build(root, license_nodes)
        xml_path = out_dir / f"{name}.xml"
        serialize(xml_root, xml_path, self.cfg.get("bits.public_id"), self.cfg.get("bits.system_id"), rules,
                  pretty=True)
        self.progress("XML Generation", 1.0, str(xml_path))

        # ---- validation (independent re-parse)
        tree = parse(xml_path)
        v = {"well_formed": True}
        v["dtd"] = validate_dtd(tree, rules)
        self.progress("DTD Validation", 1.0, "valid" if v["dtd"]["valid"] else f"{v['dtd']['error_count']} errors")
        v["ids"] = validate_ids(tree, idgen.prefix)
        v["links"] = validate_links(tree)
        v["images"] = validate_images(tree, xml_path.parent)
        v["structure"] = validate_structure(tree)
        v["content"] = content_coverage(pages, tree)
        v["dtd_model_warnings"] = sorted(set(gen.dtd_skips))[:100]
        v["id_collisions"] = idgen.collisions
        for e in v["dtd"]["errors"][:200]:
            logs["validation"].error("line %s: %s", e["line"], e["message"])
        status = quality_gate(v, failed_pages, issues, self.cfg)
        # ---- intermediate + reports
        if self.cfg.get("save_intermediate_json", True):
            with open(out_dir / "intermediate" / "document_structure.json", "w", encoding="utf-8") as fh:
                json.dump({"book": name, "style": style.__dict__, "pages": [p.to_json(with_lines=self.cfg.get("mode") == "development") for p in pages],
                           "tree": root.to_json()}, fh, ensure_ascii=False, default=str)
        registry.save(out_dir / "intermediate" / "target_registry.json")
        with open(out_dir / "intermediate" / "source.json", "w", encoding="utf-8") as fh:
            json.dump({"book": name, "input": str(book_path), "files": [str(f.path) for f in src.files],
                       "pages": [{"page": p.index, "pdf": str(src.files[src.page_map[p.index][0]].path),
                                  "pdf_page": p.pdf_page, "folio": p.folio, "rot": p.rot} for p in pages],
                       "tool_version": TOOL_VERSION, "config": self.cfg.fingerprint()}, fh, ensure_ascii=False)
        stats = {
            "pages": src.total_pages, "ocr_pages": ocr_pages, "language": lang, "language_conf": lang_conf,
            "chapters": sum(1 for n in root.walk() if n.kind == "book-part" and n.attrs.get("book-part-type") == "chapter"),
            "parts": sum(1 for n in root.walk() if n.kind == "book-part" and n.attrs.get("book-part-type") == "part"),
            **v["structure"]["counts"], "ids": v["ids"]["total"], "links": v["links"]["total"],
            "citations": len(resolver.links), "unresolved_citations": sum(1 for l in resolver.links if l["status"] != "resolved"),
            "placements": len(prep), "seconds": round(time.time() - t0, 1),
        }
        res = ConversionResult(name, status, str(xml_path), str(out_dir), stats, v, issues.to_json(), None,
                               round(time.time() - t0, 1), failed_pages)
        res.report_path = str(write_reports(out_dir, res, prep, resolver.links, registry, pages, bi, self.cfg))
        self.progress("QA", 1.0, status, status=status)
        ckpt.update(stage="done", status=status)
        clog.info("done status=%s seconds=%.1f", status, res.seconds)
        for h in [h for lg in logs.values() for h in lg.handlers]:
            h.close()
        st.logs = {}
        return res

    # ------------------------------------------------------------ helpers
    def _license_paragraphs(self, builder, idgen) -> list[Node]:
        items = builder.meta.get("copyright_lines", [])
        if not items:
            return []
        blocks = builder.parse_blocks(items, allow_secs=False)
        out = []
        for b in blocks["body"]:
            for n in b.walk():
                if n.kind == "p" and n.inlines:
                    n.id = idgen.next("fm001", "p")
                    out.append(n)
        return out

    def export_images(self, src, root: Node, pages, out_dir: Path, bi, gen, issues: IssueLog):
        img_dir = out_dir / "images"
        docs = {}
        page_by = {p.index: p for p in pages}
        author = (bi.author or bi.prefix).lower()
        isbn = bi.isbn or ""
        counters = {}
        q = self.cfg.get("images.jpeg_quality", 90)
        dpi = self.cfg.get("images.dpi", 300)

        def doc_for(page_index):
            fi, pno = src.page_map[page_index]
            if fi not in docs:
                docs[fi] = src.open(fi)
            return docs[fi], pno

        try:
            for n in root.walk():
                bbox = None
                kind = None
                if n.kind == "fig":
                    bbox, kind = n.meta.get("art"), "f"
                elif n.kind == "table-wrap" and "TABLE_STRUCTURAL_EXTRACTION_FAILED" in n.flags:
                    bbox, kind = n.meta.get("fallback_image"), "t"
                elif n.kind == "inline-graphic":
                    bbox, kind = n.meta.get("art"), "i"
                if kind is None:
                    continue
                part = (n.id or "x-x").split("-")[1] if n.id and n.id.count("-") >= 2 else "x"
                counters[(part, kind)] = counters.get((part, kind), 0) + 1
                name = f"{author}{isbn}-{part}-{kind}{counters[(part, kind)]:03d}.{self.cfg.get('images.format', 'jpg')}"
                if bbox is None or n.page is None:
                    issues.add("IMAGE_REGION_MISSING", "error", f"{n.meta.get('label') or n.kind} has no crop region", page=n.page)
                    continue
                try:
                    d, pno = doc_for(n.page)
                    pg = page_by.get(n.page)
                    keep = {id(x) for x in (n.meta.get("absorbed") or [])}
                    bx = tuple(bbox)
                    # small unlabelled graphic (feature-box icon): no text belongs in its image
                    icon = not n.meta.get("label") and max(bx[2] - bx[0], bx[3] - bx[1]) < 90
                    # erase what overlaps the crop but is not artwork: captions, running heads/folios,
                    # table/box text, and running-text lines (long body lines); short labels stay
                    erase = [l.bbox for l in (pg.lines if pg else [])
                             if id(l) not in keep and l.x1 > bx[0] and l.x0 < bx[2] and l.y1 > bx[1] and l.y0 < bx[3]
                             and (l.role in ("caption", "header", "footer", "folio", "header-iso", "footer-iso",
                                             "table-text", "table-foot", "box-text", "rotated-margin")
                                  or (l.role == "body" and len(l.text.strip()) > 45)
                                  or (icon and l.role != "figure-text"))]
                    export_region(d, pno, bx, img_dir / name, dpi=dpi, quality=q,
                                  rot=pg.rot if pg else 0, orig_size=pg.orig_size if pg else None,
                                  erase=erase if kind in ("f", "i") else None)
                    n.meta["href"] = name
                except Exception as e:
                    issues.add("IMAGE_EXPORT_FAILED", "error", f"{name}: {e}", page=n.page)
            if src.files:
                d = src.open(0)
                try:
                    export_cover(d, img_dir, f"{(bi.shortcode or bi.prefix).lower()}.gif", self.cfg.get("images.cover_width_px", 300))
                finally:
                    d.close()
        finally:
            for d in docs.values():
                d.close()


# ------------------------------------------------------------------ helpers

def merge_table_continuations(root: Node, issues: IssueLog):
    tables = [n for n in root.walk() if n.kind == "table-wrap"]
    last_by_key = {}
    for t in tables:
        key = t.meta.get("key")
        if not key:
            continue
        prev = last_by_key.get(key)
        if prev is not None and (t.meta.get("continued") or prev.page is not None and t.page == prev.page + 1):
            if prev.meta.get("rows") and t.meta.get("rows") and prev.meta.get("ncols") == t.meta.get("ncols"):
                hr = t.meta.get("header_rows", 0)
                prev.meta["rows"].extend(t.meta["rows"][hr:])
            elif t.meta.get("rows") and not prev.meta.get("rows"):
                prev.meta["rows"] = t.meta["rows"]
            prev.meta.setdefault("foot", []).extend(t.meta.get("foot") or [])
            if t.parent is not None:
                t.parent.remove(t)
            issues.add("TABLE_CONTINUATION_MERGED", "info", f"{t.meta.get('label')} continued on page {t.page}", page=t.page)
            continue
        last_by_key[key] = t


def resolve_toc(root: Node, registry, gen, issues):
    """Printed TOC entries -> nav-pointer to the part whose first page (or title) matches."""
    from .heading_detector import norm_title
    by_page = {}
    by_chapter_page = {}
    by_title = {}
    for n in root.walk():
        if n.id and n.kind in ("book-part", "front-matter-part", "preface", "foreword", "dedication", "ack", "index", "toc"):
            fp = n.meta.get("fpage")
            if fp:
                by_page.setdefault(str(fp).lower(), n.id)
                if n.attrs.get("book-part-type") == "chapter":
                    by_chapter_page.setdefault(str(fp).lower(), n.id)
            t = n.meta.get("title")
            if isinstance(t, list):
                by_title[norm_title("".join(x.get("text", "") for x in t))] = n.id
            elif isinstance(t, str):
                by_title[norm_title(t)] = n.id
    for toc in root.find_all("toc"):
        for e in toc.meta.get("entries") or []:
            rid = None
            if e.get("page"):
                is_ch = bool(re.match(r"(?i)\s*(cap[íi]tulo|chapter)", e.get("label") or ""))
                rid = (by_chapter_page.get(str(e["page"]).lower()) if is_ch else None) or by_page.get(str(e["page"]).lower())
            if rid is None:
                rid = by_title.get(norm_title(e.get("title") or ""))
            if rid is None and e.get("page"):
                rec = registry.lookup("page", None, str(e["page"]).lower())
                rid = rec["id"] if rec else None
            e["rid"] = rid


def resolve_outline(root: Node, registry):
    """Chapter-opener outlines (I. / A. lists) -> chapter-level toc with section xrefs (sample convention)."""
    from .heading_detector import norm_title
    for bp in root.find_all("book-part"):
        outline = bp.meta.get("outline")
        if not outline:
            continue
        secs = {norm_title(s.meta.get("title_text", "")): s.id for s in bp.walk() if s.kind == "sec"}
        entries = []
        buf = None
        for o in outline:
            t = o["text"]
            m = re.match(r"^\s*((?:[IVXL]+|[A-Z]|\d{1,2})[.)])\s+(.*)$", t)
            if m:
                if buf:
                    entries.append(buf)
                buf = {"label": m.group(1), "title": m.group(2).strip()}
            elif buf:
                buf["title"] += " " + t.strip()
        if buf:
            entries.append(buf)
        resolved = []
        for e in entries:
            rid = secs.get(norm_title(e["title"]))
            if rid:
                resolved.append({**e, "rid": rid})
        if entries and len(resolved) >= 0.6 * len(entries):
            bp.meta["outline_toc"] = resolved


def quality_gate(v: dict, failed_pages, issues: IssueLog, cfg) -> str:
    critical = []
    if not v["dtd"]["valid"]:
        critical.append("DTD invalid")
    if v["ids"]["duplicates"] or v["ids"]["invalid"]:
        critical.append("duplicate/invalid IDs")
    if v["links"]["broken"] or v["links"]["wrong_type"]:
        critical.append("broken links")
    if v["images"]["missing"] or v["images"]["unreadable"]:
        critical.append("invalid image references")
    if v["content"]["coverage"] < cfg.get("qa.content_coverage_min", 0.97):
        critical.append(f"content coverage {v['content']['coverage']:.3f}")
    if any(p for p in failed_pages):
        critical.append(f"{len(failed_pages)} failed page(s)")
    if issues.count("error"):
        critical.append(f"{issues.count('error')} error issue(s)")
    v["gate"] = {"critical": critical}
    if critical:
        return "FAILED"
    if issues.count("warning") or v["ids"]["pattern_violation_count"]:
        return "PASS WITH WARNINGS"
    return "PASS"
