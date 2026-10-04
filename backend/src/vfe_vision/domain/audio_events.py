"""Sounds, ambience categories and musical instruments from YAMNet scores (pure numpy).

YAMNet (Google, trained on AudioSet) gives 521 independent sigmoid scores per 0.96 s patch, one
patch every 0.48 s. This module turns that matrix into what a videographer asks about:

* ten categories (speech, music, nature, wind, water, vehicles, crowd, tools, silence, other),
  rolled up through the AudioSet ontology. The ontology is a DAG (36 YAMNet classes have
  several parents: Hiss → Cat/Snake/Steam, Bell → Musical instrument/Sounds of things), so a
  class takes the first category, in priority order, whose anchor is among its ancestors, unless
  an explicit override decides. Acoustic environment, noise and sound reproduction classes
  describe the recording rather than a source: they stay out of the categories and feed an
  environment tag and issue flags instead;
* presence (multi-label, share of the duration) and dominance (one category per instant, sums
  to 1) after a 3-frame median, per-category hysteresis, minimum durations and gap merging;
* short salient events, top labels per file or per shot, and the musical instruments heard.

The category table is derived at load time from the downloaded ontology (CC BY-SA 4.0): only
anchor mids and display-name overrides live here, so no derived table is committed.

Timeline: frame ``i`` covers the patch [0.48 i, 0.48 i + 0.96] s and owns the 0.48 s cell
centred on the patch centre (0.48 i + 0.48), the first cell starting at 0 and the last one
ending at the duration. Edges are therefore uncertain by about ± 0.5 s.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view

AUDIO_EVENTS_VERSION = 2  # bump when rules, thresholds or outputs change (part of cache keys)
HOP_S = 0.48
PATCH_S = 0.96

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
IndexArray = npt.NDArray[np.intp]
ScoreMatrix = npt.NDArray[np.floating[Any]]


class Category(StrEnum):
    SPEECH = "speech"
    MUSIC = "music"
    NATURE = "nature"
    WIND = "wind"
    WATER = "water"
    VEHICLES = "vehicles"
    CROWD = "crowd"
    TOOLS = "tools"
    SILENCE = "silence"
    OTHER = "other"


CATEGORIES: tuple[Category, ...] = tuple(Category)

CATEGORY_LABELS: Mapping[Category, str] = {
    Category.SPEECH: "Parole",
    Category.MUSIC: "Musique",
    Category.NATURE: "Nature et animaux",
    Category.WIND: "Vent",
    Category.WATER: "Eau",
    Category.VEHICLES: "Véhicules",
    Category.CROWD: "Foule",
    Category.TOOLS: "Outils et machines",
    Category.SILENCE: "Silence",
    Category.OTHER: "Autres sons",
}

MUSICAL_INSTRUMENT = "/m/04szw"

# (category, anchor mids) in priority order: the first anchor found among a class's ancestors
# (every parent followed) wins. The order settles nesting: Wind and Water sit under Natural
# sounds, Singing under Human voice, Chainsaw under Engine.
RULES: tuple[tuple[Category, frozenset[str]], ...] = (
    (Category.SILENCE, frozenset({"/m/028v0c"})),  # Silence
    (Category.WIND, frozenset({"/m/03m9d0z"})),  # Wind
    (Category.WATER, frozenset({"/m/0838f", "/m/04k94"})),  # Water, Liquid
    (Category.CROWD, frozenset({"/t/dd00012"})),  # Human group actions
    (Category.MUSIC, frozenset({"/m/04rlf", "/m/015lz1"})),  # Music, Singing
    (Category.SPEECH, frozenset({"/m/09l8g"})),  # Human voice (Singing is taken above)
    (Category.TOOLS, frozenset({"/m/07k1x", "/t/dd00077"})),  # Tools, Mechanisms
    (Category.NATURE, frozenset({"/m/0jbk", "/m/059j3w", "/t/dd00129"})),  # Animal, Natural…
    (Category.VEHICLES, frozenset({"/m/07yv9", "/m/02mk9"})),  # Vehicle, Engine
)

# Explicit decisions for multi-parent or misleading classes (display name → category).
OVERRIDES: Mapping[str, Category] = {
    "Hiss": Category.OTHER,  # Cat / Snake / Steam: mostly electronic hiss on rushes
    "Snake": Category.OTHER,  # heard on rushes as hiss or cicadas, never as a snake
    "Buzz": Category.OTHER,  # Bee / Fly / Brief tone: electrical buzz as often as insects
    "Rattle": Category.OTHER,  # Snake / Onomatopoeia
    "Crackle": Category.OTHER,  # Fire / Onomatopoeia: vinyl, electrical crackle
    "Squish": Category.OTHER,  # Liquid / Onomatopoeia
    "Bell": Category.OTHER,  # Musical instrument / Sounds of things: bells are ambience
    "Church bell": Category.OTHER,
    "Change ringing (campanology)": Category.OTHER,
    "Wind chime": Category.OTHER,
    "Chime": Category.OTHER,
    "Cowbell": Category.OTHER,  # Bell / Cattle / Percussion
    "Bicycle bell": Category.VEHICLES,
    "Jingle bell": Category.MUSIC,
    "Tuning fork": Category.MUSIC,
    "Humming": Category.MUSIC,
    "Whistling": Category.SPEECH,  # human whistling, voice-like on a rush
    "Vacuum cleaner": Category.TOOLS,
    "Blender": Category.TOOLS,
    "Hair dryer": Category.TOOLS,
    "Electric shaver, electric razor": Category.TOOLS,
    "Electric toothbrush": Category.TOOLS,
    "Microwave oven": Category.TOOLS,
    "Dental drill, dentist's drill": Category.TOOLS,
    "Lawn mower": Category.TOOLS,
    "Chainsaw": Category.TOOLS,
    "Water tap, faucet": Category.WATER,
    "Sink (filling or washing)": Category.WATER,
    "Bathtub (filling or washing)": Category.WATER,
    "Toilet flush": Category.WATER,
    "Wind noise (microphone)": Category.WIND,
}

# Context, not a source: Acoustic environment, Noise, Sound reproduction (and their children).
CONTEXT_ANCHORS = frozenset({"/t/dd00093", "/m/096m7z", "/m/07bm98"})
CONTEXT_EXCEPT = frozenset({"Hubbub, speech noise, speech babble", "Outside, rural or natural"})

# Classes that only repeat a category: poor events.
GENERIC = frozenset(
    {
        "Speech", "Music", "Silence", "Animal", "Vehicle", "Musical instrument",
        "Domestic animals, pets", "Wild animals", "Livestock, farm animals, working animals",
        "Motor vehicle (road)", "Engine", "Liquid", "Water", "Outside, rural or natural",
        "Mechanisms", "Tools", "Noise",
    }
)  # fmt: skip
_NO_EVENTS = frozenset({Category.SPEECH, Category.MUSIC, Category.SILENCE})


class Environment(StrEnum):
    INDOOR_SMALL = "indoor_small_room"
    INDOOR_LARGE = "indoor_large_hall"
    INDOOR_PUBLIC = "indoor_public_space"
    OUTDOOR_URBAN = "outdoor_urban"
    OUTDOOR_RURAL = "outdoor_rural"


ENVIRONMENT_CLASSES: Mapping[str, Environment] = {
    "Inside, small room": Environment.INDOOR_SMALL,
    "Inside, large room or hall": Environment.INDOOR_LARGE,
    "Inside, public space": Environment.INDOOR_PUBLIC,
    "Outside, urban or manmade": Environment.OUTDOOR_URBAN,
    "Outside, rural or natural": Environment.OUTDOOR_RURAL,
}
ENVIRONMENT_LABELS: Mapping[Environment, str] = {
    Environment.INDOOR_SMALL: "Intérieur, petite pièce",
    Environment.INDOOR_LARGE: "Intérieur, grande salle",
    Environment.INDOOR_PUBLIC: "Intérieur, lieu public",
    Environment.OUTDOOR_URBAN: "Extérieur, milieu urbain",
    Environment.OUTDOOR_RURAL: "Extérieur, nature",
}

# Recording issues worth flagging (display name → tag), whatever the class category.
ISSUE_CLASSES: Mapping[str, str] = {
    "Wind noise (microphone)": "wind_noise",
    "Mains hum": "hum",
    "Hum": "hum",
    "Distortion": "distortion",
    "Static": "static",
    "White noise": "noise",
    "Pink noise": "noise",
    "Echo": "echo",
    "Reverberation": "reverb",
}
LOW_LEVEL = "low_level"
ISSUE_LABELS: Mapping[str, str] = {
    "wind_noise": "Bruit de vent dans le micro",
    "hum": "Ronflement électrique",
    "distortion": "Distorsion",
    "static": "Grésillements",
    "noise": "Souffle",
    "echo": "Écho",
    "reverb": "Réverbération",
    LOW_LEVEL: "Niveau très faible : classification peu fiable",
}

# Hysteresis (on, off) on the smoothed category score.
THRESHOLDS: Mapping[Category, tuple[float, float]] = {
    Category.SPEECH: (0.50, 0.30),
    Category.MUSIC: (0.40, 0.20),
    Category.NATURE: (0.30, 0.15),
    Category.WIND: (0.25, 0.12),
    Category.WATER: (0.30, 0.15),
    Category.VEHICLES: (0.30, 0.15),
    Category.CROWD: (0.30, 0.15),
    Category.TOOLS: (0.30, 0.15),
    Category.SILENCE: (0.60, 0.40),
    Category.OTHER: (0.35, 0.20),
}
MEDIAN_FRAMES = 3  # 1.44 s: removes one-frame blips (a mouth click read as « Camera »)
DEFAULT_MIN_SEGMENT_S = 0.95  # two frames
MIN_SEGMENT_S: Mapping[Category, float] = {Category.SPEECH: 0.47}  # speech scores are reliable
MERGE_GAP_S = 1.0
RMS_SILENCE_DBFS = -60.0  # silence also when the frame RMS is below this
LOW_LEVEL_DBFS = -45.0  # YAMNet reads quiet speech or ambience as Silence below about this
LOW_LEVEL_MIN_FRACTION = 0.5
EVENT_MIN_SCORE = 0.5
EVENT_MIN_S = 0.01
MAX_EVENTS = 12
LABEL_FLOOR = 0.10
LABEL_CANDIDATES = 20
SHOT_LABELS = 3
FILE_LABELS = 5
INSTRUMENT_MIN_SCORE = 0.2
INSTRUMENT_MIN_S = 0.95
INSTRUMENT_COVER = 0.8  # a family is dropped when one of its members is heard this long
ENVIRONMENT_MIN_SCORE = 0.10
ISSUE_MIN_SCORE = 0.30
ISSUE_MIN_FRACTION = 0.05
CURVE_HZ = 1  # cells are at most 0.72 s wide, so a cell touches at most two 1 s bins
_EPS = 1e-9


# ---------------------------------------------------------------- category table
@dataclass(frozen=True, slots=True, eq=False)
class Rollup:
    """YAMNet classes in model output order, with their category and ontology ancestors."""

    names: tuple[str, ...]
    mids: tuple[str, ...]
    categories: tuple[Category, ...]
    context: tuple[bool, ...]
    ancestors: tuple[frozenset[str], ...]  # strict ancestors (mids), every parent followed
    index: Mapping[str, int] = field(repr=False)  # display name → class index
    by_mid: Mapping[str, int] = field(repr=False)  # AudioSet mid → class index
    members: Mapping[Category, IndexArray] = field(repr=False)  # context classes excluded
    instruments: IndexArray = field(repr=False)  # specific instruments, music category only
    event_classes: IndexArray = field(repr=False)  # specific non-speech/music/silence classes

    def __len__(self) -> int:
        return len(self.names)

    def index_of_mid(self, mid: str) -> int | None:
        return self.by_mid.get(mid)

    def is_ancestor(self, candidate: int, of: int) -> bool:
        """Whether class ``candidate`` is an ontology ancestor of class ``of``."""
        return self.mids[candidate] in self.ancestors[of]


def build_rollup(class_map_csv: str, ontology_json: str) -> Rollup:
    """Category table from the YAMNet class map (CSV text) and the AudioSet ontology (JSON)."""
    classes = _parse_class_map(class_map_csv)
    parents = _parse_parents(ontology_json)
    mids = tuple(mid for mid, _ in classes)
    names = tuple(name for _, name in classes)
    ancestors = tuple(frozenset(_ancestors(mid, parents)) for mid in mids)
    categories: list[Category] = []
    context: list[bool] = []
    for mid, name, lineage in zip(mids, names, ancestors, strict=True):
        family = lineage | {mid}
        context.append(bool(family & CONTEXT_ANCHORS) and name not in CONTEXT_EXCEPT)
        categories.append(OVERRIDES[name] if name in OVERRIDES else _first_rule(family))
    index: dict[str, int] = {}
    for i, name in enumerate(names):
        index.setdefault(name, i)
    members = {
        category: _indices(i for i, c in enumerate(categories) if c is category and not context[i])
        for category in CATEGORIES
    }
    instruments = _indices(
        i
        for i, lineage in enumerate(ancestors)
        if MUSICAL_INSTRUMENT in lineage and categories[i] is Category.MUSIC and not context[i]
    )
    event_classes = _indices(
        i
        for i, name in enumerate(names)
        if not context[i] and name not in GENERIC and categories[i] not in _NO_EVENTS
    )
    return Rollup(
        names=names,
        mids=mids,
        categories=tuple(categories),
        context=tuple(context),
        ancestors=ancestors,
        index=index,
        by_mid={mid: i for i, mid in reversed(list(enumerate(mids)))},
        members=members,
        instruments=instruments,
        event_classes=event_classes,
    )


def _parse_class_map(text: str) -> list[tuple[str, str]]:
    rows: dict[int, tuple[str, str]] = {}
    try:
        for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
            rows[int(row["index"])] = (row["mid"].strip(), row["display_name"].strip())
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"Table des classes YAMNet illisible : {exc}") from exc
    if not rows or sorted(rows) != list(range(len(rows))):
        raise ValueError("Table des classes YAMNet incomplète : index absents ou non contigus.")
    return [rows[i] for i in range(len(rows))]


def _parse_parents(text: str) -> dict[str, set[str]]:
    parents: dict[str, set[str]] = {}
    try:
        nodes = json.loads(text)
        for node in nodes:
            for child in node.get("child_ids") or ():
                parents.setdefault(str(child), set()).add(str(node["id"]))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Ontologie AudioSet illisible : {exc}") from exc
    return parents


def _ancestors(mid: str, parents: Mapping[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = [mid]
    while stack:
        for parent in parents.get(stack.pop(), ()):
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    seen.discard(mid)  # a cycle in the file must not make a class its own ancestor
    return seen


def _first_rule(family: frozenset[str]) -> Category:
    for category, anchors in RULES:
        if family & anchors:
            return category
    return Category.OTHER


def _indices(values: Iterable[int]) -> IndexArray:
    return np.fromiter(values, dtype=np.intp)


# ---------------------------------------------------------------- signal helpers
def frame_cells(frames: int, duration_s: float) -> tuple[FloatArray, FloatArray]:
    """Timeline cell (start, end) of each frame: 0.48 s centred on the patch centre, clamped."""
    duration = max(0.0, float(duration_s))
    centre = HOP_S * np.arange(frames, dtype=np.float64) + PATCH_S / 2
    start = np.clip(centre - HOP_S / 2, 0.0, duration)
    end = np.clip(centre + HOP_S / 2, 0.0, duration)
    if frames:
        start[0] = 0.0
        end[-1] = duration
    return start, end


def median_filter(values: npt.ArrayLike, frames: int = MEDIAN_FRAMES) -> FloatArray:
    """Centred running median along axis 0 with edge replication (numpy only, no scipy)."""
    x = np.asarray(values, dtype=np.float64)
    if frames <= 1 or len(x) == 0:
        return x.copy()
    pad = frames // 2
    padded = np.concatenate([np.repeat(x[:1], pad, axis=0), x, np.repeat(x[-1:], pad, axis=0)])
    return np.asarray(np.median(sliding_window_view(padded, frames, axis=0), axis=-1))


def hysteresis(values: npt.ArrayLike, on: float, off: float) -> BoolArray:
    """On when a value reaches ``on``, off again only when it falls below ``off``."""
    series = np.asarray(values, dtype=np.float64).reshape(-1)
    out = np.zeros(len(series), dtype=bool)
    state = False
    for i, value in enumerate(series.tolist()):
        state = value >= (off if state else on)
        out[i] = state
    return out


def _runs(mask: BoolArray) -> list[tuple[int, int]]:
    edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts = np.flatnonzero(edges == 1).tolist()
    ends = np.flatnonzero(edges == -1).tolist()
    return list(zip(starts, ends, strict=True))


def _segments(
    mask: BoolArray, start: FloatArray, end: FloatArray, *, min_s: float
) -> list[tuple[int, int]]:
    """Frame runs [i, j) of ``mask``: gaps up to MERGE_GAP_S merged, then short runs dropped."""
    merged: list[list[int]] = []
    for i, j in _runs(mask):
        if merged and start[i] - end[merged[-1][1] - 1] <= MERGE_GAP_S + _EPS:
            merged[-1][1] = j
        else:
            merged.append([i, j])
    return [(i, j) for i, j in merged if end[j - 1] - start[i] >= min_s - _EPS]


# ---------------------------------------------------------------- results
@dataclass(frozen=True, slots=True)
class AudioEvent:
    """A short salient sound: a specific class whose raw score reaches EVENT_MIN_SCORE."""

    start_s: float
    end_s: float
    label: str
    score: float  # maximum over the event
    score_mean: float
    category: Category
    class_index: int


@dataclass(frozen=True, slots=True)
class AudioScene:
    """What is heard in a file (every share is a fraction of ``duration_s``)."""

    duration_s: float
    frames: int
    presence: dict[Category, float]  # multi-label: shares may add up to more than 1
    dominant: dict[Category, float]  # one category per instant: shares add up to 1
    speech_s: float
    music_s: float
    segments: list[tuple[float, float, Category]]
    events: list[AudioEvent]
    top_labels: list[tuple[str, float]]
    instruments: list[tuple[str, float, float]]  # (label, seconds heard, maximum score)
    curves: dict[Category, list[int]]  # smoothed category scores, 0–100, one value per second
    environment: Environment | None
    issues: dict[str, float]  # issue tag → share of the duration

    @property
    def main_category(self) -> Category | None:
        return max(self.dominant, key=self.dominant.__getitem__) if self.dominant else None

    @classmethod
    def empty(cls, duration_s: float, frames: int = 0) -> AudioScene:
        return cls(
            duration_s=round(max(0.0, duration_s), 3),
            frames=frames,
            presence={},
            dominant={},
            speech_s=0.0,
            music_s=0.0,
            segments=[],
            events=[],
            top_labels=[],
            instruments=[],
            curves={},
            environment=None,
            issues={},
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form (plain str keys, lists)."""
        return {
            "version": AUDIO_EVENTS_VERSION,
            "hop_s": HOP_S,
            "patch_s": PATCH_S,
            "duration_s": self.duration_s,
            "frames": self.frames,
            "presence": {c.value: v for c, v in self.presence.items()},
            "dominant": {c.value: v for c, v in self.dominant.items()},
            "speech_s": self.speech_s,
            "music_s": self.music_s,
            "segments": [[a, b, c.value] for a, b, c in self.segments],
            "events": [
                {
                    "start_s": e.start_s,
                    "end_s": e.end_s,
                    "label": e.label,
                    "score": e.score,
                    "score_mean": e.score_mean,
                    "category": e.category.value,
                    "class_index": e.class_index,
                }
                for e in self.events
            ],
            "top_labels": [[label, score] for label, score in self.top_labels],
            "instruments": [list(item) for item in self.instruments],
            "curves": {"hz": CURVE_HZ, "series": {c.value: v for c, v in self.curves.items()}},
            "environment": self.environment.value if self.environment else None,
            "issues": dict(self.issues),
        }


