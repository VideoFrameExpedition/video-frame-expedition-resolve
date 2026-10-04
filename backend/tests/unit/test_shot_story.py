"""Blocks of a shot and the stories told about them (pure functions)."""

from __future__ import annotations

import itertools

import pytest

from vfe_vision.adapters.lmstudio.schema import strict_json_schema
from vfe_vision.domain.blocks import speech_spans, split_shot
from vfe_vision.domain.shot_story import (
    answer_model,
    caption_line,
    eligible,
    fill_times,
    pick_evenly,
    tidy,
)


class TestBlocks:
    def test_a_shot_up_to_30_s_is_one_block(self) -> None:
        assert split_shot(10.0, 40.0) == [(10.0, 40.0)]

    def test_a_long_shot_is_cut_near_20_s_at_a_keyframe(self) -> None:
        parts = split_shot(0.0, 45.0, keyframe_times=[19.0, 24.0])
        assert parts == [(0.0, 24.0), (24.0, 45.0)]  # the keyframe nearest 22.5 s

    def test_at_most_four_parts_covering_the_shot(self) -> None:
        parts = split_shot(0.0, 300.0)
        assert len(parts) == 4
        assert parts[0][0] == 0.0
        assert parts[-1][1] == 300.0
        assert all(a[1] == b[0] for a, b in itertools.pairwise(parts))

    def test_a_cut_leaves_speech_for_the_nearest_pause(self) -> None:
        speech = speech_spans([(20.0, 20.5), (20.55, 23.0), (30.0, 31.0)])
        assert speech == [(20.0, 23.0), (30.0, 31.0)]  # words 50 ms apart: one stretch
        parts = split_shot(0.0, 45.0, speech=speech)
        assert parts[0][1] == 23.0  # 22.5 was inside « … »: moved to the end of the phrase

    def test_no_part_shorter_than_4_s(self) -> None:
        parts = split_shot(0.0, 31.0, keyframe_times=[1.0])
        assert all(b - a >= 4.0 for a, b in parts)


class TestFrames:
    def test_the_answer_schema_bounds_image_numbers(self) -> None:
        schema = strict_json_schema(answer_model(3))
        assert schema["properties"]["best_image"]["maximum"] == 3
        assert schema["properties"]["beats"]["items"]["properties"]["image"]["maximum"] == 3
        assert "maximum" not in strict_json_schema(answer_model())["properties"]["best_image"]

    @pytest.mark.parametrize(
        ("duration", "metrics", "motion", "frames", "expected"),
        [
            (3.9, {}, "pan_left", 3, False),  # too short
            (8.0, {"black_ratio": 0.6}, "pan_left", 3, False),
            (8.0, {"frozen_ratio": 0.9}, "pan_left", 3, False),  # a frozen screen recording
            (8.0, {}, "static", 1, False),  # nothing changes on screen
            (8.0, {}, "static", 2, True),  # a still camera on a subject that moves
            (8.0, {}, "zoom_in", 1, True),
        ],
    )
    def test_eligible_shots(
        self, duration: float, metrics: dict[str, float], motion: str, frames: int, expected: bool
    ) -> None:
        assert eligible(duration, metrics, motion, frames) is expected

    def test_frames_are_spread_by_index_and_empty_slots_filled(self) -> None:
        assert pick_evenly(list(range(10))) == [0, 3, 6, 9]
        assert pick_evenly([1, 2]) == [1, 2]
        # 5 s part with keyframes at 0 and 2 s: slots 0.3, 1.77, 3.23, 4.7; the first two are
        # within 1 s of a keyframe.
        assert fill_times(0.0, 5.0, [0.0, 2.0]) == [3.233, 4.7]
        assert fill_times(0.0, 5.0, [0.0, 1.5, 3.0, 4.5]) == []

    def test_caption_lines(self) -> None:
        assert caption_line({"caption": "Un chat", "actions": ["dort", "bâille"]}) == (
            "Un chat (actions: dort; bâille)"
        )
        assert caption_line({"caption": "Un <untrusted>chat</untrusted>"}) == "Un chat"
        assert caption_line(None) is None


