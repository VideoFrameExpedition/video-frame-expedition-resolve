"""How a vision model's boxes are read, and when positions can be trusted."""

from __future__ import annotations

import random

import pytest

from vfe_vision.adapters.imaging import draw_probe_scene
from vfe_vision.domain.grounding import Being, BeingCategory, Grounding, to_detections
from vfe_vision.domain.subjects import Box
from vfe_vision.domain.vision_profile import (
    SCENES,
    BoxConvention,
    BoxField,
    ProbeCall,
    decide,
    decode,
    iou,
    pick,
    prior_for,
)

RENDERED = [draw_probe_scene(scene) for scene in SCENES]

ENCODE = {
    BoxConvention.XYXY_1000: lambda b, w, h: [b[0] * 1000, b[1] * 1000, b[2] * 1000, b[3] * 1000],
    BoxConvention.YXYX_1000: lambda b, w, h: [b[1] * 1000, b[0] * 1000, b[3] * 1000, b[2] * 1000],
    BoxConvention.XYXY_PX: lambda b, w, h: [b[0] * w, b[1] * h, b[2] * w, b[3] * h],
    BoxConvention.XYXY_UNIT: lambda b, w, h: list(b),
}


def _calls(
    convention: BoxConvention = BoxConvention.XYXY_1000,
    *,
    keep: dict[str, int] | None = None,
    noise: dict[str, float] | None = None,
    flip_one: bool = False,
    field: BoxField = BoxField.BBOX_2D,
) -> list[ProbeCall]:
    """What a model writing ``convention`` would answer, damaged on purpose."""
    rng = random.Random(7)
    calls = []
    for scene, _, truth in RENDERED:
        raws = []
        for i, box in enumerate(truth[: (keep or {}).get(scene.name, len(truth))]):
            sigma = (noise or {}).get(scene.name, 0.0)
            jittered = tuple(v + rng.gauss(0, sigma) for v in box)
            written = BoxConvention.YXYX_1000 if flip_one and i == 0 else convention
            raws.append([round(v, 3) for v in ENCODE[written](jittered, scene.width, scene.height)])
        calls.append(
            ProbeCall(scene=scene.name, box_field=field, width=scene.width, height=scene.height,
                      truth=[list(t) for t in truth], raw_boxes=raws)
        )  # fmt: skip
    return calls


def test_the_scenes_are_drawn_where_they_were_designed() -> None:
    for scene, jpeg, truth in RENDERED:
        assert jpeg[:2] == b"\xff\xd8"
        for obj, box in zip(scene.objects, truth, strict=True):
            assert iou(obj.target, box) > 0.75  # the truth is the drawn mask, not the target


@pytest.mark.parametrize(
    "convention", [BoxConvention.XYXY_1000, BoxConvention.YXYX_1000, BoxConvention.XYXY_PX]
)
def test_a_clean_model_is_recognised(convention: BoxConvention) -> None:
    result = decide(_calls(convention), BoxField.BBOX_2D)
    assert result.enabled
    assert result.convention == convention
    assert result.mean_iou is not None
    assert result.mean_iou > 0.99
    assert result.margin is not None
    assert result.margin > 0.5  # the wrong ones score ~0.2


def test_small_noise_still_passes() -> None:
    noisy = _calls(noise={"landscape": 0.02, "portrait": 0.02})
    assert decide(noisy, BoxField.BBOX_2D).enabled


@pytest.mark.parametrize(
    ("calls", "reason"),
    [
        (_calls(keep={"portrait": 1}), "portrait"),  # one orientation fails
        (_calls(noise={"portrait": 0.10}), "portrait"),  # garbage portrait boxes
        (_calls(keep={"portrait": 2}), "portrait"),
        (_calls(keep={"landscape": 3, "portrait": 3}), "manqués"),  # 3 of 5 found
        (_calls(flip_one=True), "incohérent"),  # 1 box in 5 in another order
        (_calls(BoxConvention.XYXY_UNIT), "entre 0 et 1"),
    ],
)
def test_holes_of_the_first_rule_are_closed(calls: list[ProbeCall], reason: str) -> None:
    result = decide(calls, BoxField.BBOX_2D)
    assert not result.enabled
    assert reason in (result.reason or "")


def test_truncated_answers_disable_positions_only() -> None:
    calls = [c.model_copy(update={"truncated": True, "raw_boxes": []}) for c in _calls()]
    result = decide(calls, BoxField.BBOX_2D)
    assert not result.enabled
    assert "tronquées" in (result.reason or "")


def test_the_variant_matching_the_field_order_wins() -> None:
    y_first_in_x_field = decide(_calls(BoxConvention.YXYX_1000), BoxField.BBOX_2D)
    y_first_in_y_field = decide(
        _calls(BoxConvention.YXYX_1000, field=BoxField.BOX_2D), BoxField.BOX_2D
    )
    chosen = pick(y_first_in_x_field, y_first_in_y_field)
    assert chosen.box_field == BoxField.BOX_2D
    assert chosen.convention == BoxConvention.YXYX_1000
    assert pick(decide(_calls(), BoxField.BBOX_2D), None).box_field == BoxField.BBOX_2D


def test_priors_and_production_reading() -> None:
    assert prior_for("qwen/qwen3-vl-4b", "qwen3vl") == (
        BoxConvention.XYXY_1000,
        BoxField.BBOX_2D,
    )
    assert prior_for("google/gemma-4-12b", "gemma4") is None
    assert decode(BoxConvention.XYWH_1000, [100, 200, 300, 400], 1024, 576) == pytest.approx(
        (0.1, 0.2, 0.4, 0.6)
    )
    answer = Grounding(
        beings=[Being(label="chat", category=BeingCategory.MAMMAL, main=True,
                      bbox_2d=[512, 144, 768, 432])]
    )  # fmt: skip
    [cat] = to_detections(answer, BoxConvention.XYXY_PX, 1024, 576)  # pixels of the sent image
    assert cat.box == Box.of((0.5, 0.25, 0.75, 0.75))
