"""PDF ↔ XML comparison for a converted book (lazy: one PDF page rendered at a time).

Left : the PDF page with every text line outlined —
       green = found in the XML page, yellow = partly, red = missing.
Right: the XML text for the same printed page (between its page targets);
       words that are not on the PDF page are highlighted.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QScrollArea, QSpinBox, QSplitter, QTextEdit,
                               QVBoxLayout, QWidget)

from .app_state import theme_tokens
from .widgets.common import Card, Page, button, label


def words(s: str) -> list[str]:
    s = unicodedata.normalize("NFKC", s or "").replace("­", "").lower()
    return re.findall(r"\w+", s)


def xml_page_texts(xml_path: Path) -> dict[str, str]:
    """page-target id -> text that follows it until the next page target (document order)."""
    from lxml import etree
    out: dict[str, list[str]] = {}
    cur = "_start"
    out[cur] = []
    blocks = {"p", "title", "label", "td", "th", "term", "license-p", "mixed-citation", "list-item", "caption"}
    for ev, el in etree.iterparse(str(xml_path), events=("start", "end"), load_dtd=False, huge_tree=True, resolve_entities=False):
        tag = el.tag.split("}", 1)[-1] if isinstance(el.tag, str) else ""
        if ev == "start":
            if tag == "target" and el.get("target-type") == "pagenum":
                cur = el.get("id")
                out.setdefault(cur, [])
            elif el.text:
                out[cur].append(el.text)
        else:
            if tag in blocks:
                out[cur].append("\n\n")
            if el.tail:
                out[cur].append(el.tail)
    res = {}
    for k, v in out.items():
        t = "".join(v)
        t = re.sub(r"[ \t]*\n[ \t\n]*\n[ \t\n]*", "\x00", t)       # paragraph breaks
        t = re.sub(r"\s+", " ", t).replace("\x00", "\n\n").strip()
        res[k] = t
    return res


class ComparePage(Page):
    def __init__(self, ctx):
        super().__init__("PDF ↔ XML Comparison", "Check a converted book page by page. Red = missing, green = matched, yellow = changed.", scroll=False)
        self.ctx = ctx
        self.book_dir = None
        self.src = None
        self.page_texts = {}
        self.page_qa = []
        self.idx = 0
        self.doc_cache = {}
        bar = Card(margins=(12, 10, 12, 10))
        h = QHBoxLayout()
        self.book_lbl = label("No converted book selected", "muted")
        h.addWidget(self.book_lbl, 1)
        pick = button("Select Output Folder", icon_name="folder")
        pick.clicked.connect(self.pick)
        h.addWidget(pick)
        bar.lay.addLayout(h)
        nav = QHBoxLayout()
        self.prev_issue = button("Previous Issue", icon_name="chevron-left")
        self.prev_issue.clicked.connect(lambda: self.jump_issue(-1))
        self.prev_btn = button("Previous", icon_name="chevron-left")
        self.prev_btn.clicked.connect(lambda: self.show_page(self.idx - 1))
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.valueChanged.connect(lambda v: self.show_page(v - 1))
        self.page_total = label("of —", "muted")
        self.next_btn = button("Next", icon_name="chevron-right")
        self.next_btn.clicked.connect(lambda: self.show_page(self.idx + 1))
        self.next_issue = button("Next Issue", icon_name="chevron-right")
        self.next_issue.clicked.connect(lambda: self.jump_issue(1))
        for w in (self.prev_issue, self.prev_btn, label("Page", "muted"), self.page_spin, self.page_total, self.next_btn, self.next_issue):
            nav.addWidget(w)
        nav.addStretch(1)
        self.issue_lbl = label("", "warn")
        nav.addWidget(self.issue_lbl)
        bar.lay.addLayout(nav)
        self.add(bar)
        sp = QSplitter(Qt.Horizontal)
        self.pdf_view = QLabel()
        self.pdf_view.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(self.pdf_view)
        sp.addWidget(sa)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.stats = label("", "muted")
        rv.addWidget(self.stats)
        self.xml_view = QTextEdit()
        self.xml_view.setReadOnly(True)
        rv.addWidget(self.xml_view, 1)
        sp.addWidget(right)
        sp.setSizes([560, 520])
        self.add(sp, 1)

    def pick(self):
        p = QFileDialog.getExistingDirectory(self, "Select a converted book folder (output/<ISBN>)", str(self.ctx.cfg.path("output_dir")))
        if p:
            self.load_book(Path(p))

    def on_show(self, book_dir=None, page=None, pdf=None, xml=None, **kw):
        if book_dir:
            self.load_book(Path(book_dir))
        elif xml and Path(xml).parent.joinpath("intermediate", "source.json").exists():
            self.load_book(Path(xml).parent)
        elif self.book_dir is None and self.ctx.last_book_dir:
            self.load_book(self.ctx.last_book_dir)
        if page is not None and self.src:
            self.show_page(int(page))

    def load_book(self, d: Path):
        sj = d / "intermediate" / "source.json"
        xs = sorted(d.glob("*.xml"))
        if not sj.exists() or not xs:
            self.book_lbl.setText(f"{d.name}: not a conversion output folder (needs intermediate/source.json and the XML).")
            return
        self.book_dir = d
        self.src = json.loads(sj.read_text(encoding="utf-8"))
        self.page_texts = xml_page_texts(xs[0])
        qa = d / "qa" / "page_qa.json"
        self.page_qa = json.loads(qa.read_text(encoding="utf-8")) if qa.exists() else []
        self.book_lbl.setText(f"{d.name}  ·  {xs[0].name}")
        n = len(self.src["pages"])
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, n))
        self.page_spin.blockSignals(False)
        self.page_total.setText(f"of {n:,}")
        self.show_page(0)

    def _doc(self, path):
        import pymupdf
        if path not in self.doc_cache:
            if len(self.doc_cache) > 3:
                for k in list(self.doc_cache)[:2]:
                    self.doc_cache.pop(k).close()
            self.doc_cache[path] = pymupdf.open(path)
        return self.doc_cache[path]

    def jump_issue(self, step):
        if not self.page_qa:
            return
        i = self.idx + step
        while 0 <= i < len(self.page_qa):
            if self.page_qa[i]["status"] != "PASS":
                self.show_page(i)
                return
            i += step

    def show_page(self, i):
        if not self.src:
            return
        pages = self.src["pages"]
        if not (0 <= i < len(pages)):
            return
        self.idx = i
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(i + 1)
        self.page_spin.blockSignals(False)
        info = pages[i]
        folio = info.get("folio")
        seg = self.page_texts.get(f"page{str(folio).lower()}", "") if folio else ""
        xml_words = Counter(words(seg))
        import pymupdf
        try:
            doc = self._doc(info["pdf"])
            page = doc[info["pdf_page"]]
        except Exception as e:
            self.pdf_view.setText(f"Cannot open PDF: {e}")
            return
        zoom = 1.4
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        qp = QPixmap.fromImage(img)
        painter = QPainter(qp)
        found = missing = partial = 0
        remaining = Counter(xml_words)
        pdf_words = Counter()
        all_lines = [l for b in page.get_text("dict")["blocks"] if b.get("type") == 0 for l in b["lines"]]
        carry = None
        for li, l in enumerate(all_lines):
            if True:
                txt = "".join(s["text"] for s in l["spans"])
                ws = words(txt)
                if carry and ws:
                    ws[0] = carry + ws[0]
                carry = None
                if txt.rstrip().endswith("-") and ws and li + 1 < len(all_lines):
                    carry = ws.pop()
                    if not ws:
                        continue
                if not ws:
                    continue
                pdf_words.update(ws)
                hit = 0
                for w in ws:
                    if remaining[w] > 0:
                        remaining[w] -= 1
                        hit += 1
                ratio = hit / len(ws)
                if ratio >= 0.9:
                    col, found = QColor(21, 128, 61), found + 1
                elif ratio >= 0.4:
                    col, partial = QColor(202, 138, 4), partial + 1
                else:
                    col, missing = QColor(185, 28, 28), missing + 1
                x0, y0, x1, y1 = l["bbox"]
                fill = QColor(col)
                fill.setAlpha(38)
                painter.setPen(QPen(col, 1.2))
                painter.setBrush(QBrush(fill))
                painter.drawRect(QRectF(x0 * zoom, y0 * zoom, (x1 - x0) * zoom, (y1 - y0) * zoom))
        painter.end()
        self.pdf_view.setPixmap(qp)
        # right side: xml text with extra words highlighted
        t = theme_tokens()
        parts = []
        for tok in re.split(r"(\w+)", seg):
            if re.fullmatch(r"\w+", tok or ""):
                w = words(tok)
                if w and pdf_words[w[0]] <= 0:
                    parts.append(f"<span style='background:{t['warning_soft']};color:{t['warning']}'>{html.escape(tok)}</span>")
                else:
                    parts.append(html.escape(tok))
            else:
                parts.append(html.escape(tok))
        body = "".join(parts).replace("\n", "<br>") or "<i>No XML content is anchored to this page (no page target).</i>"
        self.xml_view.setHtml(f"<div style='font-size:13px;line-height:1.45'>{body}</div>")
        qa = self.page_qa[i] if i < len(self.page_qa) else {}
        self.stats.setText(f"Printed page {folio or '—'}   ·   lines: {found} matched · {partial} changed · {missing} missing"
                           + (f"   ·   coverage {qa.get('coverage'):.0%}" if qa.get("coverage") is not None else ""))
        st = qa.get("status")
        self.issue_lbl.setText("" if st in (None, "PASS") else f"⚠ {st}: {', '.join(qa.get('issues') or []) or 'low coverage'}")
