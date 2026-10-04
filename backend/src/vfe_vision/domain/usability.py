"""How usable a shot or a block is for editing: a score of 0–100 and up to three short French
reasons (pure functions).

Only measurements and the vision model's defect flags count, never a language model's taste.
Nothing says what "usable" truly is (no ground truth): the score is shown as « indicative », and
its rules are versioned so that stored scores are recomputed when they change. On the 144 shots
of the library: median 88, 14 shots under 50 (black leaders, sub-second flashes, a 0.6 s shaky
shot), 116 at 70 or more.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vfe_vision.domain.shots import Motion
from vfe_vision.domain.synthesis_input import Block, Frame, Shot, Video
from vfe_vision.domain.vision import Lighting, QualityIssue

USABILITY_RULES_VERSION = 1
MAX_REASONS = 3
MIN_REASON_POINTS = 5.0  # a smaller penalty lowers the score without a reason of its own
SOURCE_SHARE = 0.8  # a defect on this share of the described keyframes belongs to the source
SOURCE_MIN_FRAMES = 5  # below this, no defect can be called a property of the source
FLAG_SHARE = 0.34  # a defect flag counts from 34 % of the shot's keyframes (1 in 3 is not enough)

# Deliberate camera moves: their shake costs half.
_INTENTIONAL = frozenset(
    {Motion.PAN_LEFT, Motion.PAN_RIGHT, Motion.TILT_UP, Motion.TILT_DOWN,
     Motion.ZOOM_IN, Motion.ZOOM_OUT}
)  # fmt: skip
# Vision-model defect flags: weight (× 30 points × share of the shot's keyframes) and label.
_FLAGS: dict[str, tuple[float, str]] = {
    QualityIssue.BLUR: (0.6, "flou"),
    QualityIssue.MOTION_BLUR: (0.5, "flou de bougé"),
    QualityIssue.OUT_OF_FOCUS_SUBJECT: (0.6, "sujet flou"),
    QualityIssue.OBSTRUCTION: (0.6, "obstruction"),
    QualityIssue.OVEREXPOSED: (0.5, "surexposé"),
    QualityIssue.UNDEREXPOSED: (0.5, "sous-exposé"),
    QualityIssue.NOISE: (0.2, "bruit"),
    QualityIssue.TILTED_HORIZON: (0.2, "horizon penché"),
}
_OTHER_FLAG_WEIGHT = 0.3

_Penalty = tuple[float, str]  # points, label


@dataclass(frozen=True, slots=True)
class Usability:
    score: int  # 0–100
    reasons: tuple[str, ...]  # short French labels, most important first, at most 3


@dataclass(frozen=True, slots=True)
class _VideoFacts:
    """What each shot is compared with, and where its keyframes are: computed once per video."""

    source_wide: frozenset[str]
    median_sharpness: float | None  # None: too few keyframes to compare
    shots: Mapping[int, Shot]
    frames: Mapping[int, Sequence[Frame]]  # by shot index, in keyframe order


class _RecentVideos:
    """The facts of the last videos scored, each recognised by identity.

    Callers score a video shot after shot, then block after block: recomputing its facts on
    each call made a pass quadratic (13 s for the 2,000 shots and blocks of a 5-hour video).
    Loaded facts are never modified, so a hit is always right. Two slots keep memory bounded
    while the stage and a request score two videos at once; a lost race only recomputes.
    """

    __slots__ = ("_entries",)
    SLOTS = 2

    def __init__(self) -> None:
        self._entries: tuple[tuple[Video, _VideoFacts], ...] = ()

    def facts(self, video: Video) -> _VideoFacts:
        entries = self._entries
        for cached, facts in entries:
            if cached is video:
                return facts
        facts = _facts(video)
        self._entries = ((video, facts), *entries[: self.SLOTS - 1])
        return facts


_RECENT = _RecentVideos()


def source_issues(frames: Sequence[Frame], share: float = SOURCE_SHARE) -> frozenset[str]:
    """Defects the vision model sees on nearly every keyframe: a property of the source (the
    grain of an archive film), reported once for the video and not held against each shot."""
    described = [frame.data for frame in frames if frame.data]
    if len(described) < SOURCE_MIN_FRAMES:
        return frozenset()
    counts = Counter(issue for data in described for issue in _issues(data))
    return frozenset(issue for issue, n in counts.items() if n / len(described) >= share)


def shot_usability(video: Video, shot: Shot) -> Usability:
    """Technical usability of a shot: 100 = long enough, stable, clean and well exposed.

    Two guards keep intent from passing for a defect: a frozen picture only costs in a shot
    that moves, and a dark one only when the vision model finds it dark too (or with black
    frames). Without them a screen recording scored 53.
    """
    facts = _RECENT.facts(video)
    return _usability(shot, facts.frames.get(shot.idx, ()), facts)


def usability_by_shot(video: Video) -> dict[int, Usability]:
    """Every shot's usability, by shot index."""
    facts = _RECENT.facts(video)
    return {s.idx: _usability(s, facts.frames.get(s.idx, ()), facts) for s in video.shots}


