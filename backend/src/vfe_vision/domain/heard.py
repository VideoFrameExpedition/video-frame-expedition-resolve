"""« Sounds heard »: the specific sounds of a file, from YAMNet and, when installed, CED-small.

The ten families (speech, nature…) say what dominates; this list names what is heard: birds,
a dog, rain, footsteps, applause. Rules measured on ESC-50 and on the corpus:

* **candidates** are specific classes: no recording context (room, noise), no class that only
  repeats a family (Animal, Vehicle), no speech (the transcript covers it), no music or
  instrument (listed apart), no silence, no recording issue (wind in the microphone, hum:
  flagged apart), and not Snake, Hiss, Rattle, Spray or Steam, which are electronic hiss or
  clatter on rushes far more often than the real thing, nor whispering and battle cries, too
  close to speech;
* **YAMNet** keeps a class whose 3-frame median stays ≥ 0.2 for ≥ 1 s in total and peaks at
  ≥ 0.35 (ESC-50: recall 0.63, precision 0.73, 0.30 false label per clip, against 0.71 / 0.65 /
  0.47 for the « ≥ 0.5 once » rule of the notable sounds);
* **with CED-small**, the list is the union of CED windows ≥ 0.3 and of YAMNet classes peaking
  at ≥ 0.5 that CED also gives ≥ 0.1 over the same time (recall 0.78, precision 0.86, 0.18 false
  label per clip; CED alone 0.74 / 0.89 / 0.14, the ungated union 0.79 / 0.78 / 0.30). A CED
  window where CED itself hears music (≥ 0.3) or speech (≥ 0.5) only counts from 0.6: under
  narration and music CED heard a cat in an archive film and a sonar over a bee. The thresholds
  come from the gate planned for bird species; this use was tuned on the corpus (22 files);
* an ancestor is dropped when the kept descendants cover ≥ 80 % of its time (Bird under
  « Chant d'oiseau »), and a family lists at most five sounds, the longest; a descendant cut
  by that limit no longer hides its ancestor.

Times: YAMNet frames are 0.48 s apart, CED windows 2.5 s. A sound YAMNet heard keeps YAMNet's
times, plus CED's cells for the windows where YAMNet did not hear it at all; a sound only CED
heard has CED's cells.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from vfe_vision.domain.audio_events import (
    GENERIC,
    ISSUE_CLASSES,
    Category,
    Rollup,
    frame_cells,
    median_filter,
)

HEARD_VERSION = 2  # bump when rules or thresholds change (part of the cache key)

SPEECH_MID = "/m/09x0r"  # Speech and its children (conversation, narration…)
MUSIC_MID = "/m/04rlf"
# Electronic hiss or clatter on rushes far more often than the real thing; speech-like cries
# (the transcript covers speech).
EXCLUDED = frozenset({"Snake", "Hiss", "Rattle", "Spray", "Steam", "Whispering", "Battle cry"})
VAGUE = frozenset({"Human voice", "Sound effect", "Inside, public space"})
_NOT_HEARD = frozenset({Category.MUSIC, Category.SILENCE})

MEDIAN_ON = 0.2
MIN_SECONDS = 1.0
YAMNET_PEAK = 0.35  # YAMNet alone
YAMNET_PEAK_WITH_CED = 0.5
CED_ON = 0.3
CED_AGREES = 0.1
# Music or speech heard by CED in the same window masks other sounds (thresholds of the planned
# bird-species gate, on CED's own scores, tuned on the corpus)…
CED_BUSY_MUSIC = 0.3
CED_BUSY_SPEECH = 0.5
CED_SURE = 0.6  # …unless CED is this sure
MERGE_GAP_S = 1.0
ANCESTOR_COVER = 0.8
PER_FAMILY = 5
MAX_SPANS = 12  # moments stored per sound for display; every moment feeds the shots and search
_EPS = 1e-9

FloatArray = npt.NDArray[np.float64]
Span = tuple[float, float]


@dataclass(frozen=True, slots=True)
class HeardSound:
    label: str  # AudioSet display name (English)
    mid: str
    category: Category
    seconds: float
    score: float  # the higher of YAMNet's median peak and CED's best window
    spans: tuple[Span, ...]  # every moment it is heard, merged, in time order
    sources: tuple[str, ...]  # "yamnet", "ced"

    @property
    def first_s(self) -> float:
        return self.spans[0][0] if self.spans else 0.0


@dataclass(frozen=True, slots=True)
class CedWindows:
    """CED probabilities per window (``probs`` [windows, classes] in ``rollup`` order)."""

    start_s: FloatArray
    end_s: FloatArray
    probs: npt.NDArray[np.floating]
    rollup: Rollup


@dataclass(slots=True)
class _Found:
    label: str
    mid: str
    category: Category
    ancestors: frozenset[str]
    seconds: float
    score: float
    spans: list[Span]
    sources: tuple[str, ...]
    windows: list[tuple[Span, Span]] = field(default_factory=list)  # CED: (window, its cell)


def heard_classes(rollup: Rollup) -> npt.NDArray[np.intp]:
    """Indices of the classes that may be listed as heard (see the module docstring)."""
    return np.fromiter(
        (
            i
            for i, (name, mid) in enumerate(zip(rollup.names, rollup.mids, strict=True))
            if not rollup.context[i]
            and name not in GENERIC
            and name not in VAGUE
            and name not in EXCLUDED
            and name not in ISSUE_CLASSES
            and rollup.categories[i] not in _NOT_HEARD
            and mid != SPEECH_MID
            and SPEECH_MID not in rollup.ancestors[i]
        ),
        dtype=np.intp,
    )


def heard_sounds(
    scores: npt.ArrayLike,
    rollup: Rollup,
    *,
    duration_s: float,
    ced: CedWindows | None = None,
) -> list[HeardSound]:
    """The specific sounds heard in a file, longest first (see the module docstring).

    Malformed CED windows (shapes that do not match) are ignored: YAMNet's rule alone applies.
    """
    duration = float(duration_s)
    matrix = np.nan_to_num(np.asarray(scores, dtype=np.float64), nan=0.0)
    if not duration > 0 or matrix.ndim != 2 or matrix.shape[1] != len(rollup):
        return []
    second = _checked(ced)
    peak = YAMNET_PEAK if second is None else YAMNET_PEAK_WITH_CED
    found = _from_yamnet(matrix, rollup, duration, peak)
    if second is not None:
        found = {mid: f for mid, f in found.items() if _ced_agrees(second, f)}
        for mid, heard in _from_ced(second, duration).items():
            mine = found.get(mid)
            if mine is None:
                found[mid] = heard
                continue
            mine.sources = ("yamnet", "ced")
            mine.score = max(mine.score, heard.score)
            # Moments YAMNet missed entirely (no overlap with its times) come from CED.
            missed = [cell for window, cell in heard.windows if not _touches(window, mine.spans)]
            if missed:
                mine.spans = _merged([*mine.spans, *missed])
                mine.seconds += sum(b - a for a, b in missed)
    return _finish(list(found.values()))


def shot_heard(
    sounds: Sequence[HeardSound], spans: Sequence[Span], *, limit: int = 4
) -> list[list[str]]:
    """Labels of the heard sounds overlapping each (start_s, end_s) shot, most heard first."""
    out: list[list[str]] = []
    for a, b in spans:
        overlap = {sound.label: _intersection(sound.spans, [(a, b)]) for sound in sounds}
        ranked = sorted((s for s in overlap.items() if s[1] > _EPS), key=lambda s: -s[1])
        out.append([label for label, _ in ranked[:limit]])
    return out


def heard_dict(sound: HeardSound) -> dict[str, object]:
    """JSON-ready form stored in the audio scene (names are localised when read): the first
    MAX_SPANS moments, and how many there are in all."""
    return {
        "label": sound.label,
        "category": sound.category.value,
        "seconds": sound.seconds,
        "score": sound.score,
        "spans": [list(span) for span in sound.spans[:MAX_SPANS]],
        "moments": len(sound.spans),
        "sources": list(sound.sources),
    }


def window_cells(
    start_s: npt.ArrayLike, end_s: npt.ArrayLike, duration_s: float
) -> tuple[FloatArray, FloatArray]:
    """Timeline cell of each window, cut halfway between window centres, clamped to the file.

    Regular 5 s windows every 2.5 s own the 2.5 s around their centre, the first from 0 and the
    last to the end: cells never overlap and their widths add up to the duration.
    """
    begin = np.asarray(start_s, dtype=np.float64)
    finish = np.asarray(end_s, dtype=np.float64)
    duration = max(0.0, float(duration_s))
    if not len(begin):
        return begin.copy(), finish.copy()
    centre = (begin + finish) / 2
    cut = (centre[:-1] + centre[1:]) / 2
    cell_start = np.clip(np.concatenate(([0.0], cut)), 0.0, duration)
    cell_end = np.clip(np.concatenate((cut, [duration])), 0.0, duration)
    return cell_start, np.maximum(cell_end, cell_start)


# ---------------------------------------------------------------- sources
def _checked(ced: CedWindows | None) -> CedWindows | None:
    """CED windows with finite probabilities in 0–1, or None when absent, empty or malformed."""
    if ced is None:
        return None
    start = np.asarray(ced.start_s, dtype=np.float64).reshape(-1)
    end = np.asarray(ced.end_s, dtype=np.float64).reshape(-1)
    probs = np.asarray(ced.probs, dtype=np.float64)
    if (
        probs.ndim != 2
        or not len(probs)
        or probs.shape != (len(start), len(ced.rollup))
        or len(end) != len(start)
    ):
        return None
    clean = np.clip(np.nan_to_num(probs, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    return CedWindows(start, end, clean, ced.rollup)


def _from_yamnet(
    matrix: FloatArray, rollup: Rollup, duration: float, peak: float
) -> dict[str, _Found]:
    columns = heard_classes(rollup)
    if not len(matrix) or not len(columns):
        return {}
    start, end = frame_cells(len(matrix), duration)
    width = end - start
    smooth = median_filter(matrix[:, columns])
    on = smooth >= MEDIAN_ON
    seconds = width @ on
    best = smooth.max(axis=0)
    found: dict[str, _Found] = {}
    for k in np.flatnonzero((seconds >= MIN_SECONDS - _EPS) & (best >= peak)).tolist():
        index = int(columns[k])
        found[rollup.mids[index]] = _found(
            rollup,
            index,
            seconds=float(seconds[k]),
            score=float(best[k]),
            spans=_merged(zip(start[on[:, k]].tolist(), end[on[:, k]].tolist(), strict=True)),
            sources=("yamnet",),
        )
    return found


def _from_ced(ced: CedWindows, duration: float) -> dict[str, _Found]:
    rollup = ced.rollup
    columns = heard_classes(rollup)
    probs = np.asarray(ced.probs, dtype=np.float64)
    if not len(columns):
        return {}
    start, end = window_cells(ced.start_s, ced.end_s, duration)
    width = end - start
    busy = np.zeros(len(probs), dtype=bool)
    for mid, limit in ((MUSIC_MID, CED_BUSY_MUSIC), (SPEECH_MID, CED_BUSY_SPEECH)):
        if (index := rollup.index_of_mid(mid)) is not None:
            busy |= probs[:, index] >= limit
    selected = probs[:, columns]
    hit = (selected >= CED_ON) & (~busy[:, np.newaxis] | (selected >= CED_SURE))
    found: dict[str, _Found] = {}
    for k in np.flatnonzero(hit.any(axis=0)).tolist():
        index = int(columns[k])
        rows = np.flatnonzero(hit[:, k]).tolist()
        cells = [(float(start[r]), float(end[r])) for r in rows]
        heard = _found(
            rollup,
            index,
            seconds=float(width[rows].sum()),
            score=float(selected[rows, k].max()),
            spans=_merged(cells),
            sources=("ced",),
        )
        heard.windows = [
            ((float(ced.start_s[r]), float(ced.end_s[r])), cell)
            for r, cell in zip(rows, cells, strict=True)
        ]
        found[rollup.mids[index]] = heard
    return found


def _ced_agrees(ced: CedWindows, found: _Found) -> bool:
    """Whether CED gives the same class ≥ CED_AGREES in a window overlapping YAMNet's times."""
    index = ced.rollup.index_of_mid(found.mid)
    if index is None:
        return False
    for a, b in found.spans:
        overlapping = (ced.start_s < b) & (ced.end_s > a)
        if overlapping.any() and float(np.max(ced.probs[overlapping, index])) >= CED_AGREES:
            return True
    return False


