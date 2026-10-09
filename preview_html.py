"""BITS XML -> readable HTML preview (open in any browser).

  py preview_html.py output\\9788419284006            (book folder)
  py preview_html.py output\\9788419284006\\9788419284006.xml

Writes <ISBN>_preview.html next to the XML; images are taken from the book's images\\ folder.
Shows: book metadata, front matter, parts/chapters, sections, paragraphs, lists, figures, tables,
boxes, references, footnotes, index; links (xref) are clickable; page markers are shown as [p. N].
The XML is not changed. This is a review aid, not the deliverable.
"""
from __future__ import annotations

import html
import shutil
import sys
import webbrowser
from pathlib import Path

from lxml import etree

XLINK = "{http://www.w3.org/1999/xlink}href"
CSS = """
@font-face{font-family:'BITS STIX Math';src:url('preview-assets/STIX2Math.woff2') format('woff2');font-display:swap}
math{font-family:'BITS STIX Math','STIX Two Math','Cambria Math',math}
body{font-family:Georgia,'Times New Roman','BITS STIX Math',serif;max-width:900px;margin:0 auto;padding:24px 20px 80px;color:#222;line-height:1.55;background:#fff}
h1,h2,h3,h4,h5,h6{font-family:Arial,Helvetica,sans-serif;color:#1d3c6e;line-height:1.25}
.meta{background:#f3f6fb;border:1px solid #d6e0ef;border-radius:6px;padding:12px 16px;font-family:Arial,sans-serif;font-size:14px}
.part{border-top:4px solid #1d3c6e;margin-top:48px;padding-top:8px}
.chapter{border-top:2px solid #8aa4cc;margin-top:40px;padding-top:6px}
.label{color:#8a8a8a;font-size:.8em;display:block}
.pg{display:inline-block;font:11px Arial,sans-serif;color:#fff;background:#b0b7c3;border-radius:3px;padding:0 4px;margin:0 3px;vertical-align:middle}
figure{margin:18px 0;padding:10px;border:1px solid #e3e3e3;border-radius:6px;background:#fafafa}
figure img{max-width:100%;height:auto;display:block;margin:0 auto}
figcaption{font-size:.9em;margin-top:6px}
.inl{height:1.2em;vertical-align:middle}
table{border-collapse:collapse;margin:8px 0;font-size:.9em;width:100%}
td,th{border:1px solid #c9c9c9;padding:4px 6px;vertical-align:top;text-align:left}
th{background:#eef2f8}
.tw{margin:18px 0;padding:10px;border:1px solid #d6e0ef;border-radius:6px}
.tw .cap{font-size:.95em;margin-bottom:6px}
.foot{font-size:.85em;color:#555}
.box{background:#fff8e1;border-left:4px solid #e0b000;padding:8px 14px;margin:16px 0}
.refs li{font-size:.9em;margin-bottom:4px}
.fn{font-size:.85em;color:#444;border-top:1px solid #ddd;margin-top:16px;padding-top:6px}
.disp-formula{display:block;text-align:center;margin:1.4em auto;padding:.55em .8em;max-width:100%;overflow-x:auto;font-size:1.12em;line-height:1.8;background:#fff;border:1px solid #e8edf4;border-radius:5px}
.disp-formula math{font-size:1.08em}
.disp-formula mfrac{vertical-align:middle}
.disp-formula .eq-fallback{display:inline-block;white-space:normal;font-style:italic;letter-spacing:.01em}
.disp-formula sub,.disp-formula sup{font-size:.72em;line-height:0;position:relative;vertical-align:baseline}
.disp-formula sub{bottom:-.35em}.disp-formula sup{top:-.55em}
a.x{color:#0b62c4;text-decoration:none;border-bottom:1px dotted #0b62c4}
.toc td{border:none;padding:2px 6px}
.idx{column-count:2;font-size:.9em}
.idx p{margin:0 0 2px}
.warn{color:#b00020}
nav.top{position:sticky;top:0;background:#fff;border-bottom:1px solid #eee;padding:6px 0;font:13px Arial,sans-serif;z-index:5}
nav.top a{margin-right:10px}
"""


def tag(el) -> str:
    t = el.tag
    return t.split("}")[-1] if isinstance(t, str) else ""


