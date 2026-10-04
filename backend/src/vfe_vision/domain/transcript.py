"""Speech transcripts: hallucination guards, coverage runs, subtitle cues, SRT/WebVTT, excerpts.

Pure logic shared by the ASR child (guards, second-pass planning) and the readers of a stored
transcript (subtitles, the ±6 s excerpt given to the vision model, MCP). Times are seconds of the
source file.

Guards (measured on 98 good and 9 bad turbo segments): faster-whisper's own thresholds only
trigger the temperature fallback and keep the T=1.0 output, so every segment is kept but flagged
``suspect`` when one rule fires. Suspect segments stay out of search, subtitles, excerpts and MCP
by default. ``no_speech_prob`` is ignored: it is 0.00 on every turbo segment, good or bad.
Bump :data:`TRANSCRIPT_GUARD_VERSION` whenever a rule or threshold changes (it is in cache keys).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

TRANSCRIPT_GUARD_VERSION = 1

SUSPECT_WINDOW_LOGPROB = -0.8  # good windows >= -0.38, bad ones <= -0.95
SUSPECT_MEAN_WORD_P = 0.5
FALLBACK_TEMPERATURE = 0.8  # with a high compression ratio: a decoding loop
FALLBACK_COMPRESSION = 2.2
HALLUCINATION_LOGPROB = -0.5  # a stock phrase is only suspect below this
REPEAT_MIN_WORDS = 3  # from this length, a text contained in the previous one is a repeat
PASS2_LANG_MIN_P = 0.8  # second pass: detected language accepted from this probability
COVERAGE_MIN_RUN_S = 1.0  # speech not covered by any word for this long -> second pass
COVERAGE_TOL_S = 0.3
CONTEXT_PAD_S = 1.0
CONTEXT_MAX_CLIP_S = 30.0  # one Whisper window: gaps closer than this share one decode

MAX_LINE = 42
MAX_LINES = 2
MAX_CUE_S = 7.0
MIN_CUE_S = 1.0
MAX_WORD_S = 1.5  # a word stretched over a pause is an alignment artefact
GAP_SPLIT_S = 0.8
_MIN_VISIBLE_S = 0.1  # floor for a cue squeezed by the next one (identical timestamps)

_SENTENCE_END = (".", "?", "!", "…", "。", "？", "！")
_SOFT_END = (",", ";", ":", "，", "、")
_CLOSING = "\"')]}»”’"

# Stock phrases Whisper produces on silence, music or noise (normalised with normalize_text).
_STOCK_PHRASES = (
    "ありがとう",
    "ありがとうございました",
    "ご視聴ありがとうございました",
    "thank you",
    "thank you very much",
    "thanks for watching",
    "thank you for watching",
    "thank you so much for watching",
    "please subscribe",
    "i'll see you next time",
    "and i'll see you next time",
    "see you next time",
    "you",
    "bye",
    "merci",
    "merci beaucoup",
    "merci d'avoir regardé",
    "merci d'avoir regardé cette vidéo",
    "abonnez-vous",
    "sous-titres réalisés par la communauté d'amara.org",
)
# Subtitle credits learnt from the training data: never real speech in a rush, whatever the
# confidence (a stricter reading of rule (d) for these only).
_CREDIT_MARKERS = (
    "amara.org",
    "sous-titrage st' 501",
    "sous-titres réalisés par",
    "sous-titrage société radio-canada",
)

_UNTRUSTED_TAG = re.compile(r"<\s*/?\s*untrusted\b[^>]*>", re.IGNORECASE)
_ARROW = re.compile(r"-{2,}>")


class WordLike(Protocol):
    @property
    def start(self) -> float: ...
    @property
    def end(self) -> float: ...
    @property
    def text(self) -> str: ...
    @property
    def probability(self) -> float: ...


class SegmentLike(Protocol):
    @property
    def start(self) -> float: ...
    @property
    def end(self) -> float: ...
    @property
    def text(self) -> str: ...
    @property
    def words(self) -> Sequence[WordLike]: ...
    @property
    def suspect(self) -> bool: ...


Span = tuple[float, float]


# ---------------------------------------------------------------- text hygiene
def normalize_text(text: str) -> str:
    """Comparison form: NFKC, case-folded, punctuation and symbols as spaces, single spaces."""
    folded = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())


_PHRASES = frozenset(normalize_text(p) for p in _STOCK_PHRASES)
_CREDITS = tuple(normalize_text(m) for m in _CREDIT_MARKERS)


def _drop_invisible(text: str) -> str:
    out: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if ch.isspace() or category in {"Zl", "Zp"}:
            out.append(" ")
        elif category not in {"Cc", "Cf"}:  # controls, bidi overrides, zero-width, BOM
            out.append(ch)
    return "".join(out)


def clean_untrusted(text: str) -> str:
    """One line of untrusted text (speech, subtitles): no control, bidi or zero-width characters,
    no ``<untrusted>`` fence tags, single spaces. Stable: cleaning twice changes nothing."""
    previous = None
    while previous != text:
        previous = text
        text = _UNTRUSTED_TAG.sub(" ", _drop_invisible(text))
    return " ".join(text.split())


# ---------------------------------------------------------------- guards
def _repeats(norm: str, previous: str) -> bool:
    """Same text as the previous segment, or a run of it: a decoding loop, or a second pass
    that copied its prompt (RIZ at 76.6 s: the previous sentence squeezed into a 1 s gap)."""
    if norm == previous:
        return True
    return len(norm.split()) >= REPEAT_MIN_WORDS and f" {norm} " in f" {previous} "


def suspect_reasons(
    text: str,
    *,
    avg_logprob: float,
    word_probabilities: Sequence[float] = (),
    temperature: float | None = 0.0,
    compression_ratio: float = 0.0,
    previous_text: str | None = None,
    language_mismatch: bool = False,
) -> tuple[str, ...]:
    """Why a segment looks hallucinated (empty: trusted).

    ``avg_logprob``, ``temperature`` and ``compression_ratio`` are per 30 s window (identical on
    every segment of a window); ``previous_text`` is the text of the segment before it (for a
    second-pass segment, the prompt it was decoded with);
    ``language_mismatch`` flags a second-pass clip decoded in a language other than the one
    detected on it.
    """
    reasons: list[str] = []
    if avg_logprob < SUSPECT_WINDOW_LOGPROB:
        reasons.append("low_logprob")
    if word_probabilities and math.fsum(word_probabilities) / len(word_probabilities) < (
        SUSPECT_MEAN_WORD_P
    ):
        reasons.append("low_word_probability")
    norm = normalize_text(text)
    if norm and previous_text is not None and _repeats(norm, normalize_text(previous_text)):
        reasons.append("repeat")
    if (temperature or 0.0) >= FALLBACK_TEMPERATURE and compression_ratio > FALLBACK_COMPRESSION:
        reasons.append("fallback_loop")
    padded = f" {norm} "
    if any(f" {marker} " in padded for marker in _CREDITS) or (
        norm in _PHRASES and avg_logprob < HALLUCINATION_LOGPROB
    ):
        reasons.append("known_hallucination")
    if language_mismatch:
        reasons.append("language_mismatch")
    return tuple(reasons)


def is_suspect(
    text: str,
    *,
    avg_logprob: float,
    word_probabilities: Sequence[float] = (),
    temperature: float | None = 0.0,
    compression_ratio: float = 0.0,
    previous_text: str | None = None,
    language_mismatch: bool = False,
) -> bool:
    return bool(
        suspect_reasons(
            text,
            avg_logprob=avg_logprob,
            word_probabilities=word_probabilities,
            temperature=temperature,
            compression_ratio=compression_ratio,
            previous_text=previous_text,
            language_mismatch=language_mismatch,
        )
    )


# ---------------------------------------------------------------- coverage (second pass)
@dataclass(frozen=True, slots=True)
class ContextClip:
    """Audio re-transcribed in the second pass: the gaps plus some context around them."""

    start: float
    end: float
    gaps: tuple[Span, ...]


def _merge_spans(spans: Iterable[Span]) -> list[Span]:
    merged: list[Span] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def uncovered_runs(
    speech: Iterable[Span],
    covered: Iterable[Span],
    *,
    min_run_s: float = COVERAGE_MIN_RUN_S,
    tol_s: float = COVERAGE_TOL_S,
) -> list[Span]:
    """Parts of the VAD speech runs that no word covers (each word widened by ``tol_s``), kept
    when at least ``min_run_s`` long: speech Whisper dropped at a 30 s window boundary."""
    words = _merge_spans((s - tol_s, e + tol_s) for s, e in covered if e >= s)
    runs: list[Span] = []
    for start, end in _merge_spans(speech):
        cursor = start
        for w_start, w_end in words:
            if w_end <= cursor:
                continue
            if w_start >= end:
                break
            if w_start > cursor:
                runs.append((cursor, w_start))
            cursor = max(cursor, w_end)
            if cursor >= end:
                break
        if cursor < end:
            runs.append((cursor, end))
    return [(round(s, 3), round(e, 3)) for s, e in runs if e - s >= min_run_s - 1e-9]


def context_clips(
    gaps: Iterable[Span],
    *,
    duration_s: float,
    pad_s: float = CONTEXT_PAD_S,
    max_clip_s: float = CONTEXT_MAX_CLIP_S,
) -> list[ContextClip]:
    """Pad each gap with context (Whisper needs it to recognise a few words) and group the gaps
    that fit in one window, so each 30 s decode serves as many gaps as possible."""
    clips: list[ContextClip] = []
    for start, end in sorted(gaps):
        lo, hi = max(0.0, start - pad_s), min(duration_s, end + pad_s)
        if hi <= lo:
            continue
        if clips and (lo <= clips[-1].end or hi - clips[-1].start <= max_clip_s):
            last = clips[-1]
            clips[-1] = ContextClip(last.start, max(last.end, hi), (*last.gaps, (start, end)))
        else:
            clips.append(ContextClip(lo, hi, ((start, end),)))
    return [ContextClip(round(c.start, 3), round(c.end, 3), c.gaps) for c in clips]


def midpoint_within(start: float, end: float, spans: Iterable[Span]) -> bool:
    """Whether a word (by its midpoint) lies inside one of ``spans``: second-pass words outside
    the gaps duplicate what the first pass already has."""
    middle = (start + end) / 2
    return any(lo <= middle <= hi for lo, hi in spans)


# ---------------------------------------------------------------- subtitle cues
@dataclass(frozen=True, slots=True)
class Cue:
    start: float
    end: float
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return " ".join(self.lines)


@dataclass(frozen=True, slots=True)
class _Piece:
    start: float
    end: float
    sep: str  # "" when glued to the previous piece (no space: CJK, split tokens)
    text: str  # cleaned, never empty, at most max_line characters


def wrap_lines(text: str, max_line: int = MAX_LINE) -> list[str]:
    """Greedy wrap at spaces (fewest lines); a token longer than a line is cut."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        token = word
        while len(token) > max_line:
            if line:
                lines.append(line)
                line = ""
            lines.append(token[:max_line])
            token = token[max_line:]
        if not token:
            continue
        if not line:
            line = token
        elif len(line) + 1 + len(token) <= max_line:
            line = f"{line} {token}"
        else:
            lines.append(line)
            line = token
    if line:
        lines.append(line)
    return lines


