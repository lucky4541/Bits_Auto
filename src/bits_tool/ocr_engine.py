"""OCR behind one interface (Tesseract, PaddleOCR) with a page-level cache.

Only pages without a usable text layer are OCR'd. Results (words, boxes,
confidence, language, engine + version, preprocessing version) are cached in
cache/<pdf-sha>/ocr/page_NNNN.json and never recomputed once successful.
Low-confidence words are kept and flagged, never dropped.
"""
from __future__ import annotations

import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .document_tree import Line, Span

PREPROC_VERSION = "1"


@dataclass
class OcrWord:
    text: str
    bbox: tuple
    conf: float
    line_key: tuple


class OcrEngine:
    name = "none"
    version = ""

    def available(self) -> bool:
        return False

    def ocr(self, image_bytes: bytes, langs: list[str], scale: float) -> list[OcrWord]:
        raise NotImplementedError


class TesseractEngine(OcrEngine):
    name = "tesseract"

    def __init__(self, cmd: str = ""):
        self.cmd = cmd
        try:
            import pytesseract
            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
            self._pt = pytesseract
            self.version = str(pytesseract.get_tesseract_version())
        except Exception:
            self._pt = None

    def available(self) -> bool:
        return self._pt is not None and (bool(self.cmd) or shutil.which("tesseract") is not None)

    def installed_langs(self) -> set[str]:
        try:
            return set(self._pt.get_languages(config=""))
        except Exception:
            return set()

    def ocr(self, image_bytes, langs, scale):
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.grayscale(img)
        img = ImageOps.autocontrast(img)
        have = self.installed_langs()
        use = [l for l in langs if l in have] or (["eng"] if "eng" in have else [])
        data = self._pt.image_to_data(img, lang="+".join(use) if use else None, output_type=self._pt.Output.DICT,
                                      config="--psm 1")
        out = []
        for i, txt in enumerate(data["text"]):
            if not txt or not txt.strip():
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            out.append(OcrWord(txt, (x / scale, y / scale, (x + w) / scale, (y + h) / scale), float(data["conf"][i]),
                               (data["block_num"][i], data["par_num"][i], data["line_num"][i])))
        return out


class PaddleEngine(OcrEngine):
    name = "paddle"

    def __init__(self):
        try:
            from paddleocr import PaddleOCR  # noqa: F401
            import paddleocr
            self._mod = paddleocr
            self.version = getattr(paddleocr, "__version__", "")
            self._inst = {}
        except Exception:
            self._mod = None

    def available(self):
        return self._mod is not None

    def ocr(self, image_bytes, langs, scale):
        import numpy as np
        from PIL import Image
        lang = {"spa": "es", "eng": "en", "fra": "fr", "deu": "german", "por": "pt", "ita": "it", "rus": "ru",
                "ara": "ar", "hin": "hi", "tel": "te", "tam": "ta", "kan": "ka", "chi_sim": "ch", "jpn": "japan",
                "kor": "korean"}.get(langs[0] if langs else "eng", "en")
        if lang not in self._inst:
            self._inst[lang] = self._mod.PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
        img = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        res = self._inst[lang].ocr(img, cls=True) or []
        out = []
        for li, item in enumerate(res[0] if res and res[0] else []):
            pts, (txt, conf) = item
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.append(OcrWord(txt, (min(xs) / scale, min(ys) / scale, max(xs) / scale, max(ys) / scale), conf * 100, (0, 0, li)))
        return out


def make_engine(name: str, tesseract_cmd: str = "") -> OcrEngine:
    if name in ("paddle",):
        e = PaddleEngine()
        if e.available():
            return e
    if name in ("auto", "tesseract", "paddle"):
        t = TesseractEngine(tesseract_cmd)
        if t.available():
            return t
        if name == "auto":
            p = PaddleEngine()
            if p.available():
                return p
    return OcrEngine()


