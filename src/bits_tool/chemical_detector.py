"""Group uncaptioned molecular artwork using atom labels and nearby bond strokes.

This recognizes a diagram region, not molecular identity or stereochemistry.
Actual PDF vectors are exported intact. The graph records geometry as evidence;
no chemical connectivity, reaction, or bond order is fabricated.
"""
import re
from .document_tree import Region

ATOM = re.compile(r'(?:(?:Cl|Br|Si|Na|Ca|Mg|Fe|Zn|[CNOHSPFIRBK])[0-9₀-₉⁰-⁹+−–-]*)+')
CORE = re.compile(r'(?:[CNOHSP][0-9₀-₉]*)+')


def _intersects(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def chemical_regions(page, style, occupied):
    em = style.body_size
    nodes = []
    for line in page.lines:
        text = line.text.strip()
        words = text.split()
        if (line.role == 'body' and words and len(text) <= 24 and
                all(ATOM.fullmatch(w.lstrip('−–-+')) or re.fullmatch('[A-Z]', w) for w in words) and
                not any(_intersects(line.bbox, box) for box in occupied)):
            nodes.append(('text', line, line.bbox))
    if len(nodes) < 4:
        return []
    text_boxes = [n[2] for n in nodes]
    for drawing in page.drawings:
        b = drawing['bbox']; w, h = b[2]-b[0], b[3]-b[1]
        if not (0 < max(w,h) <= 12 * em and
                ("l" in drawing.get("kinds", "l") or
                 (drawing.get("kinds") == "re" and min(w,h) < 2))):
            continue
        if any(_intersects(b, box) for box in occupied):
            continue
        # Bound graph size to objects close to a plausible atomic label.
        if any(max(b[0]-t[2], t[0]-b[2], 0) <= 3*em and
               max(b[1]-t[3], t[1]-b[3], 0) <= 3*em for t in text_boxes):
            nodes.append(('path', drawing, b))
    parent = list(range(len(nodes)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, (_, _, a) in enumerate(nodes):
        for j in range(i):
            b = nodes[j][2]
            dx, dy = max(a[0]-b[2], b[0]-a[2], 0), max(a[1]-b[3], b[1]-a[3], 0)
            if dx*dx + dy*dy <= (1.15*em)**2:
                parent[root(i)] = root(j)
    components = {}
    for i, node in enumerate(nodes):
        components.setdefault(root(i), []).append(node)
    regions = []
    for component in components.values():
        lines = [obj for kind, obj, _ in component if kind == 'text']
        paths = [obj for kind, obj, _ in component if kind == 'path']
        core = [l for l in lines if CORE.fullmatch(l.text.strip())]
        if len(core) < 4 or len({l.text.strip() for l in core}) < 2 or len(paths) < 4:
            continue
        if not (any(d['bbox'][2]-d['bbox'][0] > em/2 for d in paths) and
                any(d['bbox'][3]-d['bbox'][1] > em/2 for d in paths)):
            continue
        boxes = [n[2] for n in component]
        b = (min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes))
        # Any connected but omitted path extending beyond the crop is evidence
        # of an incomplete graph. Preserve normal extraction and request review.
        crossing = [d for d in page.drawings if d.get('n', 0) and d not in paths and
                    _intersects(d['bbox'], b) and not
                    (b[0] <= d['bbox'][0] and b[1] <= d['bbox'][1] and
                     b[2] >= d['bbox'][2] and b[3] >= d['bbox'][3]) and
                    not d.get('fill') and not any(_intersects(d['bbox'], box) for box in occupied)]
        if crossing:
            page.diagnostics.append({'action':'chemical-candidate-review', 'bbox':b,
                                     'reason':'connected vector paths extend outside molecular candidate'})
            continue
        prose = [l for l in page.lines if l.role == 'body' and l not in lines and _intersects(l.bbox, b)]
        if prose:
            page.diagnostics.append({'action':'chemical-candidate-review', 'bbox':b,
                                     'reason':'atomic labels and strokes overlap other text'})
            continue
        for line in lines:
            line.role = 'figure-text'
        graph = {'type':'chemical-diagram', 'recognition':'atom labels with connected spatial strokes',
                 'labels':[{'text':l.text, 'bbox':l.bbox} for l in lines],
                 'strokes':[{'bbox':d['bbox'], 'paths':d.get('paths', [])} for d in paths],
                 'semantic_bond_assignment':False}
        regions.append(Region('figure', b, page.index, .85,
                              {'art':b, 'label':None, 'key':None, 'caption':[], 'absorbed':lines,
                               'unlabeled':True, 'diagram':graph, 'asset_format':'svg'}))
        page.diagnostics.append({'action':'include-chemical-diagram', 'bbox':b,
                                 'reason':graph['recognition'], 'labels':len(lines), 'strokes':len(paths)})
    return regions
