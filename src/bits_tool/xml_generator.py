"""Serialization: DOCTYPE, safe indentation (never inside mixed content), UTF-8."""
from __future__ import annotations

from pathlib import Path

from lxml import etree

MML = "http://www.w3.org/1998/Math/MathML"


def _name(el) -> str:
    t = el.tag
    if not isinstance(t, str):
        return ""
    if t.startswith("{"):
        ns, _, n = t[1:].partition("}")
        return ("mml:" + n) if ns == MML else n
    return t


def indent(el, rules, level: int = 0, unit: str = "  "):
    """Indent element-only containers; mixed-content elements are left byte-exact."""
    name = _name(el)
    info = rules.elements.get(name) if rules else None
    mixed = (info is None) or info["mixed"] or name.startswith("mml:") or (el.text and el.text.strip())
    if mixed or len(el) == 0:
        return
    pad = "\n" + unit * (level + 1)
    el.text = pad
    for i, child in enumerate(el):
        indent(child, rules, level + 1, unit)
        child.tail = pad if i < len(el) - 1 else "\n" + unit * level


def serialize(root, path: Path, public_id: str, system_id: str, rules=None, pretty: bool = True) -> Path:
    if pretty:
        indent(root, rules)
    tree = etree.ElementTree(root)
    doctype = f'<!DOCTYPE book PUBLIC "{public_id}" "{system_id}">'
    data = etree.tostring(tree, xml_declaration=True, encoding="UTF-8", doctype=doctype)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.write(b"\n")
    tmp.replace(path)
    return path
