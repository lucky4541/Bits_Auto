"""Figure / table placement by first citation (learned rule, see mapping/placement_mapping.json).

Rule learned from the golden samples (1,416 figures, 768 tables):
  the object follows the block that contains its first body citation;
  when that block is a paragraph inside a list item, the object is appended
  inside that paragraph. Objects are never moved across book-parts
  (chapter / part / appendix); uncited objects stay at their physical
  reading-order position. Every decision is recorded (physical vs semantic).
"""
from __future__ import annotations

from .document_tree import IssueLog, Node

FLOATS = ("fig", "table-wrap")
BLOCK_PARENTS = ("body", "sec", "boxed-text", "named-book-part-body", "list-item")
BODY_CTX = ("body", "sidebar")


def _order(root: Node) -> dict[int, int]:
    return {id(n): i for i, n in enumerate(root.walk())}


def book_part_of(n: Node) -> Node | None:
    return n.ancestor("book-part", "front-matter-part", "preface", "foreword", "dedication", "index")


def citing_block(p: Node) -> Node:
    """The block-level node that holds the citation (direct child of sec/body/box, or the list-item paragraph)."""
    cur = p
    while cur.parent is not None:
        if cur.parent.kind in ("list-item",):
            return cur                         # paragraph inside a list item
        if cur.parent.kind in ("body", "sec", "boxed-text", "named-book-part-body"):
            return cur
        cur = cur.parent
    return p


class PlacementEngine:
    def __init__(self, issues: IssueLog, policy: str = "learned"):
        self.issues = issues
        self.policy = policy
        self.report: list[dict] = []

    def detect_first_citation(self, obj: Node, citations: list[tuple[Node, dict]]):
        for node, cite in citations:
            if cite.get("ctx") in BODY_CTX and node is not obj and obj not in list(node.ancestors()):
                return node, cite
        return None, None

    def run(self, root: Node, cites_by_target: dict[tuple, list[tuple[Node, dict]]], key_of) -> list[dict]:
        if self.policy == "physical":
            return self.report
        floats = [n for n in root.walk() if n.kind in FLOATS and n.meta.get("key")]
        after_counts: dict[int, int] = {}
        for obj in floats:
            key = key_of(obj)
            cites = cites_by_target.get(key, [])
            rec = {"type": "figure" if obj.kind == "fig" else "table", "label": obj.meta.get("label"),
                   "key": obj.meta.get("key"), "physical_page": obj.page, "physical_bbox": obj.bbox,
                   "citations": len(cites), "rule": None, "status": None, "node": obj}
            first_node, first_cite = self.detect_first_citation(obj, cites)
            if first_node is None:
                rec.update(rule="physical position (no first citation)", status="NO_FIRST_CITATION")
                self.issues.add("NO_FIRST_CITATION", "info", f"{obj.meta.get('label')} has no body citation", page=obj.page)
                self.report.append(rec)
                continue
            rec["first_citation_page"] = first_node.page
            rec["first_citation_text"] = first_cite.get("text")
            obj_part, cite_part = book_part_of(obj), book_part_of(first_node)
            if obj_part is not cite_part:
                code = "CROSS_SECTION_FIGURE_REFERENCE" if obj.kind == "fig" else "CROSS_SECTION_TABLE_REFERENCE"
                rec.update(rule="physical position (first citation in another book-part)", status=code)
                self.issues.add(code, "warning", f"{obj.meta.get('label')} first cited in another part", page=obj.page)
                self.report.append(rec)
                continue
            block = citing_block(first_node)
            if obj in list(block.walk()):
                rec.update(rule="already inside citing block", status="PLACED", anchor=block)
                self.report.append(rec)
                continue
            self._move(obj, block, after_counts)
            moved_sec = obj.ancestor("sec") is not None and block.ancestor("sec") is not obj.ancestor("sec")
            rec.update(rule="after first citation block" if block.parent.kind != "list-item" else "inside citing list-item paragraph",
                       status="PLACED", anchor=block)
            if moved_sec:
                rec["note"] = "moved across section (sample convention: object follows its citing block)"
            self.report.append(rec)
        return self.report

    def _move(self, obj: Node, block: Node, after_counts: dict[int, int]):
        old = obj.parent
        if old is not None:
            old.remove(obj)
        if block.parent is not None and block.parent.kind == "list-item":
            block.add(obj)                      # appended at the end of the citing paragraph
            return
        parent = block.parent
        idx = parent.children.index(block)
        n = after_counts.get(id(block), 0)
        # keep citation order for several objects anchored to the same block
        ins = idx + 1 + n
        parent.insert(min(ins, len(parent.children)), obj)
        after_counts[id(block)] = n + 1
