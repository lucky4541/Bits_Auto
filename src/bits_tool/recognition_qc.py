"""Audit recognition against retained geometry and independently parsed XML.

PASS means the stated checks passed, not a proof of semantic equivalence.
Uncertain source interpretation always remains visible as REVIEW.
"""
from collections import Counter
import html
import json
from pathlib import Path
from lxml import etree

MATH_NS = 'http://www.w3.org/1998/Math/MathML'
M = {'m': MATH_NS}
ARITY = {'mfrac': 2, 'msub': 2, 'msup': 2, 'mover': 2, 'munder': 2,
         'munderover': 3, 'msubsup': 3, 'mroot': 2}


def mathml_errors(math):
    errors = []
    for element in math.iter():
        tag = etree.QName(element)
        if tag.namespace != MATH_NS:
            errors.append('incorrect MathML namespace')
        if tag.localname in ARITY and len(element) != ARITY[tag.localname]:
            errors.append(f'{tag.localname} requires {ARITY[tag.localname]} children')
        if tag.localname in ('mi', 'mn', 'mo', 'mtext') and not ''.join(element.itertext()).strip():
            errors.append(f'empty {tag.localname} token')
    if not math.xpath('.//m:mi|.//m:mn|.//m:mo', namespaces=M):
        errors.append('no mathematical tokens')
    return sorted(set(errors))


def _intersects(a, b):
    return a and b and a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def _contains(a, b, tolerance=.2):
    return a and b and all((a[0]-tolerance <= b[0], a[1]-tolerance <= b[1],
                           a[2]+tolerance >= b[2], a[3]+tolerance >= b[3]))


def audit_recognition(root, pages, tree, issues):
    items = []
    page_by = {p.index: p for p in pages}
    xml_by = {e.get('id'): e for e in tree.iter() if e.get('id')}
    seen_math = set()

    def add(kind, status, reason, page, bbox, target=None, **details):
        item = dict(kind=kind, status=status, reason=reason, page=page,
                    pdf_page=page_by[page].pdf_page + 1 if page in page_by else None,
                    bbox=bbox, target=target, **details)
        items.append(item)
        if status != 'PASS':
            issues.add('RECOGNITION_QC_' + ('FAILED' if status == 'FAILED' else 'REVIEW'),
                       'error' if status == 'FAILED' else 'warning', reason,
                       page=page, target=target)
        return item

    for node in root.walk():
        page = page_by.get(node.page)
        if node.kind == 'disp-formula':
            xml = xml_by.get(node.id)
            math = xml.find('m:math', M) if xml is not None else None
            if math is None:
                preserved = (xml is not None and xml.find('graphic') is not None and
                             bool(node.meta.get('href')))
                add('equation', 'REVIEW' if preserved else 'FAILED',
                    node.meta.get('reason', 'equation recognition unresolved') if preserved else
                    'Equation has neither MathML nor a preserved source asset',
                    node.page, node.bbox, node.id, asset=node.meta.get('href'))
            else:
                seen_math.add(math)
                errors = mathml_errors(math)
                add('equation', 'FAILED' if errors else 'PASS',
                    '; '.join(errors) if errors else node.meta.get('reason', 'MathML structure checked'),
                    node.page, node.bbox, node.id,
                    structures=dict(Counter(etree.QName(e).localname for e in math.iter())))
        elif node.kind == 'fig':
            art = node.meta.get('art')
            diagram = node.meta.get('diagram')
            if diagram:
                components = diagram.get('labels', []) + diagram.get('strokes', [])
                enclosed = all(_contains(art, c['bbox']) for c in components)
                add('chemical-diagram', 'REVIEW' if enclosed and node.meta.get('href') else 'FAILED',
                    'Atomic labels and strokes preserved as SVG; molecular identity, bond order and stereochemistry require human review'
                    if enclosed else 'Diagram components extend outside exported region',
                    node.page, art, node.id, asset=node.meta.get('href'),
                    labels=len(diagram.get('labels', [])), strokes=len(diagram.get('strokes', [])),
                    geometry_enclosed=enclosed, semantic_bond_assignment=False)
            if not page:
                continue
            images = [im for im in page.images if _intersects(im['bbox'], art) and im.get('xref')]
            if images:
                verified = all(im.get('clip_status') == 'verified' for im in images)
                overlap = [l for l in page.lines if l.role == 'body' and _intersects(l.bbox, art)]
                add('figure-clip', 'PASS' if verified and not overlap else 'REVIEW',
                    'Image clipping bounds verified against PDF painting state; crop does not intersect body text'
                    if verified and not overlap else 'Image clipping or overlap with body text requires review',
                    node.page, art, node.id, asset=node.meta.get('href'),
                    images=[{k: im.get(k) for k in ('bbox', 'full', 'clip_bounds', 'clip_status')} for im in images])

    # Inline mathematics are audited too, including expressions inside tables.
    for math in tree.xpath('//m:math', namespaces=M):
        if math in seen_math:
            continue
        errors = mathml_errors(math)
        owner = next((e for e in math.iterancestors() if e.get('id')), None)
        target = owner.get('id') if owner is not None else None
        add('inline-math', 'FAILED' if errors else 'PASS',
            '; '.join(errors) if errors else 'MathML namespace, tokens and child counts checked',
            None, None, target)
    for page in pages:
        for d in page.diagnostics:
            if d['action'] in ('chemical-candidate-review', 'image-clip-unavailable'):
                add(d['action'], 'REVIEW', d['reason'], page.index, d.get('bbox'))
    counts = dict(Counter(i['status'] for i in items))
    return {'status': 'FAILED' if counts.get('FAILED') else 'REVIEW' if counts.get('REVIEW') else 'PASS',
            'counts': counts, 'items': items,
            'scope': 'Structural and geometric checks; PASS does not certify complete source fidelity.'}


