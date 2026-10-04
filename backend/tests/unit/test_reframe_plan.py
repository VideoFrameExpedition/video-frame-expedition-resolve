"""Reframing plans: the head-first target, hysteresis, merged pieces, rotations; and
the edit list's versioned names."""

from __future__ import annotations

import pytest

from vfe_vision.domain.edit_list import base_name, clean_props, versioned_name
from vfe_vision.domain.reframe_plan import (
    Geometry,
    PlanFrame,
    PlanOptions,
    plan_item,
    target,
)

VERTICAL = Geometry(2160, 3840, 1920, 1080)  # a phone clip in a 16:9 timeline
BAND = 1215 / 3840  # the crop's height, as a share of the picture's


def _tilt_for(centre: float) -> float:
    """The Tilt that puts the band's centre at ``centre`` (0–1 from the top)."""
    return (centre - 0.5) * 3840 * (1920 / 2160)


def test_a_small_subject_is_kept_whole() -> None:
    frame = PlanFrame(1.0, (0.3, 0.45, 0.6, 0.55))
    [piece] = plan_item([frame, PlanFrame(3.0, (0.3, 0.45, 0.6, 0.55))], VERTICAL, 0.0, 4.0)
    assert piece.frames[0].cov == 1.0
    assert piece.props["ZoomX"] == pytest.approx(3.1605, abs=1e-3)
    assert piece.props["Tilt"] == pytest.approx(_tilt_for(0.5), abs=15)
    assert piece.flags == []


def test_a_big_subject_is_aimed_at_its_top_without_a_head() -> None:
    frames = [PlanFrame(t, (0.1, 0.1, 0.9, 0.9)) for t in (0.5, 2.5, 4.5)]
    [piece] = plan_item(frames, VERTICAL, 0.0, 5.0)
    top = piece.crop.y / 3840
    assert top == pytest.approx(0.1 - 0.1 * BAND, abs=0.02)  # the top of the box, with headroom
    assert piece.props["Tilt"] < 0  # the picture moves down: its top shows
    assert piece.frames[0].body < 0.5  # the belly is not the target


def test_the_head_given_by_the_model_wins_over_the_anchor() -> None:
    # A cat seen from above, its head at the bottom of its box.
    frames = [PlanFrame(t, (0.1, 0.1, 0.9, 0.9), (0.4, 0.78, 0.6, 0.88)) for t in (0.5, 2.5)]
    [piece] = plan_item(frames, VERTICAL, 0.0, 3.0)
    assert piece.frames[0].head == 1.0
    assert piece.props["Tilt"] > 0  # the picture moves up: its lower part shows
    assert "head_cut" not in piece.flags


def test_center_anchor_for_a_subject_seen_from_above() -> None:
    frame = PlanFrame(1.0, (0.1, 0.1, 0.9, 0.9))
    region = target(frame, VERTICAL, "center")
    assert (region[1] + region[3]) / 2 == pytest.approx(0.5 * 3840)


def test_a_subject_that_moves_gets_a_new_crop_halfway() -> None:
    frames = [PlanFrame(0.5, (0.2, 0.05, 0.8, 0.25)), PlanFrame(2.5, (0.2, 0.05, 0.8, 0.25)),
              PlanFrame(4.5, (0.2, 0.75, 0.8, 0.95)), PlanFrame(6.5, (0.2, 0.75, 0.8, 0.95))]  # fmt: skip
    first, second = plan_item(frames, VERTICAL, 0.0, 7.0)
    assert (first.in_s, first.out_s, second.in_s, second.out_s) == (0.0, 3.5, 3.5, 7.0)
    assert first.props["Tilt"] < 0 < second.props["Tilt"]
    assert all(f.cov == 1.0 for f in first.frames + second.frames)