class OcrCache:
    def __init__(self, cache_dir: Path):
        self.dir = Path(cache_dir) / "ocr"

    def path(self, pno: int) -> Path:
        return self.dir / f"page_{pno + 1:04d}.json"

    def get(self, pno: int, engine: OcrEngine):
        p = self.path(pno)
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("status") == "ok" and d.get("preprocessing") == PREPROC_VERSION:
                return d
        except Exception:
            return None
        return None

    def put(self, pno: int, data: dict):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path(pno).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        tmp.replace(self.path(pno))


def needs_ocr(page_info, min_chars: int) -> bool:
    chars = sum(len(l.text.strip()) for l in page_info.lines if l.role != "slug")
    if chars >= min_chars:
        return False
    tw = page_info.trim[2] - page_info.trim[0]
    th = page_info.trim[3] - page_info.trim[1]
    big = [i for i in page_info.images if (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]) > 0.5 * tw * th]
    return bool(big)


def ocr_page(doc, pno: int, page_info, engine: OcrEngine, cache: OcrCache, langs: list[str], dpi: int):
    """Fill page_info.lines from OCR (cached). Returns mean confidence or None."""
    cached = cache.get(pno, engine)
    if cached is None:
        if not engine.available():
            page_info.errors.append("OCR_REQUIRED_BUT_NO_ENGINE")
            return None
        page = doc[pno]
        scale = dpi / 72.0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=pymupdf.Rect(*page_info.trim))
        words = engine.ocr(pix.tobytes("png"), langs, scale)
        ox, oy = page_info.trim[0], page_info.trim[1]
        cached = {"status": "ok", "engine": engine.name, "engine_version": engine.version,
                  "preprocessing": PREPROC_VERSION, "langs": langs, "dpi": dpi,
                  "words": [{"t": w.text, "b": (w.bbox[0] + ox, w.bbox[1] + oy, w.bbox[2] + ox, w.bbox[3] + oy),
                             "c": w.conf, "k": list(w.line_key)} for w in words]}
        cache.put(pno, cached)
    native = [line for line in page_info.lines if not line.ocr]
    native_spans = [span for line in native for span in line.spans if span.text.strip()]
    groups: dict[tuple, list] = {}
    for w in cached["words"]:
        b = w["b"]
        area = max(1, (b[2] - b[0]) * (b[3] - b[1]))
        if any(max(0, min(b[2], s.bbox[2]) - max(b[0], s.bbox[0])) *
               max(0, min(b[3], s.bbox[3]) - max(b[1], s.bbox[1])) >= 0.6 * area for s in native_spans):
            page_info.diagnostics.append({"action": "exclude-ocr-overlap", "bbox": b,
                                          "reason": "native text already represents this source area"})
            continue
        groups.setdefault(tuple(w["k"]), []).append(w)
    lines = []
    confs = []
    for key in sorted(groups, key=lambda k: (min(w["b"][1] for w in groups[k]), min(w["b"][0] for w in groups[k]))):
        ws = sorted(groups[key], key=lambda w: w["b"][0])
        h = sum(w["b"][3] - w["b"][1] for w in ws) / len(ws)
        spans = [Span(text=(" " if i else "") + w["t"], font="OCR", size=round(h * 0.85, 1), bbox=tuple(w["b"])) for i, w in enumerate(ws)]
        c = sum(max(0.0, w["c"]) for w in ws) / len(ws) / 100.0
        confs.append(c)
        line = Line(spans=spans, bbox=(min(w["b"][0] for w in ws), min(w["b"][1] for w in ws),
                                       max(w["b"][2] for w in ws), max(w["b"][3] for w in ws)),
                    page=page_info.index, conf=round(c, 3), ocr=True)
        lines.append(line)
    page_info.lines = sorted(native + lines, key=lambda line: (line.y0, line.x0))
    page_info.is_ocr = True
    page_info.ocr_conf = round(sum(confs) / len(confs), 3) if confs else 0.0
    return page_info.ocr_conf
