"""Read active image clipping bounds from MuPDF's PDF graphics state.

Bounds enclose the painted region; nonrectangular masks are still applied by
MuPDF when the region is exported. Unsupported bindings fail explicitly so the
caller can retain the source placement and request QC.
"""
import pymupdf

m = pymupdf.mupdf


class _ImageBounds(m.FzDevice2):
    def __init__(self):
        super().__init__()
        self.clips = []
        self.out = []
        self.masks = []
        for name in ('fill_image', 'fill_image_mask', 'clip_path',
                     'clip_stroke_path', 'clip_text', 'clip_stroke_text',
                     'clip_image_mask', 'pop_clip', 'begin_mask', 'end_mask'):
            getattr(self, 'use_virtual_' + name)()

    @staticmethod
    def box(rect):
        return rect.x0, rect.y0, rect.x1, rect.y1

    def push(self, rect):
        box = self.box(rect)
        if self.clips:
            outer = self.clips[-1]
            box = (max(outer[0], box[0]), max(outer[1], box[1]),
                   min(outer[2], box[2]), min(outer[3], box[3]))
        self.clips.append(box)

    def clip_path(self, ctx, path, even_odd, ctm, scissor):
        self.push(m.ll_fz_bound_path(path, None, ctm))

    def clip_stroke_path(self, ctx, path, stroke, ctm, scissor):
        self.push(m.ll_fz_bound_path(path, stroke, ctm))

    def clip_text(self, ctx, text, ctm, scissor):
        self.push(m.ll_fz_bound_text(text, None, ctm))

    def clip_stroke_text(self, ctx, text, stroke, ctm, scissor):
        self.push(m.ll_fz_bound_text(text, stroke, ctm))

    def clip_image_mask(self, ctx, image, ctm, scissor):
        self.push(m.ll_fz_transform_rect(m.fz_unit_rect, ctm))

    def begin_mask(self, ctx, area, luminosity, colorspace, background, color_params):
        # Painting a mask does not paint page content. Its area becomes an
        # enclosing clip when end_mask returns to normal page painting.
        self.masks.append((area, self.clips[:]))
        self.push(area)

    def end_mask(self, ctx, transfer):
        area, self.clips = self.masks.pop()
        self.push(area)

    def pop_clip(self, ctx):
        if self.clips:
            self.clips.pop()

    def fill_image(self, ctx, image, ctm, alpha, color_params):
        if self.masks:
            return
        full = self.box(m.ll_fz_transform_rect(m.fz_unit_rect, ctm))
        self.out.append((full, self.clips[-1] if self.clips else None))

    def fill_image_mask(self, ctx, image, ctm, colorspace, color, alpha, color_params):
        self.fill_image(ctx, image, ctm, alpha, color_params)


def image_paint_bounds(page):
    rotation = page.rotation
    try:
        if rotation:
            page.set_rotation(0)
        dev = _ImageBounds()
        try:
            m.fz_run_page(page.this, dev, m.FzMatrix(), m.FzCookie())
        finally:
            m.fz_close_device(dev)
        return dev.out
    finally:
        if rotation:
            page.set_rotation(rotation)
