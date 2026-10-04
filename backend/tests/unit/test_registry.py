"""Stage registry ordering and cache-key chaining."""

from __future__ import annotations

import pytest

from vfe_vision.core.errors import InvalidInputError
from vfe_vision.pipeline.registry import StageRegistry
from vfe_vision.pipeline.stage import Stage, StageContext, StageFamily, StageOutcome, cache_key
from vfe_vision.pipeline.stages import default_registry


def _stage(name: str, requires: tuple[str, ...] = (), version: int = 1) -> Stage:
    class _S(Stage):
        async def execute(self, ctx: StageContext) -> StageOutcome:
            return StageOutcome.ok()

    _S.name = name
    _S.requires = requires
    _S.version = version
    return _S()


def test_plan_includes_dependencies_in_order() -> None:
    registry = StageRegistry([_stage("a"), _stage("b", ("a",)), _stage("c", ("b",))])
    assert [s.name for s in registry.plan(["c"])] == ["a", "b", "c"]
    assert [s.name for s in registry.plan(["a"])] == ["a"]


def test_unknown_stage_is_rejected() -> None:
    registry = StageRegistry([_stage("a")])
    with pytest.raises(InvalidInputError):
        registry.plan(["zzz"])


def test_missing_dependency_is_rejected_at_registration() -> None:
    with pytest.raises(ValueError, match="inconnues"):
        StageRegistry([_stage("b", ("a",))])


def test_default_registry_order() -> None:
    assert default_registry().names == [
        "probe", "metadata", "place", "weather", "sun", "analysis_pass", "keyframes", "technical",
        "audio_levels", "audio_events", "ocr", "detections", "proxy", "vision_frames",
        "grounding", "transcript", "vision_shots", "synthesis", "translation", "index",
    ]  # fmt: skip


def test_soft_dependencies_order_but_do_not_pull() -> None:
    registry = StageRegistry(
        [_stage("meta"), _stage("frames"), _stage_after("vision", ("frames",), ("meta",))]
    )
    assert [s.name for s in registry.plan(["vision"])] == ["frames", "vision"]
    assert [s.name for s in registry.plan(["vision", "meta"])] == ["meta", "frames", "vision"]


def _stage_after(name: str, requires: tuple[str, ...], after: tuple[str, ...]) -> Stage:
    stage = _stage(name, requires)
    type(stage).after = after
    return stage


def test_cache_key_changes_with_upstream_config_and_version() -> None:
    stage = _stage("x")
    base = cache_key(stage, fingerprint="f", config={"a": 1}, upstream={"p": "k1"})
    assert base == cache_key(stage, fingerprint="f", config={"a": 1}, upstream={"p": "k1"})
    assert base != cache_key(stage, fingerprint="f", config={"a": 2}, upstream={"p": "k1"})
    assert base != cache_key(stage, fingerprint="f", config={"a": 1}, upstream={"p": "k2"})
    assert base != cache_key(stage, fingerprint="g", config={"a": 1}, upstream={"p": "k1"})
    assert base != cache_key(
        _stage("x", version=2), fingerprint="f", config={"a": 1}, upstream={"p": "k1"}
    )


def test_capture_hint_is_coarse_and_local() -> None:
    from datetime import UTC, datetime

    from vfe_vision.pipeline.stages.vision_frames import capture_hint

    utc = datetime(2026, 8, 26, 15, 58, 37, tzinfo=UTC)
    assert capture_hint(utc, "Europe/Paris") == "Capture date: 2026-08-26, around 17:00 local time"
    assert capture_hint(utc, None) == "Capture date: 2026-08-26, around 15:00 UTC"
    assert capture_hint(utc.replace(second=52), "Europe/Paris") == capture_hint(utc, "Europe/Paris")


def test_downstream_follows_hard_and_soft_readers() -> None:
    registry = default_registry()
    # soft: they read the descriptions (hints for the shot stories, input of the synthesis)
    assert registry.downstream(["vision_frames"]) == [
        "grounding", "vision_shots", "synthesis", "translation", "index",
    ]  # fmt: skip
    readers = registry.downstream(["keyframes"])
    assert set(readers) == {
        "technical", "ocr", "detections", "vision_frames", "grounding", "vision_shots",
        "synthesis", "translation", "index",
    }  # fmt: skip
    assert readers == [name for name in registry.names if name in readers]  # execution order
    assert "audio_events" not in readers
    through_metadata = registry.downstream(["metadata"])  # keyframes read the colour profile
    assert {"keyframes", "vision_frames", "grounding", "weather", "sun"} <= set(through_metadata)
    assert "metadata" not in through_metadata
    # pauses cut long shots; the synthesis reads what is said
    assert registry.downstream(["transcript"]) == [
        "vision_shots", "synthesis", "translation", "index",
    ]  # fmt: skip


def test_shot_stories_need_the_keyframes_linked_to_their_shots() -> None:
    # technical is the only writer of Keyframe.shot_id, by which the frames of a shot are chosen
    registry = default_registry()
    assert "technical" in [stage.name for stage in registry.plan(["vision_shots"])]
    assert "vision_shots" in registry.dependents("technical")  # redone, or blocked, with it
    # and its readers (the index reads the usability of the shots, from their measures)
    assert registry.downstream(["technical"]) == [
        "vision_shots", "synthesis", "translation", "index",
    ]  # fmt: skip


def test_every_stage_has_a_family() -> None:
    families = {stage.name: stage.family for stage in default_registry().plan()}
    assert StageFamily.OTHER not in families.values()
    assert families["grounding"] == StageFamily.VISION
    assert families["transcript"] == StageFamily.SOUND
