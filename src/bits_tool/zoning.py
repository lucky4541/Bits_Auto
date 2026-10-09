"""Zoning: the editable zone hierarchy between PDF analysis and BITS XML.

The zone tree IS the semantic Node tree. Every zone knows the PDF lines it owns
(`meta["_ln"]`, stable line uids "page:k"), so it can be drawn on the page,
edited, and rebuilt from the PDF text. XML is always generated from the zones:

    PDF -> analysis (auto zones, or an empty skeleton for manual zoning)
        -> zones.json  (operator reviews / edits in the Zoning screen)
        -> rebuild edited zones from their lines -> finish pipeline -> BITS XML

Nothing in a zone is typed by hand: text always comes from the PDF lines, so a
zoning edit can change structure but cannot lose or invent content.
"""
from __future__ import annotations

import copy
import gzip
import json
import pickle
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .caption_detector import label_match, number_key
from .document_tree import IssueLog, Line, Node, PageMark, Region, T
from .equation_detector import linearize, reconstruct_math
from .list_detector import marker, strip_marker_from_runs
from .paragraph_detector import append_line, despace_runs, finish_inlines, line_runs
from .reference_parser import parse_citation

ZONE_VERSION = 1
CONTENT_ROLES = ("body", "caption", "figure-text", "equation-text", "table-text", "table-foot", "box-text", "footnote")
TEXT_KINDS = ("p", "sec", "disp-formula", "ref", "fn")
REGION_KINDS = ("fig", "table-wrap", "boxed-text")
SKIP_META = ("copyright_lines", "title_lines")


def _nolog(*a, **k):
    return None


# ------------------------------------------------------------------ line ids

def assign_line_uids(pages) -> None:
    for p in pages:
        for k, l in enumerate(p.lines):
            l.uid = f"{p.index}:{k}"


def line_index(pages) -> dict[str, Line]:
    return {l.uid: l for p in pages for l in p.lines if l.uid}


def line_sort_key(l: Line):
    return (l.page, l.order if l.order >= 0 else 10_000, l.y0, l.x0)


# ----------------------------------------------------------------- ownership

def _runs_ln(runs, out: list, seen: set):
    for r in runs or []:
        if isinstance(r, dict):
            for u in r.get("ln") or []:
                if u not in seen:
                    seen.add(u)
                    out.append(u)


def own_lines_from_payload(n: Node) -> list[str]:
    """Line uids carried by the node's own text (not its children)."""
    out, seen = [], set()
    _runs_ln(n.inlines, out, seen)
    m = n.meta
    for u in m.get("_head_ln") or []:
        if u not in seen:
            seen.add(u)
            out.append(u)
    for key in ("title", "subtitle", "caption", "runs"):
        v = m.get(key)
        if isinstance(v, list):
            _runs_ln(v, out, seen)
    for f in m.get("foot") or []:
        _runs_ln(f.get("runs"), out, seen)
    for row in m.get("rows") or []:
        for c in row:
            _runs_ln(c.get("runs"), out, seen)
            for b in c.get("blocks") or []:
                for x in b.walk():
                    _runs_ln(x.inlines, out, seen)
    for o in m.get("outline") or []:
        _runs_ln(o.get("runs"), out, seen)
    for d in m.get("intro") or []:
        _runs_ln(d, out, seen)
    return out


def _center_in(l: Line, b, pad=2.0) -> bool:
    cx, cy = (l.x0 + l.x1) / 2, (l.y0 + l.y1) / 2
    return b[0] - pad <= cx <= b[2] + pad and b[1] - pad <= cy <= b[3] + pad


def attach_ownership(root: Node, pages) -> None:
    """Give every node a zone id and the PDF lines it owns."""
    owner: dict[str, Node] = {}
    by_page = {p.index: p for p in pages}
    zc = 0
    for n in root.walk():
        zc += 1
        n.meta["zid"] = f"z{zc}"
        lns = [u for u in own_lines_from_payload(n) if u not in owner]
        for u in lns:
            owner[u] = n
        n.meta["_ln"] = lns
    # regions: lines inside the frame that no text zone took (art text, failed tables)
    for n in root.walk():
        if n.kind in ("fig", "table-wrap") and n.page is not None and n.bbox:
            pg = by_page.get(n.page)
            if pg is None:
                continue
            for l in pg.lines:
                if l.uid and l.uid not in owner and l.role in CONTENT_ROLES and _center_in(l, n.bbox):
                    owner[l.uid] = n
                    n.meta["_ln"].append(l.uid)
    # toc / index: remaining body lines of their page range
    tops = _top_parts(root)
    for i, n in enumerate(tops):
        if n.kind not in ("toc", "index") or n.page is None:
            continue
        nxt = next((t.page for t in tops[i + 1:] if t.page is not None and t.page > n.page), None)
        end = (nxt - 1) if nxt is not None else max(by_page)
        for gi in range(n.page, end + 1):
            pg = by_page.get(gi)
            for l in (pg.lines if pg else []):
                if l.uid and l.uid not in owner and l.role in CONTENT_ROLES:
                    owner[l.uid] = n
                    n.meta["_ln"].append(l.uid)
    root.meta["next_zid"] = zc + 1


def _top_parts(root: Node) -> list[Node]:
    out = []
    for c in root.children:
        if c.kind in ("front-matter", "book-body", "book-back"):
            for x in c.children:
                out.append(x)
                if x.kind == "book-part" and x.attrs.get("book-part-type") == "part":
                    b = next((y for y in x.children if y.kind == "body"), None)
                    out.extend(b.children if b else [])
        else:
            out.append(c)
    return [o for o in out if o.kind not in ("body",)]


# ------------------------------------------------------------- serialisation

