"""Pipeline runner semantics: invalidation on re-execution, configuration errors, settled skips."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
import sqlalchemy as sa
import structlog

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import VfeError
from vfe_vision.db.models import LibraryRoot, StageRun, Video
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.pipeline.registry import StageRegistry
from vfe_vision.pipeline.runner import PipelineRunner
from vfe_vision.pipeline.stage import (
    Resource,
    Stage,
    StageContext,
    StageOutcome,
    Toolbox,
    VideoRef,
)

CALLS: Counter[str] = Counter()


class _Events:
    def emit(self, type_: str, **_: Any) -> None:
        pass


SETTINGS: dict[str, Any] = {}  # what a stage's cache_config reads (model, preferences…)
FACTS: dict[str, Any] = {}  # what a stage's input_facts reads (capture time, position…)


def _stage(
    name: str,
    requires: tuple[str, ...] = (),
    *,
    optional: bool = False,
    outcome: StageOutcome | Callable[[], StageOutcome] | None = None,
    config_error: bool = False,
    cancels: bool = False,
    version: int = 1,
) -> Stage:
    class _S(Stage):
        def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
            return {"setting": SETTINGS.get(name)}

        def input_facts(self, ctx: StageContext) -> dict[str, Any]:
            return {"fact": FACTS.get(name)}

        async def resolve_config(self, ctx: StageContext) -> dict[str, Any]:
            if config_error:
                raise VfeError("LM Studio a répondu 500")
            return {}

        async def execute(self, ctx: StageContext) -> StageOutcome:
            CALLS[name] += 1
            if cancels:
                ctx.cancel.cancel()
            if callable(outcome):
                return outcome()
            return outcome or StageOutcome.ok()

    _S.name, _S.version, _S.requires, _S.optional = name, version, requires, optional
    return _S()


@pytest.fixture(autouse=True)
def _reset() -> None:
    CALLS.clear()
    SETTINGS.clear()
    FACTS.clear()


@pytest.fixture
def video(db: Database, tmp_path: Path) -> VideoRef:
    with db.write() as session:
        root = LibraryRoot(path=str(tmp_path), path_key=str(tmp_path).lower(), label="t")
        session.add(root)
        session.flush()
        row = Video(
            root_id=root.id, path=str(tmp_path / "a.mp4"), path_key=str(tmp_path / "a.mp4"),
            rel_path="a.mp4", filename="a.mp4", size_bytes=1, mtime=0.0, fingerprint="f" * 16,
        )  # fmt: skip
        session.add(row)
        session.flush()
        return VideoRef(id=row.id, path=Path(row.path), filename=row.filename, fingerprint="f" * 16)


def _ctx(db: Database, video: VideoRef, cancel: CancelToken | None = None) -> StageContext:
    tools = cast(Toolbox, type("T", (), {"db": db})())
    return StageContext(
        video=video,
        prefs=AnalysisPreferences(),
        tools=tools,
        cancel=cancel or CancelToken(),
        progress=lambda _f, _m: None,
        log=structlog.get_logger("test"),
    )


def _runs(db: Database, stage: str) -> list[StageStatus]:
    with db.read() as session:
        return list(
            session.execute(
                sa.select(StageRun.status)
                .where(StageRun.stage == stage)
                .order_by(StageRun.created_at)
            ).scalars()
        )


def _asr_stage(name: str, *, skip: bool = False) -> Stage:
    class _S(Stage):
        resource = Resource.ASR

        def precheck(self, ctx: StageContext) -> StageOutcome | None:
            return StageOutcome.skipped("rien à transcrire") if skip else None

        async def execute(self, ctx: StageContext) -> StageOutcome:
            CALLS[name] += 1
            return StageOutcome.ok()

    _S.name, _S.version = name, 1
    return _S()


@pytest.mark.anyio
async def test_waiting_for_a_busy_engine_can_be_cancelled_and_frees_the_slot(
    db: Database, video: VideoRef
) -> None:
    engine = anyio.Semaphore(1)
    runner = PipelineRunner(
        StageRegistry([_asr_stage("speech")]), limits={Resource.ASR: engine}, events=_Events()
    )
    cancel = CancelToken()
    await engine.acquire()  # another video is being transcribed
    report: list[Any] = []
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:

            async def run() -> None:
                report.append(await runner.run(_ctx(db, video, cancel), job_id=None))

            tg.start_soon(run)
            await anyio.sleep(0.3)  # the job reaches the engine's queue
            assert runner.waiting == 1  # it no longer takes a video slot
            cancel.cancel()  # the user cancels the waiting job
    engine.release()
    assert report[0].statuses["speech"] == StageStatus.CANCELLED
    assert runner.waiting == 0
    assert CALLS["speech"] == 0


@pytest.mark.anyio
async def test_a_precheck_skip_does_not_wait_for_the_engine(db: Database, video: VideoRef) -> None:
    engine = anyio.Semaphore(1)
    runner = PipelineRunner(
        StageRegistry([_asr_stage("speech", skip=True)]),
        limits={Resource.ASR: engine},
        events=_Events(),
    )
    await engine.acquire()  # busy for as long as it takes
    with anyio.fail_after(5):
        report = await runner.run(_ctx(db, video), job_id=None)
    engine.release()
    assert report.statuses["speech"] == StageStatus.SKIPPED
    assert CALLS["speech"] == 0


def test_interrupted_stage_runs_do_not_stay_running(db: Database, video: VideoRef) -> None:
    from vfe_vision.jobs import queue

    with db.write() as session:
        session.add(
            StageRun(
                video_id=video.id, stage="speech", stage_version=1, cache_key="k",
                status=StageStatus.RUNNING,
            )
        )  # fmt: skip
    queue.requeue_interrupted(db)
    assert _runs(db, "speech") == [StageStatus.CANCELLED]


@pytest.mark.anyio
async def test_cancelled_forced_run_does_not_leave_stale_dependents(
    db: Database, video: VideoRef
) -> None:
    CALLS.clear()
    frames = _stage("frames")
    registry = StageRegistry([frames, _stage("describe", ("frames",))])
    runner = PipelineRunner(registry, events=_Events())
    await runner.run(_ctx(db, video), job_id=None)
    assert CALLS == {"frames": 1, "describe": 1}

    # Forced frames run, cancelled before "describe" could redo its work.
    type(frames).execute = _stage("frames", cancels=True).execute  # type: ignore[method-assign,assignment]
    report = await runner.run(_ctx(db, video), job_id=None, force=["frames"])
    assert report.cancelled
    assert report.statuses["describe"] == StageStatus.CANCELLED

    # The next ordinary run must redo "describe": its previous outputs were replaced.
    type(frames).execute = _stage("frames").execute  # type: ignore[method-assign,assignment]
    report = await runner.run(_ctx(db, video), job_id=None)
    assert report.statuses == {"frames": StageStatus.CACHED, "describe": StageStatus.SUCCEEDED}
    assert CALLS["describe"] == 2


@pytest.mark.anyio
async def test_configuration_errors_are_stage_results(db: Database, video: VideoRef) -> None:
    registry = StageRegistry(
        [_stage("probe"), _stage("vision", ("probe",), optional=True, config_error=True)]
    )
    report = await PipelineRunner(registry, events=_Events()).run(_ctx(db, video), job_id=None)
    assert report.statuses["vision"] == StageStatus.SKIPPED
    assert (report.incomplete, report.failed_required) == (True, False)
    assert _runs(db, "vision") == [StageStatus.SKIPPED]

    required = StageRegistry([_stage("needed", config_error=True)])
    report = await PipelineRunner(required, events=_Events()).run(_ctx(db, video), job_id=None)
    assert (report.statuses["needed"], report.failed_required) == (StageStatus.FAILED, True)
    assert _runs(db, "needed") == [StageStatus.FAILED]


@pytest.mark.anyio
async def test_settled_skip_does_not_block_dependents(db: Database, video: VideoRef) -> None:
    CALLS.clear()
    registry = StageRegistry(
        [
            _stage("pass", outcome=StageOutcome.skipped("Trop peu d'images décodées")),
            _stage("tech", ("pass",)),
        ]
    )
    runner = PipelineRunner(registry, events=_Events())
    first = await runner.run(_ctx(db, video), job_id=None)
    second = await runner.run(_ctx(db, video), job_id=None)
    assert first.statuses == {"pass": StageStatus.SKIPPED, "tech": StageStatus.SUCCEEDED}
    assert second.statuses == {"pass": StageStatus.CACHED, "tech": StageStatus.CACHED}
    assert not first.incomplete
    assert not second.incomplete


@pytest.mark.anyio
async def test_degraded_results_are_used_but_redone(db: Database, video: VideoRef) -> None:
    """A usable fallback (e.g. an approximate place during an outage) succeeds, lets dependents
    run, and is recomputed at the next analysis instead of being served from the cache."""
    registry = StageRegistry(
        [_stage("fallback", outcome=StageOutcome.degraded("service injoignable")),
         _stage("after_it", ("fallback",))]
    )  # fmt: skip
    runner = PipelineRunner(registry, events=_Events())
    first = await runner.run(_ctx(db, video), job_id=None)
    assert first.incomplete
    assert _runs(db, "fallback") == [StageStatus.SUCCEEDED]
    assert _runs(db, "after_it") == [StageStatus.SUCCEEDED]
    await runner.run(_ctx(db, video), job_id=None)
    assert _runs(db, "fallback") == [StageStatus.SUCCEEDED, StageStatus.SUCCEEDED]


# ------------------------------------------------ analysis modes: what is kept, what is redone
CACHED, SUCCEEDED, SKIPPED = StageStatus.CACHED, StageStatus.SUCCEEDED, StageStatus.SKIPPED


@pytest.mark.anyio
async def test_finished_work_survives_new_settings_models_and_versions(
    db: Database, video: VideoRef
) -> None:
    """An ordinary analysis keeps finished work; only an update redoes it."""
    runner = PipelineRunner(
        StageRegistry([_stage("frames"), _stage("describe", ("frames",))]), events=_Events()
    )
    await runner.run(_ctx(db, video), job_id=None)
    SETTINGS["describe"] = "qwen/qwen3-vl-4b"  # another vision model is loaded
    kept = await runner.run(_ctx(db, video), job_id=None)
    assert kept.statuses == {"frames": CACHED, "describe": CACHED}

    newer = PipelineRunner(
        StageRegistry([_stage("frames"), _stage("describe", ("frames",), version=2)]),
        events=_Events(),
    )
    assert (await newer.run(_ctx(db, video), job_id=None)).statuses["describe"] == CACHED
    updated = await newer.run(_ctx(db, video), job_id=None, refresh=True)
    assert updated.statuses == {"frames": CACHED, "describe": SUCCEEDED}
    again = await newer.run(_ctx(db, video), job_id=None, refresh=True)
    assert again.statuses == {"frames": CACHED, "describe": CACHED}
    assert CALLS == {"frames": 1, "describe": 2}


@pytest.mark.anyio
async def test_changed_video_data_is_always_redone(db: Database, video: VideoRef) -> None:
    registry = StageRegistry(
        [_stage("metadata"), _stage("weather"), _stage("frames"), _stage("describe", ("frames",))]
    )
    runner = PipelineRunner(registry, events=_Events())
    await runner.run(_ctx(db, video), job_id=None)
    FACTS["weather"] = "re-dated"  # the capture time the weather is computed for changed
    FACTS["frames"] = "log profile detected"
    report = await runner.run(_ctx(db, video), job_id=None)
    assert report.statuses == {
        "metadata": CACHED, "weather": SUCCEEDED, "frames": SUCCEEDED, "describe": SUCCEEDED
    }  # fmt: skip


@pytest.mark.anyio
async def test_skips_are_retried_once_their_cause_changed(db: Database, video: VideoRef) -> None:
    def weather() -> StageOutcome:
        if SETTINGS.get("weather") != "online":
            return StageOutcome.skipped("Services en ligne désactivés (mode hors-ligne)")
        return StageOutcome.ok()

    runner = PipelineRunner(StageRegistry([_stage("weather", outcome=weather)]), events=_Events())
    first = await runner.run(_ctx(db, video), job_id=None)
    assert (first.statuses, first.incomplete) == ({"weather": SKIPPED}, False)
    assert (await runner.run(_ctx(db, video), job_id=None)).statuses == {"weather": CACHED}
    SETTINGS["weather"] = "online"  # a skip is no result: it is redone
    assert (await runner.run(_ctx(db, video), job_id=None)).statuses == {"weather": SUCCEEDED}
    SETTINGS["weather"] = "offline"  # a result is kept
    assert (await runner.run(_ctx(db, video), job_id=None)).statuses == {"weather": CACHED}


@pytest.mark.anyio
async def test_redoing_named_stages_completes_their_dependencies(
    db: Database, video: VideoRef
) -> None:
    registry = StageRegistry(
        [_stage("probe"), _stage("frames", ("probe",)), _stage("describe", ("frames",))]
    )
    runner = PipelineRunner(registry, events=_Events())
    await runner.run(_ctx(db, video), job_id=None)
    report = await runner.run(_ctx(db, video), job_id=None, stages=["describe"], force=["describe"])
    assert report.statuses == {"probe": CACHED, "frames": CACHED, "describe": SUCCEEDED}


@pytest.mark.anyio
async def test_results_from_before_input_keys_are_kept_then_tracked(
    db: Database, video: VideoRef
) -> None:
    runner = PipelineRunner(StageRegistry([_stage("weather")]), events=_Events())
    await runner.run(_ctx(db, video), job_id=None)
    with db.write() as session:  # a result recorded before migration 0005
        session.execute(sa.update(StageRun).values(input_key=None))
    SETTINGS["weather"] = "changed"
    assert (await runner.run(_ctx(db, video), job_id=None)).statuses == {"weather": CACHED}
    with db.read() as session:
        assert session.execute(sa.select(StageRun.input_key)).scalar_one() is not None
    FACTS["weather"] = "re-dated"  # today's data became its baseline: a change is seen
    assert (await runner.run(_ctx(db, video), job_id=None)).statuses == {"weather": SUCCEEDED}


@pytest.mark.anyio
async def test_provisional_results_follow_their_settings(db: Database, video: VideoRef) -> None:
    """An approximate offline place is kept offline, and replaced once back online."""

    def place() -> StageOutcome:
        if SETTINGS.get("place") == "offline":
            return StageOutcome.provisional(source="offline")
        return StageOutcome.ok(source="nominatim")

    SETTINGS["place"] = "offline"
    runner = PipelineRunner(StageRegistry([_stage("place", outcome=place)]), events=_Events())
    assert (await runner.run(_ctx(db, video), job_id=None)).statuses == {"place": SUCCEEDED}
    assert (await runner.run(_ctx(db, video), job_id=None)).statuses == {"place": CACHED}
    SETTINGS["place"] = "online"
    assert (await runner.run(_ctx(db, video), job_id=None)).statuses == {"place": SUCCEEDED}
    SETTINGS["place"] = "offline"  # the precise place is a real result: kept offline
    assert (await runner.run(_ctx(db, video), job_id=None)).statuses == {"place": CACHED}


@pytest.mark.anyio
async def test_named_stages_bring_their_readers_up_to_date(db: Database, video: VideoRef) -> None:
    frames = _stage("frames")
    describe = _stage("describe", ("frames",))  # needs the frames: dropped when they are redone
    boxes = _stage("boxes", ("frames",))
    notes = _stage("notes")  # only reads the frames' facts: kept while they are unchanged
    type(notes).after = ("frames",)
    registry = StageRegistry([frames, describe, boxes, notes])
    runner = PipelineRunner(registry, events=_Events())
    await runner.run(_ctx(db, video), job_id=None, stages=["frames", "describe", "notes"])
    assert CALLS == {"frames": 1, "describe": 1, "notes": 1}  # boxes never asked for

    report = await runner.run(_ctx(db, video), job_id=None, stages=["frames"], force=["frames"])
    assert report.statuses == {
        "frames": StageStatus.SUCCEEDED,
        "describe": StageStatus.SUCCEEDED,  # its frames were replaced: redone at once
        "notes": StageStatus.CACHED,  # what it reads did not change
    }  # boxes had no result: left to an ordinary analysis
    assert CALLS == {"frames": 2, "describe": 2, "notes": 1}

    # A reader whose result was dropped by an earlier run (cancelled before it could redo it)
    # is still brought up to date by the next narrow request.
    type(frames).execute = _stage("frames", cancels=True).execute  # type: ignore[method-assign,assignment]
    await runner.run(_ctx(db, video), job_id=None, stages=["frames"], force=["frames"])
    type(frames).execute = _stage("frames").execute  # type: ignore[method-assign,assignment]
    report = await runner.run(_ctx(db, video), job_id=None, stages=["frames"])
    assert report.statuses["describe"] == StageStatus.SUCCEEDED


def _boom() -> StageOutcome:
    raise VfeError("ffmpeg a planté")


def _interrupt(db: Database, video: VideoRef, stage: str, dependents: tuple[str, ...] = ()) -> None:
    """What an app closed mid-stage leaves: the stage's run cancelled at restart
    (``requeue_interrupted``), its dependents' results dropped when it started."""
    with db.write() as session:
        if dependents:
            session.execute(
                sa.update(StageRun)
                .where(StageRun.video_id == video.id, StageRun.stage.in_(dependents))
                .values(cache_key="")
            )
        session.add(
            StageRun(
                video_id=video.id, stage=stage, stage_version=1, cache_key="",
                status=StageStatus.CANCELLED, summary={}, attempts=1,
            )
        )  # fmt: skip