def block_usability(
    video: Video, block: Block, *, by_shot: Mapping[int, Usability] | None = None
) -> Usability:
    """A block's usability: its shot's, or the duration-weighted mean of its shots (short shots
    merged on very long videos) with their most frequent reasons.

    ``by_shot`` (from ``usability_by_shot``) spares recomputing the shots block after block.
    """
    facts = _RECENT.facts(video)
    known = [facts.shots[i] for i in dict.fromkeys(block.shots) if i in facts.shots]
    if not known:
        # No measured shot under it: only its length and its keyframes can be judged.
        stand_in = Shot(-1, block.start, block.end, Motion.STATIC.value, 1.0, "start", {})
        return _usability(stand_in, block.frames, facts)
    given = by_shot or {}
    scores = [
        given[s.idx] if s.idx in given else _usability(s, facts.frames.get(s.idx, ()), facts)
        for s in known
    ]
    if len(scores) == 1:
        return scores[0]
    weights = [max(0.0, shot.duration) for shot in known]  # a corrupt shot never goes over 100
    total = sum(weights)
    if total > 0:
        mean = sum(u.score * w for u, w in zip(scores, weights, strict=True)) / total
    else:
        mean = statistics.fmean(u.score for u in scores)
    reasons = Counter(reason for u in scores for reason in u.reasons)
    return Usability(round(mean), tuple(r for r, _ in reasons.most_common(MAX_REASONS)))


# ---------------------------------------------------------------- scoring
def _usability(shot: Shot, frames: Sequence[Frame], facts: _VideoFacts) -> Usability:
    penalties = [
        *_length_and_camera(shot),
        *_picture(shot, frames),
        *_flags(frames, facts.source_wide),
        *_sharpness(frames, facts.median_sharpness),
    ]
    penalties.sort(key=lambda p: -p[0])
    score = max(0, round(100 - sum(points for points, _ in penalties)))
    reasons: list[str] = []
    for points, label in penalties:
        if points >= MIN_REASON_POINTS and label not in reasons:
            reasons.append(label)
    return Usability(score, tuple(reasons[:MAX_REASONS]))


def _length_and_camera(shot: Shot) -> list[_Penalty]:
    penalties: list[_Penalty] = []
    # Under 1 s a shot is a flash; under 2 s it is hard to use.
    if shot.duration < 1.0:
        penalties.append((35.0, "très court"))
    elif shot.duration < 2.0:
        penalties.append((15.0, "court"))
    shake = max(0.0, 0.5 - shot.stability) / 0.5  # 0 from a stability of 0.5 up
    if shake > 0:
        factor = 0.5 if shot.motion in _INTENTIONAL else 1.0
        penalties.append((25.0 * shake * factor, "instable"))
    return penalties


def _picture(shot: Shot, frames: Sequence[Frame]) -> list[_Penalty]:
    penalties: list[_Penalty] = []
    black = _metric(shot.metrics, "black_ratio") or 0.0
    if black > 0.3:
        penalties.append((60.0 * min(1.0, black), "noir"))
    # A frozen picture is a glitch in a moving shot; in a static one (tripod, screen recording)
    # it is just a still scene.
    frozen = _metric(shot.metrics, "frozen_ratio") or 0.0
    if frozen > 0.3 and shot.motion != Motion.STATIC:
        penalties.append((30.0 * min(1.0, frozen), "image figée"))
    # Dark only when the vision model finds it dark too, or with black frames: a dark screen
    # theme or a blue-hour shot is intentional.
    luma = _metric(shot.metrics, "luma")
    dark_flags = sum(1 for frame in frames if frame.data and _looks_dark(frame.data))
    if luma is not None and luma < 0.12 and (dark_flags * 3 >= max(1, len(frames)) or black > 0.3):
        penalties.append((20.0, "sombre"))
    elif luma is not None and luma > 0.85:
        penalties.append((15.0, "surexposé"))
    clipped = [_metric(f.metrics, "clipped_highlights") or 0.0 for f in frames if f.metrics]
    if clipped and statistics.fmean(clipped) > 0.05:
        penalties.append((10.0, "hautes lumières brûlées"))
    return penalties


def _flags(frames: Sequence[Frame], source_wide: frozenset[str]) -> list[_Penalty]:
    """The vision model's defect flags, weighted by the share of the shot's described keyframes
    that carry them; a defect of the whole source is left out."""
    described = [frame.data for frame in frames if frame.data]
    if not described:
        return []
    counts = Counter(issue for data in described for issue in _issues(data))
    penalties: list[_Penalty] = []
    for issue, n in counts.most_common():
        share = n / len(described)
        if issue in source_wide or share < FLAG_SHARE:
            continue
        weight, label = _FLAGS.get(issue, (_OTHER_FLAG_WEIGHT, issue))
        penalties.append((30.0 * weight * share, label))
    return penalties


def _sharpness(frames: Sequence[Frame], median: float | None) -> list[_Penalty]:
    # Keyframe sharpness depends on the resolution: a shot is only compared with its own video.
    mine = [s for frame in frames if (s := frame.sharpness)]
    if median is not None and median > 0 and mine and max(mine) < 0.4 * median:
        return [(12.0, "moins net que le reste")]
    return []


# ---------------------------------------------------------------- helpers
def _facts(video: Video) -> _VideoFacts:
    sharpness = [s for frame in video.frames if (s := frame.sharpness)]
    median = statistics.median(sharpness) if len(sharpness) >= 4 else None
    frames: defaultdict[int, list[Frame]] = defaultdict(list)
    for frame in video.frames:
        if frame.shot is not None:
            frames[frame.shot].append(frame)
    shots = {shot.idx: shot for shot in video.shots}
    return _VideoFacts(source_issues(video.frames), median, shots, dict(frames))


def _issues(data: Mapping[str, Any]) -> list[str]:
    """The keyframe's defect flags, each once, in their stored order (a stable reason order)."""
    value = data.get("quality_issues")
    return list(dict.fromkeys(str(q) for q in value)) if isinstance(value, list) else []


def _looks_dark(data: Mapping[str, Any]) -> bool:
    return QualityIssue.UNDEREXPOSED in _issues(data) or data.get("lighting") == Lighting.LOW_LIGHT


def _metric(metrics: Mapping[str, Any], name: str) -> float | None:
    value = metrics.get(name)
    return float(value) if isinstance(value, int | float) else None