class Renderer:
    def __init__(self, img_dir: str):
        self.img = img_dir
        self.out: list[str] = []

    def esc(self, s):
        return html.escape(s or "")

    # ------------------------------------------------------------ inline
    def inline(self, el) -> str:
        parts = [self.esc(el.text)]
        for c in el:
            parts.append(self.inline_el(c))
            parts.append(self.esc(c.tail))
        return "".join(parts)

    def inline_el(self, c) -> str:
        t = tag(c)
        inner = self.inline(c)
        if t == "bold":
            return f"<b>{inner}</b>"
        if t == "italic":
            return f"<i>{inner}</i>"
        if t == "underline":
            return f"<u>{inner}</u>"
        if t == "sup":
            return f"<sup>{inner}</sup>"
        if t == "sub":
            return f"<sub>{inner}</sub>"
        if t in ("sc", "monospace"):
            return f"<span style='font-variant:small-caps'>{inner}</span>"
        if t == "break":
            return "<br>"
        if t == "xref":
            return f"<a class='x' href='#{self.esc(c.get('rid'))}' title='{self.esc(c.get('ref-type'))}'>{inner}</a>"
        if t == "target":
            if c.get("target-type") == "pagenum":
                pid = c.get("id") or ""
                return f"<span class='pg' id='{self.esc(pid)}'>p. {self.esc(pid.replace('page', ''))}</span>"
            return f"<a id='{self.esc(c.get('id'))}'></a>{inner}"
        if t == "inline-formula":
            return self.equation_content(c)
        if t == "inline-graphic":
            return f"<img class='inl' src='{self.img}/{self.esc(c.get(XLINK))}' alt=''>"
        if t == "uri" or t == "ext-link":
            href = c.get(XLINK) or c.text or ""
            return f"<a href='{self.esc(href)}' target='_blank'>{inner}</a>"
        if t == "fig":                       # a figure placed inside a paragraph / list item
            return "</p>" + self.block_str(c) + "<p>"
        if t == "table-wrap":
            return "</p>" + self.block_str(c) + "<p>"
        if t == "nav-pointer":
            return f"<a class='x' href='#{self.esc(c.get('rid'))}'>{inner}</a>"
        return inner

    def mathml_html(self, el) -> str:
        """Render MathML nodes in the browser preview without flattening structure."""
        name = tag(el)
        allowed = {"math", "mrow", "mi", "mn", "mo", "mtext", "msub", "msup", "msubsup",
                   "mfrac", "msqrt", "mroot", "mfenced", "mtable", "mtr", "mtd", "mover", "munder", "munderover"}
        if name not in allowed:
            return self.inline(el)
        attrs = [' xmlns="http://www.w3.org/1998/Math/MathML"'] if name == "math" else []
        for key in ("display", "mathvariant", "stretchy", "fence", "separator", "accent", "columnalign", "rowalign"):
            value = el.get(key)
            if value is not None:
                attrs.append(f' {key}="{self.esc(value)}"')
        content = self.esc(el.text or "")
        for child in el:
            content += self.mathml_html(child) + self.esc(child.tail or "")
        return f"<{name}{''.join(attrs)}>{content}</{name}>"

    def equation_content(self, el) -> str:
        graphic = next((node for node in el if tag(node) == "graphic"), None)
        if graphic is not None:
            href = graphic.get("{http://www.w3.org/1999/xlink}href", "")
            alt = graphic.findtext("alt-text") or "Equation requiring review"
            return f'<img src="{self.esc(self.img + "/" + href)}" alt="{self.esc(alt)}">'
        math_nodes = [node for node in el.iter() if tag(node) == "math"]
        if math_nodes:
            return self.mathml_html(math_nodes[0])
        # Older conversion runs store only linearized text. Preserve script
        # markers as actual sub/superscripts in the preview rather than raw text.
        raw = " ".join(part.strip() for part in el.itertext() if part.strip())
        if not raw:
            return ""
        escaped = self.esc(raw)
        escaped = __import__("re").sub(r"(?<![A-Za-z0-9])([A-Za-z])_([A-Za-z0-9]+)", r"\1<sub>\2</sub>", escaped)
        escaped = __import__("re").sub(r"(?<![A-Za-z0-9])([A-Za-z])\^([A-Za-z0-9+−-]+)", r"\1<sup>\2</sup>", escaped)
        return f"<span class='eq-fallback'>{escaped}</span>"

    # ------------------------------------------------------------ blocks
    def block_str(self, el) -> str:
        saved = self.out
        self.out = []
        self.block(el, 2)
        s = "".join(self.out)
        self.out = saved
        return s

    def w(self, s):
        self.out.append(s)

    def title_of(self, el):
        tg = el.find("title-group") if el.find("title-group") is not None else None
        meta = el.find("book-part-meta")
        if meta is not None and meta.find("title-group") is not None:
            tg = meta.find("title-group")
        if tg is None:
            t = el.find("title")
            return "", (self.inline(t) if t is not None else "")
        lab = tg.find("label")
        tit = tg.find("title")
        sub = tg.find("subtitle")
        s = self.inline(tit) if tit is not None else ""
        if sub is not None:
            s += f"<br><small>{self.inline(sub)}</small>"
        return (self.inline(lab) if lab is not None else ""), s

    def heading(self, level, el, cls=""):
        lab, tit = self.title_of(el)
        level = max(1, min(6, level))
        lab_html = f"<span class='label'>{lab}</span>" if lab else ""
        self.w(f"<h{level} id='{self.esc(el.get('id'))}' class='{cls}'>{lab_html}{tit}</h{level}>")

    def block(self, el, level):
        t = tag(el)
        if t in ("book-part-meta", "title-group", "label", "title", "subtitle"):
            return
        if t == "p":
            self.w(f"<p id='{self.esc(el.get('id'))}'>{self.inline(el)}</p>")
        elif t == "sec":
            self.heading(level, el)
            for c in el:
                if tag(c) not in ("title", "label"):
                    self.block(c, level + 1)
        elif t == "list":
            lt = el.get("list-type")
            tagn = "ol" if lt in ("order", "number", "alpha-lower", "alpha-upper", "roman-lower", "roman-upper") else "ul"
            style = {"alpha-lower": "lower-alpha", "alpha-upper": "upper-alpha", "roman-lower": "lower-roman",
                     "roman-upper": "upper-roman", "simple": "none"}.get(lt, "")
            self.w(f"<{tagn} style='list-style-type:{style}'>" if style else f"<{tagn}>")
            for li in el:
                if tag(li) == "list-item":
                    self.w("<li>")
                    lab = li.find("label")
                    if lab is not None:
                        self.w(f"<b>{self.inline(lab)}</b> ")
                    for c in li:
                        if tag(c) != "label":
                            self.block(c, level)
                    self.w("</li>")
            self.w(f"</{tagn}>")
        elif t == "fig":
            self.w(f"<figure id='{self.esc(el.get('id'))}'>")
            for g in el.iter("{*}graphic", "graphic"):
                self.w(f"<img src='{self.img}/{self.esc(g.get(XLINK))}' alt='{self.esc(g.get(XLINK))}' loading='lazy'>")
            lab = el.find("label")
            cap = el.find("caption")
            capt = ""
            if cap is not None:
                capt = " ".join(self.inline(x) for x in cap if tag(x) in ("title", "p"))
            if lab is not None or capt:
                self.w(f"<figcaption><b>{self.inline(lab) if lab is not None else ''}</b> {capt}</figcaption>")
            if not list(el.iter("{*}graphic", "graphic")):
                self.w("<p class='warn'>[no image]</p>")
            self.w("</figure>")
        elif t == "table-wrap":
            self.w(f"<div class='tw' id='{self.esc(el.get('id'))}'>")
            lab = el.find("label")
            cap = el.find("caption")
            capt = " ".join(self.inline(x) for x in cap if tag(x) in ("title", "p")) if cap is not None else ""
            if lab is not None or capt:
                self.w(f"<div class='cap'><b>{self.inline(lab) if lab is not None else ''}</b> {capt}</div>")
            for g in el.findall("graphic"):
                self.w(f"<img src='{self.img}/{self.esc(g.get(XLINK))}' style='max-width:100%'>")
            for tb in el.iter("table"):
                self.w("<table>")
                for row in tb.iter("tr"):
                    self.w("<tr>")
                    for cell in row:
                        ct = tag(cell)
                        if ct in ("td", "th"):
                            span = "".join(f" {a}='{self.esc(cell.get(a))}'" for a in ("colspan", "rowspan") if cell.get(a))
                            inner = self.inline(cell) if not len([x for x in cell if tag(x) in ("p", "list")]) \
                                else "".join(self.block_str(x) if tag(x) in ("p", "list") else self.inline_el(x) for x in cell)
                            self.w(f"<{ct}{span}>{inner}</{ct}>")
                    self.w("</tr>")
                self.w("</table>")
            foot = el.find("table-wrap-foot")
            if foot is not None:
                self.w("<div class='foot'>")
                for c in foot.iter("p"):
                    self.w(f"<p>{self.inline(c)}</p>")
                self.w("</div>")
            self.w("</div>")
        elif t == "boxed-text":
            self.w(f"<div class='box' id='{self.esc(el.get('id'))}'>")
            cap = el.find("caption")
            if cap is not None:
                tt = cap.find("title")
                if tt is not None:
                    self.w(f"<p><b>{self.inline(tt)}</b></p>")
            for c in el:
                if tag(c) not in ("caption", "label"):
                    self.block(c, level + 1)
            self.w("</div>")
        elif t == "disp-formula":
            self.w(f"<div class='disp-formula' role='math' aria-label='{self.esc(' '.join(el.itertext()))}' id='{self.esc(el.get('id'))}'>{self.equation_content(el)}</div>")
        elif t == "ref-list":
            tt = el.find("title")
            if tt is not None:
                self.w(f"<h{min(6, level)}>{self.inline(tt)}</h{min(6, level)}>")
            self.w("<ol class='refs' style='list-style:none;padding-left:0'>")
            for r in el.findall("ref"):
                lab = r.find("label")
                mc = r.find("mixed-citation") if r.find("mixed-citation") is not None else r.find("element-citation")
                self.w(f"<li id='{self.esc(r.get('id'))}'><b>{self.inline(lab) if lab is not None else ''}</b> "
                       f"{self.inline(mc) if mc is not None else ''}</li>")
            self.w("</ol>")
            for c in el:
                if tag(c) == "ref-list":
                    self.block(c, level + 1)
        elif t == "fn-group":
            self.w("<div class='fn'>")
            for fn in el.iter("fn"):
                lab = fn.find("label")
                body = " ".join(self.inline(p) for p in fn.findall("p"))
                self.w(f"<p id='{self.esc(fn.get('id'))}'><sup>{self.inline(lab) if lab is not None else ''}</sup> {body}</p>")
            self.w("</div>")
        elif t in ("body", "back", "named-book-part-body", "book-body", "book-back", "front-matter"):
            for c in el:
                self.block(c, level)
        elif t == "book-part":
            kind = el.get("book-part-type")
            cls = "part" if kind == "part" else "chapter"
            self.heading(1 if kind == "part" else 2, el, cls)
            meta = el.find("book-part-meta")
            if meta is not None:
                names = [" ".join(x.strip() for x in c.itertext() if x.strip()) for c in meta.iter("contrib")]
                pages = (meta.findtext("fpage") or "", meta.findtext("lpage") or "")
                info = []
                if names:
                    info.append(", ".join(names))
                if pages[0]:
                    info.append(f"pp. {pages[0]}–{pages[1]}")
                if info:
                    self.w(f"<p style='font:13px Arial;color:#666'>{self.esc(' · '.join(info))}</p>")
            for c in el:
                if tag(c) != "book-part-meta":
                    self.block(c, 2 if kind == "part" else 3)
        elif t in ("front-matter-part", "preface", "foreword", "dedication", "ack"):
            self.heading(2, el, "chapter")
            for c in el:
                if tag(c) != "book-part-meta":
                    self.block(c, 3)
        elif t == "toc":
            self.heading(2, el, "chapter")
            self.w("<table class='toc'>")
            for te in el.iter("toc-entry"):
                lab = te.find("label")
                tt = te.find("title")
                nps = te.findall("nav-pointer")
                self.w(f"<tr><td>{self.inline(lab) if lab is not None else ''}</td><td>{self.inline(tt) if tt is not None else ''}</td>"
                       f"<td>{' '.join(self.inline_el(n) for n in nps)}</td></tr>")
            self.w("</table>")
        elif t == "index":
            self.heading(2, el, "chapter")
            self.w("<div class='idx'>")
            for ie in el.iter("index-entry"):
                depth = sum(1 for a in ie.iterancestors() if tag(a) == "index-entry")
                term = ie.find("term")
                nps = [self.inline_el(n) for n in ie.findall("nav-pointer-group/nav-pointer")]
                self.w(f"<p style='margin-left:{depth * 16}px'>{self.inline(term) if term is not None else ''} {', '.join(nps)}</p>")
            self.w("</div>")
        elif t in ("index-entry", "toc-entry", "nav-pointer-group"):
            return
        else:
            # anything else: render its children so no content is hidden
            if el.text and el.text.strip():
                self.w(f"<p>{self.inline(el)}</p>")
            else:
                for c in el:
                    self.block(c, level)

    # ------------------------------------------------------------ book
    def book(self, root):
        bm = root.find("book-meta")
        title = (bm.findtext(".//book-title") if bm is not None else "") or "Book"
        self.w(f"<h1>{self.esc(title)}</h1>")
        if bm is not None:
            rows = []
            for bid in bm.findall("book-id"):
                rows.append(("Book ID (" + (bid.get("book-id-type") or "") + ")", (bid.text or "").strip() or "<span class='warn'>empty</span>"))
            rows.append(("Short name", bm.findtext(".//alt-title") or ""))
            isbn = bm.find("isbn")
            if isbn is not None:
                rows.append(("ISBN", isbn.text or ""))
            ed = bm.findtext("edition")
            if ed:
                rows.append(("Edition", ed))
            yr = bm.findtext("pub-date/year")
            if yr:
                rows.append(("Year", yr))
            pub = bm.findtext("publisher/publisher-name")
            if pub:
                rows.append(("Publisher", pub))
            contribs = ["; ".join(" ".join(x.strip() for x in c.itertext() if x.strip()) for c in bm.iter("contrib"))]
            if contribs[0]:
                rows.append(("Contributors", contribs[0]))
            cr = bm.findtext(".//copyright-statement")
            if cr:
                rows.append(("Copyright", cr))
            self.w("<div class='meta'><table>" + "".join(
                f"<tr><th style='width:160px'>{self.esc(k)}</th><td>{v if '<span' in str(v) else self.esc(v)}</td></tr>" for k, v in rows)
                + "</table></div>")
        nav = []
        for bp in root.iter("book-part"):
            lab, tit = self.title_of(bp)
            if bp.get("id"):
                nav.append(f"<a href='#{self.esc(bp.get('id'))}'>{lab or tit[:30]}</a>")
        if nav:
            self.out.insert(0, "<nav class='top'>" + " ".join(nav[:80]) + "</nav>")
        for sect in ("front-matter", "book-body", "book-back"):
            el = root.find(sect)
            if el is not None:
                self.block(el, 2)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    p = Path(argv[0])
    if p.is_dir():
        xmls = [x for x in p.glob("*.xml")]
        if not xmls:
            print(f"no XML in {p}")
            return 2
        p = xmls[0]
    parser = etree.XMLParser(load_dtd=False, resolve_entities=False, recover=True, huge_tree=True)
    root = etree.parse(str(p), parser).getroot()
    for el in root.iter():                      # drop namespaces from element names for simpler matching
        if isinstance(el.tag, str) and "}" in el.tag and "mathml" not in el.tag.lower():
            el.tag = el.tag.split("}")[-1]
    r = Renderer("images")
    r.book(root)
    title = root.findtext(".//book-title") or p.stem
    out = p.with_name(p.stem + "_preview.html")
    assets = out.parent / "preview-assets"
    assets.mkdir(exist_ok=True)
    for name in ("STIX2Math.woff2", "OFL.txt"):
        shutil.copyfile(Path(__file__).resolve().parent / "assets" / "fonts" / name, assets / name)
    out.write_text("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
                   "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                   f"<title>{html.escape(title)} – preview</title><style>{CSS}</style></head><body>"
                   + "".join(r.out) + "</body></html>", encoding="utf-8")
    print(f"preview: {out}")
    if "--no-open" not in argv:
        try:
            webbrowser.open(out.resolve().as_uri())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
