"""A static framing for a range of a video, and the DaVinci Resolve values that apply it.

Automatic reframing (dense tracking, smoothing, a rendered preview) is left out: the
assistant reframes by itself, and needs one deterministic answer per range instead of
doing the arithmetic. The subject's boxes at the keyframes of the range (normalised 0–1 in the
displayed picture) give one crop of the timeline's shape, kept for the whole range:

- its size is the largest of that shape inside the picture (the timeline is filled, nothing is
  zoomed in further): the full height for a vertical crop of a horizontal video, the full width
  for a horizontal crop of a vertical one;
- along the free side, it is centred on the union of the boxes when the union fits (every
  position of the range is kept), else on the median of the box centres (the subject is kept
  most of the time); ``headroom`` instead puts the top of the union that share of the crop's
  height below the crop's top (a person's head); the crop never leaves the picture.

Resolve (:func:`resolve_transform`), with the clip scaled to fit the timeline (the project
default « Scale entire image to fit »), zoom about the picture's centre, square pixels:

- ``s = timeline_width / crop_width`` (= timeline_height / crop_height) is the scale that makes
  the crop fill the timeline; ``fit = min(timeline_width / width, timeline_height / height)`` is
  the scale Resolve already applies; ``ZoomX = ZoomY = s / fit``;
- the crop's centre ``(cx, cy)`` (source pixels, y down) must land on the timeline's centre:
  ``Pan = -(cx - width / 2) × s`` and ``Tilt = (cy - height / 2) × s`` in timeline pixels, Tilt
  counted upwards as in Resolve's Inspector.

Pan and Tilt depend only on ``s``: with « Scale full frame with crop » they stay the same and only
the zoom changes (``s / max(timeline_width / width, timeline_height / height)``). Their signs are
the two constants below, confirmed in Resolve 21.1 (1920×1080 timeline, 3840×2160 clip in Fit).
``s`` above 2 means the source is enlarged more than twice (a visible loss of sharpness).

A subject that moves too much for one crop (the union of its boxes is wider than the crop) is
better served by several static framings: :func:`framing_segments` cuts the range at the
keyframes where it no longer fits (Resolve 21.1 has no keyframes for the Transform values).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 normalised 0–1 (displayed picture)
_EPSILON = 1e-6  # pixels: a crop this close to the picture's side has no room to move
PAN_SIGN = -1.0  # Pan > 0 moves the picture right: a subject right of centre needs Pan < 0
TILT_SIGN = 1.0  # Tilt > 0 moves the picture up (Resolve's Y axis points up)
MAX_UPSCALE = 2.0  # timeline pixels per source pixel beyond which sharpness visibly suffers


@dataclass(frozen=True, slots=True)
class Crop:
    """A rectangle in source pixels of the displayed picture (origin top left)."""

    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


@dataclass(frozen=True, slots=True)
class Transform:
    """Resolve's Inspector values: ``ZoomX`` = ``ZoomY`` (1.0 = 100 %), Pan and Tilt in timeline
    pixels (Tilt upwards)."""

    zoom: float
    pan: float
    tilt: float


@dataclass(frozen=True, slots=True)
class Framing:
    crop: Crop
    transform: Transform
    union: Box | None  # every position of the subject in the range (None: no subject)
    coverage: float  # share of the union's area inside the crop (1.0 without a subject)
    centred_on: str  # union | median | top | picture
    upscale: float  # timeline pixels per source pixel (above MAX_UPSCALE: soft picture)


def fill_size(width: float, height: float, aspect: float) -> tuple[float, float]:
    """The largest ``aspect`` (width / height) rectangle inside a width × height picture."""
    if width / height > aspect:
        return height * aspect, height
    return width, width / aspect


def static_framing(
    boxes: Sequence[Box],
    width: float,
    height: float,
    timeline_width: float,
    timeline_height: float,
    *,
    headroom: float | None = None,
) -> Framing:
    """One crop of the timeline's shape for every box of the range (see the module docstring)."""
    if min(width, height, timeline_width, timeline_height) <= 0:
        raise ValueError("dimensions invalides")
    crop_w, crop_h = fill_size(width, height, timeline_width / timeline_height)
    if not boxes:
        crop = Crop((width - crop_w) / 2, (height - crop_h) / 2, crop_w, crop_h)
        return Framing(crop, resolve_transform(crop, width, height, timeline_width,
                                               timeline_height), None, 1.0, "picture",
                       timeline_width / crop_w)  # fmt: skip
    union: Box = (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )  # fmt: skip
    left, top, right, bottom = (union[0] * width, union[1] * height,
                                union[2] * width, union[3] * height)  # fmt: skip
    fits_x, fits_y = right - left <= crop_w, bottom - top <= crop_h
    if fits_x:
        x = (left + right) / 2 - crop_w / 2
    else:
        x = statistics.median((b[0] + b[2]) / 2 for b in boxes) * width - crop_w / 2
    if headroom is not None:
        y = top - headroom * crop_h
    elif fits_y:
        y = (top + bottom) / 2 - crop_h / 2
    else:
        y = statistics.median((b[1] + b[3]) / 2 for b in boxes) * height - crop_h / 2
    if crop_w < width - _EPSILON:  # the crop slides sideways
        how = "union" if fits_x else "median"
    elif crop_h < height - _EPSILON:  # up and down
        how = "top" if headroom is not None else "union" if fits_y else "median"
    else:
        how = "picture"  # the timeline has the picture's shape: nothing to choose
    crop = Crop(_clamp(x, 0.0, width - crop_w), _clamp(y, 0.0, height - crop_h), crop_w, crop_h)
    return Framing(
        crop=crop,
        transform=resolve_transform(crop, width, height, timeline_width, timeline_height),
        union=union,
        coverage=_coverage((left, top, right, bottom), crop),
        centred_on=how,
        upscale=timeline_width / crop_w,
    )


