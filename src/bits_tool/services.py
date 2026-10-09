"""Service interface between the UI (or CLI) and the engine.

Every function is synchronous, reports progress through a callback
`progress(**info)` and checks `cancel()` — the UI runs them in worker threads.
No UI code here and no engine logic in the UI.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from .batch_processor import BatchProcessor
from .config import TOOL_VERSION, Config
from .dtd_analyzer import DTDRules, find_dtd
from .project_analyzer import ProjectAnalyzer, materialize_samples
from .sample_analyzer import SampleAnalyzer
from .sample_mapper import SampleMapper, corpus_hash


class Cancelled(Exception):
    pass


def _noop(**kw):
    pass


# ------------------------------------------------------------------- status

def dtd_status(cfg: Config) -> dict:
    d = cfg.path("dtd_dir")
    if not d.exists():
        return {"state": "missing", "label": "Not configured", "detail": f"DTD folder not found: {d}"}
    f = find_dtd(d)
    if f is None:
        return {"state": "missing", "label": "Not configured", "detail": "No BITS-book1.dtd found"}
    try:
        DTDRules(f)
    except Exception as e:
        return {"state": "invalid", "label": "Invalid", "detail": str(e)}
    return {"state": "ok", "label": "Loaded", "detail": str(f)}


def mapping_status(cfg: Config) -> dict:
    meta_p = cfg.path("mapping_dir") / "mapping_meta.json"
    if not meta_p.exists():
        return {"state": "missing", "label": "No Mapping", "detail": "Run Sample Analysis to build the mapping profile."}
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    sig = cfg.path("analysis_dir") / "corpus_signature.json"
    stale = False
    if sig.exists():
        try:
            saved = json.loads(sig.read_text(encoding="utf-8"))
            now = samples_signature(cfg.path("samples_dir"))
            stale = bool(now) and saved.get("signature") != now
        except Exception:
            stale = False
    if stale:
        return {"state": "stale", "label": "Mapping Needs Rebuild", "detail": "Samples changed since the mapping was built.", **meta}
    return {"state": "ok", "label": "Mapping Ready", "detail": f"{len(meta.get('samples', []))} samples · built {meta.get('creation_date')}", **meta}


def samples_signature(samples_dir: Path) -> str | None:
    """Cheap change detector: names + sizes + mtimes of the Samples tree."""
    if not samples_dir.exists():
        return None
    import hashlib
    h = hashlib.sha1()
    for root, _d, files in os.walk(samples_dir):
        for f in sorted(files):
            p = Path(root) / f
            try:
                st = p.stat()
            except OSError:
                continue
            h.update(f"{p.relative_to(samples_dir)}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()


# --------------------------------------------------------- sample analysis

def analyze_samples(cfg: Config, progress=_noop, cancel=lambda: False) -> dict:
    """Scan Samples (incl. zips) -> XML/PDF/DTD analysis -> mapping knowledge base."""
    t0 = time.time()
    samples = cfg.path("samples_dir")
    analysis = cfg.path("analysis_dir")
    mapping = cfg.path("mapping_dir")
    analysis.mkdir(parents=True, exist_ok=True)

    def step(stage, frac, current=""):
        progress(stage=stage, frac=frac, current=current)
        if cancel():
            raise Cancelled()

    step("Scanning sample corpus", 0.02)
    pa = ProjectAnalyzer(cfg.root, samples, cfg.path("dtd_dir"), cfg.path("input_dir"), checksums=True, pdf_pages=True)
    counts = pa.write(analysis)
    step("PDF/XML matching", 0.12)
    pairs = materialize_samples(samples, cfg.path("cache_dir") / "samples")
    (analysis / "sample_pairs.json").write_text(json.dumps(pairs, indent=1), encoding="utf-8")
    step("Analyzing DTD", 0.2)
    dtd = DTDRules(find_dtd(cfg.path("dtd_dir")))
    dtd_summary = dtd.write(analysis)
    step("XML structure analysis", 0.25)
    xml_dir = cfg.path("cache_dir") / "samples"
    sa = SampleAnalyzer(xml_dir, analysis, progress=lambda m, f: step("XML structure analysis", 0.25 + 0.35 * f, m))
    summary = sa.run()
    step("PDF typography analysis", 0.62)
    sigs = {}
    from .pdf_sample_analyzer import align
    matched = [(k, v) for k, v in pairs.items() if v.get("xml") and v.get("pdf")]
    for i, (isbn, v) in enumerate(matched):
        step("PDF typography analysis", 0.62 + 0.28 * i / max(1, len(matched)), f"{isbn}.pdf")
        try:
            sigs[isbn] = align([Path(p) for p in v["pdf"]], Path(v["xml"]))
        except Exception as e:  # recorded, analysis continues
            sigs[isbn] = {"error": repr(e)}
    (analysis / "pdf_signatures_by_sample.json").write_text(json.dumps(sigs, indent=1), encoding="utf-8")
    step("Mapping generation", 0.92)
    meta = SampleMapper(analysis, mapping).build()
    (analysis / "corpus_signature.json").write_text(json.dumps({"signature": samples_signature(samples)}), encoding="utf-8")
    report = sample_report(cfg)
    step("Complete", 1.0)
    report["seconds"] = round(time.time() - t0, 1)
    report["inventory_counts"] = counts
    report["dtd"] = dtd_summary
    return report


def sample_report(cfg: Config) -> dict:
    """Numbers for the Sample Analysis result screen (read from the analysis files)."""
    a = cfg.path("analysis_dir")

    def load(name, default=None):
        p = a / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    inv = load("sample_inventory.json", {}) or {}
    summ = load("xml_sample_summary.json", {}) or {}
    ids = load("xml_id_patterns.json", {}) or {}
    links = load("xml_link_patterns.json", {}) or {}
    struct = (load("xml_structure_patterns.json", {}) or {}).get("structures", {})
    place = (load("placement_patterns.json", {}) or {}).get("relations", {})
    conflicts = load("mapping_conflicts.json", []) or []
    dtd = (load("dtd_analysis.json", {}) or {}).get("summary", {})
    pairs = load("sample_pairs.json", {}) or {}
    c = inv.get("counts", {})
    n_pdf = sum(1 for v in pairs.values() if v.get("pdf")) or c.get("TOTAL_PDF_ISBNS", 0)
    n_xml = sum(1 for v in pairs.values() if v.get("xml")) or c.get("TOTAL_XML_ISBNS", 0)
    n_match = sum(1 for v in pairs.values() if v.get("pdf") and v.get("xml"))
    if not n_match and isinstance(c.get("MATCHED_PDF_XML"), list):
        n_match = len(c["MATCHED_PDF_XML"])
    return {
        "pdf_samples": n_pdf, "xml_samples": n_xml, "matched": n_match,
        "dtd_version": f"BITS {dtd.get('bits_version', '?')}",
        "elements": summ.get("unique_elements", 0),
        "attributes": summ.get("unique_attributes", 0),
        "parent_child_rules": summ.get("parent_child_rules", 0),
        "id_patterns": sum(len(v) for v in ids.values()),
        "link_patterns": len(links.get("xref_targets", {})),
        "figure_patterns": len(struct.get("fig label shape", {})),
        "table_patterns": len(struct.get("table label shape", {})),
        "list_patterns": len(struct.get("list@list-type", {})),
        "index_patterns": len(struct.get("index-entry depth", {})),
        "citation_patterns": len(links.get("link_text_shapes", {})),
        "placement_rules": sum(len(v) for v in place.values()),
        "conflicts": len(conflicts),
        "coverage": _coverage(struct, place),
        "samples": summ.get("samples", []),
    }


def _coverage(struct, place) -> dict:
    def pct(d, bad=("no_citation",)):
        tot = sum(d.values()) or 1
        return round(100 * sum(v for k, v in d.items() if k not in bad) / tot)
    return {"Figures": pct(place.get("fig", {})), "Tables": pct(place.get("table-wrap", {})),
            "Lists": 100 if struct.get("list@list-type") else 0,
            "References": 100 if struct.get("mixed-citation@publication-type") else 0,
            "Index": 100 if struct.get("index-entry depth") else 0}


# ------------------------------------------------------------- conversion

def convert(cfg: Config, inputs: list[Path], out_dir: Path, progress=_noop, cancel=lambda: False, resume=True,
            book_overrides: dict | None = None) -> list[dict]:
    bp = BatchProcessor(cfg, progress=progress, cancel=cancel)
    return bp.run(inputs, out_dir, resume=resume, book_overrides=book_overrides)


def interrupted_batch(out_dir: Path) -> dict | None:
    from .checkpoint_manager import BatchState
    try:
        return BatchState(out_dir).interrupted()
    except Exception:
        return None


# -------------------------------------------------------------- validation

def validate_xml(cfg: Config, xml_path: Path, progress=_noop, cancel=lambda: False) -> dict:
    from .validators import parse, validate_dtd, validate_ids, validate_images, validate_links, validate_structure
    res: dict = {"file": str(xml_path)}
    progress(stage="Parsing", frac=0.1)
    try:
        tree = parse(xml_path)
        res["well_formed"] = {"ok": True}
    except Exception as e:
        res["well_formed"] = {"ok": False, "error": str(e)}
        res["status"] = "FAILED"
        return res
    rules = DTDRules(find_dtd(cfg.path("dtd_dir")))
    progress(stage="DTD validation", frac=0.3)
    res["dtd"] = validate_dtd(tree, rules)
    prefix = _guess_prefix(tree)
    progress(stage="IDs", frac=0.5)
    res["ids"] = validate_ids(tree, prefix)
    progress(stage="Internal links", frac=0.65)
    res["links"] = validate_links(tree)
    progress(stage="Images", frac=0.8)
    res["images"] = validate_images(tree, Path(xml_path).parent)
    res["structure"] = validate_structure(tree)
    rep = Path(xml_path).parent / "validation" / "validation_report.json"
    res["content"] = None
    if rep.exists():
        try:
            res["content"] = json.loads(rep.read_text(encoding="utf-8")).get("content")
        except Exception:
            pass
    crit = []
    if not res["dtd"]["valid"]:
        crit.append("DTD")
    if res["ids"]["duplicates"] or res["ids"]["invalid"]:
        crit.append("IDs")
    if res["links"]["broken"] or res["links"]["wrong_type"]:
        crit.append("Links")
    if res["images"]["missing"] or res["images"]["unreadable"]:
        crit.append("Images")
    warn = bool(res["structure"]["issues"]) or res["ids"]["pattern_violation_count"] > 0
    res["status"] = "FAILED" if crit else ("VALID WITH WARNINGS" if warn else "VALID")
    res["critical"] = crit
    progress(stage="Done", frac=1.0)
    return res


def _guess_prefix(tree) -> str:
    from collections import Counter
    c = Counter()
    for el in tree.getroot().iter():
        if isinstance(el.tag, str) and el.get("id") and "-" in el.get("id"):
            c[el.get("id").split("-")[0]] += 1
    return c.most_common(1)[0][0] if c else "book"


# ----------------------------------------------------------------- reports

def list_reports(out_dir: Path) -> list[dict]:
    out = []
    if not Path(out_dir).exists():
        return out
    for book in sorted(p for p in Path(out_dir).iterdir() if p.is_dir()):
        status = None
        vr = book / "validation" / "validation_report.json"
        if vr.exists():
            try:
                status = json.loads(vr.read_text(encoding="utf-8")).get("status")
            except Exception:
                status = None
        for kind, rel in (("Conversion", "qa/conversion_report.html"), ("Validation", "validation/validation_report.html"),
                          ("Placement", "qa/placement_report.html"), ("Links", "qa/link_report.json"),
                          ("Content", "qa/page_qa.json")):
            p = book / rel
            if p.exists():
                out.append({"book": book.name, "kind": kind, "path": str(p), "status": status,
                            "date": _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d %b %Y %H:%M")})
    return out


def load_book_result(book_dir: Path) -> dict:
    """Everything the UI shows for a converted book, read from its output folder."""
    book_dir = Path(book_dir)
    d = {"dir": str(book_dir)}
    for key, rel in (("validation", "validation/validation_report.json"), ("placement", "qa/placement_report.json"),
                     ("pages", "qa/page_qa.json"), ("links", "qa/link_report.json"), ("ids", "qa/id_report.json")):
        p = book_dir / rel
        if p.exists():
            try:
                d[key] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                d[key] = None
    xmls = sorted(book_dir.glob("*.xml"))
    d["xml"] = str(xmls[0]) if xmls else None
    return d


# ------------------------------------------------------------------ zoning

def zoning_books(out_dir: Path) -> list[dict]:
    """Converted books that have a zoning project (newest first)."""
    from .zoning import ZoneProject
    out = []
    root = Path(out_dir)
    if not root.exists():
        return out
    for d in root.iterdir():
        if d.is_dir() and ZoneProject.exists(d):
            info = {}
            try:
                info = json.loads((d / "zoning" / "zones.json").read_text(encoding="utf-8")[:4000].split(',"tree"')[0] + "}").get("info", {})
            except Exception:
                pass
            out.append({"dir": str(d), "book": d.name, "mode": info.get("mode"), "edits": info.get("edits", 0),
                        "saved": info.get("saved"), "mtime": (d / "zoning" / "zones.json").stat().st_mtime})
    out.sort(key=lambda x: -x["mtime"])
    return out


def start_zoning(cfg: Config, book: Path, out_dir: Path, manual: bool = False, progress=_noop, cancel=lambda: False) -> str:
    """Analyse a PDF and create its zoning project: auto zones, or an empty skeleton for manual zoning."""
    from .converter import BookConverter
    from .zoning import ZoneProject
    from .pdf_loader import discover_books
    books = discover_books(Path(book))
    if not books:
        raise ValueError(f"no PDF found in {book}")
    conv = BookConverter(cfg, progress=progress, cancel_flag=cancel)
    st = conv.analyze(books[0], Path(out_dir), manual=manual)
    zp = ZoneProject.create(st, mode="manual" if manual else "auto-review")
    zp.save()
    for h in [h for lg in st.logs.values() for h in lg.handlers]:
        h.close()
    return str(st.out_dir)


def load_zoning(cfg: Config, book_dir: Path):
    from .zoning import ZoneEditor, ZoneProject
    zp = ZoneProject.load(Path(book_dir))
    return ZoneEditor(zp, cfg)


def zoning_generate(cfg: Config, editor, progress=_noop, cancel=lambda: False) -> dict:
    """Edited zones -> BITS XML (same back half as automatic conversion) + reports; records the result."""
    from .batch_processor import _brief, _issue_counts
    from .checkpoint_manager import BatchState
    from .converter import BookConverter
    progress(stage="Rebuilding edited zones", frac=0.05)
    editor.project.save(tree_only=True)
    editor.dirty_file = False
    root, issues = editor.prepare_for_xml()
    conv = BookConverter(cfg, progress=progress, cancel_flag=cancel)
    st = editor.project.state
    res = conv.finish(st, root, issues)
    out_root = Path(st.out_dir).parent
    try:
        BatchState(out_root).mark(str(st.book_path), status=res.status, xml=res.xml_path, out_dir=res.out_dir,
                                  stats=res.stats, report=res.report_path, zoning=True)
    except Exception:
        pass
    return {"book": st.name, "status": res.status, "xml": res.xml_path, "out_dir": res.out_dir, "stats": res.stats,
            "report": res.report_path, "validation": _brief(res.validation), "issues": _issue_counts(res.issues),
            "failed_pages": len(res.failed_pages)}
