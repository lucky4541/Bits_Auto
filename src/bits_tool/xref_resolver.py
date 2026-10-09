"""Two-pass citation linking.

Pass 1 (collect): citations are detected in every inline container and indexed
by (kind, book-part, key) — no IDs yet.
Pass 2 (resolve): after placement and ID generation, every Cite item is
resolved through the TargetRegistry into an xref (fig/table/bibr/fn/chapter/
app/boxed-text). Unknown keys stay plain text and are reported
(UNRESOLVED_FIGURE_CITATION, ...). No target is ever invented or guessed.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .citation_detector import detect_citations
from .document_tree import IssueLog, Node

REF_TYPE = {"fig": "fig", "table": "table", "box": "boxed-text", "chapter": "chapter", "app": "app", "bibr": "bibr",
            "fn": "fn", "eq": "disp-formula"}
UNRESOLVED_CODE = {"fig": "UNRESOLVED_FIGURE_CITATION", "table": "UNRESOLVED_TABLE_CITATION",
                   "table-or-box": "UNRESOLVED_TABLE_CITATION", "box": "UNRESOLVED_BOX_CITATION",
                   "chapter": "UNRESOLVED_CHAPTER_CITATION", "app": "UNRESOLVED_APPENDIX_CITATION",
                   "bibr": "UNRESOLVED_BIBLIOGRAPHY_CITATION", "bibr-ay": "UNRESOLVED_BIBLIOGRAPHY_CITATION",
                   "eq": "UNRESOLVED_EQUATION_CITATION"}


def part_of(n: Node) -> Node | None:
    """The chapter-level container (chapter / appendix book-part, or a front-matter part)."""
    best = None
    for a in n.ancestors():
        if a.kind == "book-part" and a.attrs.get("book-part-type") == "chapter":
            return a
        if a.kind in ("front-matter-part", "preface", "foreword", "dedication", "ack", "index", "toc") and best is None:
            best = a
        if a.kind == "book-part" and best is None:
            best = a
    return best


def context_of(n: Node) -> str:
    kinds = [a.kind for a in n.ancestors()]
    if "ref-list" in kinds:
        return "reference"
    if "fn" in kinds or "fn-group" in kinds:
        return "footnote"
    if "table-wrap" in kinds:
        return "table"
    if "fig" in kinds:
        return "caption"
    if "boxed-text" in kinds:
        return "sidebar"
    return "body"


def inline_fields(n: Node):
    """(holder, key, ctx) for every inline list held by node n."""
    if n.kind == "p" and n.inlines:
        yield n, "inlines", context_of(n)
    elif n.kind == "sec" and n.meta.get("title"):
        yield n.meta, "title", "title"
    if n.kind in ("fig", "table-wrap") and n.meta.get("caption"):
        yield n.meta, "caption", "caption"
    if n.kind == "table-wrap":
        for row in n.meta.get("rows") or []:
            for cell in row:
                yield cell, "runs", "table"
                for b in cell.get("blocks") or []:
                    for x in b.walk():
                        if x.kind == "p" and x.inlines:
                            yield x, "inlines", "table"
        for f in n.meta.get("foot") or []:
            yield f, "runs", "table-foot"
    if n.kind == "boxed-text" and n.meta.get("title"):
        yield n.meta, "title", "sidebar-title"


class XrefResolver:
    def __init__(self, issues: IssueLog, cfg):
        self.issues = issues
        self.cfg = cfg
        self.links: list[dict] = []

    # ------------------------------------------------------------- pass 1
    def collect(self, root: Node, author_year: bool = False) -> dict[tuple, list[tuple[Node, dict]]]:
        by_target: dict[tuple, list[tuple[Node, dict]]] = defaultdict(list)
        if not self.cfg.get("links.citation_detection", True):
            return by_target
        for n in list(root.walk()):
            for holder, key, ctx in inline_fields(n):
                if ctx in ("reference",):
                    continue
                part = part_of(n)
                items = detect_citations(holder[key] if isinstance(holder, dict) else getattr(holder, key),
                                         ctx=ctx, allow_bib=self.cfg.get("links.bibliography_citations", True),
                                         author_year=author_year)
                if isinstance(holder, dict):
                    holder[key] = items
                else:
                    setattr(holder, key, items)
                for it in items:
                    if it["k"] == "cite":
                        it["_part"] = id(part) if part is not None else None
                        for k in it["keys"]:
                            kind = "table" if it["kind"] == "table-or-box" else it["kind"]
                            by_target[(kind, id(part) if part is not None else None, k)].append((n, it))
        return by_target

    # ------------------------------------------------------------- pass 2
    def resolve(self, root: Node, registry, part_ids: dict[int, str]):
        for n in root.walk():
            for holder, key, ctx in inline_fields(n):
                items = holder[key] if isinstance(holder, dict) else getattr(holder, key)
                out = []
                for it in items:
                    if it.get("k") != "cite":
                        out.append(it)
                        continue
                    res = self._resolve_one(it, registry, part_ids, n)
                    if res is None:
                        out.append({"k": "t", "text": it["text"], "st": it["st"]})
                    else:
                        out.append(res)
                if isinstance(holder, dict):
                    holder[key] = out
                else:
                    setattr(holder, key, out)

    def _scope(self, it, part_ids):
        return part_ids.get(it.get("_part"))

    def _resolve_one(self, it: dict, registry, part_ids, node: Node):
        kind = it["kind"]
        scope = self._scope(it, part_ids)
        key = it["keys"][0]
        rec = None
        rtype = None
        if kind in ("fig", "table", "box", "table-or-box"):
            order = {"fig": ["fig"], "table": ["table"], "box": ["box"], "table-or-box": ["table", "box"]}[kind]
            for k in order:
                rec = registry.lookup(k, scope, key)
                if rec is None and "-" in key and not registry.lookup(k, None, key):
                    # '1-3' in a globally numbered book: a range 1..3 -> first target
                    first = key.split("-")[0]
                    if registry.lookup(k, scope, first) and registry.lookup(k, scope, key.split("-")[-1]):
                        rec = registry.lookup(k, scope, first)
                if rec is not None:
                    rtype = REF_TYPE[k]
                    break
        elif kind == "chapter":
            rec = registry.lookup("chapter", None, key)
            rtype = "chapter"
        elif kind == "app":
            rec = registry.lookup("app", None, key)
            rtype = "app"
        elif kind == "bibr-paren":
            k0 = key.split("-")[0].split("–")[0]
            rec = registry.targets.get(("bibr", scope, k0))      # same chapter only, never a global guess
            rtype = "bibr"
            if rec is None:
                return None                  # '(2)' with no such reference: plain text, nothing to report
        elif kind in ("bibr", "bibr-or-fn", "fn"):
            k0 = key.split("-")[0].split("–")[0]
            if kind in ("fn", "bibr-or-fn"):
                rec = registry.lookup("fn", scope, key)
                rtype = "fn"
            if rec is None and kind in ("bibr", "bibr-or-fn"):
                rec = registry.lookup("bibr", scope, k0)
                rtype = "bibr"
            if rec is None and kind == "bibr-or-fn":
                return None                  # plain superscript number without a target: keep text, no report
        elif kind == "bibr-ay":
            rec = registry.lookup("bibr-ay", scope, key.lower())
            rtype = "bibr"
        elif kind == "eq":
            rec = registry.lookup("eq", scope, key)
            rtype = "disp-formula"
        ctx = it.get("ctx")
        if rec is None:
            code = UNRESOLVED_CODE.get(kind)
            if code and ctx not in ("toc", "index"):
                sev = "warning" if kind in ("fig", "table", "table-or-box") else "info"
                self.issues.add(code, sev, f"'{it['text']}' -> no target {kind}:{key}", page=node.page)
            self.links.append({"kind": kind, "key": key, "text": it["text"], "status": "unresolved", "page": node.page, "ctx": ctx})
            return None
        registry.add_incoming(rec["id"], {"text": it["text"], "page": node.page, "ctx": ctx, "source": node.id})
        self.links.append({"kind": kind, "key": key, "text": it["text"], "status": "resolved", "rid": rec["id"],
                           "ref_type": rtype, "page": node.page, "ctx": ctx, "source": node.id})
        return {"k": "xref", "ref-type": rtype, "rid": rec["id"], "text": it["text"], "st": it["st"]}


def author_year_key(text: str) -> list[str]:
    """Keys 'surname|year' for a reference text (first author surname + year)."""
    m = re.match(r"^\s*([A-ZÁÉÍÓÚÑ][\w'’\-]+)", text)
    ys = re.findall(r"\b((?:19|20)\d{2}[a-z]?)\b", text)
    if not m or not ys:
        return []
    return [f"{m.group(1).lower()}|{ys[0].lower()}"]
