"""The model bench: what a run holds, which frames it asks about, and what it measures.

Each vision model the user ticked is loaded alone, asked the requests of the analyses (frame
descriptions, living beings with their boxes) on the same frames of the library, then unloaded.
Nothing here needs a reference written by hand: the text a model reads is compared with the
lines the OCR stage read, its boxes with those of the CPU detector, and the quality of its
descriptions is rated blind by the user. Pure: the run itself is in ``jobs.bench``.
"""

from __future__ import annotations

import hashlib
import re
import statistics
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vfe_vision.domain.subjects import Box

BENCH_SCHEMA_VERSION = 1
IMAGE_CHOICES = (12, 24, 48)
DEFAULT_IMAGES = 24
MIN_IMAGES = 4  # a library this small still gives a first idea
MAX_MODELS = 12
# The same loading for every model, so that memory and speed compare: the context the analyses
# are comfortable with, four requests at a time.
CONTEXT_LENGTH = 12288
PARALLEL = 4
MATCH_IOU = 0.5  # a model's box and a detector's box are the same being (as in the fusion)
MIN_DETECTOR_SCORE = 0.5  # a detector box trusted on its own
MAX_REFERENCE_BEINGS = 6  # beyond: a crowd, where the models are told to box ten at most
FULL_VRAM_FREE_MIB = 300  # less left once loaded (NVIDIA): Windows moves memory to the RAM
MAX_RATING = 3  # 0 wrong · 1 approximate · 2 nearly right · 3 right
MAX_NOTE = 300  # what the user writes about a run, to find it again in the history
MIN_LANGUAGE_HITS = 3


class BenchRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"  # at least one model could not be tested
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_active(self) -> bool:
        return self in {BenchRunStatus.QUEUED, BenchRunStatus.RUNNING}


class BenchModelStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    LOAD_FAILED = "load_failed"  # LM Studio refused it (not enough memory, unknown model…)
    FAILED = "failed"
    CANCELLED = "cancelled"


class _Stored(BaseModel):
    """Stored as JSON in ``bench_runs``: unknown keys of a later version are dropped."""

    model_config = ConfigDict(extra="ignore")


class BenchBeing(_Stored):
    """A living being of the reference (the CPU detector), or one a model located."""

    category: str
    box: list[float]  # [x1, y1, x2, y2] in 0–1 of the image
    label: str | None = None
    main: bool = False


class BenchFrame(_Stored):
    """One frame of the library every model is asked about, with what the application already
    knows of it."""

    keyframe_id: str
    video_id: str
    filename: str
    t_s: float
    image_path: str
    thumb_path: str
    text: list[str] | None = None  # lines the OCR stage read; None: it did not run on the video
    beings: list[BenchBeing] | None = None  # detector boxes; None: it did not look at the frame


class Answer(_Stored):
    """One request and what came back."""

    ok: bool = False
    error: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    repaired: bool = False
    truncated: bool = False
    data: dict[str, Any] | None = None  # the frame description, or the beings as written
    beings: list[BenchBeing] | None = None  # the boxes, read with the model's convention


class Batch(_Stored):
    wall_s: float = 0.0
    answers: dict[str, Answer] = Field(default_factory=dict)  # by keyframe id


class Vram(_Stored):
    used: int
    free: int
    total: int


class BenchCalibration(_Stored):
    """How the model writes boxes, as the calibration measured it."""

    source: str  # prior | profile | none
    enabled: bool = False
    convention: str | None = None
    box_field: str | None = None
    mean_iou: float | None = None
    reason: str | None = None
    reasoning_tokens: int = 0
    truncated: bool = False


class ModelRun(_Stored):
    key: str
    display_name: str
    publisher: str | None = None
    architecture: str | None = None
    params: str | None = None
    quantization: str | None = None
    size_bytes: int | None = None
    reasoning_capable: bool = False
    status: BenchModelStatus = BenchModelStatus.PENDING
    error: str | None = None
    load_s: float | None = None
    context_length: int | None = None
    parallel: int | None = None
    vram_before: Vram | None = None
    vram_loaded: Vram | None = None
    vram_peak_mib: int | None = None
    calibration: BenchCalibration | None = None
    frames: Batch | None = None
    positions: Batch | None = None


class BenchPreviousModel(_Stored):
    """A model that was loaded before the run: unloaded for it, loaded again after."""

    key: str
    instance_id: str
    display_name: str
    context_length: int | None = None
    parallel: int | None = None
    restored: bool | None = None  # None: not tried yet
    error: str | None = None