# ---------------------------------------------------------------- helpers
def _found(
    rollup: Rollup,
    index: int,
    *,
    seconds: float,
    score: float,
    spans: list[Span],
    sources: tuple[str, ...],
) -> _Found:
    return _Found(
        label=rollup.names[index],
        mid=rollup.mids[index],
        category=rollup.categories[index],
        ancestors=rollup.ancestors[index],
        seconds=seconds,
        score=score,
        spans=spans,
        sources=sources,
    )


def _merged(spans: Iterable[Span]) -> list[Span]:
    """Time ranges in order, those less than MERGE_GAP_S apart joined."""
    out: list[Span] = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= MERGE_GAP_S + _EPS:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _touches(window: Span, spans: Sequence[Span]) -> bool:
    return any(a < window[1] and window[0] < b for a, b in spans)


def _intersection(first: Sequence[Span], second: Sequence[Span]) -> float:
    """Seconds shared by two lists of ranges (each list without overlaps of its own)."""
    return sum(max(0.0, min(b, y) - max(a, x)) for a, b in first for x, y in second)


def _covered(sound: _Found, others: Sequence[_Found]) -> bool:
    """Whether the kept descendants of ``sound`` cover ANCESTOR_COVER of its time."""
    descendants = [g for g in others if g is not sound and sound.mid in g.ancestors]
    total = sum(b - a for a, b in sound.spans)
    if not descendants or total <= _EPS:
        return False
    union = _merged(span for g in descendants for span in g.spans)
    return _intersection(sound.spans, union) >= ANCESTOR_COVER * total - _EPS


def _finish(found: list[_Found]) -> list[HeardSound]:
    """Ancestors covered by kept descendants give way, then five per family, longest first.

    A descendant cut by the family limit is taken out and the rule runs again, so it never
    hides an ancestor that would have been listed. Each round removes a sound: it ends.
    """
    candidates = list(found)
    while True:
        kept = [f for f in candidates if not _covered(f, candidates)]
        kept.sort(key=lambda f: (-f.seconds, -f.score, f.label))
        per_family: dict[Category, int] = {}
        listed: list[_Found] = []
        for f in kept:
            if per_family.get(f.category, 0) < PER_FAMILY:
                per_family[f.category] = per_family.get(f.category, 0) + 1
                listed.append(f)
        shown = {id(f) for f in listed}
        cut = {id(f) for f in kept if id(f) not in shown}
        if not cut:
            break
        candidates = [f for f in candidates if id(f) not in cut]
    return [
        HeardSound(
            label=f.label,
            mid=f.mid,
            category=f.category,
            seconds=round(f.seconds, 2),
            score=round(f.score, 3),
            spans=tuple((round(a, 2), round(b, 2)) for a, b in f.spans),
            sources=f.sources,
        )
        for f in listed
    ]