def _enc(v):
    if isinstance(v, Node):
        return {"__node__": node_to_json(v)}
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items() if not isinstance(x, Line)}
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v if not isinstance(x, Line)]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _dec(v):
    if isinstance(v, dict):
        if "__node__" in v and len(v) == 1:
            return node_from_json(v["__node__"])
        return {k: _dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def node_to_json(n: Node) -> dict:
    d = {"kind": n.kind}
    if n.attrs:
        d["attrs"] = dict(n.attrs)
    if n.inlines:
        d["inlines"] = _enc(n.inlines)
    if n.page is not None:
        d["page"] = n.page
    if n.bbox:
        d["bbox"] = [round(x, 2) for x in n.bbox]
    if n.conf != 1.0:
        d["conf"] = n.conf
    meta = {k: v for k, v in n.meta.items() if k not in SKIP_META}
    if meta:
        d["meta"] = _enc(meta)
    if n.flags:
        d["flags"] = list(n.flags)
    if n.id:
        d["id"] = n.id
    if n.children:
        d["children"] = [node_to_json(c) for c in n.children]
    return d


def node_from_json(d: dict) -> Node:
    n = Node(d["kind"], dict(d.get("attrs") or {}), [], _dec(d.get("inlines") or []), d.get("page"),
             tuple(d["bbox"]) if d.get("bbox") else None, d.get("conf", 1.0), _dec(d.get("meta") or {}),
             d.get("id"), list(d.get("flags") or []))
    for c in d.get("children") or []:
        n.add(node_from_json(c))
    for key in ("art", "fallback_image", "body_bbox", "rect"):
        if isinstance(n.meta.get(key), list):
            n.meta[key] = tuple(n.meta[key])
    return n


# ------------------------------------------------------------------ project

@dataclass
class ZoneProject:
    """output/<book>/zoning/: state.pkl.gz (analysis), zones.json (the editable tree), project.json."""
    dir: Path
    state: object               # converter.AnalysisState (root excluded)
    root: Node
    info: dict

    @staticmethod
    def dir_for(out_dir: Path) -> Path:
        return Path(out_dir) / "zoning"

    @classmethod
    def create(cls, st, mode: str = "auto") -> "ZoneProject":
        info = {"version": ZONE_VERSION, "book": st.name, "mode": mode, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "input": str(st.book_path), "edits": 0, "pages": len(st.pages)}
        return cls(cls.dir_for(st.out_dir), st, st.root, info)

    @classmethod
    def exists(cls, out_dir: Path) -> bool:
        d = cls.dir_for(out_dir)
        return (d / "zones.json").exists() and (d / "state.pkl.gz").exists()

    @classmethod
    def load(cls, out_dir: Path) -> "ZoneProject":
        d = cls.dir_for(out_dir)
        with gzip.open(d / "state.pkl.gz", "rb") as fh:
            st = pickle.load(fh)
        for page in st.pages:
            if not hasattr(page, "diagnostics"):
                page.diagnostics = []
            if not hasattr(page, "tables"):
                page.tables = []
        data = json.loads((d / "zones.json").read_text(encoding="utf-8"))
        root = node_from_json(data["tree"])
        info = data.get("info", {})
        st.root = root
        st.out_dir = Path(out_dir)
        st.logs = {}
        return cls(d, st, root, info)

    def backup_existing(self) -> Path | None:
        """Before an automatic re-conversion replaces the zones: keep hand-edited zones in zoning/backups/<time>/."""
        zj = self.dir / "zones.json"
        if not zj.exists():
            return None
        try:
            head = zj.read_text(encoding="utf-8")[:4000].split(',"tree"')[0] + "}"
            edits = json.loads(head).get("info", {}).get("edits", 0)
        except Exception:
            edits = 1
        if not edits:
            return None
        import shutil
        dst = self.dir / "backups" / time.strftime("%Y%m%d-%H%M%S")
        dst.mkdir(parents=True, exist_ok=True)
        for f in ("zones.json", "state.pkl.gz"):
            if (self.dir / f).exists():
                shutil.copy2(self.dir / f, dst / f)
        return dst

    def save(self, tree_only: bool = False):
        self.dir.mkdir(parents=True, exist_ok=True)
        if not tree_only or not (self.dir / "state.pkl.gz").exists():
            st = self.state
            keep = (st.logs, st.root, st.builder.log, st.builder.progress)
            st.logs, st.root = {}, None
            st.builder.log = _nolog
            st.builder.progress = _nolog
            try:
                tmp = self.dir / "state.pkl.gz.tmp"
                with gzip.open(tmp, "wb", compresslevel=3) as fh:
                    pickle.dump(st, fh, protocol=pickle.HIGHEST_PROTOCOL)
                tmp.replace(self.dir / "state.pkl.gz")
            finally:
                st.logs, st.root, st.builder.log, st.builder.progress = keep
        self.info["saved"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = self.dir / "zones.json.tmp"
        tmp.write_text(json.dumps({"info": self.info, "tree": node_to_json(self.root)}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / "zones.json")


# --------------------------------------------------------------- the editor

class ZoneEditor:
    """All zone operations. Every operation is undoable and marks edited zones dirty;
    `prepare_for_xml()` rebuilds dirty zones from their PDF lines."""

    def __init__(self, project: ZoneProject, cfg=None):
        self.project = project
        self.cfg = cfg
        self.root = project.root
        self.st = project.state
        self.builder = self.st.builder
        self.pages = self.st.pages
        self.page_by = {p.index: p for p in self.pages}
        self.lines = line_index(self.pages)
        self.undo_stack: list[tuple[str, str]] = []
        self.redo_stack: list[tuple[str, str]] = []
        self.dirty_file = False
        self.reindex()

    # ---------------------------------------------------------- indexing
    def reindex(self):
        self._pidx = None
        self.root.fix_parents()
        scope = getattr(self, "_last_scope", None)
        if scope is not None:
            self._last_scope = None
            self.normalize(scope)
            self.root.fix_parents()
        self.by_zid: dict[str, Node] = {}
        self.owner: dict[str, Node] = {}
        nz = self.root.meta.get("next_zid", 1)
        for n in self.root.walk():
            z = n.meta.get("zid")
            if not z or z in self.by_zid:
                z = f"z{nz}"
                nz += 1
                n.meta["zid"] = z
            self.by_zid[z] = n
            for u in n.meta.get("_ln") or []:
                self.owner.setdefault(u, n)
        self.root.meta["next_zid"] = nz

    def new_zid(self) -> str:
        z = self.root.meta.get("next_zid", 1)
        self.root.meta["next_zid"] = z + 1
        return f"z{z}"

    def node(self, zid) -> Node | None:
        return self.by_zid.get(zid)

    def zone_lines(self, n: Node) -> list[Line]:
        ls = [self.lines[u] for u in n.meta.get("_ln") or [] if u in self.lines]
        return ls

    def zone_boxes(self, n: Node) -> dict[int, list[tuple]]:
        """page -> rectangles (one per column run of lines) for drawing."""
        out: dict[int, list[tuple]] = {}
        if n.kind in REGION_KINDS and n.meta.get("rect"):
            pg, *b = n.meta["rect"]
            out.setdefault(pg, []).append(tuple(b))
        elif n.kind in ("fig", "table-wrap", "boxed-text") and n.bbox and n.page is not None:
            out.setdefault(n.page, []).append(tuple(n.bbox))
        groups: dict[tuple, list] = {}
        for l in sorted(self.zone_lines(n), key=line_sort_key):
            if n.kind in ("fig", "table-wrap", "boxed-text") and n.bbox and l.page == n.page and _center_in(l, n.bbox):
                continue
            key = (l.page, l.column)
            g = groups.setdefault(key, [])
            if g and l.y0 - g[-1][3] > 3 * max(l.height, 6):
                groups[(l.page, l.column, len(groups))] = g = []
            g.append(l.bbox)
        for key, bbs in groups.items():
            if bbs:
                out.setdefault(key[0], []).append((min(b[0] for b in bbs), min(b[1] for b in bbs),
                                                   max(b[2] for b in bbs), max(b[3] for b in bbs)))
        return out

    def _page_index(self) -> dict[int, list[Node]]:
        if getattr(self, "_pidx", None) is None:
            idx: dict[int, list[Node]] = {}
            for n in self.root.walk():
                if n.kind in ("book", "front-matter", "book-body", "book-back", "body", "back", "named-book-part-body",
                              "list", "ref-list", "fn-group"):
                    continue
                pages = set()
                for u in n.meta.get("_ln") or []:
                    l = self.lines.get(u)
                    if l is not None:
                        pages.add(l.page)
                if n.kind in REGION_KINDS and n.page is not None and (n.bbox or n.meta.get("rect")):
                    pages.add(n.meta["rect"][0] if n.meta.get("rect") else n.page)
                for pg in pages:
                    idx.setdefault(pg, []).append(n)
            self._pidx = idx
        return self._pidx

    def page_zones(self, page_index: int) -> list[tuple[Node, tuple]]:
        """Zones with a box on this page (containers such as boxes are drawn as frames)."""
        out = []
        for n in self._page_index().get(page_index, []):
            for b in self.zone_boxes(n).get(page_index, []):
                out.append((n, b))
        return out

    def page_lines(self, page_index: int) -> list[Line]:
        pg = self.page_by.get(page_index)
        return [l for l in (pg.lines if pg else []) if l.uid and l.role in CONTENT_ROLES and l.text.strip()]

    def unzoned_lines(self, page_index: int) -> list[Line]:
        pg = self.page_by.get(page_index)
        if not pg:
            return []
        return [l for l in pg.lines if l.uid and l.uid not in self.owner and l.role in CONTENT_ROLES and l.text.strip()]

    def lines_in_rect(self, page_index: int, rect) -> list[Line]:
        pg = self.page_by.get(page_index)
        if not pg:
            return []
        return sorted([l for l in pg.lines if l.uid and l.role in CONTENT_ROLES and _center_in(l, rect, 0.5) and l.text.strip()],
                      key=line_sort_key)

    # --------------------------------------------------------- undo/redo
    def _scope(self, nodes) -> Node:
        """Smallest top-level container covering all edited nodes (chapter-sized snapshots)."""
        chains = []
        for n in nodes:
            ch = [n] + list(n.ancestors())
            chains.append(ch[::-1])
        common = self.root
        for i in range(min(len(c) for c in chains)):
            s = {id(c[i]) for c in chains}
            if len(s) == 1:
                common = chains[0][i]
            else:
                break
        # climb to a container that survives the edit
        while common.parent is not None and common.kind not in ("book-part", "front-matter-part", "preface", "foreword",
                                                                 "dedication", "ack", "book-body", "front-matter",
                                                                 "book-back", "book", "toc", "index"):
            common = common.parent
        if common.parent is not None and common.kind in ("book-part",):
            common = common.parent if any(n is common for n in nodes) else common
        return common

    def _path(self, n: Node) -> list[int]:
        p = []
        while n.parent is not None:
            p.append(n.parent.children.index(n))
            n = n.parent
        return p[::-1]

    def _at(self, path) -> Node:
        n = self.root
        for i in path:
            n = n.children[i]
        return n

    def normalize(self, scope: Node):
        """Keep the DTD's section model: blocks may not follow a sub-section, so re-nest such flows by level."""
        tail_ok = ("notes", "fn-group", "glossary", "ref-list", "sig-block", "sec")
        for c in [x for x in scope.walk() if x.kind in ("body", "named-book-part-body", "back", "app", "boxed-text", "sec")]:
            bad = False
            for x in c.walk():
                if x.kind in ("body", "named-book-part-body", "sec", "boxed-text"):
                    seen_sec = False
                    for ch in x.children:
                        if ch.kind == "sec":
                            seen_sec = True
                        elif seen_sec and ch.kind not in tail_ok:
                            bad = True
                            break
                if bad:
                    break
            if bad:
                top = c
                while top.kind == "sec" and top.parent is not None:
                    top = top.parent
                self.renest(top)

    def snapshot(self, label: str, nodes):
        scope = self._scope([n for n in nodes if n is not None] or [self.root])
        self._last_scope = scope
        path = self._path(scope)
        self.undo_stack.append((label, json.dumps({"path": path, "tree": node_to_json(scope),
                                                   "next": self.root.meta.get("next_zid")})))
        self.undo_stack = self.undo_stack[-60:]
        self.redo_stack.clear()
        self.dirty_file = True
        self.project.info["edits"] = self.project.info.get("edits", 0) + 1

    def _restore(self, blob: str) -> str:
        d = json.loads(blob)
        path = d["path"]
        cur = self._at(path)
        current = json.dumps({"path": path, "tree": node_to_json(cur), "next": self.root.meta.get("next_zid")})
        new = node_from_json(d["tree"])
        if not path:
            new.meta["next_zid"] = max(d.get("next") or 1, self.root.meta.get("next_zid", 1))
            self.root = self.project.root = new
            self.st.root = new
        else:
            par = cur.parent
            i = par.children.index(cur)
            par.children[i] = new
            new.parent = par
        self.reindex()
        self.dirty_file = True
        return current

    def undo(self) -> str | None:
        if not self.undo_stack:
            return None
        label, blob = self.undo_stack.pop()
        cur = self._restore(blob)
        self.redo_stack.append((label, cur))
        return label

    def redo(self) -> str | None:
        if not self.redo_stack:
            return None
        label, blob = self.redo_stack.pop()
        cur = self._restore(blob)
        self.undo_stack.append((label, cur))
        return label

    # --------------------------------------------------------- helpers
    def _mark(self, n: Node):
        n.meta["dirty"] = True
        n.meta["src"] = n.meta.get("src") or "edited"

    def _new(self, kind, attrs=None, lines=None, **kw) -> Node:
        n = Node(kind, dict(attrs or {}), **kw)
        n.meta["zid"] = self.new_zid()
        n.meta["_ln"] = [l.uid for l in sorted(lines or [], key=line_sort_key)]
        n.meta["src"] = "manual"
        n.meta["dirty"] = True
        if lines:
            n.page = min(l.page for l in lines)
        return n

    def _take(self, lines: list[Line], keep: Node | None = None):
        """Remove lines from their current owners (owners left empty disappear)."""
        uids = {l.uid for l in lines}
        touched = []
        for l in lines:
            o = self.owner.get(l.uid)
            if o is None or o is keep:
                continue
            if o not in touched:
                touched.append(o)
        for o in touched:
            o.meta["_ln"] = [u for u in o.meta.get("_ln") or [] if u not in uids]
            self._mark(o)
            if not o.meta["_ln"] and o.kind in TEXT_KINDS + ("list-item",) and not any(c.kind not in ("inline-graphic",) for c in o.children) \
                    and o.parent is not None and o.kind != "sec":
                self._remove(o)
            elif o.kind == "sec" and not o.meta["_ln"]:
                self._dissolve(o)

    def _remove(self, n: Node):
        par = n.parent
        if par is None:
            return
        par.remove(n)
        # empty wrappers go too
        while par is not None and par.kind in ("list", "list-item", "ref-list", "fn-group", "fn", "boxed-text") \
                and not par.children and par.parent is not None:
            gp = par.parent
            gp.remove(par)
            par = gp

    def _dissolve(self, sec: Node) -> Node | None:
        """sec -> its title becomes a paragraph, its children move up."""
        par = sec.parent
        i = par.children.index(sec)
        par.remove(sec)
        new_nodes = []
        if sec.meta.get("_ln"):
            p = self._new("p", lines=[self.lines[u] for u in sec.meta["_ln"] if u in self.lines])
            new_nodes.append(p)
        for c in list(sec.children):
            sec.children.remove(c)
            new_nodes.append(c)
        for k, c in enumerate(new_nodes):
            par.insert(i + k, c)
        return new_nodes[0] if new_nodes else None

    def _container_for_flow(self, n: Node) -> Node:
        c = n.parent
        while c is not None and c.kind == "sec":
            c = c.parent
        return c

    def renest(self, container: Node):
        """Rebuild section nesting inside a body from the disp-level of every sec (flatten + re-nest)."""
        flat = []

        def walk(nodes):
            for x in nodes:
                if x.kind == "sec":
                    kids = list(x.children)
                    x.children = []
                    flat.append(("sec", x))
                    walk(kids)
                else:
                    flat.append(("b", x))
        kids = list(container.children)
        container.children = []
        walk(kids)
        stack: list[tuple[int, Node]] = []
        for k, x in flat:
            if k == "sec":
                lvl = int(x.attrs.get("disp-level") or 1)
                while stack and stack[-1][0] >= lvl:
                    stack.pop()
                (stack[-1][1] if stack else container).add(x)
                stack.append((lvl, x))
            else:
                (stack[-1][1] if stack else container).add(x)
        container.fix_parents()

    # --------------------------------------------------------- operations
    def retag(self, zids: list[str], kind: str, **opt) -> list[Node]:
        nodes = [self.by_zid[z] for z in zids if z in self.by_zid]
        if not nodes:
            return []
        nodes = self._siblings_in_order(nodes)
        self.snapshot(f"tag {kind}", nodes)
        out = []
        if kind in ("boxed-text",):
            out = [self._wrap(nodes, "boxed-text", opt)]
        elif kind in ("fig", "table-wrap"):
            out = [self._to_region(nodes, kind)]
        elif kind in ("chapter", "appendix", "part"):
            out = [self._new_chapter(nodes[0], {**opt, "part_type": kind})]
        elif kind == "part-title":
            out = [self._set_part_title(nodes)]
        elif kind in ("toc", "index"):
            out = [self._to_matter(nodes, kind)]
        else:
            for n in nodes:
                r = self._retag_one(n, kind, opt)
                if r is not None:
                    out.append(r)
        self.reindex()
        return out

    def _siblings_in_order(self, nodes):
        def key(n):
            ls = self.zone_lines(n)
            return min((line_sort_key(l) for l in ls), default=(n.page or 0, 0, 0, 0))
        return sorted(nodes, key=key)

    def _text_lines(self, n: Node) -> list[Line]:
        ls = self.zone_lines(n)
        for c in n.walk():
            if c is not n and c.kind in ("p",):
                ls += self.zone_lines(c)
        return sorted({l.uid: l for l in ls}.values(), key=line_sort_key)

    def _replace(self, old: Node, new: Node):
        par = old.parent
        i = par.children.index(old)
        par.children[i] = new
        new.parent = par
        for c in list(old.children):
            if c.kind in ("inline-graphic", "fig", "table-wrap", "disp-formula") and new.kind == "p":
                new.add(c)

    def _retag_one(self, n: Node, kind: str, opt) -> Node | None:
        lines = self._text_lines(n) if n.kind == "list-item" else self.zone_lines(n)
        if n.kind == "list-item":
            src = n
        elif n.kind == "p" and n.parent is not None and n.parent.kind == "list-item" and kind != "list-item":
            src = n.parent
            lines = self._text_lines(src)
        else:
            src = n
        if kind == "sec":
            lvl = int(opt.get("level") or 1)
            if src.kind == "sec":
                src.attrs["disp-level"] = str(lvl)
                self._mark(src)
                self.renest(self._container_for_flow(src))
                return src
            sec = self._new("sec", {"disp-level": str(lvl)}, lines)
            cont = self._container_for_flow(src)
            self._unlist(src, sec)
            self.renest(cont)
            return sec
        if kind == "p":
            if src.kind == "sec":
                return self._dissolve(src)
            p = self._new("p", lines=lines)
            self._unlist(src, p)
            return p
        if kind == "list-item":
            if src.kind == "list-item":
                return src
            ltype = opt.get("list_type")
            mk = marker(lines[0].text) if lines else None
            ltype = ltype or (mk[0] if mk else "bullet")
            item = self._new("list-item")
            item.meta["_ln"] = []
            p = self._new("p", lines=lines)
            p.meta["strip_marker"] = True
            item.add(p)
            target = self._dissolve(src) if src.kind == "sec" else src
            if target is None:
                return None
            par = target.parent
            i = par.children.index(target)
            prev = par.children[i - 1] if i > 0 else None
            par.remove(target)
            if prev is not None and prev.kind == "list" and prev.attrs.get("list-type") == ltype:
                prev.add(item)
                nxt = par.children[i] if i < len(par.children) else None
                if nxt is not None and nxt.kind == "list" and nxt.attrs.get("list-type") == ltype:
                    for c in list(nxt.children):
                        prev.add(c)
                    par.remove(nxt)
            else:
                lst = self._new("list", {"list-type": ltype})
                lst.meta["_ln"] = []
                lst.add(item)
                par.insert(i, lst)
            return item
        if kind in ("disp-formula", "ref", "fn"):
            new = self._new(kind, lines=lines)
            if kind == "fn":
                new.meta["label"] = opt.get("label")
            self._unlist(src, new)
            if kind == "ref" and new.parent.kind != "ref-list":
                par = new.parent
                i = par.children.index(new)
                prev = par.children[i - 1] if i > 0 else None
                par.remove(new)
                if prev is not None and prev.kind == "ref-list":
                    prev.add(new)
                else:
                    rl = self._new("ref-list")
                    rl.meta["_ln"] = []
                    rl.meta["title"] = []
                    rl.add(new)
                    par.insert(i, rl)
            return new
        return None

    def _unlist(self, src: Node, new: Node):
        """Put `new` where `src` was; a list item leaves its list (the list is split around it)."""
        if src.kind == "list-item" and src.parent is not None and src.parent.kind == "list":
            lst = src.parent
            par = lst.parent
            i = lst.children.index(src)
            after = lst.children[i + 1:]
            lst.children = lst.children[:i]
            li = par.children.index(lst)
            par.insert(li + 1, new)
            if after:
                tail = self._new("list", dict(lst.attrs))
                tail.meta["_ln"] = []
                for c in after:
                    tail.add(c)
                par.insert(li + 2, tail)
            if not lst.children:
                par.remove(lst)
            for c in src.children:
                if c.kind in ("list",):
                    j = par.children.index(new)
                    par.insert(j + 1, c)
            return
        if src.kind == "sec":
            par = src.parent
            i = par.children.index(src)
            par.insert(i, new)
            par.remove(src)
            for k, c in enumerate(list(src.children)):
                par.insert(i + 1 + k, c)
            return
        self._replace(src, new)

    def _wrap(self, nodes, kind, opt) -> Node:
        first = nodes[0]
        par = first.parent
        sib = [n for n in nodes if n.parent is par]
        i = min(par.children.index(n) for n in sib)
        box = self._new(kind, {"content-type": opt.get("content_type")} if opt.get("content_type") else {})
        box.meta["_ln"] = []
        for n in sib:
            par.remove(n)
            box.add(n)
        par.insert(i, box)
        if opt.get("title_from_first") and box.children and box.children[0].kind == "p":
            t = box.children[0]
            box.meta["_ln"] = list(t.meta.get("_ln") or [])
            box.meta["title_is_lines"] = True
            box.remove(t)
        self._mark(box)
        return box

    def unwrap(self, zid: str) -> list[Node]:
        n = self.by_zid.get(zid)
        if n is None or n.parent is None:
            return []
        self.snapshot("unwrap", [n])
        par = n.parent
        i = par.children.index(n)
        out = []
        if n.kind == "sec":
            p = self._dissolve(n)
            out = [p] if p else []
        else:
            if n.meta.get("_ln") and n.kind in ("boxed-text",):
                p = self._new("p", lines=self.zone_lines(n))
                par.insert(i, p)
                i += 1
                out.append(p)
            par.remove(n)
            for k, c in enumerate(list(n.children)):
                if n.kind == "list" and c.kind == "list-item":
                    for j, cc in enumerate(list(c.children)):
                        par.insert(i, cc)
                        i += 1
                        out.append(cc)
                    continue
                par.insert(i, c)
                i += 1
                out.append(c)
        self.reindex()
        return out

    def _to_region(self, nodes, kind) -> Node:
        lines = sorted({l.uid: l for n in nodes for l in self._text_lines(n)}.values(), key=line_sort_key)
        pg = lines[0].page if lines else nodes[0].page
        on_page = [l for l in lines if l.page == pg]
        rect = (min(l.x0 for l in on_page), min(l.y0 for l in on_page), max(l.x1 for l in on_page), max(l.y1 for l in on_page)) \
            if on_page else tuple(nodes[0].bbox)
        reg = self._new(kind, lines=lines)
        reg.page = pg
        reg.bbox = rect
        reg.meta["rect"] = (pg, *rect)
        par = nodes[0].parent
        i = par.children.index(nodes[0])
        par.insert(i, reg)
        for n in nodes:
            if n.parent is not None:
                if n.kind == "p" and n.parent.kind == "list-item":
                    self._remove(n)
                else:
                    n.parent.remove(n)
        return reg

    def _new_chapter(self, title_zone: Node, opt) -> Node:
        """The zone becomes the title of a new chapter; following zones of its body move into it."""
        lines = self._text_lines(title_zone)
        # find the body it lives in and the chapter around it
        body = title_zone.parent
        while body is not None and body.kind not in ("body", "named-book-part-body", "book-body", "book-back"):
            body = body.parent
        chapter = body.parent if body is not None and body.kind == "body" else None
        ptype = opt.get("part_type", "chapter")
        new = self._new("book-part", {"book-part-type": "part" if ptype == "part" else "chapter"})
        new.meta["_ln"] = [l.uid for l in lines]
        new.meta["title_from_lines"] = True
        new.meta["appendix"] = ptype == "appendix"
        new.page = lines[0].page if lines else title_zone.page
        newbody = Node("body", meta={"zid": self.new_zid(), "_ln": []})
        new.add(newbody)
        # move: everything after the title zone (in its flow container) into the new chapter
        top = title_zone
        while top.parent is not body:
            top = top.parent
        idx = body.children.index(top)
        moving = body.children[idx + 1:]
        # the title zone itself is consumed (its lines go to the chapter head)
        if top is title_zone:
            body.children = body.children[:idx]
        else:
            body.children = body.children[:idx + 1]
            self._remove(title_zone)
        for m in moving:
            newbody.add(m)
        if chapter is not None and chapter.kind == "book-part":
            container = chapter.parent
            ci = container.children.index(chapter)
            container.insert(ci + 1, new)
            if ptype == "appendix" and container.kind != "book-back":
                container.remove(new)
                bb = self._book_back()
                bb.add(new)
        else:
            body.add(new) if body.kind in ("book-body", "book-back") else body.parent.insert(body.parent.children.index(body) + 1, new)
        self._mark(new)
        return new

    def _set_part_title(self, nodes) -> Node | None:
        bp = nodes[0].ancestor("book-part", "front-matter-part", "preface", "foreword", "dedication", "ack")
        if bp is None:
            return None
        lines = sorted({l.uid: l for n in nodes for l in self._text_lines(n)}.values(), key=line_sort_key)
        old = [self.lines[u] for u in bp.meta.get("_ln") or [] if u in self.lines]
        bp.meta["_ln"] = [l.uid for l in lines]
        bp.meta["title_from_lines"] = True
        for n in nodes:
            if n.parent is not None:
                self._remove(n) if not (n.kind == "sec") else self._dissolve_keep_children(n)
        if old:      # the previous title text is not lost: it becomes a paragraph at the top of the body
            body = next((c for c in bp.children if c.kind in ("body", "named-book-part-body")), None)
            if body is not None:
                body.insert(0, self._new("p", lines=old))
        self._mark(bp)
        return bp

    def _dissolve_keep_children(self, sec: Node):
        par = sec.parent
        i = par.children.index(sec)
        par.remove(sec)
        for k, c in enumerate(list(sec.children)):
            par.insert(i + k, c)

    def _to_matter(self, nodes, kind) -> Node:
        lines = sorted({l.uid: l for n in nodes for l in self._text_lines(n)}.values(), key=line_sort_key)
        new = self._new(kind, lines=lines)
        for n in nodes:
            if n.parent is not None:
                self._remove(n)
        if kind == "toc":
            fm = next((c for c in self.root.children if c.kind == "front-matter"), None)
            if fm is None:
                fm = Node("front-matter", meta={"zid": self.new_zid(), "_ln": []})
                self.root.insert(0, fm)
            fm.add(new)
        else:
            self._book_back().add(new)
        return new

    def _book_back(self) -> Node:
        bb = next((c for c in self.root.children if c.kind == "book-back"), None)
        if bb is None:
            bb = self.root.add(Node("book-back", meta={"zid": self.new_zid(), "_ln": []}))
        return bb

    def set_level(self, zid: str, level: int):
        n = self.by_zid.get(zid)
        if n is None or n.kind != "sec":
            return
        self.snapshot("level", [n])
        n.attrs["disp-level"] = str(max(1, min(9, level)))
        self.renest(self._container_for_flow(n))
        self.reindex()

    def merge(self, zids: list[str]) -> Node | None:
        nodes = self._siblings_in_order([self.by_zid[z] for z in zids if z in self.by_zid])
        if len(nodes) < 2:
            return None
        self.snapshot("merge", nodes)
        first = nodes[0]
        if first.kind == "list-item":
            tgt = next((c for c in first.children if c.kind == "p"), first)
        else:
            tgt = first
        for n in nodes[1:]:
            ls = self._text_lines(n) if n.kind in ("list-item", "boxed-text") else self.zone_lines(n)
            tgt.meta["_ln"] = (tgt.meta.get("_ln") or []) + [l.uid for l in ls if l.uid not in (tgt.meta.get("_ln") or [])]
            for c in list(n.children):
                if c.kind in ("inline-graphic", "fig", "table-wrap", "disp-formula"):
                    tgt.add(c)
            if n.kind == "p" and n.parent is not None and n.parent.kind == "list-item":
                self._remove(n)
            elif n.parent is not None:
                if n.kind == "sec":
                    self._dissolve(n)
                    for x in list(self.root.walk()):
                        if x.kind == "p" and x.meta.get("_ln") and set(x.meta["_ln"]) <= set(tgt.meta["_ln"]) and x is not tgt:
                            self._remove(x)
                else:
                    self._remove(n)
        tgt.meta["_ln"] = [l.uid for l in sorted((self.lines[u] for u in tgt.meta["_ln"] if u in self.lines), key=line_sort_key)]
        self._mark(tgt)
        self.reindex()
        return tgt

    def split(self, zid: str, at_uid: str) -> Node | None:
        n = self.by_zid.get(zid)
        if n is None:
            return None
        lns = n.meta.get("_ln") or []
        if at_uid not in lns or lns.index(at_uid) == 0:
            return None
        self.snapshot("split", [n])
        k = lns.index(at_uid)
        n.meta["_ln"] = lns[:k]
        new = self._new(n.kind, dict(n.attrs), [self.lines[u] for u in lns[k:] if u in self.lines])
        if n.kind == "p" and n.parent is not None and n.parent.kind == "list-item":
            par = n.parent
            par.insert(par.children.index(n) + 1, new)
        else:
            par = n.parent
            par.insert(par.children.index(n) + 1, new)
            if n.kind == "sec":
                new.kind = "p"
                new.attrs = {}
        self._mark(n)
        self.reindex()
        return new

    def delete(self, zids: list[str]):
        nodes = [self.by_zid[z] for z in zids if z in self.by_zid]
        nodes = [n for n in nodes if n.parent is not None and n.kind not in ("book", "front-matter", "book-body", "book-back", "body")]
        if not nodes:
            return
        self.snapshot("delete", nodes)
        for n in nodes:
            if n.parent is None:
                continue
            if n.kind == "sec":
                # a wrong heading: its text stays as unzoned lines, its content moves up
                par = n.parent
                i = par.children.index(n)
                par.remove(n)
                for k, c in enumerate(list(n.children)):
                    par.insert(i + k, c)
            else:
                self._remove(n)
        self.reindex()

    def move(self, zid: str, delta: int):
        n = self.by_zid.get(zid)
        if n is None or n.parent is None:
            return
        par = n.parent
        i = par.children.index(n)
        j = i + delta
        if not (0 <= j < len(par.children)):
            return
        self.snapshot("move", [n])
        par.children.pop(i)
        par.children.insert(j, n)
        self.reindex()

    def set_attr(self, zid: str, key: str, value):
        n = self.by_zid.get(zid)
        if n is None:
            return
        self.snapshot("attribute", [n])
        if value in (None, ""):
            n.attrs.pop(key, None)
        else:
            n.attrs[key] = str(value)
        self._mark(n)

    def set_reviewed(self, zid: str, on: bool = True):
        n = self.by_zid.get(zid)
        if n is None:
            return
        n.meta["reviewed"] = bool(on)
        self.dirty_file = True

    def draw(self, page_index: int, rect: tuple, kind: str, after_zid: str | None = None, **opt) -> list[Node]:
        """Create zone(s) from the lines inside a rectangle. kind='auto' runs the detector on just those lines."""
        lines = self.lines_in_rect(page_index, rect)
        if opt.get("only_unzoned"):
            lines = [l for l in lines if l.uid not in self.owner]
        if not lines and kind not in ("fig",):
            return []
        anchor = self._anchor_for(lines[0] if lines else None, page_index, rect, after_zid)
        self.snapshot(f"draw {kind}", [anchor])
        self._take(lines)
        self.reindex()
        anchor = self._anchor_for(lines[0] if lines else None, page_index, rect, after_zid)
        new_nodes: list[Node] = []
        if kind == "auto" or (kind == "p" and opt.get("split_paragraphs", True) and len(lines) > 1):
            items = [("line", l) for l in lines]
            regs = []
            if kind == "auto" and not opt.get("only_unzoned"):
                # figures / tables / boxes the layout analysis found inside the box are rebuilt as objects
                from .reading_order import order_items
                pg = self.page_by.get(page_index)
                regs = [r for r in (pg.regions if pg else []) if not r.meta.get("in_box")
                        and rect[0] - 2 <= (r.bbox[0] + r.bbox[2]) / 2 <= rect[2] + 2
                        and rect[1] - 2 <= (r.bbox[1] + r.bbox[3]) / 2 <= rect[3] + 2]
                if regs:
                    keep = {l.uid: l.order for l in lines}
                    items = order_items(pg, [l for l in lines if l.role == "body"], regs)
                    for l in lines:
                        l.order = keep[l.uid]
            blocks = self.builder.parse_blocks(items, allow_secs=(kind == "auto"), in_box=False)
            claimed = set()
            for b in blocks["body"] + blocks["refs"]:
                for x in b.walk():
                    x.meta["zid"] = self.new_zid()
                    x.meta["_ln"] = [u for u in own_lines_from_payload(x) if u not in claimed]
                    claimed.update(x.meta["_ln"])
                    x.meta["src"] = "manual-auto"
                new_nodes.append(b)
            for b in new_nodes:
                for x in b.walk():
                    if x.kind in ("fig", "table-wrap") and x.bbox:
                        extra = [l.uid for l in lines if l.uid not in claimed and l.page == x.page and _center_in(l, x.bbox)]
                        x.meta["_ln"] += extra
                        claimed.update(extra)
        elif kind in ("fig", "table-wrap", "boxed-text"):
            reg = self._new(kind, lines=lines)
            reg.page = page_index
            reg.bbox = tuple(rect)
            reg.meta["rect"] = (page_index, *rect)
            new_nodes.append(reg)
        elif kind == "sec":
            new_nodes.append(self._new("sec", {"disp-level": str(opt.get("level", 1))}, lines))
        elif kind == "list-item":
            items = [("line", l) for l in lines]
            blocks = self.builder.parse_blocks(items, allow_secs=False, in_box=False)
            for b in blocks["body"]:
                for x in b.walk():
                    x.meta["zid"] = self.new_zid()
                    x.meta["_ln"] = own_lines_from_payload(x)
                    x.meta["src"] = "manual-auto"
                new_nodes.append(b)
        elif kind in ("toc", "index"):
            n = self._new(kind, lines=lines)
            new_nodes.append(n)
        elif kind in ("chapter", "appendix", "part", "part-title"):
            p = self._new("p", lines=lines)
            self._insert_after(anchor, [p])
            self.root.fix_parents()
            if kind == "part-title":
                out = self._set_part_title([p])
            else:
                out = self._new_chapter(p, {"part_type": kind})
            self.reindex()
            return [out] if out is not None else []
        else:
            new_nodes.append(self._new(kind if kind in TEXT_KINDS else "p", lines=lines))
        self._insert_after(anchor, new_nodes)
        if any(n.kind == "sec" for n in new_nodes):
            self.renest(self._container_for_flow(new_nodes[0]))
        self.reindex()
        return new_nodes

    def _anchor_for(self, first: Line | None, page_index: int, rect, after_zid):
        if after_zid and after_zid in self.by_zid:
            return self.by_zid[after_zid]
        key = line_sort_key(first) if first is not None else (page_index, 0, rect[1], rect[0])
        best, best_key = None, None
        for u, n in self.owner.items():
            l = self.lines.get(u)
            if l is None:
                continue
            k = line_sort_key(l)
            if k < key and (best_key is None or k > best_key):
                best, best_key = n, k
        if best is None:
            body = self._first_body(page_index)
            return body
        return best

    def _first_body(self, page_index) -> Node:
        cand = None
        for n in self.root.walk():
            if n.kind == "book-part" and (n.page or 0) <= page_index:
                b = next((c for c in n.children if c.kind == "body"), None)
                if b is not None:
                    cand = b
        if cand is None:
            cand = next((n for n in self.root.walk() if n.kind in ("body", "book-body")), self.root)
        return cand

    def _insert_after(self, anchor: Node, nodes: list[Node]):
        """Insert new zones right after the zone that owns the preceding line in reading order."""
        def put(container, start):
            for k, n in enumerate(nodes):
                container.insert(start + k, n)
        if anchor.kind in ("body", "book-body", "named-book-part-body", "book-back", "front-matter"):
            put(anchor, 0)
            return
        if anchor.kind == "book-part":           # preceding line is the chapter title
            b = next((c for c in anchor.children if c.kind == "body"), None)
            if b is None:
                b = Node("body", meta={"zid": self.new_zid(), "_ln": []})
                anchor.insert(0, b)
            put(b, 0)
            return
        if anchor.kind == "sec":                 # preceding line is a heading
            put(anchor, 0)
            return
        target = anchor
        while target.parent is not None and target.parent.kind in ("list-item", "list", "ref-list", "fn", "fn-group", "p"):
            target = target.parent
        par = target.parent
        put(par, par.children.index(target) + 1)

    # ----------------------------------------------------- flagged zones
    def flagged(self, warn: float = 0.75) -> list[Node]:
        out = []
        for n in self.root.walk():
            if n.meta.get("reviewed"):
                continue
            if n.flags or (n.conf < warn and n.kind in TEXT_KINDS + REGION_KINDS + ("list",)):
                out.append(n)
        out.sort(key=lambda n: (n.page if n.page is not None else 1 << 30))
        return out

    # ------------------------------------------------------- rebuild
    def prepare_for_xml(self) -> tuple[Node, list]:
        """A copy of the zone tree with every edited zone rebuilt from its PDF lines; issues for finish()."""
        root = node_from_json(node_to_json(self.root))
        root.fix_parents()
        issues = IssueLog()
        b = self.builder
        saved_issues = b.issues
        b.issues = issues
        try:
            first_line = self._first_line_of_pages(root)
            for n in list(root.walk()):
                if n.meta.get("dirty"):
                    self._rebuild(n, first_line)
            self._repair_page_marks(root, first_line)
            self.normalize(root)
            _strip_zone_meta(root)
        finally:
            b.issues = saved_issues
        root.fix_parents()
        return root, self._issues_for(root, issues)

    def _first_line_of_pages(self, root) -> dict[int, str]:
        owned = set()
        for n in root.walk():
            if n.kind in ("p", "sec", "ref", "disp-formula", "fn"):
                owned.update(n.meta.get("_ln") or [])
        first: dict[int, tuple] = {}
        for u in owned:
            l = self.lines.get(u)
            if l is None:
                continue
            k = line_sort_key(l)
            if l.page not in first or k < first[l.page][0]:
                first[l.page] = (k, u)
        return {pg: v[1] for pg, v in first.items()}

    def _runs_for(self, lines: list[Line], first_line: dict) -> list[dict]:
        runs: list[dict] = []
        firsts = set(first_line.values())
        for l in sorted(lines, key=line_sort_key):
            pm = None
            if l.uid in firsts:
                pg = self.page_by.get(l.page)
                if pg is not None and pg.folio:
                    pm = PageMark(pg.folio, pg.index)
            append_line(runs, l, b_hyph(self.builder), pm)
        return finish_inlines(runs)

    def _rebuild(self, n: Node, first_line: dict):
        lines = [self.lines[u] for u in n.meta.get("_ln") or [] if u in self.lines]
        k = n.kind
        if k == "p":
            runs = self._runs_for(lines, first_line)
            if n.meta.get("strip_marker") and lines:
                mk = marker(lines[0].text)
                if mk:
                    txt = lines[0].text
                    cut = len(txt) - len(txt.lstrip()) + (len(txt.lstrip()) - len(mk[2]))
                    runs = finish_inlines(strip_marker_from_runs(runs, cut))
                    if n.parent is not None and n.parent.kind == "list-item" and mk[0] == "simple":
                        n.parent.meta["label"] = mk[1]
            n.inlines = runs
        elif k == "sec":
            runs = despace_runs(self._runs_for(lines, first_line))
            n.meta["title"] = runs
            n.meta["title_text"] = "".join(r.get("text", "") for r in runs if r.get("k") == "t")
        elif k == "disp-formula":
            if lines:
                page = self.page_by.get(n.page)
                ast, reason = reconstruct_math(lines, page.drawings if page else [])
                n.meta.update(text=linearize(lines), mathml=ast, reason=reason,
                              fallback_image=n.bbox if ast is None else None)
                n.flags = [f for f in n.flags if f != "EQUATION_NEEDS_REVIEW"]
                if ast is None:
                    n.flags.append("EQUATION_NEEDS_REVIEW")
                n.conf = 0.85 if ast else 0.4
        elif k == "ref":
            runs = self._runs_for(lines, first_line)
            txt = "".join(r.get("text", "") for r in runs if r.get("k") == "t")
            m = re.match(r"^\s*(\[?\d{1,4}[.)\]]?)\s+", txt)
            label = None
            if m:
                label = m.group(1)
                from .structure_builder import clip_runs
                runs = finish_inlines(clip_runs(runs, m.end()))
                txt = "".join(r.get("text", "") for r in runs if r.get("k") == "t")
            try:
                parsed = parse_citation(txt)
            except Exception:
                parsed = {"type": "other", "segments": [("text", txt)]}
            n.meta.update({"label": label, "runs": runs, "parsed": parsed, "text": txt})
        elif k == "fn":
            runs = self._runs_for(lines, first_line)
            lab = n.meta.get("label")
            txt = "".join(r.get("text", "") for r in runs if r.get("k") == "t")
            if not lab:
                m = re.match(r"^\s*(\d{1,3}|[*†‡§]+)\s*", txt)
                lab = m.group(1) if m else None
                if m:
                    from .structure_builder import clip_runs
                    runs = finish_inlines(clip_runs(runs, m.end()))
            n.meta["label"] = lab or "*"
            n.children = [Node("p", inlines=runs, page=n.page)]
        elif k in ("fig", "table-wrap", "boxed-text"):
            self._rebuild_region(n, lines)
        elif k == "book-part":
            hl = sorted(lines, key=line_sort_key)
            if n.meta.get("title_from_lines") and hl:
                lab = None
                txt0 = hl[0].text.strip()
                m = re.match(r"^\s*((?:cap[íi]tulo|chapter|parte?|ap[ée]ndice|appendix|unidad|unit)\s+\S+|\d{1,3})[.:\s]*(.*)$", txt0, re.I)
                runs = self._runs_for(hl, {})
                if m and m.group(2):
                    lab = m.group(1)
                    from .structure_builder import clip_runs
                    runs = finish_inlines(clip_runs(runs, txt0.find(m.group(2))))
                elif m and len(hl) > 1:
                    lab = m.group(1)
                    runs = self._runs_for(hl[1:], {})
                n.meta["label"] = lab
                n.meta["title"] = runs
                n.meta["first_page"] = hl[0].page
                pgs = sorted({l.page for x in n.walk() for u in x.meta.get("_ln") or [] for l in [self.lines.get(u)] if l is not None}
                             | {l.page for l in hl})
                n.meta["fpage"] = self.page_by[pgs[0]].folio if pgs and pgs[0] in self.page_by else None
                n.meta["lpage"] = self.page_by[pgs[-1]].folio if pgs and pgs[-1] in self.page_by else None
                n.flags = [f for f in n.flags if f != "MANUAL_TITLE"]
        elif k == "toc" and lines:
            items = [("line", l) for l in sorted(lines, key=line_sort_key)]
            pages = sorted({l.page for l in lines})
            items = [("page", self.page_by[p]) for p in pages if p in self.page_by] + items
            t = self.builder.build_toc(items, pages)
            n.meta.update(t.meta)
        elif k == "index" and lines:
            from .index_detector import parse_index
            from .paragraph_detector import page_mark_for
            ls = sorted(lines, key=line_sort_key)
            title, intro, divs = parse_index(ls, self.builder.style.body_size)
            pages = sorted({l.page for l in ls})
            n.meta.update({"title": title or "Index", "intro": [finish_inlines(line_runs(l)) for l in intro], "divs": divs,
                           "marks": [(p, page_mark_for(self.page_by[p])) for p in pages if page_mark_for(self.page_by.get(p))],
                           "fpage": self.page_by[pages[0]].folio if pages else None})
            n.page = pages[0] if pages else n.page
        n.meta.pop("dirty", None)

    def _rebuild_region(self, n: Node, lines: list[Line]):
        rect = n.meta.get("rect")
        if rect:
            pg_i, *bb = rect
        else:
            pg_i, bb = n.page, list(n.bbox or (0, 0, 0, 0))
        page = self.page_by.get(pg_i)
        if page is None:
            return
        bb = tuple(bb)
        inside = [l for l in lines if l.page == pg_i]
        if n.kind == "boxed-text" and not rect:
            # a box created by wrapping zones keeps its (already zoned) children
            if n.meta.get("title_is_lines"):
                n.meta["title"] = self._runs_for(lines, {})
            n.attrs.setdefault("content-type", "box")
            return
        cap, rest = [], list(inside)
        for i, l in enumerate(inside):
            kind_, m = label_match(l.text)
            if kind_ in ("fig", "table", "box"):
                cap = [l]
                for o in inside[i + 1:]:
                    if o.y0 - cap[-1].y1 < 1.2 * max(o.height, 6) and abs(o.x0 - cap[0].x0) < 40:
                        cap.append(o)
                    else:
                        break
                rest = [o for o in inside if o not in cap]
                break
        info = {}
        if cap:
            kind_, m = label_match(cap[0].text)
            info = {"label": m.group(0).strip(), "key": number_key(m.group("num"))}
        else:
            info = {"label": None, "key": None, "unlabeled": True}
        if n.kind == "fig":
            if cap:
                cb = (min(l.x0 for l in cap), min(l.y0 for l in cap), max(l.x1 for l in cap), max(l.y1 for l in cap))
                below = (cb[1] + cb[3]) / 2 > (bb[1] + bb[3]) / 2
                art = (bb[0], bb[1], bb[2], cb[1] - 1) if below else (bb[0], cb[3] + 1, bb[2], bb[3])
                if art[3] - art[1] < 10:
                    art = bb
            else:
                art = bb
            r = Region("figure", bb, pg_i, 0.9, {**info, "art": art, "caption": cap, "absorbed": rest,
                                                  "direction": "above"})
        elif n.kind == "table-wrap":
            from .table_detector import build_grid
            body = sorted(rest, key=lambda l: (l.y0, l.x0))
            bounds = (bb[0], min((l.y0 for l in body), default=bb[1]), bb[2], max((l.y1 for l in body), default=bb[3]))
            grid = build_grid(body, page, self.builder.style, bounds) if body else None
            r = Region("table", bb, pg_i, grid.conf if grid else 0.4,
                       {**info, "caption": cap, "lines": body, "foot": [], "grid": grid, "body_bbox": bounds})
        else:
            boxlines = [l for l in inside]
            r = Region("box", bb, pg_i, 0.9, {"lines": boxlines, "regions": []})
        new = self.builder.region_node(r)
        if new is None:
            return
        n.attrs = new.attrs
        n.meta = {**{k: v for k, v in n.meta.items() if k in ("zid", "_ln", "rect", "reviewed", "src")}, **new.meta}
        n.flags = new.flags
        n.conf = new.conf
        n.page, n.bbox = pg_i, bb
        if n.kind == "boxed-text":
            n.children = []
            for c in new.children:
                n.add(c)

    def _repair_page_marks(self, root: Node, first_line: dict):
        seen = set()

        def scan(lst):
            out = []
            for it in lst:
                if isinstance(it, dict) and it.get("k") == "pg":
                    key = str(it.get("folio"))
                    if key in seen:
                        continue
                    seen.add(key)
                out.append(it)
            return out
        for n in root.walk():
            if n.inlines:
                n.inlines = scan(n.inlines)
            for key in ("title", "runs", "caption"):
                if isinstance(n.meta.get(key), list):
                    n.meta[key] = scan(n.meta[key])
            _collect_marks({k: v for k, v in n.meta.items() if k not in ("title", "runs", "caption")}, seen)
        # pages whose mark went missing: put it at the start of the first text zone of the page
        owner = {}
        for n in root.walk():
            for u in n.meta.get("_ln") or []:
                owner.setdefault(u, n)
        for pg, u in sorted(first_line.items()):
            page = self.page_by.get(pg)
            if page is None or not page.folio or str(page.folio) in seen:
                continue
            n = owner.get(u)
            if n is None:
                continue
            pm = PageMark(page.folio, page.index)
            if n.kind == "p":
                n.inlines.insert(0, pm)
            elif n.kind == "sec":
                n.meta["title"] = [pm] + list(n.meta.get("title") or [])
            elif n.kind == "ref":
                n.meta["runs"] = [pm] + list(n.meta.get("runs") or [])
            else:
                continue
            seen.add(str(page.folio))

    def _issues_for(self, root: Node, rebuild_issues: IssueLog) -> list:
        """Analysis issues still valid after editing + issues from rebuilt zones."""
        flags_now: dict[tuple, int] = {}
        for n in root.walk():
            for f in n.flags:
                flags_now[(n.page, f)] = flags_now.get((n.page, f), 0) + 1
        flag_codes = set()
        for n in self.root.walk():
            flag_codes.update(n.flags)
        reviewed = {(n.page, f) for n in self.root.walk() if n.meta.get("reviewed") for f in n.flags}
        out = []
        for i in self.st.issues.items:
            if (i.page, i.code) in reviewed:
                continue
            if i.code in flag_codes and (i.page, i.code) not in flags_now:
                continue        # the zone that caused it was edited away
            out.append(i)
        out.extend(rebuild_issues.items)
        out.append(_issue("ZONING_EDITS", "info", f"{self.project.info.get('edits', 0)} zoning edit(s) applied"))
        return out


def _collect_marks(v, seen: set):
    if isinstance(v, dict):
        if v.get("k") == "pg":
            seen.add(str(v.get("folio")))
            return
        for x in v.values():
            _collect_marks(x, seen)
    elif isinstance(v, (list, tuple)):
        for x in v:
            _collect_marks(x, seen)
    elif isinstance(v, Node):
        for x in v.walk():
            _collect_marks(x.inlines, seen)


def b_hyph(builder):
    return builder.hyph


def _issue(code, sev, msg, page=None):
    from .document_tree import Issue
    return Issue(code, sev, msg, page=page)


def _strip_zone_meta(root: Node):
    for n in root.walk():
        for k in ("zid", "_ln", "_head_ln", "dirty", "src", "rect", "reviewed", "strip_marker", "title_from_lines",
                  "title_is_lines", "next_zid"):
            n.meta.pop(k, None)
        for row in n.meta.get("rows") or []:
            for c in row:
                for b in c.get("blocks") or []:
                    _strip_zone_meta(b)
    _strip_ln(root)


def _strip_ln(root: Node):
    def clean(lst):
        for it in lst or []:
            if isinstance(it, dict):
                it.pop("ln", None)
    for n in root.walk():
        clean(n.inlines)
        for key in ("title", "subtitle", "caption", "runs"):
            if isinstance(n.meta.get(key), list):
                clean(n.meta[key])


def zone_label(n: Node) -> str:
    k = n.kind
    if k == "sec":
        return f"H{n.attrs.get('disp-level', '?')}"
    if k == "book-part":
        t = n.attrs.get("book-part-type", "")
        return "appendix" if n.meta.get("appendix") else t or "part"
    return k


def zone_text(n: Node, editor: ZoneEditor | None = None, limit: int = 160) -> str:
    def runs_txt(r):
        return "".join(x.get("text", "") for x in r or [] if isinstance(x, dict))
    if n.kind == "sec":
        t = n.meta.get("title_text") or runs_txt(n.meta.get("title"))
    elif n.kind in ("fig", "table-wrap"):
        t = (n.meta.get("label") or "") + " " + runs_txt(n.meta.get("caption"))
    elif n.kind == "ref":
        t = n.meta.get("text") or ""
    elif n.kind == "book-part":
        t = (n.meta.get("label") or "") + " " + runs_txt(n.meta.get("title"))
    elif n.kind == "disp-formula":
        t = n.meta.get("text") or ""
    elif n.kind == "boxed-text":
        t = (n.meta.get("label") or "") + " " + runs_txt(n.meta.get("title"))
    else:
        t = runs_txt(n.inlines)
    if (not t.strip() or n.meta.get("dirty")) and editor is not None:
        ls = editor.zone_lines(n)
        if ls:
            t = " ".join(l.text.strip() for l in sorted(ls, key=line_sort_key))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit] + ("…" if len(t) > limit else "")