def _layout(text: str, max_line: int) -> tuple[str, ...]:
    """Greedy lines, rebalanced at the space nearest the middle when it takes two lines."""
    greedy = wrap_lines(text, max_line)
    if len(greedy) != 2:
        return tuple(greedy)
    best: tuple[int, int] | None = None
    for i, ch in enumerate(text):
        if ch == " " and i <= max_line and len(text) - i - 1 <= max_line:
            width = max(i, len(text) - i - 1)
            if best is None or width < best[0]:
                best = (width, i)
    if best is None:
        return tuple(greedy)
    return text[: best[1]], text[best[1] + 1 :]


def _split_timed(start: float, end: float, sep: str, text: str, size: int) -> list[_Piece]:
    """Cut a too-long token into ``size``-character pieces with interpolated times."""
    if len(text) <= size:
        return [_Piece(start, end, sep, text)]
    chunks = [text[i : i + size] for i in range(0, len(text), size)]
    step = (end - start) / len(text)
    pieces: list[_Piece] = []
    offset = 0
    for n, chunk in enumerate(chunks):
        a = start + step * offset
        offset += len(chunk)
        pieces.append(_Piece(a, start + step * offset, sep if n == 0 else "", chunk))
    return pieces


def _segment_pieces(seg: SegmentLike, max_word_s: float, max_line: int) -> list[_Piece]:
    raw: list[tuple[float, float, str]] = [(w.start, w.end, w.text) for w in seg.words]
    if not raw:  # no word timings (embedded subtitles): spread the text over the segment
        tokens = seg.text.split()
        total = sum(len(t) for t in tokens) or 1
        step = max(0.0, seg.end - seg.start) / total
        at = seg.start
        for token in tokens:
            raw.append((at, at + step * len(token), " " + token))
            at += step * len(token)
    pieces: list[_Piece] = []
    for start, end, text in raw:
        cleaned = clean_untrusted(text)
        if not cleaned:
            continue
        a = max(0.0, start) if math.isfinite(start) else 0.0
        b = min(end, a + max_word_s) if math.isfinite(end) else a
        sep = " " if text[:1].isspace() else ""
        pieces.extend(_split_timed(a, max(a, b), sep, cleaned, max_line))
    return pieces


