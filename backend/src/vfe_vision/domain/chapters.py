"""Chapters: where the content of a video changes, found by code (pure functions).

Each block becomes the bag of words that describe it (its captions, its first subjects, its kind
of place and setting, the longer words said in it), weighted by rarity and normalised. The
chapters are the k runs of consecutive blocks that spread least around their mean, weighted by
duration, each long enough; an exact dynamic programme finds them. The language model only names
the chapters: it never places a boundary.

Measured (rerun on these blocks): on a 24-min composite of 22 sources, 6/6 chapters start
within 1 s of a source change, against 2/6 when picking the best-scored boundaries greedily
(61 min, 46 sources: 6/11); on the recipe and the archive documentary, the chapters follow the
narration. Within one long shot they depend on where its blocks are cut: the talk filmed in one
shot gets 1:38, 2:40 and 3:22 (its assistant, e-mail and parking demos), where the research's
keyframe-driven cuts gave 1:00, 1:39 and 2:15. The costs take O(n²·V), the programme O(k·n²)
(0.1 s for 500 blocks); past DP_MAX_BLOCKS (n² memory and time), the greedy pick takes over.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import Counter
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from vfe_vision.domain.synthesis_input import Block, Video, speech_text

CHAPTER_RULES_VERSION = 1  # stored with the chapters: a new rule recomputes them
DP_MAX_BLOCKS = 500
COMMON_SHARE = 0.6  # a word in more blocks than this says nothing about where chapters change
FEW_BLOCKS = 12  # up to this many blocks, a word of a single block still sets it apart
MIN_CHAPTER_S = 20.0  # and at least half of an even share of the video
WEIGHT_CAP_S = 30.0  # a block weighs its duration up to this, plus 1 s (a flash still counts)
SPOKEN_MIN_LETTERS = 5  # shorter spoken words (« très », « faire ») are everywhere
SUBJECTS = 3  # the first subjects of a frame (main subject first)

# The greedy fallback: 1 − Jaccard of the words of the blocks around a boundary, plus bonuses.
_GREEDY_MIN_CHAPTER_S = 15.0
_GREEDY_MIN_SHARE = 0.06  # of the video's duration
_FADE_BONUS = 0.15
_SETTING_BONUS = 0.15  # indoor ↔ outdoor
_PAUSE_BONUS = 0.1  # no word within _PAUSE_S of the boundary
_PAUSE_S = 1.5
_FADE_TOLERANCE_S = 0.05

_WORD = re.compile(r"[\w']+")
# Words of every caption and sentence, French and English (captions may come in either).
STOP = frozenset({
    "un", "une", "des", "de", "du", "la", "le", "les", "l", "d", "et", "à", "au", "aux", "en",
    "sur", "dans", "avec", "a", "the", "of", "in", "on", "and", "with",
})  # fmt: skip


def tokens(text: str) -> set[str]:
    """The words of a text that can tell two blocks apart: lower case, 3 letters or more, no
    stop word (« l'eau » stays one word)."""
    return {w for w in _WORD.findall(text.lower()) if w not in STOP and len(w) > 2}


def chapter_count(duration: float) -> int:
    """How many chapters a video gets: one under 90 s, then about one per 45 s up to 5 min
    (2 to 4), then 4 to 12, about one more per 400 s."""
    if duration < 90:
        return 1
    if duration <= 300:
        return max(2, min(4, round(duration / 45)))
    return max(4, min(12, round(3 + duration / 400)))


def block_signature(video: Video, block: Block) -> set[str]:
    """What a block is about: the words of its captions and first subjects, ``@place:`` and
    ``@setting:`` of its keyframes, and ``#`` + each longer word said in it (whole words)."""
    signature: set[str] = set()
    for frame in block.frames:
        data = frame.data or {}
        signature |= tokens(str(data.get("caption") or ""))
        for subject in list(data.get("subjects") or [])[:SUBJECTS]:
            if isinstance(subject, dict):
                signature |= tokens(str(subject.get("label") or ""))
        if place := str(data.get("place_type") or "").lower():
            signature.add(f"@place:{place}")
        if setting := str(data.get("setting") or ""):
            signature.add(f"@setting:{setting}")
    said = tokens(speech_text(video.segments, block.start, block.end))
    signature |= {f"#{w}" for w in said if len(w) >= SPOKEN_MIN_LETTERS}
    return signature


def chapter_ranges(
    video: Video, blocks: Sequence[Block], k: int | None = None
) -> list[tuple[int, int]]:
    """The chapters as (first block number, last block number), covering the blocks in order.

    ``k`` defaults to :func:`chapter_count`; fewer chapters come back when k of them cannot
    each last the minimum length.
    """
    if not blocks:
        return []
    duration = max(video.duration, blocks[-1].end)  # a duration left unknown ends at the blocks
    k = k or chapter_count(duration)
    n = len(blocks)
    if k <= 1 or n < 2:
        spans = [(0, n)]
    elif n > DP_MAX_BLOCKS:
        spans = _greedy_spans(video, blocks, k, duration)
    else:
        spans = _dp_spans(video, blocks, min(k, n), duration)
    return [(blocks[a].no, blocks[b - 1].no) for a, b in spans]


def _vectors(signatures: Sequence[set[str]]) -> npt.NDArray[np.float64]:
    """One row per block: its words, each weighted 1 / ln(1 + number of blocks with it)."""
    n = len(signatures)
    df = Counter(t for s in signatures for t in s)
    vocab = sorted(
        t
        for t, c in df.items()
        if 1 < c <= max(2, COMMON_SHARE * n) or (c == 1 and n <= FEW_BLOCKS)
    )
    index = {t: i for i, t in enumerate(vocab)}
    matrix = np.zeros((n, max(1, len(vocab))), dtype=np.float64)
    for row, signature in enumerate(signatures):
        for t in signature:
            if t in index:
                matrix[row, index[t]] = 1.0 / math.log(1 + df[t])
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    normalised: npt.NDArray[np.float64] = matrix / np.where(norms == 0, 1.0, norms)
    return normalised


def _dp_spans(
    video: Video, blocks: Sequence[Block], k: int, duration: float
) -> list[tuple[int, int]]:
    """k chapters (fewer when infeasible) as [first, end) block indices, by exact DP."""
    n = len(blocks)
    x = _vectors([block_signature(video, b) for b in blocks])
    w = np.array([min(b.duration, WEIGHT_CAP_S) for b in blocks], dtype=np.float64) + 1.0
    starts = np.array([b.start for b in blocks] + [duration])
    min_len = max(MIN_CHAPTER_S, 0.5 * duration / k)
    cost = _spreads(x, w)  # cost[i, j]: blocks i..j-1 as one chapter
    for i in range(n):
        too_short = starts[i + 1 :] - starts[i] < min_len
        cost[i, i + 1 :][too_short] = np.inf
    best = np.full((k + 1, n + 1), np.inf)  # best[m, j]: blocks 0..j-1 in m chapters
    back = np.zeros((k + 1, n + 1), dtype=np.intp)
    best[0, 0] = 0.0
    columns = np.arange(n + 1)
    for m in range(1, k + 1):
        candidates = best[m - 1][:, None] + cost  # the first tie wins, as a scan from i = 0
        back[m] = np.argmin(candidates, axis=0)
        best[m] = candidates[back[m], columns]
    m = k
    while m > 1 and not np.isfinite(best[m, n]):
        m -= 1
    if not np.isfinite(best[m, n]):
        return [(0, n)]
    spans: list[tuple[int, int]] = []
    j = n
    for chapter in range(m, 0, -1):
        i = int(back[chapter, j])
        spans.append((i, j))
        j = i
    return spans[::-1]


def _spreads(x: npt.NDArray[np.float64], w: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """spread[i, j]: how far blocks i..j-1 lie from their mean, Σ w·|x|² − |Σ w·x|² / Σ w
    (infinite unless i < j).

    |Σ w·x|² is summed from the Gram matrix of the weighted rows over growing squares: one matrix
    product instead of the research's O(n²·V) differences of prefix sums (500 blocks and 7,000
    words: 6.6 s → 0.1 s; the synthesis page recomputes the chapters when read). Its terms are
    all ≥ 0, and the costs agree with the research's to 1e-11 (only exact ties may differ).
    """
    n = len(w)
    wx = x * w[:, None]
    gram = wx @ wx.T
    pw = np.concatenate([[0.0], np.cumsum(w)])
    pn = np.concatenate([[0.0], np.cumsum(w * (x * x).sum(axis=1))])
    squares = np.zeros((n + 1, n + 1))  # squares[i, j] = |Σ w·x over blocks i..j-1|²
    for i in range(n - 1, -1, -1):
        # Block i joins the square of blocks i+1..j-1: its row counts twice, its own term once.
        squares[i, i + 1 :] = 2 * np.cumsum(gram[i, i:]) - gram[i, i]
        squares[i, i + 2 :] += squares[i + 1, i + 2 :]
    spread = np.full((n + 1, n + 1), np.inf)
    for i in range(n):
        spread[i, i + 1 :] = (pn[i + 1 :] - pn[i]) - squares[i, i + 1 :] / (pw[i + 1 :] - pw[i])
    return spread


def _greedy_spans(
    video: Video, blocks: Sequence[Block], k: int, duration: float
) -> list[tuple[int, int]]:
    """Up to k chapters as [first, end) block indices: the k − 1 best-scored block starts, far
    enough from each other and from the ends of the video."""
    signatures = [block_signature(video, b) for b in blocks]
    n = len(blocks)
    window = 2 if n < 30 else 3
    shots = {s.idx: s for s in video.shots}
    words = video.words
    word_starts = np.array([wd.start for wd in words], dtype=np.float64)
    word_ends = np.array([wd.end for wd in words], dtype=np.float64)
    scores: list[tuple[float, int]] = []
    for i in range(1, n):
        before = set[str]().union(*signatures[max(0, i - window) : i])
        after = set[str]().union(*signatures[i : i + window])
        score = 1 - len(before & after) / max(1, len(before | after))
        block = blocks[i]
        first = shots.get(block.shots[0]) if block.shots else None
        if (
            first is not None
            and first.boundary == "fade"
            and abs(first.start - block.start) < _FADE_TOLERANCE_S
        ):
            score += _FADE_BONUS
        setting_before = {t for t in signatures[i - 1] if t.startswith("@setting")}
        setting_after = {t for t in signatures[i] if t.startswith("@setting")}
        if setting_before and setting_after and not setting_before & setting_after:
            score += _SETTING_BONUS
        t = block.start
        near = (word_starts - _PAUSE_S < t) & (t < word_ends + _PAUSE_S)
        if words and not near.any():
            score += _PAUSE_BONUS
        scores.append((score, i))
    min_len = max(_GREEDY_MIN_CHAPTER_S, _GREEDY_MIN_SHARE * duration)
    chosen: list[int] = []
    for _, i in sorted(scores, reverse=True):
        t = blocks[i].start
        edges = [0.0, *(blocks[j].start for j in chosen), duration]
        if all(abs(t - e) >= min_len for e in edges):
            chosen.append(i)
        if len(chosen) >= k - 1:
            break
    return list(itertools.pairwise([0, *sorted(chosen), n]))
