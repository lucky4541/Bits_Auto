"""Builds the mapping knowledge base (mapping/*.json) from analysis/*.json.

Every rule carries its evidence (counts across samples) and the rule the
converter will apply.  Where samples disagree, the disagreement is written
to analysis/mapping_conflicts.json together with the chosen resolution and
the reason (spec > majority-of-books > majority-of-elements).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections import Counter
from pathlib import Path

TOOL_VERSION = "1.0.0"
MAPPING_VERSION = "1.0"


def _load(p: Path):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def corpus_hash(sample_files: list[str]) -> str:
    h = hashlib.sha1()
    for f in sorted(sample_files):
        p = Path(f)
        h.update(p.name.encode())
        if p.exists():
            h.update(str(p.stat().st_size).encode())
    return h.hexdigest()


def _share(counter: dict, key) -> float:
    tot = sum(counter.values()) or 1
    return round(counter.get(key, 0) / tot, 3)


class SampleMapper:
    def __init__(self, analysis_dir: Path, mapping_dir: Path):
        self.a = Path(analysis_dir)
        self.m = Path(mapping_dir)
        self.m.mkdir(parents=True, exist_ok=True)

    def build(self) -> dict:
        ids = _load(self.a / "xml_id_patterns.json")
        links = _load(self.a / "xml_link_patterns.json")
        struct = _load(self.a / "xml_structure_patterns.json")
        place = _load(self.a / "placement_patterns.json")
        summ = _load(self.a / "xml_sample_summary.json")
        pc = _load(self.a / "xml_parent_child_rules.json")
        dtd = _load(self.a / "dtd_analysis.json")["summary"] if (self.a / "dtd_analysis.json").exists() else {}
        sig = _load(self.a / "pdf_signatures_by_sample.json") if (self.a / "pdf_signatures_by_sample.json").exists() else {}
        S = struct["structures"]
        conflicts = []

        # ------------------------------------------------------------ IDs
        def top_pattern(el):
            lst = ids.get(el, [])
            return (lst[0]["pattern"], lst[0]["count"], sum(x["count"] for x in lst)) if lst else (None, 0, 0)

        codes = {}
        for el, code in [("p", "p"), ("list", "list"), ("fig", "fig"), ("table-wrap", "tbl"), ("caption", "cap"),
                         ("graphic", "gr"), ("inline-graphic", "ig"), ("sec", "sec"), ("ref", "bib"),
                         ("ref-list", "r"), ("disp-formula", "eq"), ("inline-formula", "ieq"),
                         ("boxed-text", "sb-box"), ("question-wrap", "qw"), ("question", "q"),
                         ("answer", "a"), ("answer-set", "qam"), ("aff", "aff"), ("kwd-group", "kwdgroup"),
                         ("kwd", "term"), ("def-list", "deflist"), ("def-item", "defitem"), ("def", "def"),
                         ("term", "term"), ("toc", "toc"), ("front-matter-part", "topic"),
                         ("dedication", "dedication"), ("preface", "preface"), ("license-p", "p"),
                         ("index-entry", "ie"), ("fn", "fn")]:
            pat, n, tot = top_pattern(el)
            codes[el] = {"code": code, "dominant_pattern": pat, "evidence": f"{n}/{tot}"}
        id_mapping = {
            "mapping_version": MAPPING_VERSION,
            "prefix": {"rule": "first book-level contributor surname, lowercased, ASCII-folded",
                       "source": "vendor spec 'AuthorLastName-partid-sectionid'; samples: " +
                                 ", ".join(sorted({s['id_prefix'] or '?' for s in summ['samples']})),
                       "fallback": "short-name alt-title"},
            "part_ids": {"front-matter": "fm{n:03d}", "chapter": "ch{n:03d}", "part": "pt{n:03d}",
                         "appendix": "app{n:03d}", "back-matter": "bm{n:03d}", "index": "index{n:03d}",
                         "evidence": {e["pattern"]: e["count"] for e in ids.get("book-part", [])}},
            "element_codes": codes,
            "format": "{prefix}-{part}-{code}{n:03d}",
            "counter_scope": "per book-part, per element code, document order (1-based, zero padded to 3, widens past 999)",
            "fig_number_rule": "fig/table number = number in the printed label within the chapter (e.g. Figure 3.7 -> ch003-fig007); falls back to sequence when unlabeled",
            "table_fn": "{prefix}-{part}-tbl{t:03d}-fn{n:03d}",
            "page_target": {"rule": "page{folio}", "roman": "page{roman lower}",
                            "evidence": S.get("target id shape", {})},
            "graphic_href": {"rule": "{Author}{ISBN}-{part}-f{n:03d}.jpg (inline: -i{n:03d})",
                             "evidence": S.get("fig graphic href shape", {})},
            "book_level": {"permissions": "{prefix}-per", "license": "{prefix}-license",
                           "cover": "{prefix}-cover", "thumbnail": "{prefix}-thumbnail"},
        }
        if "{P}-ch{N3}-sec{N3}" in [x["pattern"] for x in ids.get("sec", [])]:
            others = [x for x in ids.get("sec", []) if x["pattern"] != "{P}-ch{N3}-sec{N3}"]
            if others:
                conflicts.append({"topic": "sec id shape", "variants": {x["pattern"]: x["count"] for x in ids["sec"][:6]},
                                  "cause": "publisher/vendor convention differs per book (e.g. '_sec', '-s')",
                                  "resolution": "{P}-{part}-sec{NNN} (vendor spec + majority)"})
        refp = [x for x in ids.get("ref", []) if not x["pattern"].startswith("{P}")]
        if refp:
            conflicts.append({"topic": "ref id without author prefix", "variants": {x["pattern"]: x["count"] for x in refp},
                              "cause": "single book (vendor convention)", "resolution": "always prefix (vendor spec @id rule)"})
        conflicts.append({"topic": "appendix part id", "variants": {"app": codes and id_mapping["part_ids"]["evidence"].get("{P}-app{N3}", 0),
                                                                    "appx": id_mapping["part_ids"]["evidence"].get("{P}-appx{N3}", 0)},
                          "cause": "book-part @id uses 'app', book-part-id sometimes 'appx' (publisher convention)",
                          "resolution": "@id and book-part-id both 'app{NNN}' (majority); configurable"})

        # ---------------------------------------------------------- links
        xt = links["xref_targets"]
        link_mapping = {
            "xref_ref_types": {k[5:-1]: v for k, v in xt.items() if k.startswith("xref[")},
            "rules": {
                "fig": {"element": "xref", "ref-type": "fig", "target": "fig"},
                "table": {"element": "xref", "ref-type": "table", "target": "table-wrap"},
                "bibr": {"element": "xref", "ref-type": "bibr", "target": "ref"},
                "fn": {"element": "xref", "ref-type": "fn", "target": "fn", "text_wrapped_in": "sup"},
                "table-fn": {"element": "xref", "ref-type": "table-fn", "target": "fn"},
                "chapter": {"element": "xref", "ref-type": "chapter", "target": "book-part"},
                "section": {"element": "xref", "ref-type": "section", "target": "sec"},
                "app": {"element": "xref", "ref-type": "app", "target": "book-part"},
                "boxed-text": {"element": "xref", "ref-type": "boxed-text", "target": "boxed-text"},
                "disp-formula": {"element": "xref", "ref-type": "disp-formula", "target": "disp-formula"},
                "cross_part_fig_table": {"element": "xref", "note": "samples use xref across chapters (vendor spec mentions ext-link; samples: 0 ext-link[fig])"},
                "toc_entry": {"element": "nav-pointer", "specific-use": "pagenum", "rid": "book-part/sec/front-matter-part id",
                              "evidence": xt.get("nav-pointer[pagenum]", {})},
                "index_page": {"element": "nav-pointer", "rid": "page{folio}", "specific-use": None,
                               "range": "two nav-pointers with nav-pointer-type start-of-range / end-of-range",
                               "suffix": "letters after folio (f, t, b) stay inside nav-pointer text",
                               "evidence": xt.get("nav-pointer[]", {})},
                "ranges": "first @rid of the range, whole range text inside the xref (vendor spec)",
                "lists": "one xref per cited target ('2, 3' -> two xrefs)",
            },
            "evidence_stats": links["stats"],
        }

        # ------------------------------------------------------ placement
        rel = place["relations"]

        def best(d):
            return max(d, key=d.get) if d else None
        placement_mapping = {
            "figure": {
                "rule": "after_first_citation_block",
                "evidence": rel.get("fig", {}),
                "dominant": best({k: v for k, v in rel.get("fig", {}).items() if k != "no_citation"}),
                "in_list_item": "append inside the citing <p> (end) — evidence: " +
                                str(place["citing_context"].get("fig:inside_citing_paragraph_end", {})),
                "multiple_figures_same_block": "keep citation order after the block",
                "no_citation": "physical reading-order position (flag NO_FIRST_CITATION)",
                "cross_part_citation": "physical position; flag CROSS_SECTION_FIGURE_REFERENCE",
                "citation_after_physical": "move forward to first citation if within same book-part",
            },
            "table": {
                "rule": "after_first_citation_block",
                "evidence": rel.get("table-wrap", {}),
                "dominant": best({k: v for k, v in rel.get("table-wrap", {}).items() if k != "no_citation"}),
                "in_list_item": "append inside the citing <p> (end)",
                "no_citation": "physical position (flag NO_FIRST_CITATION)",
                "cross_part_citation": "physical position; flag CROSS_SECTION_TABLE_REFERENCE",
            },
            "boxed-text": {"rule": "physical position", "evidence": rel.get("boxed-text", {})},
            "citations_ignored_for_first": ["toc", "toc-entry", "index", "index-entry", "caption of the target itself"],
        }

        # ---------------------------------------------------- structures
        element_mapping = {
            "book": {"children": "book-meta front-matter book-body book-back",
                     "attributes": {"dtd-version": "1.0", "xml:lang": "en"},
                     "namespaces": {"mml": "http://www.w3.org/1998/Math/MathML", "xlink": "http://www.w3.org/1999/xlink",
                                    "xsi": "http://www.w3.org/2001/XMLSchema-instance", "xi": "http://www.w3.org/2001/XInclude"}},
            "book-meta": {"children": "book-id[publisher-id] book-title-group contrib-group pub-date isbn publisher edition supplementary-material[cover] supplementary-material[thumbnail] permissions"},
            "book-part": {"types": S.get("book-part@book-part-type", {}),
                          "children": "book-part-meta (front-matter/toc)? body back?",
                          "meta": "book-part-id[publisher-id] title-group(label?, title) fpage lpage",
                          "label_shapes": S.get("book-part label shape", {}),
                          "seq": "@seq = order in book"},
            "appendix": "book-part[@book-part-type=chapter] in book-back (vendor spec: no <app>, no <glossary>)",
            "sec": {"disp-level": S.get("sec@disp-level", {}), "sec-type": "not used",
                    "children_patterns": struct["child_sequences"].get("sec", {})},
            "p": "every p carries @id; pagenum <target> inserted at the page-break position",
            "front-matter": {"children": S.get("front-matter children", {})},
            "toc": {"book_level": "front-matter/toc: title-group + toc-entry(label?, title, nav-pointer[pagenum])",
                    "chapter_level": "book-part/front-matter/toc: toc-entry(label(xref[section]), title(xref[section])) — only some books"},
            "index": {"location": "book-back/index", "children": "title-group p? index-div(title-group/title=letter, index-entry+)",
                      "entry": "term, nav-pointer-group?, index-entry* (nested sub-entries)",
                      "see": "see-entry / see-also-entry", "depths": S.get("index-entry depth", {})},
            "back": {"children": "ref-list(title, ref+)", "ref": "label? mixed-citation[@publication-type]",
                     "publication-types": S.get("mixed-citation@publication-type", {})},
            "fn": {"parents": S.get("fn parent", {}), "chapter_notes": "back/fn-group or table-wrap-foot/fn-group"},
            "target": {"target-type": "pagenum", "parents": S.get("target parent", {})},
        }
        figure_mapping = {"structure": "fig(label?, caption(title, p?)?, graphic)", "child_patterns": struct["child_sequences"].get("fig", {}),
                          "label_shapes": S.get("fig label shape", {}), "graphic": "graphic@id + @xlink:href",
                          "attrib": "credit lines stay inside caption p (attrib rarely used)",
                          "panels": "(A)(B) panels stay one fig unless separately labelled (fig-group only when sub-labels)"}
        table_mapping = {"structure": "table-wrap[@specific-use=inline](label?, caption?(title), table, table-wrap-foot?(fn-group(fn(label, p))))",
                         "child_patterns": struct["child_sequences"].get("table-wrap", {}),
                         "label_shapes": S.get("table label shape", {}),
                         "cells": "th in thead, td in tbody; @align, @valign; cell text wrapped in <p id> in most books",
                         "fallback": "table-wrap(graphic) when structure cannot be recovered (evidence: 3 samples use graphic tables)"}
        list_mapping = {"list-types": S.get("list@list-type", {}), "nested": S.get("nested list@list-type", {}),
                        "label_rule": "label only for list-type=simple (vendor spec)",
                        "evidence_label": S.get("list-item has label", {}),
                        "item": "list-item(label?, p+, list?)"}
        boxed = {"content-types": S.get("boxed-text@content-type", {}), "specific-use": S.get("boxed-text@specific-use", {}),
                 "structure": "boxed-text(caption(title)?, p|list|sec|fig...)", "allowed_by_spec": [
                     "sidebar", "procedure", "guideline", "tips", "alert", "pearl", "case-study", "assessment", "care-plan", "process"]}
        reference_mapping = {"container": "book-part/back/ref-list(title, ref+)", "ref": "ref(label?, mixed-citation)",
                             "publication-type": S.get("mixed-citation@publication-type", {}),
                             "inner": struct["structures"].get("mixed-citation children", {}),
                             "rule": "tag only what is identifiable; never invent metadata"}
        citation_mapping = {"figure_words": ["Figure", "Figures", "Fig.", "Figs.", "Figura", "Figuras", "fig.", "figs.", "FIGURA", "FIGURE"],
                            "table_words": ["Table", "Tables", "Tabla", "Tablas", "Cuadro", "Cuadros", "TABLA", "TABLE"],
                            "chapter_words": ["Chapter", "Chapters", "Capítulo", "Capítulos"],
                            "section_words": ["Section", "Sección"], "box_words": ["Box", "Recuadro", "Cuadro"],
                            "number_shapes": ["#-#", "#.#", "#", "A-#", "#-#A"],
                            "observed_link_text": links["link_text_shapes"]}
        index_mapping = element_mapping["index"]
        toc_mapping = element_mapping["toc"]
        footnote_mapping = element_mapping["fn"]
        equation_mapping = {"display": "disp-formula(mml:math[@alttext])", "inline": "inline-formula(mml:math)",
                            "evidence": {k: v for k, v in S.items() if "formula" in k},
                            "fallback": "equation region image as graphic inside disp-formula is NOT allowed by spec; keep text + REVIEW flag"}
        inline_mapping = {"bold": "bold", "italic": "italic", "bold-italic": "bold(italic)", "superscript": "sup",
                          "subscript": "sub", "small-caps": "sc", "monospace": "monospace", "underline": "underline"}
        heading_mapping = {"levels": S.get("sec@disp-level", {}),
                           "rule": "cluster heading styles by (family, size, weight, caps, colour); order by prominence; "
                                   "cross-check with PDF bookmarks and printed TOC",
                           "typography_evidence": {k: {c: v["categories"].get(c) for c in v.get("categories", {}) if c.startswith(("sec_level", "chapter", "part"))}
                                                   for k, v in sig.items()}}
        structure_signatures = {k: v for k, v in sig.items()}
        paragraph_mapping = {"rule": "new <p> on first-line indent, vertical gap > 0.6 line, style change, or block end without continuation",
                             "evidence": {k: v["categories"].get("body_p") for k, v in sig.items()}}
        caption_mapping = {"figure": "label + caption/title; label text kept as printed (e.g. 'FIGURA 1-1')",
                           "table": "label + caption/title", "position": "figure captions below image; table captions above table"}
        attribute_mapping = {"table-wrap@specific-use": "inline", "book-part@book-part-type": ["chapter", "part"],
                             "list@list-type": list(S.get("list@list-type", {}).keys()),
                             "target@target-type": "pagenum", "nav-pointer@specific-use": "pagenum (TOC only)"}
        document_profiles = {
            s["isbn"]: {"lang_attr": s["lang"], "prefix": s["id_prefix"],
                        "profile": _profile(s["counts"])} for s in summ["samples"]}

        meta = {"mapping_version": MAPPING_VERSION, "tool_version": TOOL_VERSION,
                "sample_corpus_hash": corpus_hash([s["path"] for s in summ["samples"]]),
                "dtd_hash": dtd.get("dtd_hash"), "bits_version": dtd.get("bits_version"),
                "creation_date": _dt.date.today().isoformat(),
                "samples": [s["isbn"] for s in summ["samples"]]}
        files = {
            "mapping_meta.json": meta, "id_mapping.json": id_mapping, "link_mapping.json": link_mapping,
            "placement_mapping.json": placement_mapping, "element_mapping.json": element_mapping,
            "attribute_mapping.json": attribute_mapping, "structure_mapping.json": {"parent_child_top": {k: dict(Counter(v).most_common(12)) for k, v in pc.items()}},
            "heading_mapping.json": heading_mapping, "paragraph_mapping.json": paragraph_mapping,
            "list_mapping.json": list_mapping, "table_mapping.json": table_mapping, "figure_mapping.json": figure_mapping,
            "caption_mapping.json": caption_mapping, "reference_mapping.json": reference_mapping,
            "citation_mapping.json": citation_mapping, "index_mapping.json": index_mapping, "toc_mapping.json": toc_mapping,
            "footnote_mapping.json": footnote_mapping, "equation_mapping.json": equation_mapping,
            "inline_mapping.json": inline_mapping, "boxed_text_mapping.json": boxed,
            "structure_signatures.json": structure_signatures, "document_profiles.json": document_profiles,
        }
        for name, data in files.items():
            with open(self.m / name, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, ensure_ascii=False)
        placement_conf = [
            {"topic": "figure inside citing <p> vs after it", "variants": {k: v for k, v in rel.get("fig", {}).items() if "paragraph" in k or "immediately" in k},
             "cause": "citing paragraph inside list-item (cannot place fig between list items) + one book (9788418563263) nests figures in p",
             "resolution": "after citing block; inside citing p only when citation is in a list-item"},
            {"topic": "chapter-level TOC", "variants": {"books with book-part/front-matter/toc": "some"},
             "cause": "publisher convention", "resolution": "generate only when PDF shows a chapter outline"},
            {"topic": "figure after first citation vs end of section / print position",
             "variants": {"9788418257957": "43/72 figures are the last block of their section or sit at the printed page position, "
                                           "several paragraphs after the first citation"},
             "cause": "one book kept the typesetter's float position instead of the first-citation rule",
             "resolution": "first-citation rule (specification + majority of samples); regression placement score for this book is expected to be low"},
            {"topic": "golden sample with broken links", "variants": {"9788418563263": "21 nav-pointer rid -> missing page13"},
             "cause": "golden XML defect", "resolution": "excluded from link baseline; recorded"},
        ]
        conflicts.extend(placement_conf)
        with open(self.a / "mapping_conflicts.json", "w", encoding="utf-8") as fh:
            json.dump(conflicts, fh, indent=1, ensure_ascii=False)
        return meta


def _profile(c: dict) -> list[str]:
    tags = []
    if c.get("fig", 0) > 300:
        tags.append("figure-heavy")
    if c.get("table-wrap", 0) > 100:
        tags.append("table-heavy")
    if c.get("ref", 0) > 500:
        tags.append("reference-heavy")
    if c.get("index-entry", 0) > 3000:
        tags.append("index-heavy")
    if c.get("question-wrap", 0) > 20:
        tags.append("quiz")
    if c.get("boxed-text", 0) > 150:
        tags.append("box-heavy")
    return tags or ["textbook"]
