"""« Sounds heard » rules on synthetic scores, and CED's windowing.

A small ontology mirrors the AudioSet structure the rules depend on (speech subtree, bird
family, context, issue classes, excluded classes), so no model or download is needed.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from vfe_vision.adapters.audio_tagging import ced
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import CancelledError, ExternalToolError, ServiceUnavailableError
from vfe_vision.domain.audio_events import HOP_S, Category, Rollup, build_rollup
from vfe_vision.domain.heard import (
    CED_SURE,
    MAX_SPANS,
    PER_FAMILY,
    CedWindows,
    heard_classes,
    heard_dict,
    heard_sounds,
    shot_heard,
    window_cells,
)
from vfe_vision.domain.sound_names import FRENCH, sound_name


def _node(mid: str, name: str, *children: str) -> dict[str, Any]:
    return {"id": mid, "name": name, "child_ids": list(children), "restrictions": []}


ONTOLOGY = [
    _node("/m/0dgw9r", "Human sounds", "/m/09l8g"),
    _node("/m/09l8g", "Human voice", "/m/09x0r", "/m/01j3sz", "/m/02zsn"),
    _node("/m/09x0r", "Speech", "/m/01h8n0"),
    _node("/m/01h8n0", "Conversation"),
    _node("/m/01j3sz", "Laughter"),
    _node("/m/02zsn", "Whispering"),
    _node("/m/04rlf", "Music", "/m/04szw"),
    _node("/m/04szw", "Musical instrument", "/m/0l14j_"),
    _node("/m/0l14j_", "Flute"),
    _node("/m/0jbk", "Animal", "/m/068hy", "/m/015p6", "/m/03vt0", "/m/0f9_l", "/m/0ch8v",
          "/m/09ld4"),
    _node("/m/068hy", "Domestic animals, pets", "/m/0bt9lr", "/m/01yrx"),
    _node("/m/01yrx", "Cat"),
    _node("/m/0bt9lr", "Dog", "/m/05tny_"),
    _node("/m/05tny_", "Bark"),
    _node("/m/015p6", "Bird", "/m/020bb7"),
    _node("/m/020bb7", "Bird vocalization, bird call, bird song", "/m/07pggtn"),
    _node("/m/07pggtn", "Chirp, tweet"),
    _node("/m/03vt0", "Insect"),
    _node("/m/0f9_l", "Snake", "/m/07rjwbb"),
    _node("/m/0ch8v", "Cattle, bovinae"),
    _node("/m/09ld4", "Frog"),
    _node("/m/07rjwbb", "Hiss"),
    _node("/m/059j3w", "Natural sounds", "/m/03m9d0z", "/m/0838f"),
    _node("/m/0838f", "Water", "/m/06mb1"),
    _node("/m/03m9d0z", "Wind", "/t/dd00092"),
    _node("/t/dd00092", "Wind noise (microphone)"),
    _node("/m/06mb1", "Rain"),
    _node("/t/dd00041", "Sounds of things", "/m/07yv9"),
    _node("/m/07yv9", "Vehicle", "/m/0k4j"),
    _node("/m/0k4j", "Car"),
    _node("/t/dd00098", "Source-ambiguous sounds", "/m/028v0c"),
    _node("/m/028v0c", "Silence"),
    _node("/t/dd00123", "Channel, environment and background", "/t/dd00093", "/m/096m7z"),
    _node("/t/dd00093", "Acoustic environment", "/t/dd00125"),
    _node("/t/dd00125", "Inside, small room"),
    _node("/m/096m7z", "Noise", "/m/06xkwv"),
    _node("/m/06xkwv", "Mains hum"),
]  # fmt: skip

CLASSES = [
    ("/m/09x0r", "Speech"), ("/m/01h8n0", "Conversation"), ("/m/01j3sz", "Laughter"),
    ("/m/02zsn", "Whispering"), ("/m/04rlf", "Music"), ("/m/0l14j_", "Flute"),
    ("/m/0jbk", "Animal"), ("/m/01yrx", "Cat"), ("/m/0bt9lr", "Dog"), ("/m/05tny_", "Bark"), ("/m/015p6", "Bird"),
    ("/m/020bb7", "Bird vocalization, bird call, bird song"), ("/m/07pggtn", "Chirp, tweet"),
    ("/m/03vt0", "Insect"), ("/m/0f9_l", "Snake"), ("/m/0ch8v", "Cattle, bovinae"),
    ("/m/09ld4", "Frog"), ("/m/07rjwbb", "Hiss"), ("/m/03m9d0z", "Wind"),
    ("/t/dd00092", "Wind noise (microphone)"), ("/m/06mb1", "Rain"), ("/m/07yv9", "Vehicle"),
    ("/m/0k4j", "Car"), ("/m/028v0c", "Silence"), ("/t/dd00125", "Inside, small room"),
    ("/m/06xkwv", "Mains hum"),
]  # fmt: skip


def _csv(classes: list[tuple[str, str]]) -> str:
    rows = [f'{i},{mid},"{name}"' for i, (mid, name) in enumerate(classes)]
    return "index,mid,display_name\n" + "\n".join(rows) + "\n"


@pytest.fixture(scope="module")
def rollup() -> Rollup:
    return build_rollup(_csv(CLASSES), json.dumps(ONTOLOGY))


@pytest.fixture(scope="module")
def ced_rollup() -> Rollup:
    # CED's classes come in another order (AudioSet's 527): matched by mid, never by index.
    return build_rollup(_csv(list(reversed(CLASSES))), json.dumps(ONTOLOGY))


def _duration(frames: int) -> float:
    return HOP_S * frames + HOP_S


def _scores(rollup: Rollup, frames: int, runs: dict[str, list[tuple[int, int, float]]]) -> Any:
    matrix = np.zeros((frames, len(rollup)), dtype=np.float32)
    for name, items in runs.items():
        for first, stop, value in items:
            matrix[first:stop, rollup.index[name]] = value
    return matrix


def _windows(
    rollup: Rollup, duration: float, values: dict[str, list[float]], count: int
) -> CedWindows:
    """``count`` 5 s windows every 2.5 s (the last end-aligned), probabilities per window."""
    starts = np.asarray(ced.window_starts(round(duration * ced.SAMPLE_RATE)), dtype=np.float64)
    starts = starts[:count] / ced.SAMPLE_RATE
    probs = np.zeros((len(starts), len(rollup)), dtype=np.float32)
    for name, column in values.items():
        probs[: len(column), rollup.index[name]] = column
    return CedWindows(starts, np.minimum(starts + 5.0, duration), probs, rollup)


def _labels(sounds: Any) -> list[str]:
    return [sound.label for sound in sounds]


# ------------------------------------------------------------------ candidates
def test_candidates_are_specific_non_speech_non_music_sounds(rollup: Rollup) -> None:
    names = {rollup.names[i] for i in heard_classes(rollup).tolist()}
    assert {"Laughter", "Dog", "Bark", "Bird", "Insect", "Frog", "Wind", "Rain", "Car"} <= names
    excluded = {
        "Speech", "Conversation",  # the transcript covers speech
        "Whispering",  # too close to speech
        "Music", "Flute",  # music and instruments are listed apart
        "Animal", "Vehicle",  # only repeat a family
        "Snake", "Hiss",  # electronic hiss on rushes
        "Wind noise (microphone)", "Mains hum",  # recording issues, flagged apart
        "Silence", "Inside, small room",
    }  # fmt: skip
    assert not names & excluded


def test_snake_is_never_nature(rollup: Rollup) -> None:
    assert rollup.categories[rollup.index["Snake"]] is Category.OTHER
    assert rollup.categories[rollup.index["Frog"]] is Category.NATURE


# ------------------------------------------------------------------ YAMNet alone
def test_yamnet_rule_needs_a_second_above_the_floor_and_a_peak(rollup: Rollup) -> None:
    frames = 40
    scores = _scores(rollup, frames, {
        "Dog": [(2, 8, 0.4)],  # 6 frames ≈ 2.9 s at 0.4: kept
        "Rain": [(10, 20, 0.3)],  # long but never 0.35
        "Car": [(25, 26, 0.9)],  # one frame: smoothed away
        "Laughter": [(30, 32, 0.9)],  # 2 frames: the median keeps one, under 1 s
    })  # fmt: skip
    sounds = heard_sounds(scores, rollup, duration_s=_duration(frames))
    assert _labels(sounds) == ["Dog"]
    dog = sounds[0]
    assert dog.sources == ("yamnet",)
    assert dog.category is Category.NATURE
    assert dog.seconds == pytest.approx(6 * HOP_S, abs=0.01)
    assert dog.first_s == pytest.approx(2 * HOP_S + HOP_S / 2, abs=0.01)
    assert dog.score == pytest.approx(0.4, abs=1e-3)


def test_spans_merge_gaps_up_to_a_second(rollup: Rollup) -> None:
    frames = 60
    scores = _scores(rollup, frames, {"Dog": [(0, 6, 0.6), (8, 14, 0.6), (30, 36, 0.6)]})
    (dog,) = heard_sounds(scores, rollup, duration_s=_duration(frames))
    assert len(dog.spans) == 2  # 2 frames (0.96 s) apart: merged; 16 frames apart: not
    assert dog.spans[0][0] == 0.0


def test_an_ancestor_gives_way_to_a_descendant_heard_as_long(rollup: Rollup) -> None:
    frames = 40
    song = "Bird vocalization, bird call, bird song"
    covered = _scores(rollup, frames, {"Bird": [(0, 20, 0.6)], song: [(0, 18, 0.6)]})
    assert _labels(heard_sounds(covered, rollup, duration_s=_duration(frames))) == [song]
    partial = _scores(rollup, frames, {"Bird": [(0, 30, 0.6)], song: [(0, 6, 0.6)]})
    assert _labels(heard_sounds(partial, rollup, duration_s=_duration(frames))) == ["Bird", song]


def test_a_family_lists_five_sounds_longest_first(rollup: Rollup) -> None:
    frames = 80
    animals = ["Dog", "Bird", "Insect", "Frog", "Cattle, bovinae", "Cat"]
    runs = {name: [(0, 10 + 5 * i, 0.6)] for i, name in enumerate(animals)}
    runs["Rain"] = [(0, 4, 0.6)]
    sounds = heard_sounds(_scores(rollup, frames, runs), rollup, duration_s=_duration(frames))
    nature = [s.label for s in sounds if s.category is Category.NATURE]
    assert nature == ["Cat", "Cattle, bovinae", "Frog", "Insect", "Bird"]  # Dog, the shortest
    assert len(nature) == PER_FAMILY
    assert "Rain" in _labels(sounds)  # another family, not counted with the animals


def test_empty_or_malformed_inputs(rollup: Rollup) -> None:
    assert heard_sounds(np.zeros((0, len(rollup))), rollup, duration_s=0.0) == []
    assert heard_sounds(np.zeros((5, 3)), rollup, duration_s=3.0) == []
    nan = np.full((10, len(rollup)), np.nan, dtype=np.float32)
    assert heard_sounds(nan, rollup, duration_s=_duration(10)) == []


# ------------------------------------------------------------------ with CED
def test_ced_alone_counts_its_windows(rollup: Rollup, ced_rollup: Rollup) -> None:
    frames = 40  # 19.7 s: windows at 0, 2.5 … 12.5 and 14.7
    duration = _duration(frames)
    windows = _windows(ced_rollup, duration, {"Insect": [0.0, 0.5, 0.45, 0.1]}, count=7)
    (insect,) = heard_sounds(
        np.zeros((frames, len(rollup))), rollup, duration_s=duration, ced=windows
    )
    assert insect.label == "Insect"
    assert insect.sources == ("ced",)
    assert insect.score == pytest.approx(0.5)
    assert insect.seconds == pytest.approx(5.0)  # two 2.5 s cells
    assert insect.spans == ((3.75, 8.75),)


def test_music_or_speech_masks_unsure_ced_sounds(rollup: Rollup, ced_rollup: Rollup) -> None:
    frames = 30
    duration = _duration(frames)
    zeros = np.zeros((frames, len(rollup)))
    for busy in ({"Music": [0.5]}, {"Speech": [0.8]}):
        unsure = _windows(ced_rollup, duration, {**busy, "Cattle, bovinae": [0.45]}, count=6)
        assert heard_sounds(zeros, rollup, duration_s=duration, ced=unsure) == []
        sure = _windows(ced_rollup, duration, {**busy, "Cattle, bovinae": [CED_SURE]}, count=6)
        sounds = heard_sounds(zeros, rollup, duration_s=duration, ced=sure)
        assert _labels(sounds) == ["Cattle, bovinae"]


def test_yamnet_needs_a_higher_peak_and_ced_agreement(rollup: Rollup, ced_rollup: Rollup) -> None:
    frames = 40
    duration = _duration(frames)
    scores = _scores(rollup, frames, {
        "Dog": [(0, 10, 0.6)],  # CED hears a little dog: kept
        "Car": [(0, 10, 0.6)],  # CED hears no car: dropped
        "Rain": [(20, 30, 0.4)],  # fine alone, under 0.5 next to CED
    })  # fmt: skip
    alone = heard_sounds(scores, rollup, duration_s=duration)
    assert set(_labels(alone)) == {"Dog", "Car", "Rain"}
    windows = _windows(ced_rollup, duration, {"Dog": [0.15], "Rain": [0, 0, 0, 0, 0.2, 0.2]}, 7)
    both = heard_sounds(scores, rollup, duration_s=duration, ced=windows)
    assert _labels(both) == ["Dog"]
    assert both[0].sources == ("yamnet",)


def test_both_sources_keep_yamnet_times_and_add_what_it_missed(
    rollup: Rollup, ced_rollup: Rollup
) -> None:
    frames = 40
    duration = _duration(frames)
    scores = _scores(rollup, frames, {"Dog": [(0, 5, 0.6)]})  # 0-2.64 s
    # Windows 0-5 and 2.5-7.5 overlap YAMNet's time: its finer times stay. 5-10 does not:
    # YAMNet missed that bark, so CED's cell joins the times.
    windows = _windows(ced_rollup, duration, {"Dog": [0.8, 0.8, 0.8]}, count=7)
    (dog,) = heard_sounds(scores, rollup, duration_s=duration, ced=windows)
    assert dog.sources == ("yamnet", "ced")
    assert dog.score == pytest.approx(0.8)
    assert dog.spans == ((0.0, 2.64), (6.25, 8.75))
    assert dog.seconds == pytest.approx(2.64 + 2.5, abs=0.01)


def test_malformed_or_nan_ced_windows(rollup: Rollup, ced_rollup: Rollup) -> None:
    frames = 40
    duration = _duration(frames)
    scores = _scores(rollup, frames, {"Dog": [(0, 10, 0.6)], "Rain": [(20, 30, 0.4)]})
    good = _windows(ced_rollup, duration, {"Dog": [0.15]}, count=7)
    wrong_rows = CedWindows(good.start_s, good.end_s, good.probs[:3], ced_rollup)
    wrong_width = CedWindows(good.start_s, good.end_s, good.probs[:, :5], ced_rollup)
    alone = _labels(heard_sounds(scores, rollup, duration_s=duration))
    for broken in (wrong_rows, wrong_width):  # ignored: YAMNet's own rule applies
        assert _labels(heard_sounds(scores, rollup, duration_s=duration, ced=broken)) == alone
    probs = good.probs.copy()
    probs[0, ced_rollup.index["Dog"]] = np.nan  # a NaN window next to one that agrees
    probs[1, ced_rollup.index["Dog"]] = 0.15
    nan = CedWindows(good.start_s, good.end_s, probs, ced_rollup)
    assert _labels(heard_sounds(scores, rollup, duration_s=duration, ced=nan)) == ["Dog"]


def test_every_moment_reaches_the_shots(rollup: Rollup) -> None:
    frames = 400  # a bark every 20 frames: 20 moments over 3 minutes
    runs = {"Dog": [(i, i + 4, 0.7) for i in range(0, frames, 20)]}
    (dog,) = heard_sounds(_scores(rollup, frames, runs), rollup, duration_s=_duration(frames))
    assert len(dog.spans) == 20
    assert shot_heard([dog], [(180.0, 193.0)]) == [["Dog"]]  # after the 12th moment
    stored = heard_dict(dog)
    assert len(stored["spans"]) == MAX_SPANS  # type: ignore[arg-type]
    assert stored["moments"] == 20


def test_an_ancestor_elsewhere_in_time_is_kept(rollup: Rollup) -> None:
    frames = 160
    song = "Bird vocalization, bird call, bird song"
    runs = {"Bird": [(0, 26, 0.6)], song: [(120, 143, 0.6)]}  # 0-12 s and 58-68 s
    sounds = heard_sounds(_scores(rollup, frames, runs), rollup, duration_s=_duration(frames))
    assert set(_labels(sounds)) == {"Bird", song}
    assert shot_heard(sounds, [(0.0, 12.0)]) == [["Bird"]]


def test_a_descendant_cut_by_the_limit_does_not_hide_its_ancestor(rollup: Rollup) -> None:
    frames = 80
    song = "Bird vocalization, bird call, bird song"
    runs = {
        "Frog": [(0, 60, 0.6)], "Insect": [(0, 50, 0.6)], "Bird": [(0, 30, 0.6)],
        "Cat": [(0, 29, 0.6)], "Dog": [(0, 28, 0.6)], "Cattle, bovinae": [(0, 27, 0.6)],
        song: [(0, 26, 0.6)],  # covers Bird, but is the 6th animal
    }  # fmt: skip
    sounds = heard_sounds(_scores(rollup, frames, runs), rollup, duration_s=_duration(frames))
    assert _labels(sounds) == ["Frog", "Insect", "Bird", "Cat", "Dog"]


def test_shot_heard_ranks_by_overlap(rollup: Rollup) -> None:
    frames = 60
    scores = _scores(rollup, frames, {"Dog": [(0, 20, 0.6)], "Rain": [(15, 50, 0.6)]})
    sounds = heard_sounds(scores, rollup, duration_s=_duration(frames))
    shots = shot_heard(sounds, [(0.0, 5.0), (8.0, 20.0), (27.0, 29.0)])
    assert shots == [["Dog"], ["Rain", "Dog"], []]
    assert shot_heard(sounds, [(0.0, 30.0)], limit=1) == [["Rain"]]


# ------------------------------------------------------------------ windows
def test_windows_are_never_zero_padded() -> None:
    sr = ced.SAMPLE_RATE
    assert ced.window_starts(sr // 4) == []  # too short to hear anything
    assert ced.window_starts(3 * sr) == [0]  # shorter than 5 s: once, at its true length
    assert ced.window_starts(5 * sr) == [0]
    assert ced.window_starts(7 * sr) == [0, 2 * sr]  # the last window ends at the end
    assert ced.window_starts(10 * sr) == [0, 40_000, 80_000]


@settings(max_examples=60, deadline=None)
@given(samples=st.integers(min_value=ced.MIN_SAMPLES, max_value=400 * ced.SAMPLE_RATE))
def test_windows_cover_the_file(samples: int) -> None:
    starts = ced.window_starts(samples)
    assert starts[0] == 0
    assert all(b - a <= ced.HOP for a, b in pairwise(starts))
    assert starts[-1] + min(ced.WINDOW, samples) == samples
    begin = np.asarray(starts) / ced.SAMPLE_RATE
    end = np.minimum(begin + 5.0, samples / ced.SAMPLE_RATE)
    cell_start, cell_end = window_cells(begin, end, samples / ced.SAMPLE_RATE)
    assert cell_start[0] == 0.0
    assert cell_end[-1] == pytest.approx(samples / ced.SAMPLE_RATE)
    assert np.all(cell_end >= cell_start)
    assert np.allclose(cell_start[1:], cell_end[:-1])


class _FakeSession:
    def __init__(self, classes: int = ced.NUM_CLASSES, *, fail: bool = False) -> None:
        self.lengths: list[int] = []
        self.last: Any = None
        self.classes = classes
        self.fail = fail

    def run(self, output_names: list[str], input_feed: dict[str, Any]) -> list[Any]:
        wave = input_feed[ced.INPUT_NAME]
        self.lengths.append(wave.shape[1])
        self.last = wave
        if self.fail:
            raise RuntimeError("[ONNXRuntimeError] : 2 : INVALID_ARGUMENT")
        return [np.full((1, self.classes), 0.25, dtype=np.float32)]


def _tagger(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> ced.CedTagger:
    tagger = ced.CedTagger(Path("missing.onnx"))
    monkeypatch.setattr(tagger, "_get_session", lambda: session)
    return tagger


def test_tagger_checks_shapes_and_wraps_engine_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    wave = np.zeros(ced.SAMPLE_RATE, dtype=np.float32)
    with pytest.raises(ExternalToolError, match="Sortie CED inattendue"):
        _tagger(monkeypatch, _FakeSession(521)).scores(wave)
    with pytest.raises(ExternalToolError, match="Échec du modèle CED"):
        _tagger(monkeypatch, _FakeSession(fail=True)).scores(wave)


def test_tagger_progress_cancel_and_non_finite_input(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    tagger = _tagger(monkeypatch, session)
    wave = np.zeros(10 * ced.SAMPLE_RATE, dtype=np.float32)  # windows at 0, 2.5, 5 s
    wave[5] = np.nan
    wave[6] = np.inf
    seen: list[float] = []
    tagger.scores(wave, progress=seen.append)
    assert seen == pytest.approx([1 / 3, 2 / 3, 1.0])
    assert np.isfinite(session.last).all()
    cancel = CancelToken()
    session.lengths.clear()
    with pytest.raises(CancelledError):
        tagger.scores(wave, cancel=cancel, progress=lambda _f: cancel.cancel("stop"))
    assert len(session.lengths) == 1  # stopped before the second window
    with pytest.raises(ValueError, match="mono"):
        tagger.scores(np.zeros((2, 100), dtype=np.float32))


def test_unreadable_model_and_wrong_class_list(tmp_path: Path) -> None:
    bad = tmp_path / "model.onnx"
    bad.write_bytes(b"not a model" * 100)
    with pytest.raises(ExternalToolError, match="illisible"):
        ced.CedTagger(bad).scores(np.zeros(ced.SAMPLE_RATE, dtype=np.float32))
    (tmp_path / ced.CLASS_MAP_FILE).write_text(_csv(CLASSES[:3]), encoding="utf-8")
    ontology = tmp_path / "ontology.json"
    ontology.write_text(json.dumps(ONTOLOGY), encoding="utf-8")
    with pytest.raises(ServiceUnavailableError, match="inattendue"):
        ced.load_rollup(tmp_path, ontology)


def test_tagger_feeds_true_length_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    tagger = ced.CedTagger(Path("missing.onnx"))
    session = _FakeSession()
    monkeypatch.setattr(tagger, "_get_session", lambda: session)
    wave = np.arange(7 * ced.SAMPLE_RATE, dtype=np.float32) / 1e6
    result = tagger.scores(wave)
    assert session.lengths == [ced.WINDOW, ced.WINDOW]
    assert np.array_equal(session.last[0], wave[-ced.WINDOW :])  # end-aligned, not padded
    assert result.start_s.tolist() == [0.0, 2.0]
    assert result.end_s.tolist() == [5.0, 7.0]
    short = tagger.scores(np.zeros(3 * ced.SAMPLE_RATE, dtype=np.float32))
    assert session.lengths[-1] == 3 * ced.SAMPLE_RATE
    assert len(short) == 1


# ------------------------------------------------------------------ names
def test_french_names_cover_audioset() -> None:
    assert len(FRENCH) == 527
    assert all(0 < len(name) <= 32 for name in FRENCH.values())
    assert "Serpent" not in FRENCH.values()
    assert sound_name("Bark") == "Aboiements"
    assert sound_name("Bark", "en") == "Bark"
    assert sound_name("Unknown class") == "Unknown class"