def write_recognition_report(out_dir, report, src, pages, xml_name):
    """Render only audited display regions, opening one source document at a time."""
    from .figure_detector import export_region
    qa = Path(out_dir) / 'qa'
    qa.mkdir(exist_ok=True)
    crops = qa / 'recognition-crops'
    crops.mkdir(exist_ok=True)
    page_by = {p.index: p for p in pages}
    rows = []
    doc, current_file = None, None
    try:
        for index, item in enumerate(report['items']):
            p = page_by.get(item['page'])
            if p and item['bbox'] and item['kind'] in ('equation', 'chemical-diagram', 'figure-clip'):
                fi, pno = src.page_map[p.index]
                if fi != current_file:
                    if doc is not None:
                        doc.close()
                    doc, current_file = src.open(fi), fi
                path = crops / f'region-{index:03d}.png'
                try:
                    export_region(doc, pno, item['bbox'], path, dpi=144, pad=0,
                                  rot=p.rot, orig_size=p.orig_size, whiten=False)
                    item['source_crop'] = str(path.relative_to(qa))
                except Exception as exc:
                    item['source_crop_error'] = type(exc).__name__
            esc = html.escape
            source = ('<img alt="Original PDF region" src="' + esc(item['source_crop']) + '">'
                      if item.get('source_crop') else '')
            target = item.get('target')
            link = ('<a href="../' + esc(xml_name.replace('.xml', '_preview.html')) + '#' + esc(target) + '">Converted region</a>') if target else ''
            rows.append('<tr><td>' + esc(item['status']) + '</td><td>' + esc(item['kind']) +
                        '<br>PDF page ' + esc(str(item['pdf_page'] or '—')) + '<br>' +
                        esc(str(item['bbox'] or '')) + '</td><td>' + esc(item['reason']) +
                        '<br>' + link + '</td><td>' + source + '</td></tr>')
    finally:
        if doc is not None:
            doc.close()
    (qa / 'recognition_qc.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (qa / 'recognition_qc.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Recognition QC</title>'
        '<style>body{font:16px system-ui;margin:2rem}td,th{padding:12px;border:1px solid #bbb;vertical-align:top}'
        'table{border-collapse:collapse}img{max-width:440px;max-height:300px}td{overflow-wrap:anywhere}</style>'
        '<h1>Automatic recognition QC: ' + report['status'] + '</h1><p>' + html.escape(report['scope']) +
        '</p><p>Source crops are paired with links to the converted regions. PDF page numbers start at 1.</p>'
        '<table><tr><th>Status</th><th>Region</th><th>Evidence / review reason</th><th>Original PDF</th></tr>' +
        ''.join(rows) + '</table></html>', encoding='utf-8')
