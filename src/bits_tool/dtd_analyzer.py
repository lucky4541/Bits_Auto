"""DTD analyzer and DTD-aware structural oracle.

Loads the production BITS DTD (never modified) with lxml, and exposes:
  * a JSON inventory (elements, content models, attributes, required attrs)
  * `DTDRules`, used by the BITS generator to check "may <child> appear in
    <parent>?" before an element is created, and by the validator.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from lxml import etree

PUBLIC_ID = "-//NLM//DTD BITS Book Interchange DTD v1.0 20130520//EN"


def find_dtd(dtd_dir: Path) -> Path | None:
    """Locate BITS-book1.dtd (preferred) anywhere under dtd_dir."""
    dtd_dir = Path(dtd_dir)
    if dtd_dir.is_file() and dtd_dir.suffix.lower() == ".dtd":
        return dtd_dir
    cands = sorted(dtd_dir.rglob("*.dtd"), key=lambda p: (p.name.lower() != "bits-book1.dtd", len(p.parts)))
    # the top-level BITS-book1.dtd needs its modules next to it; prefer a copy that has them
    for c in cands:
        if c.name.lower() == "bits-book1.dtd" and (c.parent / "BITS-book-part1.ent").exists():
            return c
    return cands[0] if cands else None


def _content_to_str(c) -> str:
    if c is None:
        return "EMPTY"
    t = c.type
    occ = {"once": "", "opt": "?", "mult": "*", "plus": "+"}.get(c.occur, "")
    if t == "pcdata":
        return "#PCDATA"
    if t == "element":
        return (c.name if not getattr(c, "prefix", None) else f"{c.prefix}:{c.name}") + occ
    sep = " | " if t == "or" else ", "
    return "(" + sep.join(x for x in (_content_to_str(c.left), _content_to_str(c.right)) if x) + ")" + occ


def _content_children(c, acc: set):
    if c is None:
        return
    if c.type == "element":
        acc.add(c.name if not getattr(c, "prefix", None) else f"{c.prefix}:{c.name}")
    _content_children(c.left, acc)
    _content_children(c.right, acc)


class DTDRules:
    def __init__(self, dtd_path: Path):
        self.path = Path(dtd_path)
        self.dtd = etree.DTD(str(self.path))
        self.elements: dict[str, dict] = {}
        for el in self.dtd.iterelements():
            name = el.name if not el.prefix else f"{el.prefix}:{el.name}"
            kids: set[str] = set()
            _content_children(el.content, kids)
            attrs = {}
            for a in el.iterattributes():
                aname = a.name if not a.prefix else f"{a.prefix}:{a.name}"
                attrs[aname] = {"type": a.type, "default": a.default, "values": list(a.values()),
                                "default_value": a.default_value}
            self.elements[name] = {
                "type": el.type,  # empty / any / mixed / element
                "content": _content_to_str(el.content),
                "children": sorted(kids),
                "mixed": el.type == "mixed",
                "attributes": attrs,
                "required": sorted(k for k, v in attrs.items() if v["default"] == "required"),
            }

    # --------------------------------------------------------------- queries
    def allows_child(self, parent: str, child: str) -> bool:
        e = self.elements.get(parent)
        if e is None:
            return False
        if e["type"] == "any":
            return True
        return child in e["children"]

    def allows_text(self, parent: str) -> bool:
        e = self.elements.get(parent)
        return bool(e and e["mixed"])

    def attr_values(self, element: str, attr: str) -> list[str]:
        return self.elements.get(element, {}).get("attributes", {}).get(attr, {}).get("values", [])

    def validate(self, tree) -> tuple[bool, list[dict]]:
        ok = self.dtd.validate(tree)
        errs = [{"line": e.line, "message": e.message} for e in self.dtd.error_log]
        return ok, errs

    def sha1(self) -> str:
        h = hashlib.sha1()
        for f in sorted(self.path.parent.rglob("*")):
            if f.is_file() and f.suffix.lower() in (".dtd", ".ent", ".mod"):
                h.update(f.name.encode())
                h.update(f.read_bytes())
        return h.hexdigest()

    def summary(self) -> dict:
        txt = self.path.read_text(encoding="utf-8", errors="replace")
        ver = re.search(r"BITS Book Interchange DTD v([\d.]+)\s+(\d{8})", txt) or re.search(r"Version\s+([\d.]+)", txt)
        return {
            "dtd_path": str(self.path),
            "public_id": PUBLIC_ID,
            "system_id": self.path.name,
            "bits_version": ver.group(1) if ver else "unknown",
            "root_element": "book",
            "doctype": f'<!DOCTYPE book PUBLIC "{PUBLIC_ID}" "{self.path.name}">',
            "modules": sorted(p.name for p in self.path.parent.glob("*.ent")),
            "element_count": len(self.elements),
            "dtd_hash": self.sha1(),
        }

    def write(self, out_dir: Path) -> dict:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        s = self.summary()
        with open(out_dir / "dtd_analysis.json", "w", encoding="utf-8") as fh:
            json.dump({"summary": s, "elements": self.elements}, fh, indent=1, ensure_ascii=False)
        return s


@lru_cache(maxsize=4)
def load_rules(dtd_path: str) -> DTDRules:
    return DTDRules(Path(dtd_path))
