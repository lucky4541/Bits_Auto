"""Registry of every linkable target, built before any link is generated.

    ("fig", "ch003", "3-7")  -> "martin-ch003-fig007"
    ("chapter", None, "5")   -> "martin-ch005"
    ("page", None, "432")    -> "page432"
Lookups are exact: an unknown key is *unresolved*, never approximated.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


class TargetRegistry:
    def __init__(self):
        self.targets: dict[tuple, dict] = {}
        self.by_id: dict[str, dict] = {}
        self.global_keys: dict[tuple, list[dict]] = defaultdict(list)
        self.duplicates: list[dict] = []

    def register(self, kind: str, scope: str | None, key: str | None, target_id: str, element: str, page=None, label=None):
        rec = {"kind": kind, "scope": scope, "key": key, "id": target_id, "element": element, "page": page,
               "label": label, "incoming": []}
        if target_id in self.by_id:
            self.duplicates.append(rec)
        self.by_id[target_id] = rec
        if key is not None:
            k = (kind, scope, str(key))
            if k in self.targets and self.targets[k]["id"] != target_id:
                self.duplicates.append(rec)
            else:
                self.targets[k] = rec
            self.global_keys[(kind, str(key))].append(rec)
        return rec

    def lookup(self, kind: str, scope: str | None, key: str) -> dict | None:
        rec = self.targets.get((kind, scope, str(key)))
        if rec is not None:
            return rec
        cands = self.global_keys.get((kind, str(key)), [])
        if len(cands) == 1:
            return cands[0]
        return None

    def add_incoming(self, target_id: str, source: dict):
        rec = self.by_id.get(target_id)
        if rec is not None:
            rec["incoming"].append(source)

    def to_json(self) -> dict:
        return {"targets": [{k: v for k, v in r.items()} for r in self.by_id.values()],
                "duplicates": self.duplicates}

    def save(self, path: Path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_json(), fh, indent=1, ensure_ascii=False, default=str)
