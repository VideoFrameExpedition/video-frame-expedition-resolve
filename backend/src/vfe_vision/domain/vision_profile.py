"""How a vision model writes boxes, measured on two synthetic scenes.

The probe asks the loaded model to box five known shapes in a landscape and a portrait scene,
then reads its four numbers under each of nine conventions and keeps the one that fits. A model
is trusted with positions only when that convention fits clearly in both scenes, for nearly
every object, and no box is better explained by another convention. Qwen3-VL's convention was
verified on real frames: it is a prior that needs no probe.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROBE_VERSION = 1
MIN_IOU = 0.5  # the mean, and every scene
MIN_MARGIN = 0.25  # over the runner-up convention
MATCH_IOU = 0.5  # an object is found when its box overlaps the truth this much
MIN_MATCHED = 4  # of the five objects, in each scene
MIX_IOU, MIX_GAP = 0.5, 0.3  # a box another convention explains better
PRECISE_IOU = 0.75  # below: enabled, but "approximate"
MAX_PROBE_BOXES = 8

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in 0–1 of the image


class BoxConvention(StrEnum):
    """How four numbers map to a box of the image the model was sent (width W, height H)."""

    XYXY_1000 = "xyxy_1000"  # Qwen3-VL, Qwen3.5+, GLM-4.xV (0–999)
    YXYX_1000 = "yxyx_1000"  # Gemma 3/4, Gemini (box_2d)
    XYXY_PX = "xyxy_px"  # Qwen2.5-VL (pixels of its input)
    YXYX_PX = "yxyx_px"
    XYXY_UNIT = "xyxy_unit"  # already 0–1
    YXYX_UNIT = "yxyx_unit"
    XYWH_1000 = "xywh_1000"  # corner and size (COCO-like)
    XYWH_PX = "xywh_px"
    CXCYWH_1000 = "cxcywh_1000"  # centre and size (YOLO-like)

    @property
    def y_first(self) -> bool:
        return self in {BoxConvention.YXYX_1000, BoxConvention.YXYX_PX, BoxConvention.YXYX_UNIT}

    @property
    def fractional(self) -> bool:
        """Numbers below 1: the integer box fields of the production prompts cannot hold them."""
        return self in {BoxConvention.XYXY_UNIT, BoxConvention.YXYX_UNIT}


class BoxField(StrEnum):
    """The field the model is asked to fill, and the order its description states."""

    BBOX_2D = "bbox_2d"  # "[x1, y1, x2, y2] in 0-1000"
    BOX_2D = "box_2d"  # "[y1, x1, y2, x2] in 0-1000"

    @property
    def y_first(self) -> bool:
        return self == BoxField.BOX_2D


# Sentence shared by the probe and the production prompt (subjects.v3.user.j2): its bbox_2d form
# is byte-identical to the text the stored answers were obtained with.
BOX_SENTENCES = {
    BoxField.BBOX_2D: "bbox_2d: [x1, y1, x2, y2] in\n0-1000 relative to the image width and height",
    BoxField.BOX_2D: "box_2d: [y1, x1, y2, x2] in\n0-1000 relative to the image height and width",
}

# Verified on real frames: no probe needed (a measured profile still takes precedence).
PRIORS: dict[str, tuple[BoxConvention, BoxField]] = {
    "qwen3vl": (BoxConvention.XYXY_1000, BoxField.BBOX_2D),
}


def _alnum(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def prior_for(model_key: str, architecture: str | None) -> tuple[BoxConvention, BoxField] | None:
    name = _alnum(f"{model_key} {architecture or ''}")
    for family, prior in PRIORS.items():
        if family in name:
            return prior
    return None


def fingerprint(
    model_key: str, architecture: str | None, quantization: str | None, size_bytes: int | None
) -> str:
    """Identity of the model file: another quantization or file is probed again."""
    blob = "|".join(str(part or "") for part in (model_key, architecture, quantization, size_bytes))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------------------ geometry
def decode(convention: BoxConvention, raw: list[float], width: int, height: int) -> Box | None:
    """The box four numbers stand for under ``convention``, clipped; None when degenerate."""
    box = project(convention, raw, width, height)
    return _clip(box) if box is not None else None


def project(convention: BoxConvention, raw: list[float], width: int, height: int) -> Box | None:
    """The four numbers mapped to 0–1 of the image under ``convention``, not yet clipped or
    ordered (a model may swap the corners)."""
    if len(raw) != 4 or width <= 0 or height <= 0:
        return None
    a, b, c, d = (float(v) for v in raw)
    w, h = float(width), float(height)
    match convention:
        case BoxConvention.XYXY_1000:
            box = (a / 1000, b / 1000, c / 1000, d / 1000)
        case BoxConvention.YXYX_1000:
            box = (b / 1000, a / 1000, d / 1000, c / 1000)
        case BoxConvention.XYXY_PX:
            box = (a / w, b / h, c / w, d / h)
        case BoxConvention.YXYX_PX:
            box = (b / w, a / h, d / w, c / h)
        case BoxConvention.XYXY_UNIT:
            box = (a, b, c, d)
        case BoxConvention.YXYX_UNIT:
            box = (b, a, d, c)
        case BoxConvention.XYWH_1000:
            box = (a / 1000, b / 1000, (a + c) / 1000, (b + d) / 1000)
        case BoxConvention.XYWH_PX:
            box = (a / w, b / h, (a + c) / w, (b + d) / h)
        case BoxConvention.CXCYWH_1000:
            box = ((a - c / 2) / 1000, (b - d / 2) / 1000, (a + c / 2) / 1000, (b + d / 2) / 1000)
    return box


def _clip(box: Box) -> Box | None:
    x1, y1, x2, y2 = (min(1.5, max(-0.5, v)) for v in box)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    x1, y1, x2, y2 = (min(1.0, max(0.0, v)) for v in (x1, y1, x2, y2))
    if x2 - x1 < 0.004 or y2 - y1 < 0.004:
        return None
    return (x1, y1, x2, y2)


def iou(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def assignment(predicted: list[Box | None], truth: list[Box]) -> list[float]:
    """IoU of each truth object with its predicted box, under the one-to-one matching that
    maximises the total (labels ignored; a missed object scores 0)."""
    predicted = predicted[:MAX_PROBE_BOXES]
    scores = [[iou(p, t) if p is not None else 0.0 for p in predicted] for t in truth]

    @cache
    def best(i: int, used: int) -> tuple[float, tuple[float, ...]]:
        if i == len(truth):
            return 0.0, ()
        total, rest = best(i + 1, used)
        choice = (total, (0.0, *rest))
        for j, value in enumerate(scores[i]):
            if used >> j & 1 or value <= 0:
                continue
            total, rest = best(i + 1, used | 1 << j)
            if total + value > choice[0]:
                choice = (total + value, (value, *rest))
        return choice

    return list(best(0, 0)[1])


# ------------------------------------------------------------------------------ scenes
@dataclass(frozen=True, slots=True)
class SceneObject:
    name: str  # what the prompt calls it
    kind: str  # rectangle | ellipse | triangle | star | cat | person
    color: str
    target: Box  # where it is drawn; the truth is measured on the drawn mask


@dataclass(frozen=True, slots=True)
class ProbeScene:
    name: Literal["landscape", "portrait"]
    width: int
    height: int
    objects: tuple[SceneObject, ...]

    @property
    def object_list(self) -> str:
        names = [o.name for o in self.objects]
        return ", ".join(names[:-1]) + " and " + names[-1]


# Layouts searched so that a wrong convention scores at most ~0.2 (frozen). The
# portrait scene guards the axis that pixels and 0–1000 share at 1024 px, and aspect tricks.
SCENES = (
    ProbeScene(
        "landscape", 1024, 576,
        (
            SceneObject("red rectangle", "rectangle", "red", (0.304, 0.368, 0.488, 0.554)),
            SceneObject("blue ellipse", "ellipse", "blue", (0.753, 0.567, 0.915, 0.771)),
            SceneObject("green triangle", "triangle", "green", (0.676, 0.282, 0.784, 0.436)),
            SceneObject("purple star", "star", "purple", (0.070, 0.455, 0.173, 0.588)),
            SceneObject("black cat", "cat", "black", (0.356, 0.608, 0.552, 0.954)),
        ),
    ),
    ProbeScene(
        "portrait", 576, 1024,
        (
            SceneObject("red rectangle", "rectangle", "red", (0.371, 0.359, 0.520, 0.621)),
            SceneObject("blue ellipse", "ellipse", "blue", (0.044, 0.431, 0.320, 0.615)),
            SceneObject("green triangle", "triangle", "green", (0.584, 0.706, 0.757, 0.872)),
            SceneObject("purple star", "star", "purple", (0.515, 0.139, 0.671, 0.242)),
            SceneObject(
                "black person silhouette", "person", "black", (0.558, 0.276, 0.808, 0.573)
            ),
        ),
    ),
)  # fmt: skip


# ------------------------------------------------------------------------------ answers
class ProbeBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="The object's name, as given.")
    bbox_2d: list[float] = Field(
        min_length=4,
        max_length=4,
        description="Tight box [x1, y1, x2, y2] in 0-1000 relative to the image width and height.",
    )


class ProbeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objects: list[ProbeBox] = Field(max_length=MAX_PROBE_BOXES)


class ProbeBoxYFirst(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="The object's name, as given.")
    box_2d: list[float] = Field(
        min_length=4,
        max_length=4,
        description="Tight box [y1, x1, y2, x2] in 0-1000 relative to the image height and width.",
    )


class ProbeAnswerYFirst(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objects: list[ProbeBoxYFirst] = Field(max_length=MAX_PROBE_BOXES)


def probe_schema(box_field: BoxField) -> type[ProbeAnswer] | type[ProbeAnswerYFirst]:
    return ProbeAnswerYFirst if box_field == BoxField.BOX_2D else ProbeAnswer


def raw_boxes(answer: ProbeAnswer | ProbeAnswerYFirst) -> list[list[float]]:
    return [
        list(o.bbox_2d if isinstance(o, ProbeBox) else o.box_2d)
        for o in answer.objects[:MAX_PROBE_BOXES]
    ]


# ------------------------------------------------------------------------------ results
class ProbeCall(BaseModel):
    """One probe request and what came back."""

    model_config = ConfigDict(extra="forbid")

    scene: Literal["landscape", "portrait"]
    box_field: BoxField
    width: int
    height: int
    truth: list[list[float]]  # measured on the drawn masks, 0–1
    raw_boxes: list[list[float]] = Field(max_length=MAX_PROBE_BOXES)
    finish_reason: str | None = None
    reasoning_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int = 0
    truncated: bool = False


class ConventionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    convention: BoxConvention
    mean_iou: float


class GroundingCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    convention: BoxConvention | None
    box_field: BoxField | None
    mean_iou: float | None
    iou_by_scene: dict[str, float]
    margin: float | None
    matched_by_scene: dict[str, int]  # objects found at IoU >= MATCH_IOU
    mixed_boxes: int  # boxes another convention explains clearly better
    precise: bool | None  # mean_iou >= PRECISE_IOU
    ranking: list[ConventionScore]  # the best conventions and their mean IoU
    reason: str | None  # French: why positions are off, shown as the stage's skip reason


class VisionProfile(BaseModel):
    """What the probe measured for one model file. Stored; never a prior."""

    model_config = ConfigDict(extra="forbid")

    probe_version: int
    fingerprint: str
    model_key: str
    display_name: str
    architecture: str | None
    quantization: str | None
    size_bytes: int | None
    probed_at: datetime
    truncated: bool
    reasoning_tokens_seen: int  # an observation; the stages' summaries report it again
    grounding: GroundingCalibration
    wall_ms: int
    prompt_tokens: int
    completion_tokens: int
    calls: list[ProbeCall] = Field(max_length=6)


def _fr(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _scene_scores(
    calls: list[ProbeCall], convention: BoxConvention
) -> tuple[dict[str, float], dict[str, int]]:
    ious: dict[str, float] = {}
    matched: dict[str, int] = {}
    for call in calls:
        truth = [(t[0], t[1], t[2], t[3]) for t in call.truth]
        predicted = [decode(convention, raw, call.width, call.height) for raw in call.raw_boxes]
        per_object = assignment(predicted, truth)
        ious[call.scene] = sum(per_object) / len(truth) if truth else 0.0
        matched[call.scene] = sum(1 for value in per_object if value >= MATCH_IOU)
    return ious, matched


def _fit(call: ProbeCall, convention: BoxConvention, raw: list[float]) -> float:
    """How well one answer box matches its closest shape under ``convention``."""
    box = decode(convention, raw, call.width, call.height)
    if box is None:
        return 0.0
    return max((iou(box, (t[0], t[1], t[2], t[3])) for t in call.truth), default=0.0)


def _mixed_boxes(calls: list[ProbeCall], chosen: BoxConvention) -> int:
    """Boxes clearly better explained by another convention: a model mixing orders."""
    count = 0
    for call in calls:
        for raw in call.raw_boxes:
            own = _fit(call, chosen, raw)
            if any(
                (other := _fit(call, convention, raw)) >= MIX_IOU and other >= own + MIX_GAP
                for convention in BoxConvention
                if convention != chosen
            ):
                count += 1
    return count


def _best_in(
    scored: dict[BoxConvention, tuple[dict[str, float], dict[str, int]]], scene: str
) -> BoxConvention:
    return max(BoxConvention, key=lambda convention: scored[convention][0].get(scene, 0.0))


def decide(calls: list[ProbeCall], box_field: BoxField) -> GroundingCalibration:
    """The convention the answers follow, and whether positions can be trusted with it."""
    off = GroundingCalibration(
        enabled=False, convention=None, box_field=box_field, mean_iou=None, iou_by_scene={},
        margin=None, matched_by_scene={}, mixed_boxes=0, precise=None, ranking=[], reason=None,
    )  # fmt: skip
    if not calls:
        return off.model_copy(update={"reason": "aucune réponse du modèle"})
    if truncated := [call for call in calls if call.truncated]:
        why = (
            "réponses tronquées : le modèle raisonne malgré la consigne"
            if any(call.reasoning_tokens for call in truncated)
            else "réponses tronquées (boucle ou raisonnement)"
        )
        return off.model_copy(update={"reason": why})
    scored = {c: _scene_scores(calls, c) for c in BoxConvention}
    means = {c: sum(ious.values()) / len(ious) for c, (ious, _) in scored.items()}
    ranking = sorted(means.items(), key=lambda item: item[1], reverse=True)
    best, best_mean = ranking[0]
    margin = best_mean - ranking[1][1]
    ious, matched = scored[best]
    mixed = _mixed_boxes(calls, best)
    result = GroundingCalibration(
        enabled=False, convention=best, box_field=box_field, mean_iou=round(best_mean, 4),
        iou_by_scene={k: round(v, 4) for k, v in ious.items()}, margin=round(margin, 4),
        matched_by_scene=matched, mixed_boxes=mixed, precise=best_mean >= PRECISE_IOU,
        ranking=[ConventionScore(convention=c, mean_iou=round(v, 4)) for c, v in ranking[:3]],
        reason=None,
    )  # fmt: skip
    scene_best = {call.scene: _best_in(scored, call.scene) for call in calls}
    worst_scene, worst = min(ious.items(), key=lambda item: item[1])
    reason: str | None = None
    if best_mean < MIN_IOU:
        reason = f"positions non fiables (IoU {_fr(best_mean)} < 0,5)"
    elif worst < MIN_IOU:
        reason = f"positions non fiables en {_scene_fr(worst_scene)} (IoU {_fr(worst)} < 0,5)"
    elif any(convention != best for convention in scene_best.values()):
        reason = "convention différente selon l'orientation de l'image"
    elif margin < MIN_MARGIN:
        reason = f"convention ambiguë (écart {_fr(margin)} < 0,25)"
    elif (few := min(matched.values())) < MIN_MATCHED:
        reason = f"trop d'objets manqués ({few} sur {len(calls[0].truth)} trouvés)"
    elif mixed:
        reason = f"ordre des coordonnées incohérent ({mixed} cadres)"
    elif best.fractional:
        reason = "coordonnées entre 0 et 1, non prises en charge par le repérage"
    if reason is not None:
        return result.model_copy(update={"reason": reason})
    return result.model_copy(update={"enabled": True})


def _scene_fr(scene: str) -> str:
    return "paysage" if scene == "landscape" else "portrait"


def consistent(calibration: GroundingCalibration) -> bool:
    """The measured order matches the order the field's description states."""
    if calibration.convention is None or calibration.box_field is None:
        return False
    return calibration.convention.y_first == calibration.box_field.y_first


def pick(first: GroundingCalibration, second: GroundingCalibration | None) -> GroundingCalibration:
    """Round 1 asks for bbox_2d; round 2 (box_2d) runs only when round 1 did not pass or its
    order contradicts the field. A passing, self-consistent variant wins."""
    if second is None:
        return first
    for candidate in (first, second):
        if candidate.enabled and consistent(candidate):
            return candidate
    for candidate in (first, second):
        if candidate.enabled:
            return candidate
    return first
