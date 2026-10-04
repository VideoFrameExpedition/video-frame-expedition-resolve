"""A reframing plan for an edit: static crops per piece of each item, aimed at the
subject's head, without jumps, each keyframe scored.

``domain/framing.py`` gives one crop for a range; an edit needs more (measured on real edits):

- **a target, not the box.** A subject filmed close fills most of a vertical picture while a 16:9
  band keeps a third of it: centred on its box, the crop shows the belly. The crop must keep the
  subject's *target*: the whole box when it fits, else a part of it ``TARGET`` of the crop long,
  placed at the **head** — the head's box when the vision model gave it (``services/reframe_plan``),
  else the top of the box (``anchor``: an upright animal or person), with ``HEADROOM`` beyond;
- **hysteresis.** One crop while every keyframe keeps ``keep`` of its target; a new piece only
  when that fails, cut halfway between the two keyframes;
- **no jumpy pieces.** Pieces shorter than ``min_piece_s`` join the neighbour that keeps the
  subject best; crops closer than ``MIN_JUMP_PX`` timeline pixels are merged;
- **a score.** ``cov`` (share of the target kept), ``body`` (of the whole box), ``head`` (of the
  head's box) at every keyframe; weak pieces are flagged, for a look.

The Transform values follow ``domain/framing.resolve_transform`` (item scaled to Fit). A clip
filmed upside down (``rotation`` 180) keeps its boxes in the upside-down picture: the head is at
the bottom of the box, and Pan/Tilt change sign (Resolve applies them after the rotation). A
clip filmed sideways (±90) holds a landscape picture: it fills a landscape timeline by zoom only.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from vfe_vision.domain.framing import MAX_UPSCALE, Crop, fill_size, resolve_transform

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 normalised 0–1 (displayed picture)
Anchor = Literal["auto", "top", "center", "bottom"]
TARGET = 0.8  # share of the crop kept for the part of a subject bigger than the crop
HEADROOM = 0.1  # share of the crop left beyond the head
MIN_JUMP_PX = 150.0  # timeline pixels: smaller moves between pieces are merged
GRID = 61  # positions tried along each free side
EDGE_S = 0.5  # keyframes this far outside the range still count
HEAD_KEPT = 0.6  # a head less inside than this is « cut »


@dataclass(frozen=True, slots=True)
class PlanFrame:
    t_s: float
    box: Box
    head: Box | None = None


@dataclass(frozen=True, slots=True)
class PlanOptions:
    anchor: Anchor = "auto"
    rotation: int = 0  # 0, 90, -90, 180
    min_piece_s: float = 2.5
    keep: float = 0.75
    flag: float = 0.6


@dataclass(frozen=True, slots=True)
class FrameScore:
    t_s: float
    cov: float
    body: float
    head: float | None


@dataclass(frozen=True, slots=True)
class Piece:
    in_s: float
    out_s: float
    crop: Crop  # source pixels of the displayed picture
    props: dict[str, float]  # ZoomX, ZoomY, Pan, Tilt, RotationAngle
    frames: list[FrameScore]
    flags: list[str] = field(default_factory=list)
    upscale: float = 1.0


@dataclass(frozen=True, slots=True)
class Geometry:
    """The picture (w × h, displayed), the timeline (tw × th) and the crop that fills it."""

    w: float
    h: float
    tw: float
    th: float

    @property
    def crop_size(self) -> tuple[float, float]:
        return fill_size(self.w, self.h, self.tw / self.th)

    @property
    def scale(self) -> float:
        """Timeline pixels per source pixel of the crop."""
        return self.tw / self.crop_size[0]

    def px(self, box: Box) -> Box:
        return box[0] * self.w, box[1] * self.h, box[2] * self.w, box[3] * self.h


Region = Callable[[PlanFrame], Box]


@dataclass(slots=True)
class _Group:
    a: float
    b: float
    frames: list[PlanFrame]
    pos: tuple[float, float] = (0.0, 0.0)


def _along(
    lo: float, hi: float, head: tuple[float, float] | None, size: float, anchor: str
) -> tuple[float, float]:
    """The target along one side: the box when it fits, else ``TARGET`` of the crop at the head
    (the body on its other side), else at the anchor."""
    if hi - lo <= size:
        return lo, hi
    span = TARGET * size
    if head is not None:
        if (head[0] + head[1]) / 2 <= (lo + hi) / 2:  # the head in the first half: body after
            start = max(lo, head[0] - HEADROOM * size)
            return start, start + span
        end = min(hi, head[1] + HEADROOM * size)
        return end - span, end
    if anchor == "center":
        middle = (lo + hi) / 2
        return middle - span / 2, middle + span / 2
    if anchor == "bottom":
        return hi - span, hi
    return lo, lo + span  # top / auto: the head of an upright subject


def target(frame: PlanFrame, geo: Geometry, anchor: str) -> Box:
    """The part of the subject the crop must keep, in source pixels."""
    cw, ch = geo.crop_size
    x1, y1, x2, y2 = geo.px(frame.box)
    head = geo.px(frame.head) if frame.head else None
    tx = _along(x1, x2, (head[0], head[2]) if head else None, cw, "center")
    ty = _along(y1, y2, (head[1], head[3]) if head else None, ch, anchor)
    return tx[0], ty[0], tx[1], ty[1]


def coverage(region: Box, x: float, y: float, size: tuple[float, float]) -> float:
    x1, y1, x2, y2 = region
    area = (x2 - x1) * (y2 - y1)
    if area <= 0:
        return 1.0
    inside_w = min(x2, x + size[0]) - max(x1, x)
    inside_h = min(y2, y + size[1]) - max(y1, y)
    return max(0.0, inside_w) * max(0.0, inside_h) / area


def best_position(
    regions: Sequence[Box], geo: Geometry, prefer: tuple[float, float] | None = None
) -> tuple[tuple[float, float], float, float]:
    """Crop origin keeping the most of the regions: best mean coverage, then best minimum, then
    closest to the regions' centre (or to ``prefer``, the previous piece: fewer moves)."""
    cw, ch = geo.crop_size
    xs = [0.0] if geo.w - cw < 1 else [(geo.w - cw) * i / (GRID - 1) for i in range(GRID)]
    ys = [0.0] if geo.h - ch < 1 else [(geo.h - ch) * i / (GRID - 1) for i in range(GRID)]
    scored = []
    for x in xs:
        for y in ys:
            covs = [coverage(r, x, y, (cw, ch)) for r in regions]
            scored.append((sum(covs) / len(covs), min(covs), x, y))
    top_mean = max(s[0] for s in scored)
    pool = [s for s in scored if s[0] >= top_mean - 0.01]
    top_min = max(s[1] for s in pool)
    pool = [s for s in pool if s[1] >= top_min - 0.01]
    if prefer is None:
        cx = sum((r[0] + r[2]) / 2 for r in regions) / len(regions)
        cy = sum((r[1] + r[3]) / 2 for r in regions) / len(regions)
        prefer = (cx - cw / 2, cy - ch / 2)
    choice = min(pool, key=lambda s: (s[2] - prefer[0]) ** 2 + (s[3] - prefer[1]) ** 2)
    return (choice[2], choice[3]), choice[0], choice[1]


