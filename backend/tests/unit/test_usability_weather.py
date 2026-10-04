"""Usability of shots and blocks, and the weather consensus of a video (pure functions)."""

from __future__ import annotations

from typing import Any

import pytest

from vfe_vision.domain import usability as usability_module
from vfe_vision.domain.synthesis_input import Block, Frame, Shot, Video, WeatherFacts
from vfe_vision.domain.usability import (
    Usability,
    block_usability,
    shot_usability,
    source_issues,
    usability_by_shot,
)
from vfe_vision.domain.weather_consensus import prompt_weather, weather_consensus


def _described(
    *,
    issues: tuple[str, ...] = (),
    lighting: str = "natural_soft",
    weather: str = "sky_not_visible",
    setting: str = "outdoor",
    shot_type: str = "wide",
) -> dict[str, Any]:
    return {
        "caption": "Un chemin entre des oliviers.",
        "shot_type": shot_type,
        "setting": setting,
        "weather": weather,
        "lighting": lighting,
        "quality_issues": list(issues),
    }


def _frame(
    idx: int,
    *,
    shot: int | None = 0,
    data: dict[str, Any] | None = None,
    sharpness: float | None = None,
    metrics: dict[str, Any] | None = None,
) -> Frame:
    return Frame(
        idx=idx,
        keyframe_id=f"k{idx}",
        t=float(idx),
        shot=shot,
        sharpness=sharpness,
        metrics=metrics or {},
        data=data,
    )


def _shot(
    idx: int,
    start: float,
    end: float,
    *,
    motion: str = "static",
    stability: float = 1.0,
    **metrics: float,
) -> Shot:
    return Shot(idx, start, end, motion, stability, "cut", dict(metrics))


def _video(
    shots: tuple[Shot, ...] = (),
    frames: tuple[Frame, ...] = (),
    weather: WeatherFacts | None = None,
) -> Video:
    return Video(
        id="v1",
        filename="clip.mp4",
        duration=shots[-1].end if shots else 10.0,
        orientation="horizontal",
        capture_local=None,
        place_label=None,
        place_feature=None,
        light_phase=None,
        day_part=None,
        weather=weather,
        presence={},
        heard=(),
        instruments=(),
        transcript_language=None,
        segments=(),
        silences=(),
        shots=shots,
        frames=frames,
    )


def _block(no: int, start: float, end: float, shots: tuple[int, ...], video: Video) -> Block:
    frames = tuple(f for f in video.frames if start <= f.t < end)
    return Block(no, start, end, shots, frames, 0.0, ())


def _alone(shot: Shot, *frames: Frame) -> Usability:
    return shot_usability(_video((shot,), frames), shot)


# ---------------------------------------------------------------- usability: camera and length
class TestLengthAndCamera:
    def test_a_long_stable_clean_shot_scores_100(self) -> None:
        shot = _shot(0, 0.0, 10.0)
        assert _alone(shot, _frame(0, data=_described()), _frame(1, data=_described())) == (
            Usability(100, ())
        )

    def test_a_shot_without_any_keyframe_is_judged_on_its_measurements(self) -> None:
        assert _alone(_shot(0, 0.0, 10.0)) == Usability(100, ())

    def test_a_short_shot_is_hard_to_use(self) -> None:
        assert _alone(_shot(0, 0.0, 1.5)) == Usability(85, ("court",))

    def test_the_sub_second_shaky_shot_scores_low(self) -> None:
        # The 0.6 s handheld shot of RIZ: 35 (flash) + 25 × 0.8 (shake).
        usable = _alone(_shot(0, 0.0, 0.6, motion="handheld", stability=0.1))
        assert usable == Usability(45, ("très court", "instable"))
        assert usable.score < 50

    def test_a_deliberate_camera_move_pays_half_its_shake(self) -> None:
        handheld = _alone(_shot(0, 0.0, 10.0, motion="handheld", stability=0.0))
        pan = _alone(_shot(0, 0.0, 10.0, motion="pan_left", stability=0.0))
        assert handheld == Usability(75, ("instable",))
        assert pan == Usability(88, ("instable",))  # 100 − 12.5

    def test_a_small_penalty_lowers_the_score_without_a_reason(self) -> None:
        assert _alone(_shot(0, 0.0, 10.0, motion="handheld", stability=0.45)) == Usability(98, ())


