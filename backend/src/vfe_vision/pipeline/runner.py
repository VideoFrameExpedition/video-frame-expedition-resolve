"""Execute an analysis plan for one video: cache checks, resources, stage runs, events."""

from __future__ import annotations

import time
from collections.abc import Collection, Mapping
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import anyio
import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.core.errors import CancelledError, VfeError
from vfe_vision.db.models import StageRun
from vfe_vision.domain.enums import StageStatus
from vfe_vision.pipeline.registry import StageRegistry
from vfe_vision.pipeline.stage import (
    ProgressFn,
    Resource,
    Stage,
    StageContext,
    StageOutcome,
    cache_key,
    input_key,
)

FRESH_STATUSES = frozenset({StageStatus.SUCCEEDED, StageStatus.CACHED})
# Resources held for minutes by one video: a job waiting for them does not take a video slot.
LONG_RESOURCES = frozenset({Resource.ASR})
LIMITER_POLL_S = 0.5  # how often a stage waiting for its resource checks for cancellation
REUSABLE_STATUSES = frozenset({StageStatus.SUCCEEDED, StageStatus.SKIPPED})


def is_reusable(status: StageStatus, key: str, summary: Mapping[str, Any] | None) -> bool:
    """Whether a stage's latest run can be kept: not failed, degraded or invalidated."""
    return status in REUSABLE_STATUSES and bool(key) and not (summary or {}).get("retryable")


def latest_runs(session: Session, video_id: str) -> list[StageRun]:
    """The latest real run of each stage of a video (cache hits are not recorded as results),
    oldest first."""
    latest = (
        sa.select(StageRun.stage, sa.func.max(StageRun.created_at).label("at"))
        .where(StageRun.video_id == video_id, StageRun.status != StageStatus.CACHED)
        .group_by(StageRun.stage)
        .subquery()
    )
    return list(
        session.execute(
            sa.select(StageRun)
            .join(
                latest,
                sa.and_(StageRun.stage == latest.c.stage, StageRun.created_at == latest.c.at),
            )
            .where(StageRun.video_id == video_id)
            .order_by(StageRun.created_at)
        ).scalars()
    )


def _has_result(run: Any, redone: set[str]) -> bool:
    """A result to keep up to date: succeeded (even invalidated since), skipped for good, or
    skipped only because stages that are now being redone had failed."""
    if run.status == StageStatus.SUCCEEDED:
        return True
    if run.status != StageStatus.SKIPPED:
        return False
    summary = run.summary or {}
    if summary.get("blocked_by"):
        return set(summary["blocked_by"]) <= redone
    return not summary.get("retryable")


@dataclass(frozen=True, slots=True)
class _Previous:
    run_id: str
    status: StageStatus
    cache_key: str
    input_key: str | None  # None: recorded before input keys existed
    provisional: bool  # tied to its settings (e.g. an offline answer)

    @property
    def keeps_across_settings(self) -> bool:
        return self.status == StageStatus.SUCCEEDED and not self.provisional


Scope = bool | Collection[str]


def _scope(value: Scope, names: list[str]) -> set[str]:
    return set(names) if value is True else set(value or ())