# ---------------------------------------------------------------- analysis
def analyze(
    scores: ScoreMatrix,
    rollup: Rollup,
    *,
    duration_s: float,
    rms_db: npt.ArrayLike | None = None,
) -> AudioScene:
    """Summarise a [frames, classes] YAMNet score matrix over a file of ``duration_s``.

    ``rms_db`` (one level per frame, same 0.96 s patch, dBFS) adds the level-based silence
    rule and the low-level flag; frames it does not cover count as loud.
    """
    matrix = _check_scores(scores, rollup)
    frames = len(matrix)
    duration = float(duration_s)
    if frames == 0 or not duration > 0:
        return AudioScene.empty(duration, frames)
    start, end = frame_cells(frames, duration)
    width = end - start
    smooth = median_filter(_category_scores(matrix, rollup))
    levels = _frame_levels(rms_db, frames)
    silent = levels < RMS_SILENCE_DBFS if levels is not None else np.zeros(frames, dtype=bool)

    masks = np.zeros((frames, len(CATEGORIES)), dtype=bool)
    segments: list[tuple[float, float, Category]] = []
    for k, category in enumerate(CATEGORIES):
        on, off = THRESHOLDS[category]
        state = hysteresis(smooth[:, k], on, off)
        if category is Category.SILENCE:
            state |= silent
        min_s = MIN_SEGMENT_S.get(category, DEFAULT_MIN_SEGMENT_S)
        for i, j in _segments(state, start, end, min_s=min_s):
            masks[i:j, k] = True
            segments.append((round(float(start[i]), 2), round(float(end[j - 1]), 2), category))
    segments.sort(key=lambda s: (s[0], CATEGORIES.index(s[2])))
    seconds = {c: float(width[masks[:, k]].sum()) for k, c in enumerate(CATEGORIES)}

    # Level-based silence also counts in the dominance and the curves.
    level = smooth.copy()
    k_silence = CATEGORIES.index(Category.SILENCE)
    level[:, k_silence] = np.where(
        silent, np.maximum(level[:, k_silence], 1.0), level[:, k_silence]
    )
    on_values = np.array([THRESHOLDS[c][0] for c in CATEGORIES])
    ratio = np.where(masks, level / on_values, -np.inf)
    winner = np.where(masks.any(axis=1), ratio.argmax(axis=1), CATEGORIES.index(Category.OTHER))
    dominant = {c: float(width[winner == k].sum()) / duration for k, c in enumerate(CATEGORIES)}

    return AudioScene(
        duration_s=round(duration, 3),
        frames=frames,
        presence=_shares({c: s / duration for c, s in seconds.items()}),
        dominant=_shares(dominant),
        speech_s=round(seconds[Category.SPEECH], 2),
        music_s=round(seconds[Category.MUSIC], 2),
        segments=segments,
        events=_events(matrix, rollup, start, end),
        top_labels=_top_labels(matrix, rollup, 0.0, duration, FILE_LABELS),
        instruments=_instruments(matrix, rollup, width),
        curves=_curves(level, start, end, duration),
        environment=_environment(matrix, rollup, width, duration),
        issues=_issues(matrix, rollup, width, duration, levels),
    )