@pytest.mark.anyio
async def test_an_interrupted_reader_is_not_forgotten_when_the_job_resumes(
    db: Database, video: VideoRef
) -> None:
    frames, describe = _stage("frames"), _stage("describe", ("frames",))
    runner = PipelineRunner(StageRegistry([frames, describe]), events=_Events())
    await runner.run(_ctx(db, video), job_id=None)

    # « ↻ frames » stopped while the descriptions were being redone (app closed, job cancelled).
    await runner.run(_ctx(db, video), job_id=None, stages=["frames"], force=["frames"])
    _interrupt(db, video, "describe")

    # The same job, resumed: the cancelled run is no result, the one before it decides.
    report = await runner.run(_ctx(db, video), job_id=None, stages=["frames"], force=["frames"])
    assert report.statuses["describe"] == StageStatus.SUCCEEDED


@pytest.mark.anyio
async def test_a_requirement_that_runs_again_takes_its_readers_along(
    db: Database, video: VideoRef
) -> None:
    frames = _stage("frames")
    registry = StageRegistry([frames, _stage("describe", ("frames",)), _stage("ocr", ("frames",))])
    runner = PipelineRunner(registry, events=_Events())
    await runner.run(_ctx(db, video), job_id=None)
    _interrupt(db, video, "frames", ("describe", "ocr"))  # a « ↻ frames » cut short
    CALLS.clear()

    # Only the OCR is asked for, but the frames it needs must run again first (their last run
    # was interrupted): the descriptions, dropped by that run, are redone in the same job.
    report = await runner.run(_ctx(db, video), job_id=None, stages=["ocr"])
    assert report.statuses == {
        "frames": StageStatus.SUCCEEDED,
        "ocr": StageStatus.SUCCEEDED,
        "describe": StageStatus.SUCCEEDED,
    }
    assert CALLS == {"frames": 1, "ocr": 1, "describe": 1}


@pytest.mark.anyio
async def test_redoing_a_failed_stage_also_runs_what_it_blocked(
    db: Database, video: VideoRef
) -> None:
    frames = _stage("frames", outcome=_boom)
    runner = PipelineRunner(
        StageRegistry([frames, _stage("describe", ("frames",)), _stage("other")]),
        events=_Events(),
    )
    first = await runner.run(_ctx(db, video), job_id=None)
    assert first.statuses["describe"] == StageStatus.SKIPPED  # « Dépend de : frames »

    type(frames).execute = _stage("frames").execute  # type: ignore[method-assign,assignment]
    report = await runner.run(_ctx(db, video), job_id=None, stages=["frames"], force=["frames"])
    assert report.statuses == {"frames": StageStatus.SUCCEEDED, "describe": StageStatus.SUCCEEDED}
