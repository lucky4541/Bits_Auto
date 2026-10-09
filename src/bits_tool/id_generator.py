"""Deterministic, sample-derived ID generation (the only place IDs are made).

Pattern learned from the golden corpus + vendor spec:
    {prefix}-{part}-{code}{NNN}     e.g. martin-ch003-p126, brenner-ch001-tbl001
    per book-part counters, document order, 3-digit padding (widening past 999)
    figure/table numbers follow the printed label (Figure 3.7 -> ch003-fig007)
    unlabelled figure/table -> unfig / untbl (vendor spec examples)
    page targets -> page{folio}
Same input + config + tool version -> identical IDs (no randomness, no time).
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

DEFAULT_CODES = {
    "p": "p", "list": "list", "fig": "fig", "unfig": "unfig", "table-wrap": "tbl", "untbl": "untbl", "caption": "cap",
    "graphic": "gr", "inline-graphic": "ig", "sec": "sec", "ref": "bib", "ref-list": "r", "disp-formula": "eq",
    "inline-formula": "ieq", "boxed-text": "sb-box", "case-study": "cs-box", "fn": "fn", "toc": "toc",
    "front-matter-part": "topic", "dedication": "dedication", "preface": "preface", "foreword": "foreword",
    "ack": "sec", "aff": "aff", "index-entry": "ie", "question-wrap": "qw", "def-list": "deflist", "kwd-group": "kwdgroup",
}
PART_CODES = {"front-matter": "fm", "chapter": "ch", "part": "pt", "appendix": "app", "back-matter": "bm", "index": "index"}
XML_ID_RE = re.compile(r"^[A-Za-z_][\w.\-]*$")


def ascii_slug(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\-]", "", s.lower().replace(" ", "-")) or "book"


class IdGenerator:
    def __init__(self, prefix: str, mapping_path: Path | None = None):
        self.prefix = ascii_slug(prefix)
        if not re.match(r"^[a-z_]", self.prefix):
            self.prefix = "b" + self.prefix
        self.codes = dict(DEFAULT_CODES)
        self.part_codes = dict(PART_CODES)
        if mapping_path and Path(mapping_path).exists():
            with open(mapping_path, encoding="utf-8") as fh:
                m = json.load(fh)
            for el, info in m.get("element_codes", {}).items():
                if el in self.codes and info.get("code"):
                    self.codes[el] = info["code"]
        self.counters: dict[tuple, int] = defaultdict(int)
        self.part_counters: dict[str, int] = defaultdict(int)
        self.used: set[str] = set()
        self.collisions: list[str] = []

    # ---------------------------------------------------------- helpers
    @staticmethod
    def pad(n: int) -> str:
        return f"{n:03d}"

    def _unique(self, value: str) -> str:
        if value in self.used:
            self.collisions.append(value)
            k = 2
            while f"{value}-{k}" in self.used:
                k += 1
            value = f"{value}-{k}"
        self.used.add(value)
        return value

    # ------------------------------------------------------------- parts
    def generate_book_id(self, what: str) -> str:
        return self._unique(f"{self.prefix}-{what}")

    def generate_part_id(self, part_kind: str, word: str | None = None) -> str:
        code = self.part_codes.get(part_kind, part_kind)
        if part_kind == "part" and word:
            w = word.lower()
            code = "sec" if w.startswith(("secc", "sect")) else "unit" if w.startswith(("unit", "unid")) else "pt"
        self.part_counters[code] += 1
        return f"{code}{self.pad(self.part_counters[code])}"

    def generate_chapter_id(self, part_code: str) -> str:
        return self._unique(f"{self.prefix}-{part_code}")

    # ---------------------------------------------------------- elements
    def next(self, part: str, element: str) -> str:
        code = self.codes.get(element, element)
        self.counters[(part, code)] += 1
        return self._unique(f"{self.prefix}-{part}-{code}{self.pad(self.counters[(part, code)])}")

    def numbered(self, part: str, element: str, number: int | None) -> str:
        """fig/table IDs follow the printed number; fall back to the sequence when absent or taken."""
        code = self.codes.get(element, element)
        if number is not None:
            cand = f"{self.prefix}-{part}-{code}{self.pad(number)}"
            if cand not in self.used:
                self.used.add(cand)
                self.counters[(part, code)] = max(self.counters[(part, code)], number)
                return cand
            self.collisions.append(cand)
        return self.next(part, element)

    def generate_paragraph_id(self, part):
        return self.next(part, "p")

    def generate_section_id(self, part):
        return self.next(part, "sec")

    def generate_list_id(self, part):
        return self.next(part, "list")

    def generate_figure_id(self, part, number=None, unlabeled=False):
        return self.next(part, "unfig") if unlabeled else self.numbered(part, "fig", number)

    def generate_table_id(self, part, number=None, unlabeled=False):
        return self.next(part, "untbl") if unlabeled else self.numbered(part, "table-wrap", number)

    def generate_equation_id(self, part):
        return self.next(part, "disp-formula")

    def generate_footnote_id(self, part, table_id: str | None = None):
        if table_id:
            key = (table_id, "fn")
            self.counters[key] += 1
            return self._unique(f"{table_id}-fn{self.pad(self.counters[key])}")
        return self.next(part, "fn")

    def generate_reference_id(self, part):
        return self.next(part, "ref")

    def generate_index_id(self, part):
        return self.next(part, "index-entry")

    @staticmethod
    def page_target(folio: str) -> str:
        f = str(folio).strip().lower()
        return f"page{f}" if XML_ID_RE.match(f"page{f}") else "page" + re.sub(r"[^\w]", "", f)

    @staticmethod
    def label_number(key: str | None) -> int | None:
        if not key:
            return None
        m = re.search(r"(\d+)$", key)
        return int(m.group(1)) if m else None
