"""Safe in and out points for any range an editor asks about (pure functions).

Claude reads a range from a DaVinci Resolve timeline (or chooses one) and asks where to cut; the
answer must not depend on the model doing arithmetic. Two rules, in this order:

1. **Shots**: an edge less than 0.5 s from a cut goes to it (the block rule): just after a cut,
   back to it (the whole shot opens); just before one, forward to it (no flash of a few frames of
   the ending shot). The same holds for the out point, the other way round.
2. **Words**: an edge inside a word moves to the best pause (``domain.editing.cut_edges``, the
   rules of the synthesis' in/out points): the picture stays within the shots of the range, and
   when a sentence crosses an edge the sound alone starts earlier (J-cut) or ends later (L-cut),
   up to 3 s beyond.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from vfe_vision.domain.editing import EDGE_SNAP_S, Clip, cut_edges
from vfe_vision.domain.synthesis_input import Shot, Video

MIN_RANGE_S = 0.1  # a snap never leaves less than this between in and out


@dataclass(frozen=True, slots=True)
class CutPoints:
    """Where to cut a range, in seconds of the source file."""

    requested_in: float
    requested_out: float
    clip: Clip  # picture in/out; sound in/out when they differ (J-cut, L-cut); word notes
    shots: tuple[int, ...]  # 0-based indices of the shots the picture shows
    cuts: tuple[float, ...]  # cuts between two shots inside the picture range
    notes: tuple[str, ...]  # French, for the editor: shot snaps first, then the words


def safe_cut(video: Video, start: float, end: float) -> CutPoints:
    """Safe in/out points for [start, end] (seconds of the source file, clamped to the video)."""
    if video.duration > 0:
        start, end = max(0.0, start), min(end, video.duration)
    if end - start < MIN_RANGE_S:
        raise ValueError("intervalle vide : la fin doit suivre le début")
    requested = (start, end)
    shots = sorted(video.shots, key=lambda s: s.start)
    notes: list[str] = []
    lo, hi = 0.0, max(video.duration, end)
    if shots:
        start, end = _snap_to_shots(shots, start, end, notes)
        lo, hi = _shot_at(shots, start).start, _shot_at(shots, end, closing=True).end
    clip = cut_edges(video, lo, hi, start, end)
    inside = [s for s in shots if s.end > clip.picture_in and s.start < clip.picture_out]
    cuts = tuple(s.start for s in inside if clip.picture_in < s.start < clip.picture_out)
    return CutPoints(
        requested_in=requested[0],
        requested_out=requested[1],
        clip=clip,
        shots=tuple(s.idx for s in inside),
        cuts=cuts,
        notes=(*notes, *clip.notes),
    )


def _snap_to_shots(
    shots: Sequence[Shot], start: float, end: float, notes: list[str]
) -> tuple[float, float]:
    first = _shot_at(shots, start)
    if 0 < start - first.start <= EDGE_SNAP_S:
        start = first.start
        notes.append(f"entrée calée sur le début du plan {first.idx + 1}")
    elif 0 < first.end - start <= EDGE_SNAP_S and first.end < end - MIN_RANGE_S:
        start = first.end
        notes.append(f"entrée calée sur le début du plan {first.idx + 2} (pas d'éclair du plan "
                     f"{first.idx + 1})")  # fmt: skip
    last = _shot_at(shots, end, closing=True)
    if 0 < last.end - end <= EDGE_SNAP_S:
        end = last.end
        notes.append(f"sortie calée sur la fin du plan {last.idx + 1}")
    elif 0 < end - last.start <= EDGE_SNAP_S and last.start > start + MIN_RANGE_S:
        end = last.start
        notes.append(f"sortie calée sur la fin du plan {last.idx} (pas d'éclair du plan "
                     f"{last.idx + 1})")  # fmt: skip
    return start, end


def _shot_at(shots: Sequence[Shot], t: float, *, closing: bool = False) -> Shot:
    """The shot shown at ``t`` (``closing``: the shot a range ending at ``t`` ends in, so a cut
    at ``t`` belongs to the shot before it)."""
    for shot in shots:
        if (shot.start < t <= shot.end) if closing else (shot.start <= t < shot.end):
            return shot
    return shots[-1] if t >= shots[-1].start else shots[0]
