"""QA reports: JSON for machines, self-contained HTML for people.

reports written per book (output/<book>/qa and /validation):
  validation_report.json/.html  placement_report.json/.html  reference_links.json
  page_qa.json  id_report.json  link_report.json  conversion_report.html
All numbers come from the validators — nothing is estimated.
"""
from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#1d2330;--mute:#5d6778;--line:#e3e6eb;--blue:#2563eb;--ok:#15803d;--warn:#b45309;--err:#b91c1c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Segoe UI",system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 20px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.sub{color:var(--mute);margin:0 0 18px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}.k{color:var(--mute);font-size:12px}
.v{font-size:20px;font-weight:600}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px}th{background:#f1f3f6;font-weight:600}
.s{font-weight:600;white-space:nowrap}.PASS,.ok{color:var(--ok)}.WARN,.warn,.REVIEW{color:var(--warn)}.FAILED,.err{color:var(--err)}
.badge{display:inline-block;padding:3px 10px;border-radius:999px;font-weight:600;border:1px solid currentColor}
code{font:12px ui-monospace,Consolas,monospace;background:#f1f3f6;padding:1px 4px;border-radius:4px}
"""


def _esc(x) -> str:
    return html.escape("" if x is None else str(x))


def _page(title: str, body: str) -> str:
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{_esc(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>"


def _status_cls(s: str) -> str:
    return {"PASS": "PASS", "PASS WITH WARNINGS": "WARN", "FAILED": "FAILED"}.get(s, "WARN")


def _mark(ok: bool, warn: bool = False) -> str:
    if ok:
        return "<span class='ok'>✓ OK</span>"
    return "<span class='warn'>⚠ Review</span>" if warn else "<span class='err'>✕ Failed</span>"


def write_reports(out_dir: Path, res, placement, links, registry, pages, bi, cfg) -> Path:
    qa = out_dir / "qa"
    val = out_dir / "validation"
    qa.mkdir(parents=True, exist_ok=True)
    val.mkdir(parents=True, exist_ok=True)
    v = res.validation

    def dump(p: Path, data):
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False, default=str)

    # ---------------------------------------------------------- placement
    prows = []
    for r in placement:
        n = r.get("node")
        anchor = r.get("anchor")
        prows.append({"type": r["type"], "label": r.get("label"), "target_id": n.id if n is not None else None,
                      "physical_page": r.get("physical_page"), "physical_bbox": r.get("physical_bbox"),
                      "first_citation_page": r.get("first_citation_page"), "first_citation_text": r.get("first_citation_text"),
                      "first_citation_paragraph": anchor.id if anchor is not None else None,
                      "xml_insertion_point": f"after {anchor.id}" if anchor is not None and r.get("status") == "PLACED" else "physical position",
                      "placement_rule": r.get("rule"), "citations": r.get("citations"),
                      "incoming_links": len(registry.by_id.get(n.id, {}).get("incoming", [])) if n is not None and n.id else 0,
                      "status": r.get("status"), "note": r.get("note")})
    folio = {p.index: p.folio for p in pages}
    for pr in prows:
        pr["physical_folio"] = folio.get(pr["physical_page"])
        pr["first_citation_folio"] = folio.get(pr.get("first_citation_page"))
    dump(qa / "placement_report.json", prows)
    dump(qa / "issues.json", res.issues)
    dump(qa / "reference_links.json", {"citations": links, "targets": [
        {"id": t["id"], "kind": t["kind"], "label": t.get("label"), "incoming": len(t["incoming"])}
        for t in registry.by_id.values() if t["kind"] in ("fig", "table", "bibr", "fn", "chapter", "box", "app")]})
    dump(qa / "page_qa.json", page_qa(pages, v, res.issues, cfg))
    dump(qa / "id_report.json", {"total": v["ids"]["total"], "unique": v["ids"]["unique"], "duplicates": v["ids"]["duplicates"],
                                 "invalid": v["ids"]["invalid"], "pattern_violations": v["ids"]["pattern_violations"],
                                 "collisions_renamed_before_linking": v.get("id_collisions", [])})
    lk = v["links"]
    unresolved = [l for l in links if l["status"] != "resolved"]
    dump(qa / "link_report.json", {"total_internal_links": lk["total"], "valid": lk["valid"], "invalid": len(lk["broken"]) + len(lk["wrong_type"]),
                                   "broken": lk["broken"], "wrong_type": lk["wrong_type"], "unresolved_citations": unresolved,
                                   "duplicate_targets": registry.duplicates, "by_kind": lk["by_kind"]})
    vjson = {k: v[k] for k in v if k != "content"}
    vjson["content"] = {k: v["content"][k] for k in ("pdf_words", "matched", "coverage", "xml_extra_words", "missing_samples")}
    vjson["status"] = res.status
    dump(val / "validation_report.json", vjson)

    # ------------------------------------------------------------- HTML
    s = res.stats
    fig_rows = [p for p in prows if p["type"] == "figure"]
    tab_rows = [p for p in prows if p["type"] == "table"]
    unresolved_fig = sum(1 for l in unresolved if l["kind"] == "fig")
    unresolved_tab = sum(1 for l in unresolved if l["kind"] in ("table", "table-or-box"))
    gates = [
        ("XML well-formed", True, False),
        ("DTD validation", v["dtd"]["valid"], False),
        ("IDs unique & valid", not v["ids"]["duplicates"] and not v["ids"]["invalid"], False),
        ("Internal links", not lk["broken"] and not lk["wrong_type"], False),
        ("Image references", not v["images"]["missing"] and not v["images"]["unreadable"], False),
        (f"Content coverage {v['content']['coverage']:.2%}", v["content"]["coverage"] >= cfg.get("qa.content_coverage_min", 0.97), False),
        ("No failed pages", not res.failed_pages, False),
    ]
    cards = [("Pages", s.get("pages")), ("OCR pages", s.get("ocr_pages")), ("Chapters", s.get("chapters")),
             ("Sections", s.get("sec")), ("Paragraphs", s.get("p")), ("Lists", s.get("list")), ("Figures", s.get("fig")),
             ("Tables", s.get("table-wrap")), ("Boxes", s.get("boxed-text")), ("Equations", s.get("disp-formula")),
             ("References", s.get("ref")), ("Footnotes", s.get("fn")), ("Index entries", s.get("index-entry")),
             ("TOC entries", s.get("toc-entry")), ("IDs", s.get("ids")), ("Internal links", s.get("links")),
             ("Citations found", s.get("citations")), ("Unresolved citations", s.get("unresolved_citations"))]
    ic = Counter((i["severity"], i["code"]) for i in res.issues)
    body = f"""
<h1>Conversion report — {_esc(res.book)}</h1>
<p class='sub'>{_esc(bi.title)} · ISBN {_esc(bi.isbn_display)} · ID prefix <code>{_esc(bi.prefix)}</code> · {s.get('seconds')} s</p>
<p><span class='badge {_status_cls(res.status)}'>{_esc(res.status)}</span></p>
<h2>Quality gates</h2><table><tr><th>Check</th><th>Result</th></tr>
{''.join(f"<tr><td>{_esc(n)}</td><td>{_mark(ok, w)}</td></tr>" for n, ok, w in gates)}</table>
{('<p class="err">Critical: ' + _esc('; '.join(v['gate']['critical'])) + '</p>') if v.get('gate', {}).get('critical') else ''}
<h2>Summary</h2><div class='grid'>{''.join(f"<div class='card'><div class='k'>{_esc(k)}</div><div class='v'>{_esc(val if val is not None else 0)}</div></div>" for k, val in cards)}</div>
<h2>Figures</h2><div class='grid'>
<div class='card'><div class='k'>Total</div><div class='v'>{s.get('fig', 0)}</div></div>
<div class='card'><div class='k'>With citations</div><div class='v'>{sum(1 for r in fig_rows if r['citations'])}</div></div>
<div class='card'><div class='k'>Without citations</div><div class='v'>{sum(1 for r in fig_rows if not r['citations'])}</div></div>
<div class='card'><div class='k'>Placed by first citation</div><div class='v'>{sum(1 for r in fig_rows if r['status'] == 'PLACED')}</div></div>
<div class='card'><div class='k'>Linked (validator)</div><div class='v'>{lk['figures']['linked']}</div></div>
<div class='card'><div class='k'>Unresolved citations</div><div class='v'>{unresolved_fig}</div></div></div>
<h2>Tables</h2><div class='grid'>
<div class='card'><div class='k'>Total</div><div class='v'>{s.get('table-wrap', 0)}</div></div>
<div class='card'><div class='k'>With citations</div><div class='v'>{sum(1 for r in tab_rows if r['citations'])}</div></div>
<div class='card'><div class='k'>Placed by first citation</div><div class='v'>{sum(1 for r in tab_rows if r['status'] == 'PLACED')}</div></div>
<div class='card'><div class='k'>Linked (validator)</div><div class='v'>{lk['tables']['linked']}</div></div>
<div class='card'><div class='k'>Unresolved citations</div><div class='v'>{unresolved_tab}</div></div>
<div class='card'><div class='k'>Image fallbacks</div><div class='v'>{sum(1 for i in res.issues if i['code'] == 'TABLE_STRUCTURAL_EXTRACTION_FAILED')}</div></div></div>
<h2>Links</h2><div class='grid'>
<div class='card'><div class='k'>Total internal links</div><div class='v'>{lk['total']}</div></div>
<div class='card'><div class='k'>Valid</div><div class='v'>{lk['valid']}</div></div>
<div class='card'><div class='k'>Broken</div><div class='v'>{len(lk['broken'])}</div></div>
<div class='card'><div class='k'>Wrong target type</div><div class='v'>{len(lk['wrong_type'])}</div></div>
<div class='card'><div class='k'>Unresolved (kept as text)</div><div class='v'>{len(unresolved)}</div></div>
<div class='card'><div class='k'>Duplicate IDs</div><div class='v'>{len(v['ids']['duplicates'])}</div></div></div>
<h2>Recognition QC</h2><p><a href="../qa/recognition_qc.html">Open automatic recognition checks and source comparisons</a> · {_esc(v.get("recognition", {}).get("status", "unavailable"))}</p>
<h2>Issues</h2><table><tr><th>Severity</th><th>Code</th><th>Count</th></tr>
{''.join(f"<tr><td class='{'err' if sev in ('error','critical') else 'warn' if sev=='warning' else ''}'>{_esc(sev)}</td><td><code>{_esc(code)}</code></td><td>{n}</td></tr>" for (sev, code), n in sorted(ic.items(), key=lambda x: (-['info','warning','error','critical'].index(x[0][0]) if x[0][0] in ['info','warning','error','critical'] else 0, -x[1])))}</table>
<h2>DTD</h2><p>{'Valid against ' + _esc(cfg.get('bits.public_id')) if v['dtd']['valid'] else str(v['dtd']['error_count']) + ' errors'}</p>
{'<table><tr><th>Line</th><th>Message</th></tr>' + ''.join(f"<tr><td>{e['line']}</td><td>{_esc(e['message'])}</td></tr>" for e in v['dtd']['errors'][:50]) + '</table>' if not v['dtd']['valid'] else ''}
<h2>Pages needing review</h2>{page_table(page_qa(pages, v, res.issues, cfg))}
<p class='sub'>Detailed data: qa/placement_report.json · qa/link_report.json · qa/id_report.json · qa/page_qa.json · validation/validation_report.json</p>
"""
    report = qa / "conversion_report.html"
    report.write_text(_page(f"Conversion report {res.book}", body), encoding="utf-8")
    (val / "validation_report.html").write_text(_page("Validation report", body), encoding="utf-8")
    prow_html = "".join(
        f"<tr><td>{_esc(r['type'])}</td><td>{_esc(r['label'])}</td><td><code>{_esc(r['target_id'])}</code></td>"
        f"<td>{_esc(r['physical_folio'] or r['physical_page'])}</td><td>{_esc(r['first_citation_folio'] or r.get('first_citation_page') or '—')}</td>"
        f"<td>{_esc(r['first_citation_text'] or '—')}</td><td>{_esc(r['xml_insertion_point'])}</td><td>{_esc(r['placement_rule'])}</td>"
        f"<td>{r['citations']}</td><td>{r['incoming_links']}</td><td class='s {'PASS' if r['status']=='PLACED' else 'REVIEW'}'>{_esc(r['status'])}</td></tr>"
        for r in prows)
    (qa / "placement_report.html").write_text(_page("Figure & table placement", f"""
<h1>Figure &amp; table placement</h1><p class='sub'>Physical position (PDF) vs semantic position (XML) for every labelled figure and table.</p>
<table><tr><th>Type</th><th>Label</th><th>Target ID</th><th>Physical page</th><th>First citation page</th><th>First citation</th>
<th>XML position</th><th>Rule</th><th>Citations</th><th>Incoming links</th><th>Status</th></tr>{prow_html}</table>"""), encoding="utf-8")
    return report


def page_qa(pages, v, issues, cfg) -> list[dict]:
    cov = {p["page"]: p for p in v["content"]["pages"]}
    by_page = Counter()
    codes = {}
    for i in issues:
        if i.get("page") is not None:
            by_page[(i["page"], i["severity"])] += 1
            codes.setdefault(i["page"], set()).add(i["code"])
    out = []
    for p in pages:
        c = cov.get(p.index, {})
        err = by_page[(p.index, "error")] + (1 if any(e.startswith("EXTRACTION_ERROR") for e in p.errors) else 0)
        warn = by_page[(p.index, "warning")]
        low = c.get("coverage", 1.0) < cfg.get("qa.page_coverage_warn", 0.9) and c.get("words", 0) > 20
        status = "ERROR" if err else ("WARNING" if warn or low or (p.is_ocr and (p.ocr_conf or 0) < 0.7) else "PASS")
        out.append({"page": p.index, "folio": p.folio, "pdf": p.pdf, "pdf_page": p.pdf_page + 1, "status": status,
                    "coverage": c.get("coverage"), "words": c.get("words"), "ocr": p.is_ocr, "ocr_conf": p.ocr_conf,
                    "regions": Counter(r.kind for r in p.regions), "issues": sorted(codes.get(p.index, []))})
    return out


def _pct(x) -> str:
    return "" if x is None else f"{x:.0%}"


def page_table(rows) -> str:
    bad = [r for r in rows if r["status"] != "PASS"][:300]
    if not bad:
        return "<p class='ok'>✓ All pages passed.</p>"
    return "<table><tr><th>Page</th><th>Folio</th><th>File</th><th>Status</th><th>Coverage</th><th>Issues</th></tr>" + "".join(
        f"<tr><td>{r['page']}</td><td>{_esc(r['folio'])}</td><td>{_esc(r['pdf'])} p{r['pdf_page']}</td><td class='{'err' if r['status']=='ERROR' else 'warn'}'>{r['status']}</td>"
        f"<td>{_pct(r['coverage'])}</td><td>{_esc(', '.join(r['issues']))}</td></tr>"
        for r in bad) + "</table>"