def shot_labels(
    scores: ScoreMatrix, rollup: Rollup, spans: Sequence[tuple[float, float]]
) -> list[list[tuple[str, float]]]:
    """Top labels of each (start_s, end_s) span: overlap-weighted mean of the class scores."""
    matrix = _check_scores(scores, rollup)
    return [_top_labels(matrix, rollup, a, b, SHOT_LABELS) for a, b in spans]


def _check_scores(scores: ScoreMatrix, rollup: Rollup) -> npt.NDArray[np.float32]:
    matrix = np.asarray(scores, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != len(rollup):
        raise ValueError(
            f"Scores YAMNet inattendus : forme {matrix.shape}, {len(rollup)} classes attendues."
        )
    return np.nan_to_num(matrix, nan=0.0, posinf=1.0, neginf=0.0)


def _category_scores(matrix: npt.NDArray[np.float32], rollup: Rollup) -> FloatArray:
    """[T, 521] → [T, C]: max over member classes (sigmoid outputs are not exclusive)."""
    out = np.zeros((len(matrix), len(CATEGORIES)), dtype=np.float64)
    for k, category in enumerate(CATEGORIES):
        members = rollup.members[category]
        if len(members):
            out[:, k] = matrix[:, members].max(axis=1)
    return out


def _frame_levels(rms_db: npt.ArrayLike | None, frames: int) -> FloatArray | None:
    if rms_db is None:
        return None
    levels = np.nan_to_num(np.asarray(rms_db, dtype=np.float64).reshape(-1), nan=0.0)
    if len(levels) < frames:  # unknown level: count it as loud, never as silence
        levels = np.concatenate([levels, np.zeros(frames - len(levels))])
    return median_filter(levels[:frames])


def _shares(values: Mapping[Category, float]) -> dict[Category, float]:
    kept = {c: round(min(1.0, v), 4) for c, v in values.items() if v > _EPS}
    return dict(sorted(kept.items(), key=lambda item: -item[1]))


def _events(
    matrix: npt.NDArray[np.float32], rollup: Rollup, start: FloatArray, end: FloatArray
) -> list[AudioEvent]:
    candidates = rollup.event_classes
    if not len(candidates):
        return []
    hot = matrix[:, candidates] >= EVENT_MIN_SCORE
    found: list[AudioEvent] = []
    for column in np.flatnonzero(hot.any(axis=0)).tolist():
        index = int(candidates[column])
        for i, j in _segments(hot[:, column], start, end, min_s=EVENT_MIN_S):
            run = matrix[i:j, index]
            found.append(
                AudioEvent(
                    start_s=round(float(start[i]), 2),
                    end_s=round(float(end[j - 1]), 2),
                    label=rollup.names[index],
                    score=round(float(run.max()), 3),
                    score_mean=round(float(run.mean()), 3),
                    category=rollup.categories[index],
                    class_index=index,
                )
            )
    # An event whose class is an ancestor of an overlapping event's class says less (Rail
    # transport and Train over Train horn): keep the specific one.
    found = [
        e
        for e in found
        if not any(
            f.start_s < e.end_s
            and e.start_s < f.end_s
            and rollup.is_ancestor(e.class_index, of=f.class_index)
            for f in found
        )
    ]
    found.sort(key=lambda e: (-e.score, e.start_s))
    return sorted(found[:MAX_EVENTS], key=lambda e: (e.start_s, e.label))


def _top_labels(
    matrix: npt.NDArray[np.float32], rollup: Rollup, a: float, b: float, k: int
) -> list[tuple[str, float]]:
    """Overlap-weighted mean over [a, b], context excluded, floor, ontology-ancestor dedupe."""
    patch_start = HOP_S * np.arange(len(matrix), dtype=np.float64)
    weight = np.clip(np.minimum(patch_start + PATCH_S, b) - np.maximum(patch_start, a), 0, None)
    total = float(weight.sum())
    if total <= 0:
        return []
    mean = (weight @ matrix) / total
    mean[np.asarray(rollup.context, dtype=bool)] = 0.0
    order = np.argsort(-mean, kind="stable")[:LABEL_CANDIDATES].tolist()
    chosen: list[int] = []
    for i in order:
        if mean[i] < LABEL_FLOOR:
            break
        if any(rollup.is_ancestor(i, of=j) for j in chosen):
            continue  # a more specific label is already listed
        # drop listed ancestors of this label (Wind instrument before Flute), except the top one
        chosen = [j for n, j in enumerate(chosen) if n == 0 or not rollup.is_ancestor(j, of=i)]
        chosen.append(i)
        if len(chosen) == k:
            break
    return [(rollup.names[i], round(float(mean[i]), 2)) for i in chosen]


def _instruments(
    matrix: npt.NDArray[np.float32], rollup: Rollup, width: FloatArray
) -> list[tuple[str, float, float]]:
    heard: dict[int, tuple[float, float]] = {}
    for index in rollup.instruments.tolist():
        column = matrix[:, index]
        seconds = float(width[column >= INSTRUMENT_MIN_SCORE].sum())
        if seconds >= INSTRUMENT_MIN_S - _EPS:
            heard[index] = (seconds, float(column.max()))
    # A family (Wind instrument) adds nothing when a member (Flute) is heard about as long.
    kept = [
        i
        for i in heard
        if not any(
            j != i and rollup.is_ancestor(i, of=j) and heard[j][0] >= INSTRUMENT_COVER * heard[i][0]
            for j in heard
        )
    ]
    kept.sort(key=lambda i: (-heard[i][0], -heard[i][1], rollup.names[i]))
    return [(rollup.names[i], round(heard[i][0], 2), round(heard[i][1], 2)) for i in kept]


def _curves(
    level: FloatArray, start: FloatArray, end: FloatArray, duration: float
) -> dict[Category, list[int]]:
    bins = max(1, math.ceil(duration * CURVE_HZ - _EPS))
    out = np.zeros((bins, level.shape[1]), dtype=np.float64)
    valid = end > start
    first = np.clip(np.floor(start * CURVE_HZ).astype(np.intp), 0, bins - 1)
    last = np.clip(np.ceil(end * CURVE_HZ - _EPS).astype(np.intp) - 1, 0, bins - 1)
    for which in (first, last):
        np.maximum.at(out, which[valid], level[valid])
    values = np.rint(np.clip(out, 0.0, 1.0) * 100).astype(int)
    return {c: values[:, k].tolist() for k, c in enumerate(CATEGORIES)}


def _environment(
    matrix: npt.NDArray[np.float32], rollup: Rollup, width: FloatArray, duration: float
) -> Environment | None:
    means = {
        environment: float(width @ matrix[:, rollup.index[name]]) / duration
        for name, environment in ENVIRONMENT_CLASSES.items()
        if name in rollup.index
    }
    if not means:
        return None
    best = max(means, key=means.__getitem__)
    return best if means[best] >= ENVIRONMENT_MIN_SCORE else None


def _issues(
    matrix: npt.NDArray[np.float32],
    rollup: Rollup,
    width: FloatArray,
    duration: float,
    levels: FloatArray | None,
) -> dict[str, float]:
    issues: dict[str, float] = {}
    for name, tag in ISSUE_CLASSES.items():
        if name not in rollup.index:
            continue
        flagged = median_filter(matrix[:, rollup.index[name]]) >= ISSUE_MIN_SCORE
        share = float(width[flagged].sum()) / duration
        if share >= ISSUE_MIN_FRACTION:
            issues[tag] = max(issues.get(tag, 0.0), round(min(1.0, share), 4))
    if levels is not None:
        quiet = float(width[levels < LOW_LEVEL_DBFS].sum()) / duration
        if quiet >= LOW_LEVEL_MIN_FRACTION:
            issues[LOW_LEVEL] = round(min(1.0, quiet), 4)
    return issues
