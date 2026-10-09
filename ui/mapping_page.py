"""Mapping explorer: learned rules per category with evidence; raw JSON under Advanced."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

from .widgets.common import Card, Page, button, label

CATS = [
    ("Elements", "element_mapping.json"), ("Attributes", "attribute_mapping.json"), ("IDs", "id_mapping.json"),
    ("Figures", "figure_mapping.json"), ("Tables", "table_mapping.json"), ("Lists", "list_mapping.json"),
    ("References", "reference_mapping.json"), ("Citations", "citation_mapping.json"), ("Index", "index_mapping.json"),
    ("TOC", "toc_mapping.json"), ("Placement", "placement_mapping.json"), ("Headings", "heading_mapping.json"),
    ("Boxed text", "boxed_text_mapping.json"), ("Conflicts", "../analysis/mapping_conflicts.json"),
]


def _share(d: dict, key) -> float:
    tot = sum(v for v in d.values() if isinstance(v, (int, float))) or 1
    return d.get(key, 0) / tot


class MappingPage(Page):
    def __init__(self, ctx):
        super().__init__("Mapping", "Rules learned from the approved samples and applied to every new PDF.", scroll=False)
        self.ctx = ctx
        row = QHBoxLayout()
        self.cats = QListWidget()
        self.cats.setObjectName("categoryList")
        self.cats.setFixedWidth(190)
        for c, _f in CATS:
            self.cats.addItem(c)
        self.cats.currentRowChanged.connect(self.show_cat)
        row.addWidget(self.cats)
        right = QVBoxLayout()
        self.card = Card()
        self.title = label("", "sectionTitle")
        self.body_lbl = label("", wrap=True, selectable=True)
        self.body_lbl.setTextFormat(Qt.RichText)
        self.conf = QProgressBar()
        self.conf.setRange(0, 100)
        self.conf.setMaximumHeight(8)
        self.conf_lbl = label("", "muted")
        self.card.lay.addWidget(self.title)
        self.card.lay.addWidget(self.body_lbl)
        ch = QHBoxLayout()
        ch.addWidget(label("Confidence", "faint"))
        ch.addWidget(self.conf, 1)
        ch.addWidget(self.conf_lbl)
        self.card.lay.addLayout(ch)
        right.addWidget(self.card)
        adv = QHBoxLayout()
        self.adv_btn = button("Advanced Details", "ghost", "code")
        self.adv_btn.setCheckable(True)
        self.adv_btn.toggled.connect(lambda on: self.raw.setVisible(on))
        adv.addWidget(self.adv_btn)
        adv.addStretch(1)
        right.addLayout(adv)
        self.raw = QPlainTextEdit()
        self.raw.setObjectName("code")
        self.raw.setReadOnly(True)
        self.raw.hide()
        right.addWidget(self.raw, 1)
        right.addStretch(0)
        w = QWidget()
        w.setLayout(right)
        row.addWidget(w, 1)
        self.body_layout = row
        self.body_lbl.setMinimumWidth(400)
        self.add_layout(row)

    def add_layout(self, lay):
        w = QWidget()
        w.setLayout(lay)
        self.add(w, 1)

    def on_show(self, **kw):
        if self.cats.currentRow() < 0:
            self.cats.setCurrentRow(0)
        else:
            self.show_cat(self.cats.currentRow())

    def _load(self, fname):
        p = (self.ctx.cfg.path("mapping_dir") / fname).resolve()
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def show_cat(self, i):
        if i < 0:
            return
        name, fname = CATS[i]
        data = self._load(fname)
        self.title.setText(name.upper())
        if data is None:
            self.body_lbl.setText("No mapping profile found. Run <b>Sample Analysis</b> first.")
            self.raw.setPlainText("")
            self.conf.setValue(0)
            self.conf_lbl.setText("—")
            return
        self.raw.setPlainText(json.dumps(data, indent=2, ensure_ascii=False)[:400000])
        meta = self._load("mapping_meta.json") or {}
        n = len(meta.get("samples", []))
        html, conf = self._summary(name, data, n)
        self.body_lbl.setText(html)
        self.conf.setValue(int(conf * 100))
        self.conf_lbl.setText(f"{conf:.0%}")

    def _summary(self, name, d, n):
        ev = f"<br><span style='color:gray'>Evidence: {n} approved samples</span>"
        if name == "Figures":
            place = self._load("placement_mapping.json") or {}
            fe = place.get("figure", {}).get("evidence", {})
            c = _share({k: v for k, v in fe.items() if k != "no_citation"}, "immediately_after_citing_block")
            return (f"<b>PDF pattern:</b> labelled figure (image or vector art) + caption<br>"
                    f"<b>Label shapes:</b> {', '.join(list(d.get('label_shapes', {}))[:6])}<br>"
                    f"<b>Mapped structure:</b> <code>{d.get('structure')}</code><br>"
                    f"<b>ID pattern:</b> <code>{{author}}-{{part}}-fig{{NNN}}</code> (number from the printed label)<br>"
                    f"<b>Placement:</b> after first valid citation{ev}"), max(c, 0.5)
        if name == "Tables":
            return (f"<b>PDF pattern:</b> caption above, ruled or aligned text grid<br><b>Mapped structure:</b> <code>{d.get('structure')}</code><br>"
                    f"<b>Cells:</b> {d.get('cells')}<br><b>Fallback:</b> {d.get('fallback')}<br>"
                    f"<b>ID pattern:</b> <code>{{author}}-{{part}}-tbl{{NNN}}</code>{ev}"), 0.9
        if name == "IDs":
            codes = d.get("element_codes", {})
            rows = "".join(f"<tr><td><code>{el}</code></td><td>{v.get('code')}</td><td>{v.get('evidence')}</td></tr>" for el, v in list(codes.items())[:18])
            return (f"<b>Format:</b> <code>{d.get('format')}</code><br><b>Prefix:</b> {d.get('prefix', {}).get('rule')}<br>"
                    f"<b>Counters:</b> {d.get('counter_scope')}<br><b>Figures/tables:</b> {d.get('fig_number_rule')}<br>"
                    f"<b>Pages:</b> {d.get('page_target', {}).get('rule')}<br><table cellspacing=6><tr><th align=left>Element</th><th align=left>Code</th><th align=left>Evidence</th></tr>{rows}</table>{ev}"), 0.95
        if name == "Placement":
            fe = d.get("figure", {})
            te = d.get("table", {})
            return (f"<b>Figures:</b> {fe.get('rule')} · dominant relation <code>{fe.get('dominant')}</code><br>"
                    f"&nbsp;&nbsp;in list items: {fe.get('in_list_item')}<br>&nbsp;&nbsp;no citation: {fe.get('no_citation')}<br>"
                    f"&nbsp;&nbsp;cross-part: {fe.get('cross_part_citation')}<br><b>Tables:</b> {te.get('rule')} · <code>{te.get('dominant')}</code><br>"
                    f"<b>Ignored for first citation:</b> {', '.join(d.get('citations_ignored_for_first', []))}{ev}"), 0.85
        if name == "Conflicts":
            items = "".join(f"<li><b>{c.get('topic')}</b> — cause: {c.get('cause')}<br>resolution: {c.get('resolution')}</li>" for c in d)
            return f"<ul>{items}</ul>", 1.0
        if name == "Lists":
            return (f"<b>list-type values:</b> {', '.join(d.get('list-types', {}).keys())}<br><b>Nested:</b> {', '.join(d.get('nested', {}).keys())}<br>"
                    f"<b>Labels:</b> {d.get('label_rule')}<br><b>Item:</b> <code>{d.get('item')}</code>{ev}"), 0.9
        if name == "Citations":
            return (f"<b>Figure words:</b> {', '.join(d.get('figure_words', []))}<br><b>Table words:</b> {', '.join(d.get('table_words', []))}<br>"
                    f"<b>Chapter words:</b> {', '.join(d.get('chapter_words', []))}<br><b>Number shapes:</b> {', '.join(d.get('number_shapes', []))}{ev}"), 0.85
        text = json.dumps(d, ensure_ascii=False)
        return f"<span>{text[:900]}{'…' if len(text) > 900 else ''}</span>{ev}", 0.8
