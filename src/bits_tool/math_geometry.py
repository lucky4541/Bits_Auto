"""Recognize drawn arrows from shafts and arrowheads, never from boxes alone."""
from .document_tree import Span


def horizontal_arrow(drawing):
    b = drawing['bbox']
    w, h = b[2] - b[0], b[3] - b[1]
    if h < 2 or w < 10 or w < 3 * h:
        return None
    paths = drawing.get('paths', [])
    shafts = [p for p in paths if p[0] == 'l' and
              abs(p[1][1] - p[2][1]) < .1 * h and abs(p[1][0] - p[2][0]) > .65 * w]
    if not shafts:
        return None
    cy = sum(p[1][1] + p[2][1] for p in shafts) / (2 * len(shafts))
    pts = [pt for p in paths if p[0] in ('l', 'c') for pt in p[1:]]
    wings = [p for p in pts if abs(p[1] - cy) > .22 * h]
    if not wings or not (any(p[1] < cy for p in wings) and any(p[1] > cy for p in wings)):
        return None
    for side, tip in ((1, b[2]), (-1, b[0])):
        if (all(side * (p[0] - (b[0] + b[2]) / 2) > .22 * w for p in wings) and
                any(abs(p[0] - tip) < .05 * w and abs(p[1] - cy) < .2 * h for p in pts)):
            return side
    return None


def vector_arrows(drawings, bbox, em):
    candidates = []
    for d in drawings:
        b = d['bbox']
        if b[0] < bbox[0] - em or b[2] > bbox[2] + em or b[1] < bbox[1] - em or b[3] > bbox[3] + em:
            continue
        direction = horizontal_arrow(d)
        if direction:
            candidates.append((d, direction))
    used, spans, evidence = set(), [], []
    for i, (d, direction) in enumerate(candidates):
        if i in used:
            continue
        b = d['bbox']
        paired = [(j, other) for j, (other, direct) in enumerate(candidates) if j > i and j not in used
                  and direct != direction and abs(other['bbox'][0]-b[0]) < .2 * em
                  and abs(other['bbox'][2]-b[2]) < .2 * em
                  and abs(sum(other['bbox'][1::2])/2 - sum(b[1::2])/2) < em]
        arrow = '→' if direction > 0 else '←'
        sources = [d]
        if len(paired) == 1:
            j, other = paired[0]
            used.add(j)
            top_direction = direction if b[1] < other['bbox'][1] else -direction
            arrow = '⇄' if top_direction > 0 else '⇆'
            c = other['bbox']
            b = (min(b[0], c[0]), min(b[1], c[1]), max(b[2], c[2]), max(b[3], c[3]))
            sources.append(other)
        spans.append(Span(arrow, 'PDF-vector-arrow', em, bbox=b))
        evidence.extend(sources)
    return spans, evidence
