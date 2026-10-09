"""List markers, list types and nesting (by marker indentation)."""
from __future__ import annotations

import re

BULLETS = "•●■□▪◆◇◦○‣∙·►▶✓✔➢➤❑❖–—"
GLYPHS = "•●■□▪◆◇◦○‣►▶✓✔➢➤❑❖"   # bullets that may touch their text (no space); dashes may not
MARKER_RE = re.compile(
    rf"^\s*(?P<m>[{BULLETS}]|\(?(?P<num>\d{{1,3}})[.)](?!\d)|\(?(?P<la>[a-z])[.)]|\(?(?P<ua>[A-Z])[.)]|\(?(?P<lr>(?:i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii))[.)]|\(?(?P<ur>(?:I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII))[.)])(?P<sp>\s+|\t|(?<=[{GLYPHS}])(?=\S))")


def marker(text: str):
    """(list_type, marker_text, rest) or None."""
    m = MARKER_RE.match(text)
    if not m:
        return None
    mk = m.group("m").strip()
    if m.group("num"):
        t = "number"
    elif m.group("lr"):
        t = "roman-lower"
    elif m.group("ur"):
        t = "roman-upper"
    elif m.group("la"):
        t = "alpha-lower"
    elif m.group("ua"):
        t = "alpha-upper"
    elif mk in "–—":
        t = "simple"         # dash lists keep their dash as label (vendor spec: labels only for simple)
    else:
        t = "bullet"
    return t, mk, text[m.end():]


def ordinal(list_type: str, mk: str) -> int | None:
    core = mk.strip("().")
    if list_type == "number":
        return int(core)
    if list_type in ("alpha-lower", "alpha-upper"):
        return ord(core.lower()) - 96
    if list_type in ("roman-lower", "roman-upper"):
        vals = {"i": 1, "v": 5, "x": 10}
        s = core.lower()
        tot = 0
        for i, ch in enumerate(s):
            v = vals[ch]
            tot += -v if i + 1 < len(s) and vals[s[i + 1]] > v else v
        return tot
    return None


def strip_marker_from_runs(runs: list[dict], mk_len: int) -> list[dict]:
    """Remove the first mk_len characters (marker + spacing) from inline runs."""
    out = []
    remaining = mk_len
    for r in runs:
        if remaining > 0 and r["k"] == "t":
            t = r["text"]
            if len(t) <= remaining:
                remaining -= len(t)
                continue
            r = {**r, "text": t[remaining:].lstrip()}
            remaining = 0
        out.append(r)
    return out