def _joined(pieces: Sequence[_Piece]) -> str:
    # Cleaned again as a whole: neighbouring words could otherwise rebuild a fence tag.
    return clean_untrusted("".join(p.sep + p.text for p in pieces))


def build_cues(
    segments: Iterable[SegmentLike],
    *,
    max_line: int = MAX_LINE,
    max_lines: int = MAX_LINES,
    max_cue_s: float = MAX_CUE_S,
    min_cue_s: float = MIN_CUE_S,
    max_word_s: float = MAX_WORD_S,
    gap_split_s: float = GAP_SPLIT_S,
    include_suspect: bool = False,
) -> list[Cue]:
    """Readable subtitle cues from word timings (Whisper segments can span 26 s of silence).

    A cue ends on a pause longer than ``gap_split_s``, after a sentence once it lasts
    ``min_cue_s``, after a comma once it holds a full line, and before it would exceed
    ``max_lines`` lines of ``max_line`` characters or ``max_cue_s`` seconds. Cues are sorted,
    never overlap, and last at least ``min_cue_s`` when the next cue leaves room.
    """
    # Segments in time order (second-pass ones arrive last), words in Whisper's own order.
    ordered = sorted(
        (seg for seg in segments if include_suspect or not seg.suspect), key=lambda s: s.start
    )
    pieces = [piece for seg in ordered for piece in _segment_pieces(seg, max_word_s, max_line)]
    groups: list[list[_Piece]] = []
    current: list[_Piece] = []
    for piece in pieces:
        if current:
            last = current[-1].text.rstrip(_CLOSING)
            end_so_far = max(p.end for p in current)
            if (
                piece.start - end_so_far > gap_split_s
                or len(wrap_lines(_joined([*current, piece]), max_line)) > max_lines
                or max(end_so_far, piece.end) - current[0].start > max_cue_s
                or (last.endswith(_SENTENCE_END) and end_so_far - current[0].start >= min_cue_s)
                or (last.endswith(_SOFT_END) and len(_joined(current)) > max_line)
            ):
                groups.append(current)
                current = []
        current.append(piece)
    if current:
        groups.append(current)

    cues: list[Cue] = []
    floor = 0.0
    for i, group in enumerate(groups):
        start = max(group[0].start, floor)
        end = max(start + min_cue_s, *(p.end for p in group))
        if i + 1 < len(groups):
            end = min(end, groups[i + 1][0].start)
        end = max(end, start + _MIN_VISIBLE_S)
        cues.append(Cue(round(start, 3), round(end, 3), _layout(_joined(group), max_line)))
        floor = end
    return cues