def transform_props(crop: Crop, geo: Geometry, rotation: int) -> dict[str, float]:
    move = resolve_transform(crop, geo.w, geo.h, geo.tw, geo.th)
    pan, tilt = round(move.pan, 1) + 0.0, round(move.tilt, 1) + 0.0
    if rotation == 180:
        pan, tilt = -pan + 0.0, -tilt + 0.0
    zoom = round(move.zoom, 4)
    return {"ZoomX": zoom, "ZoomY": zoom, "Pan": pan, "Tilt": tilt,
            "RotationAngle": float(rotation)}  # fmt: skip


def sideways_props(geo: Geometry, rotation: int) -> dict[str, float]:
    """A clip filmed sideways, turned by ``rotation`` (±90): the zoom that fills the timeline."""
    fit = min(geo.tw / geo.w, geo.th / geo.h)
    zoom = round(max(geo.tw / (geo.h * fit), geo.th / (geo.w * fit)), 4)
    return {"ZoomX": zoom, "ZoomY": zoom, "Pan": 0.0, "Tilt": 0.0,
            "RotationAngle": float(rotation)}  # fmt: skip


def plan_item(
    frames: Sequence[PlanFrame],
    geo: Geometry,
    in_s: float,
    out_s: float,
    options: PlanOptions = PlanOptions(),  # noqa: B008 - frozen, never changed
) -> list[Piece]:
    """The pieces of one item (at least one)."""
    rotation = options.rotation
    if rotation in (90, -90):
        whole = Crop(0.0, 0.0, geo.w, geo.h)
        return [Piece(in_s, out_s, whole, sideways_props(geo, rotation), [],
                      [f"rotated_{rotation}"])]  # fmt: skip
    anchor: str = options.anchor
    if rotation == 180:  # the boxes are in the upside-down picture: the head is at its bottom
        anchor = {"auto": "bottom", "top": "bottom", "bottom": "top"}.get(anchor, anchor)
    cw, ch = geo.crop_size
    flags = [f"rotated_{rotation}"] if rotation else []
    if geo.scale > MAX_UPSCALE:
        flags.append(f"upscale_{geo.scale:.1f}")
    ordered = sorted(frames, key=lambda f: f.t_s)
    inside = [f for f in ordered if in_s - EDGE_S <= f.t_s <= out_s + EDGE_S]
    if len(inside) >= 2:  # the keyframe before the range only when the range has too few
        ordered = inside
    if not ordered:
        crop = Crop((geo.w - cw) / 2, (geo.h - ch) / 2, cw, ch)
        return [Piece(in_s, out_s, crop, transform_props(crop, geo, rotation), [],
                      [*flags, "no_subject"], geo.scale)]  # fmt: skip

    def region(frame: PlanFrame) -> Box:
        return target(frame, geo, anchor)

    groups = _hysteresis(ordered, region, geo, (options.keep, in_s, out_s))
    groups = _merge_short(groups, region, geo, options.min_piece_s)
    groups = _place(groups, region, geo)
    return [_piece(g, region, geo, (rotation, options.flag), flags) for g in groups]


