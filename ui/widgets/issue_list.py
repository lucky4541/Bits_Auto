"""Grouped issue list (severity icon + code + count), with expandable messages."""
from __future__ import annotations

from collections import defaultdict

from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from .. import icons
from ..app_state import theme_tokens

SEV_ORDER = {"critical": 0, "error": 1, "warning": 2, "info": 3}


class IssueList(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["Issue", "Count / page"])
        self.setColumnWidth(0, 420)
        self.setAlternatingRowColors(True)

    def load(self, issues: list[dict]):
        self.clear()
        t = theme_tokens()
        groups = defaultdict(list)
        for i in issues:
            groups[(i.get("severity", "info"), i.get("code", "?"))].append(i)
        for (sev, code), items in sorted(groups.items(), key=lambda x: (SEV_ORDER.get(x[0][0], 9), -len(x[1]))):
            name, col = {"critical": ("error", t["error"]), "error": ("error", t["error"]),
                         "warning": ("warning", t["warning"])}.get(sev, ("circle", t["faint"]))
            top = QTreeWidgetItem([f"{code}  ({sev})", str(len(items))])
            top.setIcon(0, icons.icon(name, col, 14))
            for it in items[:200]:
                QTreeWidgetItem(top, [it.get("message", ""), "" if it.get("page") is None else f"p{it['page']}"])
            self.addTopLevelItem(top)