# ---------------------------------------------------------------- SRT / WebVTT
def _stamp(seconds: float, sep: str) -> str:
    ms = max(0, round(seconds * 1000))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def _safe_lines(cue: Cue) -> list[str]:
    """Cue lines as single non-empty lines without a timing arrow (a blank line or ``-->`` in
    the text would end the cue or start a new one)."""
    lines = (_ARROW.sub("->", clean_untrusted(line)) for line in cue.lines)
    return [line for line in lines if line]


def to_srt(cues: Iterable[Cue], offset_s: float = 0.0) -> str:
    """SubRip text (UTF-8, LF). ``offset_s`` shifts every time (e.g. a source timecode)."""
    blocks: list[str] = []
    for cue in cues:
        lines = _safe_lines(cue)
        if lines:
            stamps = f"{_stamp(cue.start + offset_s, ',')} --> {_stamp(cue.end + offset_s, ',')}"
            blocks.append(f"{len(blocks) + 1}\n{stamps}\n" + "\n".join(lines) + "\n")
    return "\n".join(blocks)


def _vtt_escape(line: str) -> str:
    return line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_vtt(cues: Iterable[Cue], offset_s: float = 0.0) -> str:
    """WebVTT text: markup characters escaped, so the browser never parses speech as tags."""
    blocks = ["WEBVTT\n"]
    for cue in cues:
        lines = _safe_lines(cue)
        if lines:
            stamps = f"{_stamp(cue.start + offset_s, '.')} --> {_stamp(cue.end + offset_s, '.')}"
            blocks.append(f"{stamps}\n" + "\n".join(_vtt_escape(line) for line in lines) + "\n")
    return "\n".join(blocks)


