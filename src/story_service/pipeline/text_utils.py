"""Text helpers shared by the pipeline (spec 5.0 `norm_ar`, duration estimate)."""
from __future__ import annotations

import re
import unicodedata

_DIACRITICS = re.compile("[\u064b-\u0652\u0670]")
_ARABIC_INDIC = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_EXT_ARABIC_INDIC = {ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_ALEF_VARIANTS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})


def norm_ar(text: str) -> str:
    """Normalize Arabic text for quote, substring and similarity checks."""
    text = _DIACRITICS.sub("", text).replace("\u0640", "")
    text = text.translate(_ALEF_VARIANTS)
    text = text.translate(_ARABIC_INDIC).translate(_EXT_ARABIC_INDIC)
    text = text.lower()
    # strip punctuation (anything that is not a letter, digit or whitespace)
    text = "".join(
        " " if unicodedata.category(ch)[0] in "PS" else ch for ch in text
    )
    return " ".join(text.split())


def word_count(text: str) -> int:
    return len(text.split())


def estimate_line_seconds(text_spoken: str, language: str, wps: dict[str, float]) -> float:
    """Spec S4 rule 3: words / wps plus 0.3 s per line."""
    return word_count(text_spoken) / wps[language] + 0.3