def resolve_transform(
    crop: Crop, width: float, height: float, timeline_width: float, timeline_height: float
) -> Transform:
    """ZoomX/ZoomY, Pan and Tilt that make ``crop`` fill the timeline (module docstring)."""
    scale = timeline_width / crop.width
    fit = min(timeline_width / width, timeline_height / height)
    cx, cy = crop.center
    return Transform(
        zoom=scale / fit,
        pan=PAN_SIGN * (cx - width / 2) * scale,
        tilt=TILT_SIGN * (cy - height / 2) * scale,
    )


def framing_segments(
    boxes: Sequence[Box],
    width: float,
    height: float,
    timeline_width: float,
    timeline_height: float,
) -> list[tuple[int, int]]:
    """Consecutive runs of ``boxes`` (in time order) whose union fits in one crop of the
    timeline's shape, as (first, last) indices: one run when the subject stays in a crop."""
    crop_w, crop_h = fill_size(width, height, timeline_width / timeline_height)
    runs: list[tuple[int, int]] = []
    first = 0
    low_x = low_y = float("inf")
    high_x = high_y = float("-inf")
    for index, box in enumerate(boxes):
        lx, ly = min(low_x, box[0] * width), min(low_y, box[1] * height)
        hx, hy = max(high_x, box[2] * width), max(high_y, box[3] * height)
        if index > first and (hx - lx > crop_w + _EPSILON or hy - ly > crop_h + _EPSILON):
            runs.append((first, index - 1))
            first = index
            lx, ly, hx, hy = box[0] * width, box[1] * height, box[2] * width, box[3] * height
        low_x, low_y, high_x, high_y = lx, ly, hx, hy
    if boxes:
        runs.append((first, len(boxes) - 1))
    return runs


def _coverage(area: tuple[float, float, float, float], crop: Crop) -> float:
    left, top, right, bottom = area
    inside_w = min(right, crop.x + crop.width) - max(left, crop.x)
    inside_h = min(bottom, crop.y + crop.height) - max(top, crop.y)
    total = (right - left) * (bottom - top)
    if total <= 0:
        return 1.0
    return max(0.0, inside_w) * max(0.0, inside_h) / total


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), max(low, high))