# ---------------------------------------------------------------- excerpt for the vision model
def excerpt_at(
    t: float,
    segments: Iterable[SegmentLike],
    *,
    before: float = 6.0,
    after: float = 6.0,
    max_chars: int = 240,
) -> str | None:
    """What is said in ``[t - before, t + after]``, from trusted segments only.

    Deterministic, with no model name or score in it: the per-frame vision cache key only
    changes where the speech around that frame changed. When too long, the words farthest from
    ``t`` are dropped first and an ellipsis marks each cut side.
    """
    lo, hi = t - before, t + after
    items: list[tuple[float, float, str, str]] = []
    for seg in segments:
        if seg.suspect:
            continue
        timed = [(w.start, w.end, w.text) for w in seg.words] or [(seg.start, seg.end, seg.text)]
        for start, end, text in timed:
            cleaned = clean_untrusted(text)
            if cleaned and end > lo and start < hi:
                sep = "" if text[:1] and not text[:1].isspace() and seg.words else " "
                items.append((start, end, sep, cleaned))
    if not items:
        return None
    items.sort(key=lambda item: item[0])
    first, last = 0, len(items)

    def render(a: int, b: int) -> str:
        body = "".join(sep + text for _s, _e, sep, text in items[a:b]).strip()
        return ("… " if a > 0 else "") + body + (" …" if b < len(items) else "")

    text = render(first, last)
    while len(text) > max_chars and last - first > 1:
        head, tail = items[first], items[last - 1]
        if abs((head[0] + head[1]) / 2 - t) > abs((tail[0] + tail[1]) / 2 - t):
            first += 1
        else:
            last -= 1
        text = render(first, last)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text or None
