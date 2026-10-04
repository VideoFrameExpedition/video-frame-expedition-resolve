"""The model bench: which frames a run asks about, and what it measures."""

from __future__ import annotations

from vfe_vision.domain.bench import (
    Answer,
    Batch,
    BenchBeing,
    BenchCalibration,
    BenchFrame,
    BenchModelStatus,
    Candidate,
    ModelRun,
    Vram,
    choose_frames,
    has_foreign_script,
    image_set,
    language_of,
    match_beings,
    score,
    text_words,
    wrong_language,
)

FRENCH = "Un chat noir dort sur le canapé du salon. Il est roulé en boule dans une couverture."
ENGLISH = "A black cat is sleeping on the sofa. It is curled up with its head on a blanket."


def _candidates() -> list[Candidate]:
    # 6 videos of 10 frames: every third frame shows a being, frames 0 and 5 of v0/v1 a text.
    return [
        Candidate(f"v{v}-k{k}", f"v{v}", has_text=v < 2 and k in {0, 5}, has_beings=k % 3 == 0)
        for v in range(6)
        for k in range(10)
    ]


class TestChooseFrames:
    def test_the_same_library_gives_the_same_frames(self) -> None:
        first = choose_frames(_candidates(), 12)
        again = choose_frames(list(reversed(_candidates())), 12)
        assert first == again
        assert len(first) == len(set(first)) == 12

    def test_frames_are_spread_over_the_videos_with_text_and_beings(self) -> None:
        by_id = {c.keyframe_id: c for c in _candidates()}
        chosen = [by_id[key] for key in choose_frames(_candidates(), 12)]
        assert len({c.video_id for c in chosen}) == 6
        assert sum(c.has_text for c in chosen) >= 3  # a quarter, when the library has them
        assert sum(c.has_beings for c in chosen) >= 6  # half
        assert any(not c.has_text and not c.has_beings for c in chosen)

    def test_a_small_library_gives_what_it_has(self) -> None:
        few = _candidates()[:5]
        assert sorted(choose_frames(few, 24)) == sorted(c.keyframe_id for c in few)
        assert choose_frames([], 24) == []


def test_the_same_frames_give_the_same_image_set() -> None:
    def frames(*ids: str) -> list[BenchFrame]:
        return [
            BenchFrame(keyframe_id=key, video_id="v", filename="a.mp4", t_s=0.0,
                       image_path="", thumb_path="")
            for key in ids
        ]  # fmt: skip

    assert image_set(frames("k1", "k2")) == image_set(frames("k2", "k1"))
    assert image_set(frames("k1", "k2")) != image_set(frames("k1", "k3"))
    assert image_set([]) == ""  # a run that has not gathered its frames yet


class TestLanguage:
    def test_the_language_of_a_description(self) -> None:
        assert language_of(FRENCH) == "fr"
        assert language_of(ENGLISH) == "en"
        assert language_of("Der Hund läuft mit dem Ball über die Wiese und ist nicht müde.") == "de"
        assert language_of("Chat noir.") is None  # too short to tell

    def test_an_answer_in_another_language_is_wrong(self) -> None:
        assert wrong_language(ENGLISH, "fr") is True
        assert wrong_language(FRENCH, "fr") is False
        assert wrong_language(FRENCH, "en") is True
        assert wrong_language("Chat noir.", "fr") is None
        assert wrong_language(ENGLISH, "pt") is None  # no word list: not checked

    def test_words_of_another_script_are_seen_anywhere_in_an_answer(self) -> None:
        assert has_foreign_script({"tags": ["chat", "茄子"], "caption": "Un chat"})
        assert not has_foreign_script({"tags": ["chat"], "people_count": 2, "x": None})


def test_the_words_worth_reading_again() -> None:
    assert text_words(["Premium Iranian Rice", "Net Weight 4.5 Kg", "É"]) == {
        "premium", "iranian", "rice", "net", "weight",
    }  # fmt: skip