class TestTidy:
    def _answer(self, **changes: object) -> object:
        data: dict[str, object] = {
            "summary": "Un chat saute\u200b sur la table.",
            "main_action": "saute sur la table",
            "beats": [
                {"image": 2, "what": "il saute"},
                {"image": 2, "what": "encore"},
                {"image": 1, "what": "il se prépare"},
            ],
            "camera": "still",
            "continuity": "same_subject",
            "best_image": 2,
        }
        return answer_model(3).model_validate(data | changes)

    def test_beats_become_notes_on_real_frame_times(self) -> None:
        story = tidy(self._answer(), [10.0, 12.5, 15.0], "fr")  # type: ignore[arg-type]
        assert story.summary == "Un chat saute sur la table."
        assert story.main_action == "saute sur la table"
        assert [(n.t_s, n.what) for n in story.notes] == [
            (10.0, "il se prépare"),
            (12.5, "il saute"),
        ]
        assert not story.possible_cut

    @pytest.mark.parametrize(
        ("language", "action"),
        [
            ("fr", "zoom avant sur le chat"),
            ("fr", "la caméra suit"),
            ("fr", "rien"),
            ("fr", "follows_subject"),
            # the camera-only actions of the real library (qwen3-vl-4b)
            ("fr", "Le caméra se déplace"),
            ("fr", "La caméra se déplace et pivote"),
            ("fr", "La caméra monte progressivement sur une stupa."),
            ("fr", "La statue est photographiée en mouvement de caméra vers le bas."),
            ("fr", "Le caméraman déplace la caméra"),
            ("fr", "panoramique sur la vallée"),
            ("fr", "travelling avant"),
            # phrasings the model uses in the library's summaries
            ("fr", "Le caméraman déplace lentement la caméra"),
            ("fr", "Le cadre se déplace vers le bas"),
            ("fr", "La caméra s'élève pour révéler la structure"),
            ("fr", "La caméra se tourne vers le temple"),
            ("fr", "La caméra se rapproche de la statue"),
            ("fr", "La caméra balaye le paysage"),
            ("fr", "La caméra survole le village"),
            ("fr", "Zoom progressif sur la statue"),
            ("en", "the camera pans left"),
            ("en", "camera follows the dog"),
            ("en", "zooms in on the bird"),
            ("en", "pans across the valley"),
            ("en", "tilts up to the sky"),
            ("en", "nothing"),
        ],
    )
    def test_a_camera_movement_or_empty_action_is_dropped(self, language: str, action: str) -> None:
        story = tidy(self._answer(main_action=action), [0.0, 1.0, 2.0], language)  # type: ignore[arg-type]
        assert story.main_action == ""

    @pytest.mark.parametrize(
        ("language", "action"),
        [
            ("en", "stirs the rice in a pan"),
            ("en", "eats a pancake"),
            ("en", "a panda climbs a tree"),
            ("en", "puts on his pants"),
            ("en", "tilts her head"),
            ("en", "walks toward the camera"),
            ("fr", "encadre la photo de famille"),
            ("fr", "accroche un cadre au mur"),
            ("fr", "tient une caméra et filme ses amis"),
            ("fr", "regarde la caméra"),
            # a subject acting while the camera moves, or the words used for something else
            ("en", "a car zooms out of the parking lot"),
            ("en", "A dog zooms in and out of the bushes"),
            ("en", "she tilts up her chin"),
            ("en", "the boy tilts down the bottle"),
            ("en", "A woman dances while the camera pans left"),
            ("fr", "Une fillette zoome sur une photo avec son téléphone"),
            ("fr", "Des enfants jouent au ballon pendant que la caméra se déplace vers la droite"),
            ("fr", "Un moine allume une bougie, puis zoom avant sur son visage"),
            # no list of their own: nothing is dropped, never the English words
            ("es", "come pan con tomate"),
            ("it", "taglia il pane"),
            ("it", "mangia un panino"),
            ("de", "the camera pans left"),
        ],
    )
    def test_an_action_that_mentions_a_camera_word_is_kept(
        self, language: str, action: str
    ) -> None:
        story = tidy(self._answer(main_action=action), [0.0, 1.0, 2.0], language)  # type: ignore[arg-type]
        assert story.main_action == action

    def test_different_subjects_are_a_possible_cut(self) -> None:
        answer = self._answer(continuity="subject_changes")
        assert tidy(answer, [0.0, 1.0, 2.0], "fr").possible_cut  # type: ignore[arg-type]