def _hysteresis(
    frames: list[PlanFrame], region: Region, geo: Geometry, limits: tuple[float, float, float]
) -> list[_Group]:
    keep, in_s, out_s = limits
    runs: list[list[PlanFrame]] = []
    current = [frames[0]]
    for frame in frames[1:]:
        _, _, low = best_position([region(f) for f in [*current, frame]], geo)
        if low >= keep:
            current.append(frame)
        else:
            runs.append(current)
            current = [frame]
    runs.append(current)
    cuts = [in_s]
    for run, following in itertools.pairwise(runs):
        cuts.append(min(out_s, max(in_s, (run[-1].t_s + following[0].t_s) / 2)))
    cuts.append(out_s)
    return [_Group(cuts[i], cuts[i + 1], run) for i, run in enumerate(runs)]


def _merged(groups: list[_Group], i: int, j: int) -> list[_Group]:
    lo, hi = sorted((i, j))
    joined = _Group(groups[lo].a, groups[hi].b, groups[lo].frames + groups[hi].frames)
    return [*groups[:lo], joined, *groups[hi + 1 :]]


def _merge_short(
    groups: list[_Group], region: Region, geo: Geometry, min_piece_s: float
) -> list[_Group]:
    while len(groups) > 1:
        short = [i for i, g in enumerate(groups) if g.b - g.a < min_piece_s]
        if not short:
            break
        i = min(short, key=lambda k: groups[k].b - groups[k].a)
        neighbours = [j for j in (i - 1, i + 1) if 0 <= j < len(groups)]
        j = max(
            neighbours,
            key=lambda n: best_position(
                [region(f) for f in groups[i].frames + groups[n].frames], geo
            )[2],
        )
        groups = _merged(groups, i, j)
    return groups


def _place(groups: list[_Group], region: Region, geo: Geometry) -> list[_Group]:
    """Each group's crop; neighbours closer than ``MIN_JUMP_PX`` merged (then placed again)."""
    while True:
        previous: tuple[float, float] | None = None
        for g in groups:
            g.pos = best_position([region(f) for f in g.frames], geo, previous)[0]
            previous = g.pos
        close = next(
            (
                i
                for i in range(len(groups) - 1)
                if abs(groups[i].pos[0] - groups[i + 1].pos[0]) * geo.scale < MIN_JUMP_PX
                and abs(groups[i].pos[1] - groups[i + 1].pos[1]) * geo.scale < MIN_JUMP_PX
            ),
            None,
        )
        if close is None:
            return groups
        groups = _merged(groups, close, close + 1)


def _piece(
    g: _Group, region: Region, geo: Geometry, how: tuple[int, float], flags: list[str]
) -> Piece:
    rotation, flag = how
    cw, ch = geo.crop_size
    x, y = g.pos
    crop = Crop(x, y, cw, ch)
    scores = [
        FrameScore(
            t_s=f.t_s,
            cov=round(coverage(region(f), x, y, (cw, ch)), 2),
            body=round(coverage(geo.px(f.box), x, y, (cw, ch)), 2),
            head=round(coverage(geo.px(f.head), x, y, (cw, ch)), 2) if f.head else None,
        )
        for f in g.frames
    ]
    marks = list(flags)
    if scores and min(s.cov for s in scores) < flag:
        marks.append("low_coverage")
    if any(s.head is not None and s.head < HEAD_KEPT for s in scores):
        marks.append("head_cut")
    return Piece(round(g.a, 3), round(g.b, 3), crop, transform_props(crop, geo, rotation),
                 scores, marks, geo.scale)  # fmt: skip