# ---------------------------------------------------------------- usability: picture
class TestPicture:
    def test_a_screen_recording_is_neither_frozen_nor_dark(self) -> None:
        # Static and frozen (a still screen) with a dark theme: intent, not defects.
        shot = _shot(0, 0.0, 60.0, frozen_ratio=0.95, luma=0.08)
        frames = [
            _frame(i, data=_described(lighting="artificial", setting="indoor")) for i in (0, 1, 2)
        ]
        assert _alone(shot, *frames) == Usability(100, ())

    def test_a_frozen_picture_in_a_moving_shot_is_a_glitch(self) -> None:
        shot = _shot(0, 0.0, 60.0, motion="handheld", stability=0.8, frozen_ratio=0.95)
        assert _alone(shot) == Usability(72, ("image figée",))  # 100 − 28.5

    def test_dark_only_when_the_vision_model_sees_it_dark_too(self) -> None:
        shot = _shot(0, 0.0, 10.0, luma=0.08)
        clean = [_frame(i, data=_described()) for i in (0, 1, 2)]
        assert _alone(shot, *clean) == Usability(100, ())
        one_dark = [*clean[:2], _frame(2, data=_described(lighting="low_light"))]
        # A third of the keyframes is enough; the flag itself is below the 34% bar.
        assert _alone(shot, *one_dark) == Usability(80, ("sombre",))

    def test_dark_with_black_frames(self) -> None:
        shot = _shot(0, 0.0, 10.0, luma=0.05, black_ratio=0.5)
        assert _alone(shot) == Usability(50, ("noir", "sombre"))

    def test_a_bright_shot_flagged_overexposed_gives_one_reason(self) -> None:
        shot = _shot(0, 0.0, 10.0, luma=0.9)
        frames = [_frame(i, data=_described(issues=("overexposed",))) for i in (0, 1)]
        assert _alone(shot) == Usability(85, ("surexposé",))
        assert _alone(shot, *frames) == Usability(70, ("surexposé",))  # 15 + 30 × 0.5

    def test_clipped_highlights_on_average(self) -> None:
        frames = [
            _frame(0, metrics={"clipped_highlights": 0.1}),
            _frame(1, metrics={"clipped_highlights": 0.02}),
        ]
        assert _alone(_shot(0, 0.0, 10.0), *frames) == Usability(90, ("hautes lumières brûlées",))

    def test_defect_flags_weigh_by_their_share_of_the_shot(self) -> None:
        shot = _shot(0, 0.0, 10.0)
        blurred = [_frame(i, data=_described(issues=("blur",))) for i in (0, 1)]
        assert _alone(shot, *blurred) == Usability(82, ("flou",))  # 30 × 0.6
        once = [_frame(0, data=_described(issues=("blur",)))]
        once += [_frame(i, data=_described()) for i in (1, 2)]
        assert _alone(shot, *once) == Usability(100, ())  # 1/3 < 34%
        two_of_five = [_frame(i, data=_described(issues=("motion_blur",))) for i in (0, 1)]
        two_of_five += [_frame(i, data=_described()) for i in (2, 3, 4)]
        assert _alone(shot, *two_of_five) == Usability(94, ("flou de bougé",))  # 30 × 0.5 × 0.4

    def test_undescribed_keyframes_do_not_dilute_the_flags(self) -> None:
        frames = [_frame(0, data=_described(issues=("blur",))), _frame(1), _frame(2)]
        assert _alone(_shot(0, 0.0, 10.0), *frames) == Usability(82, ("flou",))

    def test_at_most_three_reasons_most_important_first(self) -> None:
        shot = _shot(0, 0.0, 0.6, motion="handheld", stability=0.3, luma=0.05, black_ratio=0.5)
        # 35 (flash) + 30 (black) + 20 (dark) + 10 (shake) = 95.
        assert _alone(shot) == Usability(5, ("très court", "noir", "sombre"))

    def test_a_shot_less_sharp_than_the_rest_of_its_video(self) -> None:
        shots = (_shot(0, 0.0, 40.0), _shot(1, 40.0, 50.0))
        frames = tuple(_frame(i, sharpness=s) for i, s in enumerate((100.0, 110.0, 90.0, 105.0)))
        blurry = _frame(4, shot=1, sharpness=30.0)
        video = _video(shots, (*frames, blurry))
        assert shot_usability(video, shots[1]) == Usability(88, ("moins net que le reste",))
        assert shot_usability(video, shots[0]) == Usability(100, ())
        # Fewer than four measured keyframes: nothing to compare with.
        small = _video(shots, (*frames[:2], blurry))
        assert shot_usability(small, shots[1]) == Usability(100, ())


