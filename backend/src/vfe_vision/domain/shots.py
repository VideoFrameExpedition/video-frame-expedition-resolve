"""Shot segmentation and camera-motion classification from per-frame signals (pure functions).

Inputs come from a low-resolution analysis decode (``analysis_pass``): the HSV content change
between consecutive sampled frames (0–255 scale, as PySceneDetect's ContentDetector) and the
dense optical flow summarised per frame.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class Motion(StrEnum):
    STATIC = "static"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    TILT_UP = "tilt_up"
    TILT_DOWN = "tilt_down"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    HANDHELD = "handheld"
    MOVING = "moving"  # tracking / complex movement


MOTION_FR = {
    "static": "fixe", "pan_left": "panoramique gauche", "pan_right": "panoramique droite",
    "tilt_up": "tilt haut", "tilt_down": "tilt bas", "zoom_in": "zoom avant",
    "zoom_out": "zoom arrière", "handheld": "caméra à l'épaule", "moving": "mouvement",
}  # fmt: skip
MOTION_EN = {
    "static": "static", "pan_left": "pan left", "pan_right": "pan right", "tilt_up": "tilt up",
    "tilt_down": "tilt down", "zoom_in": "zoom in", "zoom_out": "zoom out",
    "handheld": "handheld", "moving": "free movement",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class CutParams:
    hard_threshold: float = 45.0  # a change this large is always a cut
    min_content: float = 18.0  # below this, never a cut
    adaptive_ratio: float = 3.0  # …or this many times the recent average change
    window: int = 6
    min_shot_s: float = 0.6


def detect_cuts(
    times: Sequence[float], deltas: Sequence[float], params: CutParams | None = None
) -> list[float]:
    """Times at which a new shot starts (excluding 0)."""
    p = params or CutParams()
    cuts: list[float] = []
    last_cut = times[0] if times else 0.0
    for i in range(1, len(deltas)):
        delta = deltas[i]
        if delta < p.min_content:
            continue
        recent = deltas[max(1, i - p.window) : i]
        baseline = float(np.mean(recent)) if len(recent) else 0.0
        is_cut = delta >= p.hard_threshold or delta >= p.adaptive_ratio * max(baseline, 1.0)
        if is_cut and times[i] - last_cut >= p.min_shot_s:
            cuts.append(times[i])
            last_cut = times[i]
    return cuts


def build_shots(
    cuts: Sequence[float], duration: float, params: CutParams | None = None
) -> list[tuple[float, float]]:
    """Shot spans; a closing flash or fade shorter than a shot joins the previous shot."""
    min_shot = (params or CutParams()).min_shot_s
    inner = [c for c in cuts if 0 < c < duration]
    while inner and duration - inner[-1] < min_shot:
        inner.pop()
    bounds = [0.0, *inner, duration]
    return [(a, b) for a, b in itertools.pairwise(bounds) if b > a]


@dataclass(frozen=True, slots=True)
class MotionSummary:
    motion: Motion
    score: float  # median flow magnitude (pixels per sampled frame at analysis width)
    stability: float  # 0 = shaky … 1 = locked off


# A weak "soft" label (zoom, complex move, shake) with little flow is a still camera filming a
# moving subject, or noise: 32/35 test shots right against 30/35 with the vision model.
# Applied when the label is read, so stored shots need no new decode.
SOFT_MOTIONS = frozenset({Motion.ZOOM_IN, Motion.ZOOM_OUT, Motion.MOVING, Motion.HANDHELD})
SOFT_MIN_SCORE = 0.5


def shown_motion(motion: str, score: float) -> str:
    """The camera label shown and used (API, MCP, synthesis) for a stored shot."""
    return Motion.STATIC.value if motion in SOFT_MOTIONS and score < SOFT_MIN_SCORE else motion


def classify_motion(
    dx: Sequence[float],
    dy: Sequence[float],
    magnitude: Sequence[float],
    divergence: Sequence[float],
) -> MotionSummary:
    """Summarise a shot's camera movement from per-frame median flow statistics.

    Image content moves opposite to the camera: a camera panning right makes the flow point
    left (negative ``dx``).
    """
    if len(magnitude) == 0:
        return MotionSummary(Motion.STATIC, 0.0, 1.0)
    mag = float(np.median(magnitude))
    mean_dx, mean_dy = float(np.mean(dx)), float(np.mean(dy))
    mean_div = float(np.mean(divergence))
    # Jitter: frame-to-frame variation of the flow vector, relative to its size.
    jitter = float(np.mean(np.hypot(np.diff(dx), np.diff(dy)))) if len(dx) > 1 else 0.0
    stability = float(np.clip(1.0 / (1.0 + 2.0 * jitter), 0.0, 1.0))
    if mag < 0.25:
        return MotionSummary(Motion.STATIC, mag, stability)
    if abs(mean_div) > 0.35 * mag and abs(mean_div) > 0.08:
        return MotionSummary(Motion.ZOOM_IN if mean_div > 0 else Motion.ZOOM_OUT, mag, stability)
    directional = np.hypot(mean_dx, mean_dy)
    if directional < 0.35 * mag:
        motion = Motion.HANDHELD if stability < 0.6 else Motion.MOVING
        return MotionSummary(motion, mag, stability)
    if abs(mean_dx) >= 2 * abs(mean_dy):
        return MotionSummary(Motion.PAN_RIGHT if mean_dx < 0 else Motion.PAN_LEFT, mag, stability)
    if abs(mean_dy) >= 2 * abs(mean_dx):
        return MotionSummary(Motion.TILT_DOWN if mean_dy < 0 else Motion.TILT_UP, mag, stability)
    return MotionSummary(Motion.MOVING, mag, stability)