class TestPositions:
    def test_a_box_of_the_same_kind_that_overlaps_is_the_same_being(self) -> None:
        cat = BenchBeing(category="mammal", box=[0.2, 0.3, 0.7, 0.9])
        person = BenchBeing(category="person", box=[0.0, 0.0, 0.3, 1.0])
        found = [
            BenchBeing(category="mammal", box=[0.22, 0.3, 0.7, 0.88]),
            BenchBeing(category="body_part", box=[0.0, 0.05, 0.3, 1.0]),  # a person, for a model
        ]
        overlaps = match_beings([cat, person], found)
        assert all(overlap > 0.8 for overlap in overlaps)

    def test_a_missed_being_or_another_kind_counts_for_nothing(self) -> None:
        cat = BenchBeing(category="mammal", box=[0.2, 0.3, 0.7, 0.9])
        assert match_beings([cat], []) == [0.0]
        assert match_beings([cat], [BenchBeing(category="bird", box=[0.2, 0.3, 0.7, 0.9])]) == [0.0]
        assert match_beings([cat], [BenchBeing(category="mammal", box=[0.8, 0.0, 1.0, 0.2])]) == [
            0.0
        ]

    def test_a_box_is_used_once(self) -> None:
        twins = [BenchBeing(category="mammal", box=[0.1, 0.1, 0.5, 0.5])] * 2
        overlaps = match_beings(twins, [BenchBeing(category="mammal", box=[0.1, 0.1, 0.5, 0.5])])
        assert sorted(overlaps) == [0.0, 1.0]


def _described(text: str, visible: str = "", **extra: object) -> Answer:
    return Answer(
        ok=True, latency_ms=4000, completion_tokens=300,
        data={"caption": text.split(".", maxsplit=1)[0], "description": text, "visible_text": visible},
        **extra,  # type: ignore[arg-type]
    )  # fmt: skip


def _frames() -> list[BenchFrame]:
    def frame(key: str, **facts: object) -> BenchFrame:
        return BenchFrame(
            keyframe_id=key, video_id="v", filename="a.mp4", t_s=1.0, image_path=f"{key}.jpg",
            thumb_path=f"{key}.t.jpg", **facts,  # type: ignore[arg-type]
        )  # fmt: skip

    return [
        frame("text", text=["RIZ BASMATI", "500 g"], beings=[]),
        frame("blank", text=[], beings=[BenchBeing(category="mammal", box=[0.2, 0.3, 0.7, 0.9])]),
        frame("unknown"),  # neither the OCR nor the detector looked at it
    ]


def _model() -> ModelRun:
    located = Answer(ok=True, beings=[BenchBeing(category="mammal", box=[0.2, 0.3, 0.7, 0.9])])
    return ModelRun(
        key="m", display_name="M", status=BenchModelStatus.DONE,
        vram_before=Vram(used=1000, free=11000, total=12000),
        vram_loaded=Vram(used=6500, free=5500, total=12000),
        calibration=BenchCalibration(source="profile", enabled=True, mean_iou=0.9, reasoning_tokens=5),
        frames=Batch(
            wall_s=6.0,
            answers={
                "text": _described(FRENCH, "Riz Basmati"),
                "blank": _described(ENGLISH, "KEEP OUT", repaired=True),
                "unknown": Answer(error="tronquée", truncated=True, reasoning_tokens=900),
            },
        ),
        positions=Batch(wall_s=3.0, answers={"text": Answer(ok=True, beings=[]), "blank": located}),
    )  # fmt: skip


def test_every_measure_of_a_model() -> None:
    scores = score(_model(), _frames(), "fr", {"text": {"m": 3, "other": 0}, "blank": {"m": 2}})
    assert (scores.vram_mib, scores.vram_free_mib, scores.vram_full) == (5500, 5500, False)
    assert (scores.seconds_per_image, scores.seconds_per_position) == (3.0, 1.5)
    assert scores.tokens_per_s == 100.0
    assert (scores.requests, scores.valid, scores.repaired, scores.truncated) == (5, 4, 1, 1)
    assert scores.reasoning_tokens == 905
    assert (scores.language_checked, scores.wrong_language) == (2, 1)
    # « RIZ BASMATI » read, « 500 » not; on the frame where the OCR read nothing, a text given.
    assert (scores.text_frames, scores.text_recall) == (1, 0.6667)
    assert (scores.blank_frames, scores.unconfirmed_text) == (1, 1)
    assert (scores.position_frames, scores.position_beings) == (1, 1)
    assert (scores.position_recall, scores.position_iou) == (1.0, 1.0)
    assert (scores.positions_enabled, scores.calibration_iou) == (True, 0.9)
    assert (scores.rated, scores.rating) == (2, 2.5)


def test_a_model_that_could_not_be_loaded_has_no_measure() -> None:
    scores = score(
        ModelRun(key="m", display_name="M", status=BenchModelStatus.LOAD_FAILED), [], "fr"
    )
    assert scores.vram_mib is None
    assert scores.seconds_per_image is None
    assert (scores.requests, scores.rating) == (0, None)


def test_a_card_left_almost_full_is_flagged() -> None:
    model = ModelRun(
        key="m", display_name="M",
        vram_before=Vram(used=1800, free=10200, total=12000),
        vram_loaded=Vram(used=11800, free=200, total=12000),
    )  # fmt: skip
    assert score(model, [], "fr").vram_full