class BenchData(_Stored):
    """Everything a run stores (``bench_runs.data``)."""

    schema_version: int = BENCH_SCHEMA_VERSION
    images: int = DEFAULT_IMAGES
    language: str = "fr"
    context_length: int = CONTEXT_LENGTH
    parallel: int = PARALLEL
    model_keys: list[str] = Field(default_factory=list)
    frames: list[BenchFrame] = Field(default_factory=list)
    models: list[ModelRun] = Field(default_factory=list)
    previous: list[BenchPreviousModel] | None = None  # None: LM Studio not looked at yet
    error: str | None = None
    note: str | None = None  # the user's words about this run


Ratings = Mapping[str, Mapping[str, int]]  # keyframe id → model key → 0..MAX_RATING


# ------------------------------------------------------------------------------ which frames
@dataclass(frozen=True, slots=True)
class Candidate:
    keyframe_id: str
    video_id: str
    has_text: bool
    has_beings: bool


def _rank(value: str) -> str:
    """A stable shuffle: the same library gives the same frames, run after run."""
    return hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()


def image_set(frames: Sequence[BenchFrame]) -> str:
    """A short name for the frames of a run: two runs with the same name asked about the same
    frames, and everything they measured compares; otherwise only memory and speed do."""
    if not frames:
        return ""
    return _rank("|".join(sorted(frame.keyframe_id for frame in frames)))[:12]


