"""Graphic regions: raster images + clusters of vector drawings, figure regions
anchored on captions, crop rectangles and deterministic image export."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from .document_tree import Line, PageInfo


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _near(a, b, tol):
    return not (a[2] + tol < b[0] or b[2] + tol < a[0] or a[3] + tol < b[1] or b[3] + tol < a[1])


def _intersects(a, b, tol=0.0):
    return a[0] < b[2] - tol and b[0] < a[2] - tol and a[1] < b[3] - tol and b[1] < a[3] - tol


def graphic_blocks(page: PageInfo, min_pt: float = 12.0) -> list[dict]:
    """Candidate graphic objects: images and clustered vector art (not rules/backgrounds)."""
    tx0, ty0, tx1, ty1 = page.trim
    tarea = (tx1 - tx0) * (ty1 - ty0)
    out = []
    for im in page.images:
        b = im["bbox"]
        if _area(b) > 0.97 * tarea and len(page.lines) > 10:
            continue  # full-page background behind text
        if im.get("std") is not None and im["std"] < 4.0:
            continue  # flat tint: box/table background, not artwork
        out.append({"bbox": b, "kind": "image", "n": 1, "xref": im.get("xref")})
    # vector drawings: ignore thin rules (tables) and huge page-background rects
    art = []
    for d in page.drawings:
        b = d["bbox"]
        if d["hline"] or d["vline"]:
            continue
        if _area(b) > 0.6 * tarea:
            continue
        if b[2] - b[0] < 1 and b[3] - b[1] < 1:
            continue
        # plain filled rectangles are usually box backgrounds, not art
        if d["kinds"] in ("re",) and d["fill"] and d["n"] <= 1 and (b[2] - b[0]) > 60 and (b[3] - b[1]) > 20:
            continue
        art.append(dict(d))
    clusters: list[dict] = []
    for d in sorted(art, key=lambda d: (d["bbox"][1], d["bbox"][0])):
        placed = False
        for c in clusters:
            if _near(c["bbox"], d["bbox"], 6):
                c["bbox"] = _union(c["bbox"], d["bbox"])
                c["n"] += d["n"]
                c["curves"] += "c" in d["kinds"]
                placed = True
                break
        if not placed:
            clusters.append({"bbox": d["bbox"], "kind": "vector", "n": d["n"], "curves": int("c" in d["kinds"])})
    # merge clusters transitively
    changed = True
    while changed:
        changed = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if _near(clusters[i]["bbox"], clusters[j]["bbox"], 6):
                    clusters[i]["bbox"] = _union(clusters[i]["bbox"], clusters[j]["bbox"])
                    clusters[i]["n"] += clusters[j]["n"]
                    clusters[i]["curves"] += clusters[j]["curves"]
                    clusters.pop(j)
                    changed = True
                    break
            if changed:
                break
    for c in clusters:
        b = c["bbox"]
        if (b[2] - b[0]) >= min_pt and (b[3] - b[1]) >= min_pt and c["n"] >= 4:
            out.append(c)
    return out


def _stacked(page: PageInfo, a, b, vgap: float = 75, hgap: float = 45) -> bool:
    """Panels of one figure set next to / below each other, separated only by white space or short labels
    (no running-text line between them)."""
    def running_text_in(x0, y0, x1, y1):
        return any(l.role == "body" and len(l.text.strip()) > 45 and l.y0 >= y0 - 1 and l.y1 <= y1 + 1
                   and min(l.x1, x1) - max(l.x0, x0) > 0 for l in page.lines)
    hov = min(a[2], b[2]) - max(a[0], b[0])
    vov = min(a[3], b[3]) - max(a[1], b[1])
    if hov >= 0.3 * min(a[2] - a[0], b[2] - b[0]):          # one above the other
        top, bot = (a, b) if a[3] <= b[1] else (b, a)
        g = bot[1] - top[3]
        return 0 <= g <= vgap and not running_text_in(max(a[0], b[0]), top[3], min(a[2], b[2]), bot[1])
    if vov >= 0.3 * min(a[3] - a[1], b[3] - b[1]):          # side by side
        lft, rgt = (a, b) if a[2] <= b[0] else (b, a)
        g = rgt[0] - lft[2]
        return 0 <= g <= hgap and not running_text_in(lft[2], max(a[1], b[1]), rgt[0], min(a[3], b[3]))
    return False


def figure_region_for_caption(page: PageInfo, cap_lines: list[Line], graphics: list[dict], columns, used: list) -> tuple | None:
    """Region of the artwork belonging to a caption (above, below or beside it)."""
    cb = (min(l.x0 for l in cap_lines), min(l.y0 for l in cap_lines), max(l.x1 for l in cap_lines), max(l.y1 for l in cap_lines))
    cand = [g for g in graphics if not any(_intersects(g["bbox"], u, 1) for u in used)]
    if not cand:
        return None
    cx0, cx1 = cb[0], cb[2]
    col = next(((a, b) for a, b in columns if a - 4 <= (cx0 + cx1) / 2 <= b + 4), (page.trim[0], page.trim[2]))

    def hover(g):
        b = g["bbox"]
        return min(b[2], max(cx1, col[1])) - max(b[0], min(cx0, col[0]))
    above = [g for g in cand if g["bbox"][3] <= cb[1] + 6 and hover(g) > 10]
    below = [g for g in cand if g["bbox"][1] >= cb[3] - 6 and hover(g) > 10]
    beside = [g for g in cand if not (g["bbox"][3] <= cb[1] + 6 or g["bbox"][1] >= cb[3] - 6)
              and (g["bbox"][3] - g["bbox"][1]) > 30]
    def xov(g):
        b = g["bbox"]
        return min(b[2], cx1) - max(b[0], cx0)
    # side-by-side figures: a caption takes the artwork standing over its own text first
    above = [g for g in above if xov(g) > 0.3 * (cx1 - cx0)] or above
    below = [g for g in below if xov(g) > 0.3 * (cx1 - cx0)] or below
    beside = [g for g in beside if xov(g) > 0.3 * (cx1 - cx0)] or beside
    region = None
    for group, direction in ((above, "above"), (beside, "beside"), (below, "below")):
        if not group:
            continue
        if direction == "above":
            group.sort(key=lambda g: -g["bbox"][3])
            nearest = group[0]
            if cb[1] - nearest["bbox"][3] > 0.35 * (page.trim[3] - page.trim[1]):
                continue
        elif direction == "below":
            group.sort(key=lambda g: g["bbox"][1])
            nearest = group[0]
            if nearest["bbox"][1] - cb[3] > 0.25 * (page.trim[3] - page.trim[1]):
                continue
        else:
            group.sort(key=lambda g: abs((g["bbox"][1] + g["bbox"][3]) / 2 - (cb[1] + cb[3]) / 2))
            nearest = group[0]
        region = nearest["bbox"]
        # grow with graphics touching/stacked close to the region (multi-panel figures)
        grown = True
        while grown:
            grown = False
            for g in group:
                if g["bbox"] == region or _intersects(g["bbox"], region, -1):
                    if not (region[0] <= g["bbox"][0] and region[2] >= g["bbox"][2] and region[1] <= g["bbox"][1] and region[3] >= g["bbox"][3]):
                        region = _union(region, g["bbox"])
                        grown = True
                    continue
                if (_near(g["bbox"], region, 18) or _stacked(page, g["bbox"], region)) and \
                        not (direction == "above" and g["bbox"][1] > cb[1]):
                    region = _union(region, g["bbox"])
                    grown = True
        return region, direction
    return None


def absorb_text(region: tuple, lines: list[Line], cap_ids: set) -> list[Line]:
    """Text lines lying inside the artwork (axis labels, call-outs) belong to the figure."""
    inside = []
    for l in lines:
        if id(l) in cap_ids or l.role not in ("body",):
            continue
        b = l.bbox
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if region[0] - 2 <= cx <= region[2] + 2 and region[1] - 2 <= cy <= region[3] + 2:
            inside.append(l)
    # call-out labels just outside the drawing (the leader line ends at the art's edge)
    body_fams = {}
    for l in lines:
        if l.role == "body" and len(l.text.strip()) > 45:
            f = l.main.font.split("-")[0].lower()
            body_fams[f] = body_fams.get(f, 0) + 1
    running = max(body_fams, key=body_fams.get) if body_fams else None
    for l in lines:
        if id(l) in cap_ids or l.role != "body" or l in inside or len(l.text.strip()) > 40:
            continue
        if running and l.main.font.split("-")[0].lower() == running:
            continue           # same face as the running text: not a label
        b = l.bbox
        vov = min(b[3], region[3]) - max(b[1], region[1])
        hov = min(b[2], region[2]) - max(b[0], region[0])
        dx = max(region[0] - b[2], b[0] - region[2], 0)
        dy = max(region[1] - b[3], b[1] - region[3], 0)
        if (vov > 0 and dx <= 14) or (hov > 0 and dy <= 10):
            inside.append(l)
    return inside


def _whiten_background(pix):
    """Tinted panel / page background behind a figure or icon -> white (flood fill from the crop border,
    only through pixels of the border's own light colour, so the artwork itself is untouched)."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    w, h = img.size
    if w < 8 or h < 8:
        return img
    px = img.load()
    border = [px[x, 0] for x in range(0, w, 3)] + [px[x, h - 1] for x in range(0, w, 3)] + \
             [px[0, y] for y in range(0, h, 3)] + [px[w - 1, y] for y in range(0, h, 3)]
    from collections import Counter
    tinted = [(r, g, b) for r, g, b in border if min(r, g, b) < 246]
    if not tinted:
        return img
    q = Counter((r // 8, g // 8, b // 8) for r, g, b in tinted)
    (qr, qg, qb), n = q.most_common(1)[0]
    bg = (qr * 8 + 4, qg * 8 + 4, qb * 8 + 4)
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    if n < 0.2 * len(border) or lum < 170:
        return img           # no dominant light tint on the border
    seeds = [(x, y) for x, y in [(x, 0) for x in range(0, w, 7)] + [(x, h - 1) for x in range(0, w, 7)] +
             [(0, y) for y in range(0, h, 7)] + [(w - 1, y) for y in range(0, h, 7)]
             if sum(abs(a - b) for a, b in zip(px[x, y], bg)) < 36]
    for sxy in seeds:
        if px[sxy] != (255, 255, 255):
            ImageDraw.floodfill(img, sxy, (255, 255, 255), thresh=24)
    return img


def export_region(doc, pdf_page: int, bbox: tuple, out_path: Path, dpi: int = 300, quality: int = 90, pad: float = 2.0,
                  rot: int = 0, orig_size: tuple | None = None, erase: list | None = None, whiten: bool = True):
    page = doc[pdf_page]
    if rot and orig_size:
        from .text_extractor import unrot_bbox
        bbox = unrot_bbox(bbox, rot, orig_size[0], orig_size[1])
    r = pymupdf.Rect(bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad) & page.rect
    if r.is_empty or r.width < 2 or r.height < 2:
        raise ValueError("empty crop region")
    mat = pymupdf.Matrix(dpi / 72, dpi / 72)
    if rot:
        mat = mat.prerotate(-rot)      # render the landscape object upright
    pix = page.get_pixmap(matrix=mat, clip=r, alpha=False)
    if erase and not rot:
        # running text / captions that fall inside the crop rectangle are not part of the artwork
        z = dpi / 72
        for eb in erase:
            e = pymupdf.Rect(eb) & r
            if e.is_empty:
                continue
            # pixmap coordinates start at the clip's own origin (pix.x, pix.y), not at 0
            ir = pymupdf.IRect(pix.x + int((e.x0 - r.x0) * z) - 1, pix.y + int((e.y0 - r.y0) * z) - 1,
                               pix.x + int((e.x1 - r.x0) * z) + 2, pix.y + int((e.y1 - r.y0) * z) + 2)
            pix.set_rect(ir & pix.irect, (255, 255, 255))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = _whiten_background(pix) if whiten else None
    if img is not None:
        # trim the white margin left by erased text / background, keeping a small border
        try:
            from PIL import ImageChops, Image as _I
            diff = ImageChops.difference(img, _I.new("RGB", img.size, (255, 255, 255))).convert("L").point(lambda v: 255 if v > 18 else 0)
            bb = diff.getbbox()
            if bb:
                m = max(4, int(dpi / 72 * 3))
                bb = (max(0, bb[0] - m), max(0, bb[1] - m), min(img.width, bb[2] + m), min(img.height, bb[3] + m))
                if (bb[2] - bb[0]) * (bb[3] - bb[1]) < 0.97 * img.width * img.height:
                    img = img.crop(bb)
        except Exception:
            pass
        if out_path.suffix.lower() in (".jpg", ".jpeg"):
            img.save(str(out_path), quality=quality, dpi=(dpi, dpi))
        else:
            img.save(str(out_path))
        return img.size
    if out_path.suffix.lower() in (".jpg", ".jpeg"):
        pix.save(str(out_path), jpg_quality=quality)
    else:
        pix.save(str(out_path))
    return pix.width, pix.height


def export_cover(doc, out_dir: Path, thumb_name: str, width_px: int = 300):
    """cover.jpg (300 px wide) + thumbnail gif from the first page, per vendor spec."""
    from PIL import Image
    page = doc[0]
    zoom = width_px / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    out_dir.mkdir(parents=True, exist_ok=True)
    cover = out_dir / "cover.jpg"
    pix.save(str(cover), jpg_quality=90)
    img = Image.open(cover)
    img.thumbnail((120, 180))
    img.convert("P", palette=Image.ADAPTIVE).save(out_dir / thumb_name)
    return cover


def text_art_region(page: PageInfo, cap_lines: list[Line], style, direction: str = "above") -> tuple | None:
    """Artwork drawn as text + rules (diagrams, flow charts): the stack of non-body-style
    lines directly above the caption, up to the previous body-style text."""
    cb = (min(l.x0 for l in cap_lines), min(l.y0 for l in cap_lines), max(l.x1 for l in cap_lines), max(l.y1 for l in cap_lines))
    cap_ids = {id(l) for l in cap_lines}
    cands = sorted((l for l in page.lines if l.role == "body" and id(l) not in cap_ids and l.y1 <= cb[1] + 1),
                   key=lambda l: -l.y1)
    picked = []
    last_top = cb[1]
    for l in cands:
        if style.is_body_like(l) and len(l.text) > 25:
            break
        if last_top - l.y1 > 4 * style.line_pitch:
            break
        picked.append(l)
        last_top = min(last_top, l.y0)
    if len(picked) < 2:
        return None
    x0 = min(l.x0 for l in picked)
    y0 = min(l.y0 for l in picked)
    x1 = max(l.x1 for l in picked)
    y1 = max(l.y1 for l in picked)
    for d in page.drawings:
        b = d["bbox"]
        if b[1] >= y0 - 20 and b[3] <= cb[1] + 1 and b[2] - b[0] < 0.95 * (page.trim[2] - page.trim[0]):
            x0, y0, x1, y1 = min(x0, b[0]), min(y0, b[1]), max(x1, b[2]), max(y1, b[3])
    return (x0, y0, x1, y1)