def test_short_pieces_are_merged_rather_than_jumping() -> None:
    frames = [PlanFrame(0.2, (0.2, 0.05, 0.8, 0.25)), PlanFrame(1.2, (0.2, 0.75, 0.8, 0.95)),
              PlanFrame(2.2, (0.2, 0.05, 0.8, 0.25))]  # fmt: skip
    pieces = plan_item(frames, VERTICAL, 0.0, 2.4)
    assert len(pieces) == 1
    assert "low_coverage" in pieces[0].flags  # one crop cannot keep both places: say it


def test_small_moves_are_merged() -> None:
    frames = [PlanFrame(0.5, (0.2, 0.40, 0.8, 0.60)), PlanFrame(4.5, (0.2, 0.43, 0.8, 0.63))]
    assert len(plan_item(frames, VERTICAL, 0.0, 6.0)) == 1


def test_keyframes_outside_the_range_only_when_needed() -> None:
    before = PlanFrame(-1.5, (0.2, 0.75, 0.8, 0.95))  # the one the application adds
    inside = [PlanFrame(0.5, (0.2, 0.05, 0.8, 0.25)), PlanFrame(2.5, (0.2, 0.05, 0.8, 0.25))]
    [piece] = plan_item([before, *inside], VERTICAL, 0.0, 3.0)
    assert [f.t_s for f in piece.frames] == [0.5, 2.5]
    [alone] = plan_item([before], VERTICAL, 0.0, 3.0)
    assert [f.t_s for f in alone.frames] == [-1.5]


def test_upside_down_clip_turns_and_mirrors_the_values() -> None:
    frames = [PlanFrame(t, (0.1, 0.1, 0.9, 0.9)) for t in (0.5, 2.5)]
    upright = plan_item(frames, VERTICAL, 0.0, 3.0, PlanOptions(anchor="bottom"))[0]
    turned = plan_item(frames, VERTICAL, 0.0, 3.0, PlanOptions(rotation=180))[0]
    assert turned.props["RotationAngle"] == 180.0
    assert turned.props["Tilt"] == -upright.props["Tilt"]  # the head: the picture's bottom
    assert "rotated_180" in turned.flags


def test_sideways_clip_fills_the_timeline_by_zoom() -> None:
    [piece] = plan_item([], VERTICAL, 0.0, 3.0, PlanOptions(rotation=-90))
    assert piece.props == {"ZoomX": 1.7778, "ZoomY": 1.7778, "Pan": 0.0, "Tilt": 0.0,
                           "RotationAngle": -90.0}  # fmt: skip


def test_no_subject_is_centred_and_flagged() -> None:
    [piece] = plan_item([], VERTICAL, 0.0, 3.0)
    assert piece.props["Tilt"] == 0.0
    assert "no_subject" in piece.flags


def test_same_shape_needs_no_move_and_enlarged_sources_are_flagged() -> None:
    [same] = plan_item([PlanFrame(1.0, (0.2, 0.2, 0.4, 0.4))], Geometry(3840, 2160, 1920, 1080),
                       0.0, 2.0)  # fmt: skip
    assert same.props == {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0,
                          "RotationAngle": 0.0}  # fmt: skip
    [small] = plan_item([], Geometry(720, 1280, 1920, 1080), 0.0, 2.0)
    assert any(flag.startswith("upscale_") for flag in small.flags)


def test_versioned_names() -> None:
    names = ["Timeline 1", "Gros plans - vfe v3", "Gros plans - vfe v4 (2)", "Autre - vfe v9"]
    assert versioned_name("Gros plans", names) == "Gros plans - vfe v5"
    assert versioned_name("Gros plans - vfe v2", names) == "Gros plans - vfe v5"
    assert versioned_name("Nouveau", names) == "Nouveau - vfe v1"
    assert versioned_name("  ", []) == "Montage - vfe v1"
    assert base_name("Chats - vfe v12") == "Chats"


def test_transform_values_are_cleaned() -> None:
    assert clean_props({"ZoomX": 2.0, "Tilt": -10, "Scaling": 3}) == {
        "ZoomX": 2.0, "Tilt": -10.0, "ZoomY": 2.0,
    }  # fmt: skip
    assert clean_props({"Pan": float("nan")}) is None
    assert clean_props(None) is None
