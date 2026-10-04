"""Blocks: the pieces of a video the vision model and the synthesis look at (pure functions).

A shot of at most 30 s is one block. A longer shot is cut into pieces of about 20 s (4 at most):
each cut goes to the keyframe nearest its ideal place, then out of speech into the nearest pause,
so that neither the images nor the words of a block straddle two blocks.

The synthesis cuts its blocks the same way, without the cap of 4 (a talk filmed in one 263 s
shot needs its 13 blocks to be chaptered), and merges short shots on very long lists.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence

from vfe_vision.domain.synthesis_input import Block, Frame, Video, speech_seconds

LONG_SHOT_S = 30.0  # a shot up to this long is one block
PART_S = 20.0  # the length a longer shot is cut into
MAX_PARTS = 4
MIN_PART_S = 4.0  # no cut closer than this to another one or to the shot's edges
KEYFRAME_REACH = 0.25  # of the ideal part length: how far a cut moves to meet a keyframe
PAUSE_REACH_S = 3.0  # how far a cut moves to leave speech
WORD_GAP_S = 0.15  # words closer than this are one stretch of speech
# Past this many blocks, the synthesis merges the shots shorter than MERGE_BELOW_S into their
# neighbours (blocks up to LONG_SHOT_S): a 61-min composite went from 377 blocks to 284.
MERGE_ABOVE_BLOCKS = 150
MERGE_BELOW_S = 4.0
_EDGE_S = 1e-6  # float noise: a keyframe stored at a block's first image belongs to it

_Piece = tuple[float, float, tuple[int, ...]]  # start, end, 0-based shot indices


def speech_spans(words: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """Stretches of continuous speech, from word (or segment) times."""
    spans: list[tuple[float, float]] = []
    for start, end in sorted(words):
        if spans and start - spans[-1][1] < WORD_GAP_S:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


def split_shot(
    start: float,
    end: float,
    *,
    keyframe_times: Sequence[float] = (),
    speech: Sequence[tuple[float, float]] = (),
    max_parts: int | None = MAX_PARTS,
) -> list[tuple[float, float]]:
    """The blocks of one shot, in order, covering it exactly (``max_parts=None``: no cap)."""
    duration = end - start
    if duration <= LONG_SHOT_S:
        return [(start, end)]
    count = max(2, round(duration / PART_S))
    if max_parts is not None:
        count = min(max_parts, count)
    span = duration / count
    cuts: list[float] = []
    previous = start
    for index in range(1, count):
        low, high = previous + MIN_PART_S, end - MIN_PART_S * (count - index)
        ideal = start + index * span
        near = [t for t in keyframe_times if abs(t - ideal) <= KEYFRAME_REACH * span]
        cut = min(near, key=lambda t: abs(t - ideal)) if near else ideal
        cut = _out_of_speech(cut, speech, low, high)
        cut = min(max(cut, low), high)
        cuts.append(cut)
        previous = cut
    return list(itertools.pairwise([start, *cuts, end]))


def _out_of_speech(
    t: float, speech: Sequence[tuple[float, float]], low: float, high: float
) -> float:
    inside = next((s for s in speech if s[0] < t < s[1]), None)
    if inside is None:
        return t
    edges = [e for e in inside if low <= e <= high and abs(e - t) <= PAUSE_REACH_S]
    return min(edges, key=lambda e: abs(e - t)) if edges else t


def synthesis_blocks(video: Video) -> list[Block]:
    """The blocks the synthesis talks about, numbered from 1, in time order.

    One block per shot, a shot longer than LONG_SHOT_S cut as above with no cap; past
    MERGE_ABOVE_BLOCKS pieces, the short ones are merged. A block owns the keyframes shown
    during it; a block without any borrows the one in force at its start (a keyframe stands for
    [t, next keyframe)). No shot, no block.
    """
    speech = speech_spans((w.start, w.end) for w in video.words)
    keyframes: dict[int, list[float]] = {}
    for frame in video.frames:
        if frame.shot is not None:
            keyframes.setdefault(frame.shot, []).append(frame.t)
    pieces: list[_Piece] = [
        (a, b, (shot.idx,))
        for shot in sorted(video.shots, key=lambda s: s.start)
        for a, b in split_shot(
            shot.start,
            shot.end,
            keyframe_times=keyframes.get(shot.idx, ()),
            speech=speech,
            max_parts=None,
        )
    ]
    if len(pieces) > MERGE_ABOVE_BLOCKS:
        pieces = _merge_short(pieces)
    heard = {shot.idx: shot.heard for shot in video.shots}
    frames = sorted(video.frames, key=lambda f: f.t)
    return [
        Block(
            no=no,
            start=a,
            end=b,
            shots=shots,
            frames=_frames_of(frames, a, b),
            speech_s=speech_seconds(video.segments, a, b),
            heard=tuple(dict.fromkeys(label for s in shots for label in heard[s])),
        )
        for no, (a, b, shots) in enumerate(pieces, 1)
    ]


def _merge_short(pieces: Sequence[_Piece]) -> list[_Piece]:
    """A piece joins the block before it when either is shorter than MERGE_BELOW_S and the
    merged block stays within LONG_SHOT_S (flashes and cutaways of a fast edit)."""
    merged: list[_Piece] = []
    for start, end, shots in pieces:
        if merged:
            first, last, before = merged[-1]
            short = end - start < MERGE_BELOW_S or last - first < MERGE_BELOW_S
            if short and end - first <= LONG_SHOT_S:
                merged[-1] = (first, end, tuple(dict.fromkeys((*before, *shots))))
                continue
        merged.append((start, end, shots))
    return merged


def _frames_of(frames: Sequence[Frame], start: float, end: float) -> tuple[Frame, ...]:
    """The keyframes (sorted by time) shown in [start, end), or the one in force at start."""
    inside = tuple(f for f in frames if start - _EDGE_S <= f.t < end - _EDGE_S)
    if inside:
        return inside
    return tuple(f for f in frames if f.t <= start)[-1:]