class EventSink(Protocol):
    def emit(
        self,
        type_: str,
        *,
        job_id: str | None = None,
        video_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None: ...


@dataclass(slots=True)
class RunReport:
    statuses: dict[str, StageStatus] = field(default_factory=dict)
    failed_required: bool = False
    incomplete: bool = False  # optional failure or retryable skip
    cancelled: bool = False
    errors: dict[str, str] = field(default_factory=dict)
    # Stages skipped for good (nothing to do, e.g. no audio track): they do not block the
    # stages that need them, exactly as when the same skip is served from the cache.
    settled: set[str] = field(default_factory=set)


class PipelineRunner:
    def __init__(
        self,
        registry: StageRegistry,
        *,
        limits: Mapping[Resource, AbstractAsyncContextManager[Any]] | None = None,
        events: EventSink,
    ) -> None:
        self.registry = registry
        self.limits = dict(limits or {})
        self.events = events
        self.waiting = 0  # jobs queued for a long resource (see LONG_RESOURCES)

    async def run(
        self,
        ctx: StageContext,
        *,
        job_id: str | None,
        stages: list[str] | None = None,
        force: Scope = False,
        refresh: Scope = False,
    ) -> RunReport:
        """Run ``stages`` (default: all) and their dependencies for ``ctx.video``.

        A stage keeps its earlier result while the video data it reads is unchanged, even when
        its settings, the model or its version changed since. ``refresh`` stages are
        also redone when those changed; ``force`` stages are always redone. Both take ``True``
        (every stage) or stage names.
        """
        # Named stages also bring up to date the stages that read their results and have one:
        # the soft readers are kept while what they read is unchanged; the hard
        # ones are added whenever a stage actually runs again, since that drops their results.
        # Judged from the results as they were when the job started (no other job of the video
        # runs then, and a resumed job sees the same thing).
        wanted = set(stages) if stages is not None else None
        before = await anyio.to_thread.run_sync(self._latest_results, ctx) if wanted else {}
        redone = set(wanted or ())
        if wanted is not None:
            wanted |= {
                name
                for name in self.registry.downstream(wanted)
                if name in before and _has_result(before[name], redone)
            }
        plan = self.registry.plan(wanted)
        forced = _scope(force, self.registry.names)
        refreshed = _scope(refresh, self.registry.names)
        report = RunReport()
        keys: dict[str, str] = {}
        base_progress = ctx.progress
        count = len(plan)

        for position, stage in enumerate(plan):

            def stage_progress(
                fraction: float, message: str | None, _pos: int = position, _count: int = count
            ) -> None:
                base_progress((_pos + max(0.0, min(1.0, fraction))) / _count, message)

            ctx.progress = stage_progress
            blocked_by = [
                dep
                for dep in stage.requires
                if report.statuses.get(dep) not in {*FRESH_STATUSES, None}
                and dep not in report.settled
            ]
            if report.cancelled or ctx.cancel.cancelled:
                report.cancelled = True
                report.statuses[stage.name] = StageStatus.CANCELLED
                continue
            if blocked_by:
                await self._record_skip(
                    ctx,
                    job_id,
                    stage,
                    f"Dépend de : {', '.join(blocked_by)}",
                    blocked_by=blocked_by,
                )
                report.statuses[stage.name] = StageStatus.SKIPPED
                report.incomplete = True
                continue

            previous: _Previous | None = None
            try:
                facts = stage.input_facts(ctx)
                ctx.facts = facts
                data_key = input_key(stage, fingerprint=ctx.video.fingerprint, facts=facts)
                if stage.name not in forced:
                    previous = await anyio.to_thread.run_sync(self._previous, ctx, stage)
                if (
                    previous is not None
                    and previous.keeps_across_settings
                    and stage.name not in refreshed
                    and previous.input_key in {None, data_key}
                ):
                    # Same video data: the result is kept whatever changed in the settings.
                    # A skip is no result, and a provisional one depends on its settings: both
                    # are redone once anything in their key changed (e.g. online services
                    # switched back on, a model installed).
                    if previous.input_key is None:  # older result: today's data is its baseline
                        await anyio.to_thread.run_sync(
                            self._adopt_input_key, ctx, previous.run_id, data_key
                        )
                    keys[stage.name] = previous.cache_key
                    self._cached(ctx, job_id, stage, report, stage_progress)
                    continue
                config = {
                    **stage.cache_config(ctx.prefs, ctx),
                    **facts,
                    **(await stage.resolve_config(ctx)),
                }
            except Exception as exc:  # noqa: BLE001 - reported as the stage's own failure
                report.statuses[stage.name] = await self._config_failure(
                    ctx, job_id, stage, exc, report
                )
                continue
            key = cache_key(
                stage,
                fingerprint=ctx.video.fingerprint,
                config=config,
                upstream={dep: keys[dep] for dep in stage.requires if dep in keys},
            )
            keys[stage.name] = key
            if previous is not None and previous.cache_key == key:
                self._cached(ctx, job_id, stage, report, stage_progress)
                continue

            status = await self._run_stage(ctx, job_id, stage, (key, data_key), report)
            report.statuses[stage.name] = status
            if wanted is not None:
                redone.add(stage.name)
                added = {
                    name
                    for name in self.registry.dependents(stage.name)
                    if name not in wanted and name in before and _has_result(before[name], redone)
                }
                if added:  # the rest of the plan (a list being iterated: extended in place)
                    wanted |= added
                    seen = {s.name for s in plan[: position + 1]}
                    plan[position + 1 :] = [
                        s for s in self.registry.plan(wanted) if s.name not in seen
                    ]
                    count = len(plan)

        ctx.progress = base_progress
        return report

    # ------------------------------------------------------------------ internals
    def _cached(
        self,
        ctx: StageContext,
        job_id: str | None,
        stage: Stage,
        report: RunReport,
        progress: ProgressFn,
    ) -> None:
        report.statuses[stage.name] = StageStatus.CACHED
        self.events.emit(
            "stage.cached", job_id=job_id, video_id=ctx.video.id, data={"stage": stage.name}
        )
        progress(1.0, None)

    @staticmethod
    def _latest_results(ctx: StageContext) -> dict[str, Any]:
        """The latest finished run of each stage: cache hits, runs in progress and cancelled
        runs (an interrupted job) are not results, the run before them decides."""
        not_results = (StageStatus.CACHED, StageStatus.RUNNING, StageStatus.CANCELLED)
        with ctx.tools.db.read() as session:
            rows = session.execute(
                sa.select(StageRun.stage, StageRun.status, StageRun.summary)
                .where(StageRun.video_id == ctx.video.id)
                .where(StageRun.status.not_in(not_results))
                .order_by(StageRun.created_at)
            ).all()
        return {row.stage: row for row in rows}  # the latest run of each stage wins

    @staticmethod
    def _previous(ctx: StageContext, stage: Stage) -> _Previous | None:
        """The stage's latest result, when it can be kept (see ``is_reusable``)."""
        with ctx.tools.db.read() as session:
            last = session.execute(
                sa.select(
                    StageRun.id,
                    StageRun.status,
                    StageRun.cache_key,
                    StageRun.input_key,
                    StageRun.summary,
                )
                .where(StageRun.video_id == ctx.video.id, StageRun.stage == stage.name)
                .where(StageRun.status != StageStatus.CACHED)
                .order_by(StageRun.created_at.desc())
                .limit(1)
            ).first()
        if last is None or not is_reusable(last.status, last.cache_key, last.summary):
            return None
        return _Previous(
            last.id,
            last.status,
            last.cache_key,
            last.input_key,
            provisional=bool((last.summary or {}).get("provisional")),
        )

    @staticmethod
    def _adopt_input_key(ctx: StageContext, run_id: str, data_key: str) -> None:
        with ctx.tools.db.write() as session:
            session.execute(
                sa.update(StageRun)
                .where(StageRun.id == run_id, StageRun.input_key.is_(None))
                .values(input_key=data_key)
            )

    async def _config_failure(
        self,
        ctx: StageContext,
        job_id: str | None,
        stage: Stage,
        exc: Exception,
        report: RunReport,
    ) -> StageStatus:
        """A stage whose configuration cannot be resolved (e.g. LM Studio answering an error),
        or whose job was cancelled meanwhile (e.g. during a vision model calibration)."""
        if isinstance(exc, CancelledError):
            report.cancelled = True
            return StageStatus.CANCELLED
        detail = exc.detail if isinstance(exc, VfeError) else f"{type(exc).__name__}: {exc}"
        ctx.log.warning("stage configuration failed", stage=stage.name, error=detail)
        if stage.optional:
            await self._record_skip(ctx, job_id, stage, f"Configuration impossible : {detail}")
            report.incomplete = True
            return StageStatus.SKIPPED
        await self._record_skip(
            ctx, job_id, stage, detail, status=StageStatus.FAILED, retryable=True
        )
        report.errors[stage.name] = detail
        report.failed_required = True
        return StageStatus.FAILED

    async def _run_stage(
        self,
        ctx: StageContext,
        job_id: str | None,
        stage: Stage,
        keys: tuple[str, str],  # cache key, input key
        report: RunReport,
    ) -> StageStatus:
        dependents = self.registry.dependents(stage.name)
        run_id = await anyio.to_thread.run_sync(
            self._start_run, ctx, job_id, stage, keys, dependents
        )
        self.events.emit(
            "stage.started", job_id=job_id, video_id=ctx.video.id, data={"stage": stage.name}
        )
        ctx.progress(0.0, _STAGE_LABELS.get(stage.name, stage.name))
        log = ctx.log.bind(stage=stage.name)
        started = time.perf_counter()
        outcome: StageOutcome | None = None
        error: str | None = None
        status: StageStatus
        try:
            outcome = await anyio.to_thread.run_sync(stage.precheck, ctx)
            if outcome is None:
                limiter = self.limits.get(stage.resource) or nullcontext()
                await self._enter(limiter, stage, ctx)
                try:
                    outcome = await stage.execute(ctx)
                finally:
                    await limiter.__aexit__(None, None, None)
            status = outcome.status
        except CancelledError:
            status = StageStatus.CANCELLED
            report.cancelled = True
        except VfeError as exc:
            status, error = StageStatus.FAILED, exc.detail
        except Exception as exc:
            log.exception("stage crashed")
            status, error = StageStatus.FAILED, f"{type(exc).__name__}: {exc}"

        duration_ms = round((time.perf_counter() - started) * 1000)
        if status == StageStatus.FAILED:
            report.errors[stage.name] = error or "échec"
            if stage.optional:
                report.incomplete = True
            else:
                report.failed_required = True
            log.warning("stage failed", error=error, duration_ms=duration_ms)
        elif outcome is not None and outcome.retryable:
            report.incomplete = True  # skipped for now, or degraded: worth another run
            # Said in the log too: an outage (LM Studio asleep, a service down) leaves the
            # video incomplete without any failure, and is otherwise only seen on the stage.
            log.warning(
                "stage to be redone",
                status=status.value,
                reason=outcome.skip_reason,
                duration_ms=duration_ms,
            )
        elif outcome is not None and outcome.status == StageStatus.SKIPPED:
            report.settled.add(stage.name)
        await anyio.to_thread.run_sync(
            lambda: self._finish_run(
                ctx, run_id, status=status, outcome=outcome, error=error, duration_ms=duration_ms
            )
        )
        self.events.emit(
            "stage.finished",
            job_id=job_id,
            video_id=ctx.video.id,
            data={
                "stage": stage.name,
                "status": status.value,
                "duration_ms": duration_ms,
                "error": error,
                "skip_reason": outcome.skip_reason if outcome else None,
            },
        )
        return status

    async def _enter(
        self, limiter: AbstractAsyncContextManager[Any], stage: Stage, ctx: StageContext
    ) -> None:
        """Wait for the stage's resource; a cancelled job stops waiting."""
        long_wait = stage.resource in LONG_RESOURCES
        self.waiting += long_wait
        try:
            while True:
                ctx.cancel.raise_if_cancelled()
                with anyio.move_on_after(LIMITER_POLL_S):
                    await limiter.__aenter__()
                    return
        finally:
            self.waiting -= long_wait

    @staticmethod
    def _start_run(
        ctx: StageContext,
        job_id: str | None,
        stage: Stage,
        keys: tuple[str, str],  # cache key, input key
        dependents: list[str],
    ) -> str:
        with ctx.tools.db.write() as session:
            if dependents:
                session.execute(
                    sa.update(StageRun)
                    .where(StageRun.video_id == ctx.video.id, StageRun.stage.in_(dependents))
                    .values(cache_key="")
                )
            run = StageRun(
                job_id=job_id,
                video_id=ctx.video.id,
                stage=stage.name,
                stage_version=stage.version,
                cache_key=keys[0],
                input_key=keys[1],
                status=StageStatus.RUNNING,
                attempts=1,
                started_at=datetime.now(UTC),
            )
            session.add(run)
            session.flush()
            return run.id

    @staticmethod
    def _finish_run(
        ctx: StageContext,
        run_id: str,
        *,
        status: StageStatus,
        outcome: StageOutcome | None,
        error: str | None,
        duration_ms: int,
    ) -> None:
        with ctx.tools.db.write() as session:
            run = session.get_one(StageRun, run_id)
            run.status = status
            run.error = error
            run.finished_at = datetime.now(UTC)
            run.duration_ms = duration_ms
            if outcome is not None:
                run.summary = {**outcome.summary, "retryable": outcome.retryable}
                run.skip_reason = outcome.skip_reason

    async def _record_skip(
        self,
        ctx: StageContext,
        job_id: str | None,
        stage: Stage,
        reason: str,
        *,
        status: StageStatus = StageStatus.SKIPPED,
        retryable: bool = True,
        blocked_by: list[str] | None = None,
    ) -> None:
        failed = status == StageStatus.FAILED
        summary: dict[str, Any] = {"retryable": retryable}
        if blocked_by:
            summary["blocked_by"] = blocked_by

        def write() -> None:
            with ctx.tools.db.write() as session:
                session.add(
                    StageRun(
                        job_id=job_id,
                        video_id=ctx.video.id,
                        stage=stage.name,
                        stage_version=stage.version,
                        cache_key="",
                        status=status,
                        skip_reason=None if failed else reason,
                        error=reason if failed else None,
                        summary=summary,
                        finished_at=datetime.now(UTC),
                    )
                )

        await anyio.to_thread.run_sync(write)
        self.events.emit(
            "stage.finished",
            job_id=job_id,
            video_id=ctx.video.id,
            data={
                "stage": stage.name,
                "status": status.value,
                "skip_reason": None if failed else reason,
                "error": reason if failed else None,
            },
        )


_STAGE_LABELS = {
    "probe": "Analyse du conteneur",
    "keyframes": "Extraction des images clés",
    "audio_events": "Reconnaissance des sons",
    "ocr": "Lecture du texte à l'écran",
    "detections": "Repérage des personnes et des animaux",
    "proxy": "Copie de visionnage",
    "vision_frames": "Description des images (LM Studio)",
    "grounding": "Position des sujets (LM Studio)",
    "transcript": "Transcription de la parole",
    "vision_shots": "Récit des plans (LM Studio)",
    "index": "Index de recherche",
}