# ---------------------------------------------------------------- usability: the source
class TestSourceIssues:
    def test_archive_grain_is_not_held_against_each_shot(self) -> None:
        shots = tuple(_shot(i, 10.0 * i, 10.0 * (i + 1)) for i in range(5))
        frames = []
        for i in range(10):
            issues: tuple[str, ...] = ("noise",) if i != 0 else ()
            if i in (8, 9):
                issues = ("noise", "blur")
            frames.append(_frame(i, shot=i // 2, data=_described(issues=issues)))
        video = _video(shots, tuple(frames))
        assert source_issues(video.frames) == frozenset({"noise"})  # 9 of 10 keyframes
        assert [shot_usability(video, s) for s in shots[:4]] == [Usability(100, ())] * 4
        assert shot_usability(video, shots[4]) == Usability(82, ("flou",))

    def test_the_same_grain_in_a_short_clip_counts(self) -> None:
        shot = _shot(0, 0.0, 10.0)
        frames = [_frame(i, data=_described(issues=("noise",))) for i in range(4)]
        assert source_issues(frames) == frozenset()  # under five described keyframes
        assert _alone(shot, *frames) == Usability(94, ("bruit",))

    def test_only_described_keyframes_count(self) -> None:
        frames = [_frame(i, data=_described(issues=("noise",))) for i in range(5)]
        frames += [_frame(i) for i in range(5, 15)]
        assert source_issues(frames) == frozenset({"noise"})
        assert source_issues([]) == frozenset()

    def test_the_share_is_adjustable(self) -> None:
        frames = [_frame(i, data=_described(issues=("noise",) if i < 3 else ())) for i in range(5)]
        assert source_issues(frames) == frozenset()
        assert source_issues(frames, share=0.6) == frozenset({"noise"})


# ---------------------------------------------------------------- usability: blocks
class TestBlockUsability:
    def test_a_one_shot_block_has_its_shot_usability(self) -> None:
        shot = _shot(0, 0.0, 1.5)
        video = _video((shot,))
        assert block_usability(video, _block(1, 0.0, 1.5, (0,), video)) == Usability(85, ("court",))

    def test_merged_shots_weigh_by_duration(self) -> None:
        shots = (_shot(0, 0.0, 0.5), _shot(1, 0.5, 10.0))
        video = _video(shots)
        block = _block(1, 0.0, 10.0, (0, 1), video)
        # (65 × 0.5 + 100 × 9.5) / 10 = 98.25
        assert block_usability(video, block) == Usability(98, ("très court",))
        by_shot = usability_by_shot(video)
        assert block_usability(video, block, by_shot=by_shot) == Usability(98, ("très court",))

    def test_shots_without_length_are_averaged_plainly(self) -> None:
        shots = (_shot(0, 5.0, 5.0), _shot(1, 5.0, 5.0, motion="handheld", stability=0.0))
        video = _video(shots)
        usable = block_usability(video, _block(1, 5.0, 5.0, (0, 1), video))
        assert usable == Usability(52, ("très court", "instable"))  # (65 + 40) / 2 = 52.5

    def test_a_block_without_a_measured_shot_is_judged_on_its_length_and_keyframes(self) -> None:
        video = _video((), (_frame(0, shot=None, data=_described(issues=("blur",))),))
        assert block_usability(video, _block(1, 0.0, 10.0, (), video)) == Usability(82, ("flou",))
        assert block_usability(video, _block(1, 2.0, 2.5, (7,), video)) == Usability(
            65, ("très court",)
        )

    def test_a_corrupt_shot_length_never_lifts_a_block_over_100(self) -> None:
        # A shot ending before it starts weighed −8 s: (100 × 10 − 65 × 8) / 2 = 240.
        shots = (_shot(0, 0.0, 10.0), _shot(1, 10.0, 2.0))
        video = _video(shots)
        usable = block_usability(video, _block(1, 0.0, 10.0, (0, 1), video))
        assert usable == Usability(100, ("très court",))

    def test_video_facts_are_computed_once_per_video(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Shot after shot then block after block, as the stage and the service call it: the
        # video-wide facts were recomputed on each call (13 s on a 5-hour video).
        calls: list[str] = []
        compute = usability_module._facts

        def counted(video: Video) -> Any:
            calls.append(video.id)
            return compute(video)

        monkeypatch.setattr(usability_module, "_facts", counted)
        shots = tuple(_shot(i, 5.0 * i, 5.0 * (i + 1)) for i in range(50))
        first = _video(shots, tuple(_frame(i, shot=i) for i in range(50)))
        for shot in shots:
            shot_usability(first, shot)
        for no, shot in enumerate(shots, 1):
            block_usability(first, _block(no, shot.start, shot.end, (shot.idx,), first))
        assert calls == ["v1"]
        # Another video (even an equal one) is never served the first one's facts.
        second = _video(shots, (_frame(0, data=_described(issues=("blur",))), *first.frames[1:]))
        assert shot_usability(second, shots[0]) == Usability(82, ("flou",))
        assert shot_usability(first, shots[0]) == Usability(100, ())
        assert shot_usability(_video(shots, first.frames), shots[0]) == Usability(100, ())
        assert len(calls) == 3

    def test_a_very_long_video(self) -> None:
        # One hour, 400 shots, 1,200 keyframes: every shot scored once, the same either way.
        shots = tuple(
            _shot(i, 9.0 * i, 9.0 * (i + 1), motion="handheld", stability=0.2 + (i % 5) / 10)
            for i in range(400)
        )
        frames = tuple(
            _frame(k, shot=k // 3, sharpness=100.0 + k % 7,
                   data=_described(issues=("blur",) if k % 9 == 0 else ()))
            for k in range(1200)
        )  # fmt: skip
        video = _video(shots, frames)
        by_shot = usability_by_shot(video)
        assert len(by_shot) == 400
        for i in range(0, 400, 37):
            assert by_shot[i] == shot_usability(video, shots[i])
        block = _block(1, 90.0, 135.0, tuple(range(10, 15)), video)
        assert block_usability(video, block) == block_usability(video, block, by_shot=by_shot)
        assert all(0 <= u.score <= 100 and len(u.reasons) <= 3 for u in by_shot.values())


# ---------------------------------------------------------------- weather consensus
def _weather(
    category: str | None,
    *,
    cloud: float | None = 50.0,
    sun: float | None = None,
    day: bool | None = True,
    temperature: float | None = 20.0,
    rain_mm: float | None = 0.0,
) -> WeatherFacts:
    return WeatherFacts(category, cloud, sun, day, temperature, rain_mm)


def _sky(*views: tuple[str, str, str]) -> tuple[Frame, ...]:
    """Frames from (weather, shot type, setting)."""
    return tuple(
        _frame(i, data=_described(weather=w, shot_type=t, setting=s))
        for i, (w, t, s) in enumerate(views)
    )


WIDE_CLEAR = ("clear", "wide", "outdoor")


class TestWeatherConsensus:
    def test_indoor_frames_never_vote(self) -> None:
        # The museum clip: two gallery frames said « clear » and the weather reached the prose.
        frames = _sky(("clear", "medium", "indoor"), ("clear", "wide", "indoor"))
        consensus = weather_consensus(_video(frames=frames, weather=_weather("partly_cloudy")))
        assert consensus.category == "partly_cloudy"
        assert consensus.agreement == "api_only"
        assert consensus.confidence == "medium"
        assert consensus.visual_votes == {}
        assert not consensus.outdoor_view
        assert "ciel non visible" in consensus.line
        assert prompt_weather(consensus) is None

    def test_a_sunny_overcast_hour_is_a_high_veil(self) -> None:
        weather = _weather("overcast", cloud=90.0, sun=1.0, temperature=24.0)
        consensus = weather_consensus(_video(frames=_sky(WIDE_CLEAR, WIDE_CLEAR), weather=weather))
        assert (consensus.category, consensus.agreement, consensus.confidence) == (
            "clear", "close", "medium",
        )  # fmt: skip
        assert consensus.line == (
            "ciel dégagé, voile nuageux possible (Open-Meteo : couvert, nuages 90 %, soleil "
            "100 % de l'heure, 24 °C ; images : ciel dégagé (100 % des votes, poids 2,0))"
        )
        assert consensus.visual_votes == {"clear": 2.0}
        assert consensus.outdoor_view
        assert prompt_weather(consensus) == "ciel dégagé (Open-Meteo and pictures close)"

    def test_a_veil_keeps_partly_cloudy_frames(self) -> None:
        frames = _sky(("partly_cloudy", "wide", "outdoor"))
        consensus = weather_consensus(_video(frames=frames, weather=_weather("overcast", sun=0.8)))
        assert (consensus.category, consensus.agreement) == ("partly_cloudy", "close")
        assert "voile nuageux possible" in consensus.line

    def test_opposite_ends_without_sunshine_disagree(self) -> None:
        weather = _weather("overcast", sun=0.2)
        few = weather_consensus(_video(frames=_sky(WIDE_CLEAR, WIDE_CLEAR), weather=weather))
        assert (few.category, few.agreement, few.confidence) == ("overcast", "disagree", "low")
        assert few.line.startswith("couvert, sources en désaccord (")
        many = weather_consensus(_video(frames=_sky(*[WIDE_CLEAR] * 4), weather=weather))
        assert (many.category, many.agreement, many.confidence) == ("clear", "disagree", "low")
        assert prompt_weather(many) == "ciel dégagé (sources disagree)"

    def test_one_step_apart_is_partly_cloudy(self) -> None:
        frames = _sky(("partly_cloudy", "wide", "outdoor"))
        consensus = weather_consensus(_video(frames=frames, weather=_weather("clear")))
        assert (consensus.category, consensus.agreement, consensus.confidence) == (
            "partly_cloudy", "close", "medium",
        )  # fmt: skip
        dim = weather_consensus(_video(frames=frames, weather=_weather("overcast", sun=0.3)))
        assert (dim.category, dim.agreement) == ("partly_cloudy", "close")
        assert "voile" not in dim.line

    def test_both_sources_agree(self) -> None:
        consensus = weather_consensus(_video(frames=_sky(WIDE_CLEAR), weather=_weather("clear")))
        assert (consensus.agreement, consensus.confidence) == ("agree", "high")
        assert consensus.line.startswith("ciel dégagé, Open-Meteo et images d'accord (")
        assert prompt_weather(consensus) == "ciel dégagé (Open-Meteo and pictures agree)"

    def test_open_meteo_only_when_no_frame_shows_the_sky(self) -> None:
        # The night stage of the library: outdoor wide views, the sky out of frame. The weather
        # still belongs to the scene (an outdoor wide or medium frame is enough).
        frames = _sky(
            ("sky_not_visible", "wide", "outdoor"), ("sky_not_visible", "close_up", "outdoor")
        )
        consensus = weather_consensus(_video(frames=frames, weather=_weather("overcast")))
        assert (consensus.category, consensus.agreement, consensus.confidence) == (
            "overcast", "api_only", "medium",
        )  # fmt: skip
        assert consensus.outdoor_view
        assert prompt_weather(consensus) == "couvert (Open-Meteo only)"
        close = weather_consensus(_video(frames=frames[1:], weather=_weather("overcast")))
        assert not close.outdoor_view
        assert prompt_weather(close) is None

    def test_open_meteo_only_with_a_glimpse_of_sky(self) -> None:
        # One medium frame weighs 0.5: no visual opinion, but an outdoor view of the sky.
        frames = _sky(("overcast", "medium", "outdoor"))
        consensus = weather_consensus(_video(frames=frames, weather=_weather("overcast")))
        assert consensus.agreement == "api_only"
        assert "ciel peu visible" in consensus.line
        assert consensus.visual_votes == {"overcast": 0.5}
        assert consensus.outdoor_view
        assert prompt_weather(consensus) == "couvert (Open-Meteo only)"

    def test_pictures_only(self) -> None:
        two_wide = weather_consensus(_video(frames=_sky(WIDE_CLEAR, WIDE_CLEAR)))
        assert (two_wide.category, two_wide.agreement, two_wide.confidence) == (
            "clear", "visual_only", "medium",
        )  # fmt: skip
        assert two_wide.line == (
            "ciel dégagé d'après les images seules (images : ciel dégagé (100 % des votes, "
            "poids 2,0))"
        )
        assert prompt_weather(two_wide) == "ciel dégagé (pictures only)"
        one_wide = weather_consensus(
            _video(frames=_sky(WIDE_CLEAR, ("clear", "close_up", "outdoor")))
        )
        assert (one_wide.agreement, one_wide.confidence) == ("visual_only", "low")
        split = weather_consensus(
            _video(frames=_sky(WIDE_CLEAR, WIDE_CLEAR, ("overcast", "wide", "outdoor")))
        )
        assert (split.category, split.confidence) == ("clear", "low")  # 67% < 70%

    def test_unknown_without_open_meteo_or_sky(self) -> None:
        for frames in ((), _sky(("clear", "wide", "indoor")), (_frame(0), _frame(1))):
            consensus = weather_consensus(_video(frames=frames))
            assert consensus.category is None
            assert (consensus.agreement, consensus.confidence) == ("unknown", "low")
            assert consensus.line.startswith("météo inconnue")
            assert prompt_weather(consensus) is None

    def test_rain_seen_without_rain_in_the_model_hour(self) -> None:
        rainy = _sky(("rain", "wide", "outdoor"), ("rain", "wide", "outdoor"))
        consensus = weather_consensus(_video(frames=rainy, weather=_weather("overcast")))
        assert (consensus.category, consensus.agreement, consensus.confidence) == (
            "overcast", "disagree", "low",
        )  # fmt: skip
        assert "pluie vue sur l'image non confirmée" in consensus.line
        snowy = _sky(("snow", "wide", "outdoor"))
        assert (
            "neige vue sur l'image"
            in weather_consensus(_video(frames=snowy, weather=_weather("clear"))).line
        )
        wet = weather_consensus(_video(frames=rainy, weather=_weather("overcast", rain_mm=1.2)))
        assert wet.agreement == "disagree"
        assert "non confirmée" not in wet.line
        assert "pluie 1,2 mm" in wet.line
        assert (
            weather_consensus(_video(frames=rainy, weather=_weather("rain"))).agreement == "agree"
        )

    def test_close_ups_weigh_little(self) -> None:
        frames = _sky(*[("clear", "close_up", "outdoor")] * 3)  # 0.75 < 1
        consensus = weather_consensus(_video(frames=frames, weather=_weather("overcast")))
        assert consensus.agreement == "api_only"
        assert consensus.visual_votes == {"clear": 0.75}
        assert not consensus.outdoor_view  # close-ups are not an outdoor view

    def test_mixed_and_unknown_settings_vote_but_are_not_an_outdoor_view(self) -> None:
        frames = _sky(("clear", "wide", "mixed"), ("clear", "wide", "unknown"))
        consensus = weather_consensus(_video(frames=frames, weather=_weather("clear")))
        assert consensus.agreement == "agree"
        assert not consensus.outdoor_view
        assert prompt_weather(consensus) is None

    def test_a_tie_goes_to_the_first_sky_seen(self) -> None:
        frames = _sky(WIDE_CLEAR, ("overcast", "wide", "outdoor"))
        consensus = weather_consensus(_video(frames=frames))
        assert consensus.category == "clear"
        assert "50 % des votes" in consensus.line

    @pytest.mark.parametrize(
        ("weather", "expected"),
        [
            (_weather("clear", sun=0.9, day=False), "Open-Meteo : ciel dégagé, nuages 50 %, 20 °C"),
            (_weather("clear", cloud=None, temperature=None), "Open-Meteo : ciel dégagé ;"),
            (_weather("clear", temperature=-0.3), "nuages 50 %, 0 °C"),
            (_weather("clear", sun=0.25), "soleil 25 % de l'heure"),
        ],
    )
    def test_the_open_meteo_details(self, weather: WeatherFacts, expected: str) -> None:
        consensus = weather_consensus(_video(frames=_sky(WIDE_CLEAR), weather=weather))
        assert expected in consensus.line
        if not weather.is_day:
            assert "soleil" not in consensus.line

    def test_an_unknown_model_category_leaves_the_pictures(self) -> None:
        weather = _weather(None, cloud=30.0)
        consensus = weather_consensus(_video(frames=_sky(WIDE_CLEAR, WIDE_CLEAR), weather=weather))
        assert consensus.agreement == "visual_only"
        assert "Open-Meteo : nuages 30 %" in consensus.line
