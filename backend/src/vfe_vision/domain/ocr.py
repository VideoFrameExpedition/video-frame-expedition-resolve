"""Text read in the frames (OCR): line filtering, cleaning, normalised boxes and search folding.

The multilingual PP-OCRv6 recogniser hallucinates CJK glyphs and one-character tokens on
textures (foliage, grills, water). The filter below removed every false positive of the sample
keyframes while keeping the cooktop display (« P2 ») and its printed labels (« 3—Boil »). Bump
``OCR_FILTER_VERSION`` whenever a rule changes, so cached OCR results are recomputed.

OCR text comes from the filmed scene and is attacker-controllable: ``clean_line`` only makes it
displayable (one physical line, no bidi overrides); fencing it as untrusted content before it
reaches a prompt or an MCP answer stays the caller's job.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

OCR_FILTER_VERSION = 1

MIN_SCORE = 0.8  # mean CTC probability of a line with 3 alphanumerics or more
MIN_ALNUM = 3
SHORT_ALNUM = 2  # « P2 », « 3h »: kept only when the recogniser is very sure
SHORT_MIN_SCORE = 0.9
BOX_DECIMALS = 4

# CJK ideographs and radicals, kana, Hangul, bopomofo, CJK symbols and punctuation (including the
# ideographic space) and the halfwidth / fullwidth forms block.
_CJK_RE = re.compile(
    "["
    "\u1100-\u11ff"  # Hangul Jamo
    "\u2e80-\u2fff"  # CJK radicals, Kangxi radicals, ideographic description characters
    "\u3000-\u9fff"  # CJK symbols, kana, bopomofo, Hangul compatibility, CJK ext. A, unified
    "\ua960-\ua97f"  # Hangul Jamo extended A
    "\uac00-\ud7ff"  # Hangul syllables, Hangul Jamo extended B
    "\uf900-\ufaff"  # CJK compatibility ideographs
    "\ufe30-\ufe4f"  # CJK compatibility forms
    "\uff00-\uffef"  # halfwidth and fullwidth forms
    "\U0001aff0-\U0001b2ff"  # kana extended / supplement, small kana
    "\U00020000-\U0003ffff"  # CJK extensions B and later (ideographic planes)
    "]"
)
_SPACES_RE = re.compile(r"\s+")
_LIGATURES = str.maketrans({"œ": "oe", "æ": "ae"})


def drop_cjk(text: str) -> str:
    """Remove the CJK / kana / Hangul / fullwidth characters, then NFC and collapse the spaces."""
    return _SPACES_RE.sub(" ", unicodedata.normalize("NFC", _CJK_RE.sub("", text))).strip()


def keep_line(text: str, score: float) -> bool:
    """Whether a recognised line is worth keeping.

    CJK-family characters never count (they are dropped), then a line needs a mean score of at
    least 0.8 and 3 alphanumerics, or exactly 2 alphanumerics with a score of at least 0.9.
    """
    alnum = sum(ch.isalnum() for ch in drop_cjk(text))
    if alnum >= MIN_ALNUM:
        return score >= MIN_SCORE
    return alnum == SHORT_ALNUM and score >= SHORT_MIN_SCORE


def clean_line(text: str) -> str:
    """NFC, control and format characters (bidi overrides, zero-width…) removed, every run of
    whitespace (line breaks included) collapsed to one space, ends trimmed."""
    kept = "".join(
        ch for ch in text if ch.isspace() or unicodedata.category(ch) not in {"Cc", "Cf"}
    )
    # NFC last: removing a zero-width joiner can bring a letter and its accent together.
    return _SPACES_RE.sub(" ", unicodedata.normalize("NFC", kept)).strip()


def filter_line(text: str, score: float) -> str | None:
    """The text to store for a recognised line (cleaned, CJK removed), or ``None`` to drop it."""
    return clean_line(drop_cjk(text)) if keep_line(text, score) else None


def normalized_box(
    box: Sequence[tuple[float, float]], width: int, height: int
) -> list[list[float]]:
    """The 4 corners of a box in pixels as ``[x, y]`` fractions of the image (0..1, 4 decimals),
    in the same order (TL, TR, BR, BL for the OCR engine)."""
    if width <= 0 or height <= 0:
        raise ValueError(f"taille d'image invalide : {width}×{height}")
    if len(box) != 4:
        raise ValueError(f"une zone de texte a 4 coins, pas {len(box)}")
    return [
        [
            round(min(1.0, max(0.0, x / width)), BOX_DECIMALS),
            round(min(1.0, max(0.0, y / height)), BOX_DECIMALS),
        ]
        for x, y in box
    ]


def fold(text: str) -> str:
    """Search key: compatibility decomposition without diacritics, lowercase, œ → oe, æ → ae.

    OCR often loses accents and ligatures on blurred text (« ŒUVRE » read « CEUVRE », « naïf »
    read « naif »), so both the stored text and the query are folded before matching.
    """
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", text).lower())
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return bare.translate(_LIGATURES)
