"""Lightweight language / script detection (no network, no model files).

Script is identified from Unicode character names; Latin-script languages
are separated with stop-word frequencies. Returns ISO 639-1 codes plus the
Tesseract language code to use for OCR.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

STOP = {
    "en": "the of and to in is that for with as are was this by be on from or which an at not",
    "es": "de la que el en y los las del se por un una con para es al lo como más su pero sus",
    "fr": "de la le et les des en du un une est que pour dans qui sur par au pas plus",
    "de": "der die und in den von zu das mit sich des auf für ist im dem nicht ein eine",
    "it": "di e il la che in per un del della le si con non una sono gli al",
    "pt": "de a o que e do da em um para é com não uma os no se na por mais",
    "nl": "de het een en van in is dat op te zijn met voor niet aan er",
}
STOP = {k: set(v.split()) for k, v in STOP.items()}
SCRIPTS = [("ARABIC", "ar"), ("HEBREW", "he"), ("DEVANAGARI", "hi"), ("TELUGU", "te"), ("TAMIL", "ta"),
           ("KANNADA", "kn"), ("MALAYALAM", "ml"), ("BENGALI", "bn"), ("THAI", "th"), ("HANGUL", "ko"),
           ("HIRAGANA", "ja"), ("KATAKANA", "ja"), ("CJK", "zh"), ("CYRILLIC", "ru"), ("GREEK", "el")]
TESS = {"en": "eng", "es": "spa", "fr": "fra", "de": "deu", "it": "ita", "pt": "por", "nl": "nld", "ru": "rus",
        "ar": "ara", "he": "heb", "hi": "hin", "te": "tel", "ta": "tam", "kn": "kan", "ml": "mal", "bn": "ben",
        "zh": "chi_sim", "ja": "jpn", "ko": "kor", "th": "tha", "vi": "vie", "el": "ell"}


def detect(text: str) -> tuple[str | None, float]:
    if not text or len(text) < 20:
        return None, 0.0
    sc = Counter()
    for ch in text[:5000]:
        if ch.isalpha():
            try:
                name = unicodedata.name(ch)
            except ValueError:
                continue
            for key, code in SCRIPTS:
                if key in name:
                    sc[code] += 1
                    break
            else:
                if "LATIN" in name:
                    sc["latin"] += 1
    if not sc:
        return None, 0.0
    script, n = sc.most_common(1)[0]
    if script != "latin":
        return script, round(n / sum(sc.values()), 2)
    if re.search(r"[ơư]|[ạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", text):
        return "vi", 0.8
    words = re.findall(r"[a-záéíóúüñàèìòùâêîôûçäöß]+", text.lower())
    if not words:
        return None, 0.0
    scores = {lang: sum(1 for w in words if w in sw) for lang, sw in STOP.items()}
    best = max(scores, key=scores.get)
    total = sum(scores.values()) or 1
    return (best, round(scores[best] / total, 2)) if scores[best] >= 3 else (None, 0.0)


def tesseract_code(lang: str | None) -> str:
    return TESS.get(lang or "", "eng")
