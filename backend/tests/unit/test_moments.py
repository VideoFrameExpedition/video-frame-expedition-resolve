"""Which part of a block a clip shows: the best-scored stretch, not the seconds
around the sharpest keyframe. Cases rebuilt from real rushes that went wrong."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from hypothesis import given
from hypothesis import strategies as st

from vfe_vision.domain.editing import (
    CLIP_S,
    SCENERY_ONLY,
    THING,
    UNDESCRIBED,
    best_frame,
    best_window,
    clip_for,
    is_scenery,
    moment_scores,
    subject_score,
)
from vfe_vision.domain.synthesis_input import Block, Box, Frame, Picture, Video


def frame(
    idx: int,
    t: float,
    subjects: Sequence[str] = (),
    *,
    issues: Sequence[str] = (),
    sharpness: float = 100.0,
    described: bool = True,
) -> Frame:
    data = {
        "caption": " ".join(subjects) or "vide",
        "subjects": [{"label": s, "is_main": True, "description": ""} for s in subjects],
        "quality_issues": list(issues),
        "editing_value": [],
    }
    return Frame(idx, f"k{idx}", t, 0, sharpness, {}, data if described else None)


def block(start: float, end: float, frames: Sequence[Frame]) -> Block:
    return Block(1, start, end, (0,), tuple(frames), 0.0, ())


def video(duration: float) -> Video:
    return Video(
        id="v", filename="v.mp4", duration=duration, orientation="horizontal",
        capture_local=None, place_label=None, place_feature=None, light_phase=None,
        day_part=None, weather=None, presence={}, heard=(), instruments=(),
        transcript_language=None, segments=(), silences=(), shots=(), frames=(),
    )  # fmt: skip


def picture(
    duration: float,
    *,
    motion: Mapping[float, float] | None = None,
    luma: Mapping[float, float] | None = None,
    beings: Mapping[str, tuple[Box, ...]] | None = None,
    base_motion: float = 0.0,
) -> Picture:
    """2 Hz signals over ``duration``: ``motion``/``luma`` override from a time on (sorted)."""
    ts = tuple(i / 2 for i in range(int(duration * 2) + 1))

    def at(overrides: Mapping[float, float] | None, t: float, default: float) -> float:
        value = default
        for since, v in sorted((overrides or {}).items()):
            if t >= since:
                value = v
        return value

    return Picture(
        hz=2.0,
        t=ts,
        motion=tuple(at(motion, t, base_motion) for t in ts),
        luma=tuple(at(luma, t, 0.55) for t in ts),
        beings=dict(beings or {}),
    )


# ---------------------------------------------------------------- scenery
def test_labels_naming_the_setting_or_a_body_part_are_no_subject() -> None:
    for label in ("toit en briques", "Feuilles vertes", "green leaves", "ciel nuageux", "main humaine",
                  "jambe", "fleurs de lavande", "arbres"):  # fmt: skip
        assert is_scenery(label), label
    for label in ("avion militaire", "oiseaux", "chat noir", "abeille", "DJ", "écran d'ordinateur"):
        assert not is_scenery(label), label


def test_a_living_being_counts_more_than_a_thing_and_a_thing_more_than_scenery() -> None:
    bee = frame(0, 0.0, ["lavande"])
    boxed = Picture(beings={"k0": ((0.3, 0.3, 0.7, 0.7),)})
    assert subject_score(bee, boxed) > subject_score(bee, None) == THING
    assert subject_score(frame(1, 0.0, ["toit", "ciel"]), None) == SCENERY_ONLY
    assert subject_score(frame(2, 0.0, []), None) == SCENERY_ONLY
    assert subject_score(frame(3, 0.0, described=False), None) == UNDESCRIBED


def test_more_beings_bigger_and_inside_the_frame_score_higher() -> None:
    f = frame(0, 0.0, ["oiseau"])
    one = Picture(beings={"k0": ((0.4, 0.4, 0.6, 0.6),)})
    four = Picture(beings={"k0": ((0.4, 0.4, 0.6, 0.6),) * 4})
    tiny = Picture(beings={"k0": ((0.5, 0.5, 0.51, 0.51),)})
    leaving = Picture(beings={"k0": ((0.0, 0.4, 0.2, 0.6),)})
    assert subject_score(f, four) > subject_score(f, one) > subject_score(f, tiny)
    assert subject_score(f, leaving) < subject_score(f, one)


# ---------------------------------------------------------------- where in the block
def test_the_plane_not_the_leaves_once_it_has_flown_past() -> None:
    """20260721_162947_1: the plane from 4 s to 14 s, then leaves filling the frame (sharpest by
    far) as the phone came down; the old rule took 13.4–19.4."""
    frames = [frame(0, 0.0), frame(1, 2.0)]
    frames += [frame(i, t, ["avion"]) for i, t in enumerate((4.0, 6.1, 8.1, 10.1, 12.2, 14.2), 2)]
    frames += [frame(8, 16.2, ["feuilles", "ciel"], sharpness=15.0),
               frame(9, 18.3, ["arbuste"], sharpness=261.0)]  # fmt: skip
    b = block(0.0, 19.4, frames)
    pic = picture(19.4, motion={18.5: 0.46, 19.0: 0.05})
    start, end = best_window(video(19.4), b, pic, CLIP_S)
    assert start >= 4.0
    assert end <= 15.0


def test_the_birds_not_the_roofs() -> None:
    """20260721_162429: roofs named main at most keyframes, birds at 6–10 s."""
    frames = [frame(i, t, ["toit", "maison"]) for i, t in enumerate((0.0, 2.0, 4.1))]
    frames += [
        frame(3, 6.1, ["oiseau"]),
        frame(4, 8.1, ["oiseau"] * 4),
        frame(5, 10.2, ["oiseaux", "toit"]),
    ]
    frames += [frame(6, 12.2, ["toit"]), frame(7, 14.2, ["toit", "arbre"])]
    birds = {"k3": ((0.4, 0.4, 0.42, 0.42),) * 3, "k4": ((0.4, 0.3, 0.42, 0.32),) * 4}
    b = block(0.0, 15.2, frames)
    start, end = best_window(video(15.2), b, picture(15.2, beings=birds), CLIP_S)
    assert start <= 6.1
    assert end >= 10.2


def test_the_end_of_the_recording_is_avoided() -> None:
    """The phone lowered at the end: a jolt of motion and the last second."""
    frames = [frame(i, t, ["chat"]) for i, t in enumerate((0.0, 2.0, 4.1, 8.1, 10.2, 12.2))]
    b = block(0.0, 13.2, frames)
    pic = picture(13.2, motion={12.5: 1.1, 13.0: 1.9}, base_motion=0.3)
    _, end = best_window(video(13.2), b, pic, CLIP_S)
    assert end <= 12.5


def test_a_handheld_macro_only_avoids_its_jolts() -> None:
    """buzz.mp4: the camera follows bees all along (motion ~1–2); only the 10× jolts count, so the
    bees at 2–4 s win over the calmer lavender alone."""
    frames = [frame(0, 0.0, ["lavande"]), frame(1, 2.0, ["abeille"]), frame(2, 4.0, ["abeille"])]
    frames += [frame(i, t, ["lavande"]) for i, t in enumerate((8.0, 10.0, 12.0, 14.0, 16.0), 3)]
    bees = {"k1": ((0.4, 0.4, 0.5, 0.5),), "k2": ((0.4, 0.4, 0.5, 0.5),)}
    pic = picture(18.0, motion={0.0: 1.5, 9.0: 9.0, 10.0: 0.5}, beings=bees)
    start, end = best_window(video(30.0), block(0.0, 18.0, frames), pic, CLIP_S)
    assert start <= 2.0
    assert end >= 4.0


def test_blurred_and_black_moments_are_avoided() -> None:
    frames = [frame(i, t, ["chat"]) for i, t in enumerate((0.0, 2.0, 4.0))]
    frames += [
        frame(i, t, ["chat"], issues=["motion_blur"]) for i, t in enumerate((6.0, 8.0, 10.0), 3)
    ]
    b = block(0.0, 20.0, [*frames, frame(6, 12.0, ["chat"]), frame(7, 14.0, ["chat"])])
    _, end = best_window(video(30.0), b, picture(20.0, luma={12.0: 0.01}), CLIP_S)
    assert end <= 6.5


def test_a_short_block_is_taken_whole_and_edges_snap() -> None:
    b = block(3.0, 7.0, [frame(0, 3.0, ["chat"])])
    assert best_window(video(30.0), b, picture(30.0), CLIP_S) == (3.0, 7.0)
    long = block(0.0, 6.4, [frame(0, 0.0, ["chat"])])
    assert best_window(video(30.0), long, picture(30.0), CLIP_S) == (0.0, 6.4)


def test_ties_go_to_the_earliest_stretch() -> None:
    frames = [frame(i, float(t), ["chat"]) for i, t in enumerate(range(0, 30, 2))]
    assert best_window(video(60.0), block(0.0, 30.0, frames), picture(30.0), CLIP_S) == (0.0, 6.0)


@given(
    start=st.floats(0.0, 50.0),
    length=st.floats(0.5, 40.0),
    target=st.sampled_from([6.0, 8.0, 10.0]),
    jolt=st.floats(0.0, 40.0),
)
def test_the_window_stays_in_its_block_and_keeps_its_length(
    start: float, length: float, target: float, jolt: float
) -> None:
    end = start + length
    frames = [frame(0, start, ["chat"]), frame(1, start + length / 2, ["toit"])]
    b = block(start, end, frames)
    lo, hi = best_window(video(end + 5.0), b, picture(end + 5.0, motion={jolt: 5.0}), target)
    assert start - 1e-9 <= lo <= hi <= end + 1e-9
    if length > target:
        assert target - 1e-6 <= hi - lo <= target + 1.0 + 1e-6  # + up to two snapped edges
    else:
        assert (lo, hi) == (start, end)
    assert all(0.0 <= s <= 1.2 for _, s in moment_scores(video(end + 5.0), b, None))


def test_clip_for_uses_the_picture_facts_and_the_frame_follows_the_clip() -> None:
    frames = [frame(0, 0.0, ["toit"], sharpness=900.0), frame(1, 8.0, ["chat"], sharpness=10.0)]
    frames += [frame(2, 10.0, ["chat"], sharpness=12.0), frame(3, 18.0, ["arbre"], sharpness=800.0)]
    b = block(0.0, 20.0, frames)
    pic = picture(20.0)
    clip = clip_for(video(30.0), b, picture=pic)
    assert clip.picture_in <= 8.0
    assert clip.picture_out >= 10.0
    shown = best_frame(b, pic, (clip.picture_in, clip.picture_out))
    assert shown is not None
    assert shown.keyframe_id in {"k1", "k2"}
    old = clip_for(video(30.0), b)  # without them: around the sharpest keyframe
    assert old.picture_in == 0.0