def choose_frames(candidates: Sequence[Candidate], count: int) -> list[str]:
    """``count`` frames spread over the videos, about a quarter with text on screen and half
    with living beings when the library has them; always the same for the same library, so that
    a model tested next month compares with those tested today."""
    by_video: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_video.setdefault(candidate.video_id, []).append(candidate)
    queues = [
        sorted(frames, key=lambda c: _rank(c.keyframe_id), reverse=True)
        for _, frames in sorted(by_video.items(), key=lambda item: _rank(item[0]))
    ]
    spread: list[Candidate] = []  # one frame of each video in turn
    while any(queues):
        spread += [queue.pop() for queue in queues if queue]
    position = {candidate.keyframe_id: index for index, candidate in enumerate(spread)}
    chosen: dict[str, None] = {}

    def take(wanted: Sequence[Candidate], limit: int) -> None:
        for candidate in wanted:
            if limit <= 0 or len(chosen) >= count:
                return
            if candidate.keyframe_id not in chosen:
                chosen[candidate.keyframe_id] = None
                limit -= 1

    take([c for c in spread if c.has_text], count // 4)
    take([c for c in spread if c.has_beings and not c.has_text], count // 2)
    take(spread, count)
    return sorted(chosen, key=position.__getitem__)


# ------------------------------------------------------------------------------ language
# Words that tell a language apart in a short description (articles, prepositions, « to be »).
_FUNCTION_WORDS: dict[str, frozenset[str]] = {
    "fr": frozenset(
        {
            "le",
            "les",
            "des",
            "une",
            "est",
            "dans",
            "avec",
            "sur",
            "du",
            "au",
            "aux",
            "et",
            "qui",
            "sont",
            "cette",
            "ces",
            "sous",
            "vers",
            "près",
            "pour",
            "par",
            "elle",
            "ils",
            "leur",
            "être",
            "très",
        }
    ),
    "en": frozenset(
        {
            "the",
            "and",
            "with",
            "is",
            "are",
            "of",
            "there",
            "its",
            "this",
            "that",
            "from",
            "near",
            "while",
            "which",
            "into",
            "it",
            "has",
            "behind",
            "their",
            "being",
            "very",
        }
    ),
    "es": frozenset(
        {
            "el",
            "los",
            "las",
            "una",
            "con",
            "del",
            "está",
            "están",
            "sobre",
            "hacia",
            "cerca",
            "para",
            "por",
            "sus",
            "este",
            "esta",
            "es",
            "muy",
            "hay",
            "junto",
        }
    ),
    "de": frozenset(
        {
            "der",
            "die",
            "das",
            "und",
            "ist",
            "mit",
            "auf",
            "ein",
            "eine",
            "einem",
            "einer",
            "im",
            "von",
            "zu",
            "den",
            "dem",
            "sind",
            "über",
            "unter",
            "neben",
            "vor",
            "nicht",
            "sich",
            "wird",
        }
    ),
    "it": frozenset(
        {
            "il",
            "lo",
            "gli",
            "è",
            "della",
            "dei",
            "nel",
            "nella",
            "sono",
            "che",
            "verso",
            "vicino",
            "per",
            "sul",
            "sulla",
            "di",
            "degli",
            "delle",
            "alla",
            "molto",
        }
    ),
}
_WORD = re.compile(r"[^\W\d_]+")
_FOREIGN_SCRIPT = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def language_of(text: str) -> str | None:
    """The language a short text is written in, among those the analyses know; None when it
    cannot be told (too short, or none of them)."""
    words = [word.casefold() for word in _WORD.findall(text)]
    hits = {
        language: sum(1 for word in words if word in known)
        for language, known in _FUNCTION_WORDS.items()
    }
    best = max(hits, key=lambda language: hits[language])
    if hits[best] < MIN_LANGUAGE_HITS:
        return None
    return best


def wrong_language(text: str, asked: str) -> bool | None:
    """Whether a description asked in ``asked`` came back in another language; None when the
    check does not apply (a language without a word list, or a text too short)."""
    if asked not in _FUNCTION_WORDS:
        return None
    found = language_of(text)
    if found is None:
        return None
    if found == asked:
        return False
    words = [word.casefold() for word in _WORD.findall(text)]
    own = sum(1 for word in words if word in _FUNCTION_WORDS[asked])
    other = sum(1 for word in words if word in _FUNCTION_WORDS[found])
    return other > own


def has_foreign_script(value: object) -> bool:
    """Chinese, Japanese or Korean characters somewhere in an answer (Qwen slips them in)."""
    if isinstance(value, str):
        return _FOREIGN_SCRIPT.search(value) is not None
    if isinstance(value, Mapping):
        return any(has_foreign_script(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(has_foreign_script(item) for item in value)
    return False


# ------------------------------------------------------------------------------ text read
def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def text_words(lines: Sequence[str]) -> set[str]:
    """The words worth finding again in what a model read: three characters or more."""
    words: set[str] = set()
    for line in lines:
        for word in re.split(r"[^a-z0-9]+", _plain(line)):
            if len(word) >= 3:  # shorter ones are noise of the OCR
                words.add(word)
    return words


def _squashed(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _plain(text))


# ------------------------------------------------------------------------------ positions
_SAME_KIND = {
    "person": {"person", "body_part"},
    "mammal": {"mammal"},
    "bird": {"bird"},
}


def match_beings(reference: Sequence[BenchBeing], found: Sequence[BenchBeing]) -> list[float]:
    """For each reference being, the overlap (IoU) with the model's box of the same kind that
    fits it best, each box used once; 0 when the model did not locate it."""
    boxes = [Box.of(being.box) for being in found]
    used: set[int] = set()
    overlaps: list[float] = []
    ordered = sorted(reference, key=lambda being: _area(being.box), reverse=True)
    for being in ordered:
        target = Box.of(being.box)
        kinds = _SAME_KIND.get(being.category, {being.category})
        best, best_index = 0.0, -1
        for index, box in enumerate(boxes):
            if index in used or box is None or target is None:
                continue
            if found[index].category not in kinds:
                continue
            overlap = target.iou(box)
            if overlap > best:
                best, best_index = overlap, index
        if best >= MATCH_IOU:
            used.add(best_index)
            overlaps.append(best)
        else:
            overlaps.append(0.0)
    return overlaps


def _area(box: Sequence[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1]) if len(box) == 4 else 0.0


# ------------------------------------------------------------------------------ scores
class BenchScores(BaseModel):
    """What a run measured for one model (computed when read, from what is stored)."""

    vram_mib: int | None = None  # what the model takes once loaded (NVIDIA; a Mac measures none)
    vram_free_mib: int | None = None  # what is left then
    vram_total_mib: int | None = None
    vram_full: bool = False  # so little left that Windows spills into the system RAM
    seconds_per_image: float | None = None  # descriptions, at the instance's parallel requests
    seconds_per_position: float | None = None
    tokens_per_s: float | None = None
    requests: int = 0
    valid: int = 0
    repaired: int = 0
    truncated: int = 0
    reasoning_tokens: int = 0
    language_checked: int = 0
    wrong_language: int = 0
    foreign_script: int = 0
    text_frames: int = 0  # frames where the OCR read something
    text_recall: float | None = None  # share of its words the model read too (mean per frame)
    blank_frames: int = 0  # frames where the OCR read nothing
    unconfirmed_text: int = 0  # of those, how many the model read a text on
    position_frames: int = 0
    position_beings: int = 0
    position_recall: float | None = None  # share of the detector's beings the model located
    position_iou: float | None = None  # how tightly, on those it located
    positions_enabled: bool | None = None
    calibration_iou: float | None = None
    rated: int = 0
    rating: float | None = None  # mean of the blind ratings, 0–MAX_RATING


def _text_scores(
    scores: BenchScores, frames: Sequence[BenchFrame], answers: Mapping[str, Answer]
) -> None:
    shares: list[float] = []  # per frame: a screen full of text weighs as much as a sign
    for frame in frames:
        answer = answers.get(frame.keyframe_id)
        if frame.text is None or answer is None or not answer.ok or answer.data is None:
            continue
        read = str(answer.data.get("visible_text") or "")
        reference = text_words(frame.text)
        if reference:
            squashed = _squashed(read)
            shares.append(sum(1 for word in reference if word in squashed) / len(reference))
        else:
            scores.blank_frames += 1
            scores.unconfirmed_text += bool(text_words([read]))
    scores.text_frames = len(shares)
    scores.text_recall = round(statistics.mean(shares), 4) if shares else None


def _language_scores(
    scores: BenchScores, answers: Mapping[str, Answer], language: str, positions: Batch | None
) -> None:
    for answer in answers.values():
        if not answer.ok or answer.data is None:
            continue
        prose = f"{answer.data.get('caption') or ''} {answer.data.get('description') or ''}"
        wrong = wrong_language(prose, language)
        if wrong is not None:
            scores.language_checked += 1
            scores.wrong_language += wrong
        scores.foreign_script += has_foreign_script(answer.data)
    for answer in (positions.answers if positions else {}).values():
        if answer.ok:
            scores.foreign_script += has_foreign_script(answer.data)


def _position_scores(scores: BenchScores, frames: Sequence[BenchFrame], positions: Batch) -> None:
    overlaps: list[float] = []
    for frame in frames:
        answer = positions.answers.get(frame.keyframe_id)
        if answer is None or not answer.ok or answer.beings is None or not frame.beings:
            continue
        if len(frame.beings) > MAX_REFERENCE_BEINGS:
            continue
        scores.position_frames += 1
        overlaps += match_beings(frame.beings, answer.beings)
    scores.position_beings = len(overlaps)
    located = [overlap for overlap in overlaps if overlap > 0]
    scores.position_recall = round(len(located) / len(overlaps), 4) if overlaps else None
    scores.position_iou = round(statistics.mean(located), 4) if located else None


def _batch_scores(scores: BenchScores, batch: Batch) -> int:
    """Count a batch's requests; return how many answered."""
    valid = 0
    for answer in batch.answers.values():
        scores.requests += 1
        valid += answer.ok
        scores.repaired += answer.repaired
        scores.truncated += answer.truncated
        scores.reasoning_tokens += answer.reasoning_tokens or 0
    scores.valid += valid
    return valid


def score(
    model: ModelRun, frames: Sequence[BenchFrame], language: str, ratings: Ratings | None = None
) -> BenchScores:
    """Every measure of one model, from what its run stored."""
    scores = BenchScores()
    if model.vram_loaded is not None:
        scores.vram_free_mib = model.vram_loaded.free
        scores.vram_total_mib = model.vram_loaded.total
        scores.vram_full = model.vram_loaded.free < FULL_VRAM_FREE_MIB
        if model.vram_before is not None:
            scores.vram_mib = max(0, model.vram_loaded.used - model.vram_before.used)
    if model.calibration is not None:
        scores.positions_enabled = model.calibration.enabled
        scores.calibration_iou = model.calibration.mean_iou
        scores.reasoning_tokens += model.calibration.reasoning_tokens
    if model.frames is not None:
        valid = _batch_scores(scores, model.frames)
        if valid and model.frames.wall_s > 0:
            scores.seconds_per_image = round(model.frames.wall_s / valid, 2)
            tokens = sum(a.completion_tokens or 0 for a in model.frames.answers.values() if a.ok)
            scores.tokens_per_s = round(tokens / model.frames.wall_s, 1)
        _language_scores(scores, model.frames.answers, language, model.positions)
        _text_scores(scores, frames, model.frames.answers)
    if model.positions is not None:
        valid = _batch_scores(scores, model.positions)
        if valid and model.positions.wall_s > 0:
            scores.seconds_per_position = round(model.positions.wall_s / valid, 2)
        _position_scores(scores, frames, model.positions)
    given = [
        marks[model.key]
        for marks in (ratings or {}).values()
        if isinstance(marks.get(model.key), int)
    ]
    scores.rated = len(given)
    scores.rating = round(statistics.mean(given), 2) if given else None
    return scores
