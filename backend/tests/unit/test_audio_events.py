"""YAMNet post-processing: ontology rollup, smoothing, segments, summaries, instruments, shots.

Everything runs on synthetic score matrices and a tiny ontology that mirrors the real AudioSet
structure (same mids, same multi-parent classes), so no model or download is needed.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra import numpy as hnp

from vfe_vision.domain.audio_events import (
    CATEGORIES,
    CATEGORY_LABELS,
    HOP_S,
    ISSUE_LABELS,
    AudioScene,
    Category,
    Environment,
    Rollup,
    analyze,
    build_rollup,
    frame_cells,
    hysteresis,
    median_filter,
    shot_labels,
)


def _node(mid: str, name: str, *children: str) -> dict[str, Any]:
    return {"id": mid, "name": name, "child_ids": list(children), "restrictions": []}


ONTOLOGY = [
    _node("/m/0dgw9r", "Human sounds", "/m/09l8g", "/t/dd00012", "/m/01w250"),
    _node("/m/09l8g", "Human voice", "/m/09x0r", "/m/015lz1"),
    _node("/m/09x0r", "Speech"),
    _node("/m/015lz1", "Singing", "/m/0l14jd"),
    _node("/m/01w250", "Whistling"),
    _node("/t/dd00012", "Human group actions", "/m/03qtwd", "/m/07qfr4h"),
    _node("/m/03qtwd", "Crowd"),
    _node("/m/04rlf", "Music", "/m/04szw"),
    _node("/m/04szw", "Musical instrument", "/m/085jw", "/m/0fx80y", "/m/0395lw", "/m/0l14jd"),
    _node("/m/085jw", "Wind instrument, woodwind instrument", "/m/0l14j_"),
    _node("/m/0l14j_", "Flute"),
    _node("/m/0fx80y", "Plucked string instrument", "/m/0342h"),
    _node("/m/0342h", "Guitar"),
    _node("/m/0l14jd", "Choir"),
    _node("/m/0395lw", "Bell", "/m/03w41f", "/m/027m70_"),
    _node("/m/03w41f", "Church bell"),
    _node("/m/027m70_", "Jingle bell"),
    _node("/m/0jbk", "Animal", "/m/068hy"),
    _node("/m/068hy", "Domestic animals, pets", "/m/01yrx", "/m/0bt9lr"),
    _node("/m/01yrx", "Cat", "/m/07rjwbb"),
    _node("/m/0bt9lr", "Dog"),
    _node("/m/07rjwbb", "Hiss"),
    _node("/m/059j3w", "Natural sounds", "/m/03m9d0z", "/m/0838f"),
    _node("/m/03m9d0z", "Wind", "/t/dd00092"),
    _node("/t/dd00092", "Wind noise (microphone)"),
    _node("/m/0838f", "Water", "/m/06wzb"),
    _node("/m/06wzb", "Steam", "/m/07rjwbb"),  # Hiss: Cat and Steam, as in AudioSet
    _node("/t/dd00041", "Sounds of things", "/m/07yv9", "/m/0395lw", "/m/02mk9"),
    _node("/m/07yv9", "Vehicle", "/m/0k4j"),
    _node("/m/0k4j", "Car"),
    _node("/m/02mk9", "Engine", "/m/01j4z9"),
    _node("/m/01j4z9", "Chainsaw"),
    _node("/t/dd00098", "Source-ambiguous sounds", "/m/028v0c"),
    _node("/m/028v0c", "Silence"),
    _node("/t/dd00123", "Channel, environment and background", "/t/dd00093", "/m/096m7z",
          "/m/07bm98"),
    _node("/t/dd00093", "Acoustic environment", "/t/dd00125", "/t/dd00129"),
    _node("/t/dd00125", "Inside, small room"),
    _node("/t/dd00129", "Outside, rural or natural"),
    _node("/m/096m7z", "Noise", "/m/07qfr4h", "/m/06xkwv"),
    _node("/m/06xkwv", "Mains hum"),
    _node("/m/07qfr4h", "Hubbub, speech noise, speech babble"),
    _node("/m/07bm98", "Sound reproduction", "/m/07c52"),
    _node("/m/07c52", "Television"),
]  # fmt: skip

CLASSES = [
    ("/m/09x0r", "Speech"),
    ("/m/015lz1", "Singing"),
    ("/m/01w250", "Whistling"),
    ("/m/03qtwd", "Crowd"),
    ("/m/07qfr4h", "Hubbub, speech noise, speech babble"),
    ("/m/04rlf", "Music"),
    ("/m/04szw", "Musical instrument"),
    ("/m/085jw", "Wind instrument, woodwind instrument"),
    ("/m/0l14j_", "Flute"),
    ("/m/0fx80y", "Plucked string instrument"),
    ("/m/0342h", "Guitar"),
    ("/m/0l14jd", "Choir"),
    ("/m/0395lw", "Bell"),
    ("/m/03w41f", "Church bell"),
    ("/m/027m70_", "Jingle bell"),
    ("/m/0jbk", "Animal"),
    ("/m/01yrx", "Cat"),
    ("/m/0bt9lr", "Dog"),
    ("/m/07rjwbb", "Hiss"),
    ("/m/03m9d0z", "Wind"),
    ("/t/dd00092", "Wind noise (microphone)"),
    ("/m/0838f", "Water"),
    ("/m/07yv9", "Vehicle"),
    ("/m/0k4j", "Car"),
    ("/m/01j4z9", "Chainsaw"),
    ("/m/028v0c", "Silence"),
    ("/t/dd00125", "Inside, small room"),
    ("/t/dd00129", "Outside, rural or natural"),
    ("/m/06xkwv", "Mains hum"),
    ("/m/07c52", "Television"),
    ("/x/not-in-ontology", "Mystery"),
]


def _class_map_csv() -> str:
    lines = ["index,mid,display_name"]
    lines += [f'{i},{mid},"{name}"' for i, (mid, name) in enumerate(CLASSES)]
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def rollup() -> Rollup:
    return build_rollup(_class_map_csv(), json.dumps(ONTOLOGY))


def _scores(rollup: Rollup, frames: int, spans: dict[str, list[tuple[int, int, float]]]) -> Any:
    matrix = np.zeros((frames, len(rollup)), dtype=np.float32)
    for name, runs in spans.items():
        for first, stop, value in runs:
            matrix[first:stop, rollup.index[name]] = value
    return matrix


def _duration(frames: int) -> float:
    """A duration whose last cell ends exactly on the natural cell edge."""
    return HOP_S * frames + HOP_S


# ------------------------------------------------------------------ rollup
def test_rollup_applies_priority_overrides_and_multiple_parents(rollup: Rollup) -> None:
    category = dict(zip(rollup.names, rollup.categories, strict=True))
    expected = {
        "Speech": Category.SPEECH,
        "Singing": Category.MUSIC,  # Human voice, but music comes first
        "Whistling": Category.SPEECH,  # override (only under Human sounds)
        "Crowd": Category.CROWD,
        "Hubbub, speech noise, speech babble": Category.CROWD,  # Human group actions + Noise
        "Musical instrument": Category.MUSIC,
        "Flute": Category.MUSIC,
        "Choir": Category.MUSIC,
        "Bell": Category.OTHER,  # override: Musical instrument + Sounds of things
        "Church bell": Category.OTHER,
        "Jingle bell": Category.MUSIC,
        "Cat": Category.NATURE,
        "Hiss": Category.OTHER,  # override: would be water through Steam
        "Wind": Category.WIND,
        "Wind noise (microphone)": Category.WIND,
        "Water": Category.WATER,
        "Car": Category.VEHICLES,
        "Chainsaw": Category.TOOLS,  # override: would be vehicles through Engine
        "Silence": Category.SILENCE,
        "Outside, rural or natural": Category.NATURE,
        "Mystery": Category.OTHER,  # unknown to the ontology
    }
    assert {name: category[name] for name in expected} == expected


def test_context_classes_stay_out_of_the_categories(rollup: Rollup) -> None:
    context = {name for name, ctx in zip(rollup.names, rollup.context, strict=True) if ctx}
    assert context == {"Inside, small room", "Mains hum", "Television"}
    all_members = {int(i) for members in rollup.members.values() for i in members}
    assert rollup.index["Mains hum"] not in all_members
    assert rollup.index["Hubbub, speech noise, speech babble"] in rollup.members[Category.CROWD]
    assert rollup.index["Outside, rural or natural"] in rollup.members[Category.NATURE]


def test_ancestors_follow_every_parent(rollup: Rollup) -> None:
    hiss = rollup.ancestors[rollup.index["Hiss"]]
    assert {"/m/01yrx", "/m/068hy", "/m/0jbk", "/m/06wzb", "/m/0838f", "/m/059j3w"} <= hiss
    choir = rollup.ancestors[rollup.index["Choir"]]
    assert {"/m/015lz1", "/m/09l8g", "/m/04szw", "/m/04rlf"} <= choir
    assert "/m/0l14jd" not in choir  # strict ancestors
    assert rollup.ancestors[rollup.index["Mystery"]] == frozenset()
    assert rollup.is_ancestor(rollup.index["Musical instrument"], of=rollup.index["Flute"])


def test_instrument_and_event_classes(rollup: Rollup) -> None:
    instruments = {rollup.names[i] for i in rollup.instruments}
    assert instruments == {
        "Wind instrument, woodwind instrument", "Flute", "Plucked string instrument",
        "Guitar", "Choir", "Jingle bell",
    }  # fmt: skip
    events = {rollup.names[i] for i in rollup.event_classes}
    assert {"Dog", "Cat", "Car", "Chainsaw", "Hiss", "Bell", "Wind"} <= events
    assert not events & {"Speech", "Music", "Flute", "Silence", "Animal", "Mains hum", "Water"}


def test_malformed_inputs_are_rejected() -> None:
    ontology = json.dumps(ONTOLOGY)
    with pytest.raises(ValueError, match="illisible"):
        build_rollup("index,name\n0,Speech\n", ontology)
    with pytest.raises(ValueError, match="incomplète"):
        build_rollup("index,mid,display_name\n1,/m/09x0r,Speech\n", ontology)
    with pytest.raises(ValueError, match="Ontologie"):
        build_rollup(_class_map_csv(), "{not json")
    with pytest.raises(ValueError, match="Ontologie"):
        build_rollup(_class_map_csv(), json.dumps({"id": "/m/1"}))


# ------------------------------------------------------------------ signal helpers
def test_frame_cells_tile_the_duration() -> None:
    start, end = frame_cells(6, 3.0)
    assert start.tolist() == pytest.approx([0.0, 0.72, 1.2, 1.68, 2.16, 2.64])
    assert end.tolist() == pytest.approx([0.72, 1.2, 1.68, 2.16, 2.64, 3.0])
    assert float((end - start).sum()) == pytest.approx(3.0)
    start, end = frame_cells(8, 2.0)  # frames past the end get empty cells
    assert (end - start).min() == 0.0
    assert float((end - start).sum()) == pytest.approx(2.0)
    empty = frame_cells(0, 5.0)
    assert len(empty[0]) == len(empty[1]) == 0


def test_median_and_hysteresis() -> None:
    assert median_filter([0, 0, 0.9, 0, 0]).tolist() == [0, 0, 0, 0, 0]
    assert median_filter([0.9, 0.9, 0, 0.9]).tolist() == [0.9, 0.9, 0.9, 0.9]
    two_d = median_filter(np.array([[0.0, 1.0], [1.0, 1.0], [0.0, 0.0]]))
    assert two_d.tolist() == [[0.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
    values = [0.1, 0.6, 0.35, 0.31, 0.29, 0.45, 0.55]
    assert hysteresis(values, 0.5, 0.3).tolist() == [False, True, True, True, False, False, True]


# ------------------------------------------------------------------ analysis
def test_speech_block_presence_dominance_and_segments(rollup: Rollup) -> None:
    scores = _scores(rollup, 20, {"Speech": [(0, 10, 0.9)]})
    scene = analyze(scores, rollup, duration_s=_duration(20))
    assert scene.frames == 20
    assert scene.presence == {Category.SPEECH: 0.5}
    assert scene.dominant == {Category.SPEECH: 0.5, Category.OTHER: 0.5}
    assert scene.speech_s == pytest.approx(5.04)
    assert scene.music_s == 0.0
    assert scene.segments == [(0.0, 5.04, Category.SPEECH)]
    assert scene.main_category is Category.SPEECH


def test_hysteresis_keeps_a_category_through_dips(rollup: Rollup) -> None:
    held = _scores(rollup, 20, {"Music": [(0, 10, 0.5), (10, 20, 0.25)]})
    assert analyze(held, rollup, duration_s=_duration(20)).presence == {Category.MUSIC: 1.0}
    dropped = _scores(rollup, 20, {"Music": [(0, 10, 0.5), (10, 20, 0.15)]})
    assert analyze(dropped, rollup, duration_s=_duration(20)).presence == {Category.MUSIC: 0.5}
    never = _scores(rollup, 20, {"Music": [(0, 20, 0.35)]})  # above off, never reaches on
    assert analyze(never, rollup, duration_s=_duration(20)).presence == {}


def test_gaps_up_to_one_second_are_merged(rollup: Rollup) -> None:
    two_frames = _scores(rollup, 30, {"Music": [(0, 10, 0.9), (12, 22, 0.9)]})
    scene = analyze(two_frames, rollup, duration_s=_duration(30))
    assert [s[:2] for s in scene.segments] == [(0.0, 10.8)]
    three_frames = _scores(rollup, 30, {"Music": [(0, 10, 0.9), (13, 23, 0.9)]})
    scene = analyze(three_frames, rollup, duration_s=_duration(30))
    assert [s[:2] for s in scene.segments] == [(0.0, 5.04), (6.48, 11.28)]


def test_single_frame_blips_are_smoothed_away(rollup: Rollup) -> None:
    scores = _scores(rollup, 20, {"Dog": [(7, 8, 0.95)]})
    scene = analyze(scores, rollup, duration_s=_duration(20))
    assert Category.NATURE not in scene.presence
    assert [e.label for e in scene.events] == ["Dog"]  # events use the raw scores


def test_minimum_duration_depends_on_the_category(rollup: Rollup) -> None:
    # The last two cells are cut by the end of the file: 0.8 s in all.
    scores = _scores(rollup, 9, {"Dog": [(7, 9, 0.9)], "Speech": [(7, 9, 0.9)]})
    scene = analyze(scores, rollup, duration_s=4.4)
    assert scene.segments == [(3.6, 4.4, Category.SPEECH)]  # nature needs 0.95 s


def test_presence_is_multi_label_and_dominance_sums_to_one(rollup: Rollup) -> None:
    scores = _scores(rollup, 20, {"Speech": [(0, 20, 0.9)], "Music": [(0, 10, 0.5), (10, 20, 0.8)]})
    scene = analyze(scores, rollup, duration_s=_duration(20))
    assert scene.presence == {Category.SPEECH: 1.0, Category.MUSIC: 1.0}
    # score / on-threshold: speech 1.8 against music 1.25, then 2.0
    assert scene.dominant == {Category.SPEECH: 0.5, Category.MUSIC: 0.5}
    assert sum(scene.dominant.values()) == pytest.approx(1.0)


def test_level_based_silence_and_low_level_flag(rollup: Rollup) -> None:
    scores = _scores(rollup, 10, {})
    duration = _duration(10)
    silent = analyze(scores, rollup, duration_s=duration, rms_db=np.full(10, -70.0))
    assert silent.presence == {Category.SILENCE: 1.0}
    assert silent.dominant == {Category.SILENCE: 1.0}
    assert silent.issues == {"low_level": 1.0}
    assert set(silent.curves[Category.SILENCE]) == {100}
    unknown = analyze(scores, rollup, duration_s=duration)
    assert unknown.presence == {}
    assert unknown.dominant == {Category.OTHER: 1.0}
    assert unknown.issues == {}
    quiet = analyze(scores, rollup, duration_s=duration, rms_db=np.full(10, -50.0))
    assert quiet.presence == {}
    assert quiet.issues == {"low_level": 1.0}
    short = analyze(scores, rollup, duration_s=duration, rms_db=np.full(4, -70.0))
    assert short.presence[Category.SILENCE] < 0.5  # frames without a level count as loud


def test_events_are_specific_raw_peaks(rollup: Rollup) -> None:
    scores = _scores(
        rollup,
        20,
        {
            "Dog": [(3, 5, 0.8)],
            "Car": [(10, 11, 0.7)],
            "Speech": [(0, 20, 0.9)],
            "Musical instrument": [(0, 20, 0.9)],
            "Flute": [(0, 20, 0.9)],
            "Mains hum": [(0, 20, 0.9)],
            "Vehicle": [(10, 11, 0.9)],
            "Cat": [(15, 16, 0.49)],
        },
    )
    events = analyze(scores, rollup, duration_s=_duration(20)).events
    assert [(e.label, e.start_s, e.end_s, e.score) for e in events] == [
        ("Dog", 1.68, 2.64, 0.8),
        ("Car", 5.04, 5.52, 0.7),
    ]
    assert events[0].category is Category.NATURE
    assert events[0].class_index == rollup.index["Dog"]


def test_events_keep_the_twelve_strongest_in_time_order(rollup: Rollup) -> None:
    runs = {"Dog": [(4 * i, 4 * i + 1, 0.50 + 0.01 * i) for i in range(15)]}
    events = analyze(_scores(rollup, 60, runs), rollup, duration_s=_duration(60)).events
    assert len(events) == 12
    assert min(e.score for e in events) == pytest.approx(0.53)
    assert [e.start_s for e in events] == sorted(e.start_s for e in events)


def test_overlapping_ancestor_events_give_way_to_the_specific_one(rollup: Rollup) -> None:
    scores = _scores(
        rollup,
        30,
        {
            "Cat": [(3, 6, 0.9), (20, 22, 0.8)],  # Cat is an ancestor of Hiss
            "Hiss": [(4, 6, 0.7)],
            "Bell": [(10, 12, 0.9)],
            "Church bell": [(11, 13, 0.6)],
        },
    )
    events = analyze(scores, rollup, duration_s=_duration(30)).events
    assert [(e.label, e.start_s) for e in events] == [
        ("Hiss", 2.16),
        ("Church bell", 5.52),
        ("Cat", 9.84),
    ]


def test_top_labels_skip_context_and_ontology_ancestors(rollup: Rollup) -> None:
    scores = _scores(
        rollup,
        20,
        {
            "Music": [(0, 20, 0.9)],
            "Musical instrument": [(0, 20, 0.5)],
            "Wind instrument, woodwind instrument": [(0, 20, 0.4)],
            "Flute": [(0, 20, 0.3)],
            "Inside, small room": [(0, 20, 0.8)],
            "Dog": [(0, 20, 0.05)],
        },
    )
    scene = analyze(scores, rollup, duration_s=_duration(20))
    assert scene.top_labels == [("Music", 0.9), ("Flute", 0.3)]


def test_instruments_heard(rollup: Rollup) -> None:
    scores = _scores(
        rollup,
        30,
        {
            "Music": [(0, 30, 0.9)],
            "Flute": [(5, 15, 0.6)],
            "Wind instrument, woodwind instrument": [(5, 15, 0.5)],  # covered by Flute
            "Plucked string instrument": [(5, 13, 0.4)],  # Guitar alone does not cover it
            "Guitar": [(5, 8, 0.3)],
            "Jingle bell": [(20, 21, 0.9)],  # one frame: too short
            "Bell": [(0, 30, 0.9)],  # ambience, not an instrument
            "Musical instrument": [(0, 30, 0.9)],  # too generic
            "Choir": [(20, 30, 0.1)],  # below the score floor
        },
    )
    scene = analyze(scores, rollup, duration_s=_duration(30))
    assert scene.instruments == [
        ("Flute", 4.8, 0.6),
        ("Plucked string instrument", 3.84, 0.4),
        ("Guitar", 1.44, 0.3),
    ]


def test_curves_are_one_value_per_second(rollup: Rollup) -> None:
    scores = _scores(rollup, 20, {"Speech": [(0, 10, 0.9)]})
    scene = analyze(scores, rollup, duration_s=_duration(20))
    assert set(scene.curves) == set(CATEGORIES)
    speech = scene.curves[Category.SPEECH]
    assert len(speech) == math.ceil(_duration(20))
    assert speech[:6] == [90] * 6  # the frame 9 cell ends at 5.04 s
    assert speech[6:] == [0] * 5
    assert all(0 <= v <= 100 for curve in scene.curves.values() for v in curve)


def test_environment_and_issues(rollup: Rollup) -> None:
    scores = _scores(
        rollup,
        20,
        {"Inside, small room": [(0, 20, 0.5)], "Mains hum": [(5, 10, 0.9)]},
    )
    scene = analyze(scores, rollup, duration_s=_duration(20))
    assert scene.environment is Environment.INDOOR_SMALL
    assert scene.issues == {"hum": pytest.approx(0.2381, abs=1e-4)}
    blip = _scores(rollup, 20, {"Inside, small room": [(0, 20, 0.05)], "Mains hum": [(5, 6, 0.9)]})
    scene = analyze(blip, rollup, duration_s=_duration(20))
    assert scene.environment is None
    assert scene.issues == {}


def test_shot_labels_are_overlap_weighted(rollup: Rollup) -> None:
    scores = _scores(rollup, 20, {"Dog": [(0, 5, 0.8)], "Car": [(10, 20, 0.6)]})
    labels = shot_labels(scores, rollup, [(0.0, 2.0), (6.0, 9.0), (2.0, 3.5), (50.0, 60.0)])
    assert labels[0] == [("Dog", 0.8)]
    assert labels[1] == [("Car", 0.6)]
    # patches 3-7 overlap [2.0, 3.5] by 0.40, 0.88, 0.96, 0.62, 0.14 s; 3 and 4 hear the dog:
    # 0.8 × 1.28 / 3.0
    assert labels[2] == [("Dog", 0.34)]
    assert labels[3] == []


def test_shot_labels_keep_three_after_dedupe(rollup: Rollup) -> None:
    scores = _scores(
        rollup,
        10,
        {
            "Music": [(0, 10, 0.9)],
            "Musical instrument": [(0, 10, 0.6)],
            "Wind instrument, woodwind instrument": [(0, 10, 0.5)],
            "Flute": [(0, 10, 0.4)],
            "Dog": [(0, 10, 0.3)],
            "Car": [(0, 10, 0.2)],
        },
    )
    assert shot_labels(scores, rollup, [(0.0, 5.0)]) == [
        [("Music", 0.9), ("Flute", 0.4), ("Dog", 0.3)]
    ]


def test_invalid_and_empty_inputs(rollup: Rollup) -> None:
    with pytest.raises(ValueError, match="classes attendues"):
        analyze(np.zeros((5, 3)), rollup, duration_s=3.0)
    with pytest.raises(ValueError, match="classes attendues"):
        shot_labels(np.zeros(5), rollup, [(0.0, 1.0)])
    empty = analyze(np.zeros((0, len(rollup))), rollup, duration_s=4.0)
    assert empty == AudioScene.empty(4.0)
    assert empty.main_category is None
    nan = np.full((6, len(rollup)), np.nan, dtype=np.float32)
    assert analyze(nan, rollup, duration_s=3.0).dominant == {Category.OTHER: 1.0}


def test_to_dict_is_plain_json(rollup: Rollup) -> None:
    scores = _scores(
        rollup, 20, {"Speech": [(0, 10, 0.9)], "Dog": [(12, 14, 0.8)], "Flute": [(0, 20, 0.5)]}
    )
    data = analyze(scores, rollup, duration_s=_duration(20), rms_db=np.full(20, -20.0)).to_dict()
    text = json.dumps(data)
    back = json.loads(text)
    assert back["presence"]["speech"] == 0.5
    assert back["segments"][0] == [0.0, 5.04, "speech"]
    assert back["events"][0]["label"] == "Dog"
    assert back["instruments"][0][0] == "Flute"
    assert set(back["curves"]["series"]) == {c.value for c in CATEGORIES}
    assert all(type(key) is str for key in data["presence"])


def test_labels_are_french_and_complete() -> None:
    assert set(CATEGORY_LABELS) == set(CATEGORIES)
    assert CATEGORY_LABELS[Category.SPEECH] == "Parole"
    assert {"wind_noise", "hum", "noise", "low_level"} <= set(ISSUE_LABELS)


@settings(max_examples=40, deadline=None)
@given(
    data=st.data(),
    frames=st.integers(min_value=1, max_value=60),
    extra=st.floats(min_value=0.0, max_value=0.47),
)
def test_summaries_stay_consistent(
    rollup: Rollup, data: st.DataObject, frames: int, extra: float
) -> None:
    scores = data.draw(
        hnp.arrays(
            np.float32,
            (frames, len(rollup)),
            elements=st.floats(0.0, 1.0, width=32),
        )
    )
    duration = HOP_S * frames + HOP_S / 2 + extra  # consistent with the frame count
    scene = analyze(scores, rollup, duration_s=duration)
    assert sum(scene.dominant.values()) == pytest.approx(1.0, abs=1e-3)
    assert all(0.0 < share <= 1.0 for share in scene.presence.values())
    for start, end, category in scene.segments:
        assert 0.0 <= start < end <= round(duration, 2) + 1e-9
        assert category in scene.presence
    assert all(len(curve) == math.ceil(duration) for curve in scene.curves.values())
    assert len(scene.events) <= 12
    assert all(label not in {"Mains hum", "Television"} for label, _ in scene.top_labels)
