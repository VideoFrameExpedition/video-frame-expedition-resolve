"""Persistent job queue operations (shared by the API and the worker)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.core.errors import ConflictError, NotFoundError
from vfe_vision.db.models import Job, LibraryRoot, StageRun, Video
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import AnalysisMode, JobKind, JobStatus, StageStatus, VideoStatus

ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)
# Never analysed, failed or incomplete: what an ordinary (« complete ») analysis finishes.
NEEDS_WORK = frozenset({VideoStatus.NEW, VideoStatus.FAILED, VideoStatus.PARTIAL})

Scope = bool | list[str]  # every stage (True), none (False) or the named ones
_MODE_ORDER = [mode.value for mode in AnalysisMode]  # weakest first


def union_scope(a: Scope | None, b: Scope | None) -> Scope:
    if a is True or b is True:
        return True
    names = {*(a or ()), *(b or ())}
    return sorted(names) if names else False


def merge_analysis(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """One queued analysis doing what both requests asked (stages, forced and refreshed ones)."""
    merged = {**old, **new}  # the newest focus wins
    modes = [p["mode"] for p in (old, new) if p.get("mode") in _MODE_ORDER]
    if modes:  # informative only: the scopes below are what the worker executes
        merged["mode"] = max(modes, key=_MODE_ORDER.index)
    for scope in ("force", "refresh"):
        merged[scope] = union_scope(old.get(scope), new.get(scope))
    if old.get("stages") and new.get("stages"):
        merged["stages"] = sorted({*old["stages"], *new["stages"]})
    else:  # one of them asked for every stage
        merged.pop("stages", None)
    return merged


def merge_sync(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """One queued update of a timeline bin doing what both requests asked, for the
    videos the bin held before either."""
    return {
        **old,
        **new,
        "allow_new_folders": bool(old.get("allow_new_folders") or new.get("allow_new_folders")),
        "analyze": bool(old.get("analyze") or new.get("analyze")),
        "videos": sorted({*old.get("videos", ()), *new.get("videos", ())}),
    }


def enqueue(
    db: Database,
    kind: JobKind,
    *,
    video_id: str | None = None,
    root_id: str | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
) -> Job:
    """Queue a job, or return the active job already queued for the same target."""
    with db.write() as session:
        return enqueue_in(
            session, kind, video_id=video_id, root_id=root_id, payload=payload, priority=priority
        )


def enqueue_in(
    session: Session,
    kind: JobKind,
    *,
    video_id: str | None = None,
    root_id: str | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
) -> Job:
    """``enqueue`` within the caller's write."""
    stmt = (
        sa.select(Job)
        .where(Job.kind == kind, Job.status.in_(ACTIVE))
        .where(Job.video_id == video_id if video_id else Job.video_id.is_(None))
        .where(Job.root_id == root_id if root_id else Job.root_id.is_(None))
    )
    if kind == JobKind.SYNC_TIMELINE:  # the target is the timeline bin: never merged across bins
        stmt = stmt.where(Job.payload["bin_id"].as_string() == (payload or {}).get("bin_id"))
    stmt = stmt.order_by((Job.status == JobStatus.QUEUED).desc())  # a queued one takes the request
    existing = session.execute(stmt.limit(1)).scalar_one_or_none()
    if existing is not None and existing.status == JobStatus.QUEUED:
        # Merge the new request into the queued one (e.g. more stages to redo).
        if kind == JobKind.ANALYZE_VIDEO:
            existing.payload = merge_analysis(existing.payload, payload or {})
        elif kind == JobKind.SYNC_TIMELINE:
            existing.payload = merge_sync(existing.payload, payload or {})
        else:
            existing.payload = {**existing.payload, **(payload or {})}
        existing.priority = min(existing.priority, priority)
        return existing
    job = Job(
        kind=kind,
        video_id=video_id,
        root_id=root_id,
        payload=payload or {},
        priority=priority,
    )
    session.add(job)
    if kind == JobKind.ANALYZE_VIDEO and video_id:
        session.execute(
            sa.update(Video)
            .where(Video.id == video_id, Video.status != VideoStatus.ANALYZING)
            .values(status=VideoStatus.QUEUED)
        )
    session.flush()
    return job


def active_analysis(session: Session, video_ids: Collection[str]) -> set[str]:
    """Those of these videos with an analysis queued or running."""
    rows = session.execute(
        sa.select(Job.video_id)
        .where(Job.kind == JobKind.ANALYZE_VIDEO, Job.status.in_(ACTIVE))
        .where(Job.video_id.in_(list(video_ids)))
    ).scalars()
    return {video_id for video_id in rows if video_id is not None}


def active_job(db: Database, kind: JobKind) -> Job | None:
    """A job of this kind already queued or running (the oldest)."""
    with db.read() as session:
        return session.execute(
            sa.select(Job)
            .where(Job.kind == kind, Job.status.in_(ACTIVE))
            .order_by(Job.created_at)
            .limit(1)
        ).scalar_one_or_none()


def claim_next(
    db: Database,
    worker_pid: int,
    *,
    kinds: Collection[JobKind] | None = None,
    alone: Collection[JobKind] = (),
    busy: bool = False,
) -> Job | None:
    """Atomically move the next runnable job to ``running`` (one job per video at a time;
    ``kinds``: only jobs of these kinds).

    ``alone``: kinds that run with nothing else running (the model bench loads and unloads
    vision models). When the next job is one of them and the worker is ``busy``,
    nothing is claimed: the queue waits for the running jobs to end, then gives it its turn.
    """
    busy_videos = (
        sa.select(Job.video_id)
        .where(Job.status == JobStatus.RUNNING, Job.video_id.is_not(None))
        .scalar_subquery()
    )
    with db.write() as session:
        job = session.execute(
            sa.select(Job)
            .where(Job.status == JobStatus.QUEUED)
            .where(sa.or_(Job.video_id.is_(None), Job.video_id.not_in(busy_videos)))
            .where(Job.kind.in_(kinds) if kinds is not None else sa.true())
            .order_by(Job.priority, Job.created_at)
            .limit(1)
        ).scalar_one_or_none()
        if job is None or (busy and job.kind in alone):
            return None
        now = datetime.now(UTC)
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.started_at = now
        job.heartbeat_at = now
        job.worker_pid = worker_pid
        job.progress = 0.0
        job.error = None
        return job


def finish(
    db: Database,
    job_id: str,
    status: JobStatus,
    *,
    error: str | None = None,
    message: str | None = None,
) -> None:
    with db.write() as session:
        job = session.get_one(Job, job_id)
        job.status = status
        job.error = error
        job.message = message or job.message
        job.finished_at = datetime.now(UTC)
        if status in {JobStatus.SUCCEEDED, JobStatus.PARTIAL}:
            job.progress = 1.0


def has_queued_analysis(session: Session, video_id: str, *, besides: str) -> bool:
    """Whether another analysis of the video waits in the queue (not the job ``besides``)."""
    return (
        session.execute(
            sa.select(Job.id)
            .where(Job.video_id == video_id, Job.id != besides)
            .where(Job.kind == JobKind.ANALYZE_VIDEO, Job.status == JobStatus.QUEUED)
            .limit(1)
        ).first()
        is not None
    )


def requeue_interrupted(db: Database) -> int:
    """Put jobs left ``running`` by a crashed or stopped worker back in the queue; their stage
    runs left ``running`` are marked cancelled (they would otherwise show as running forever).
    A model bench is not taken up again: it would load models at the next start, unasked."""
    with db.write() as session:
        session.execute(
            sa.update(Job)
            .where(Job.status == JobStatus.RUNNING, Job.kind == JobKind.BENCH_MODELS)
            .values(
                status=JobStatus.FAILED,
                worker_pid=None,
                finished_at=datetime.now(UTC),
                error="Interrompu par l'arrêt de l'application",
            )
        )
        session.execute(
            sa.update(StageRun)
            .where(StageRun.status == StageStatus.RUNNING)
            .values(status=StageStatus.CANCELLED, error="Interrompue (arrêt du worker)")
        )
        result = session.execute(
            sa.update(Job)
            .where(Job.status == JobStatus.RUNNING)
            .values(status=JobStatus.QUEUED, worker_pid=None, message="Repris après interruption")
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


def heartbeat(db: Database, job_ids: list[str]) -> set[str]:
    """Refresh heartbeats; return the ids whose cancellation was requested."""
    if not job_ids:
        return set()
    with db.write() as session:
        session.execute(
            sa.update(Job).where(Job.id.in_(job_ids)).values(heartbeat_at=datetime.now(UTC))
        )
        rows = session.execute(
            sa.select(Job.id).where(Job.id.in_(job_ids), Job.cancel_requested.is_(True))
        ).scalars()
        return set(rows)


def request_cancel(db: Database, job_id: str) -> Job:
    with db.write() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise NotFoundError(f"Job introuvable : {job_id}")
        if job.status.is_terminal:
            raise ConflictError("Ce job est déjà terminé.")
        _cancel(session, job)
        return job


def cancel_active(
    db: Database,
    *,
    kinds: Collection[JobKind] = (),
    root_id: str | None = None,
    video_ids: Collection[str] = (),
) -> tuple[int, int]:
    """Stop every queued or running job (of these kinds, of this folder's videos, of these
    videos): the queued ones are cancelled at once, the running ones are asked to stop.
    Returns (cancelled, stopping). Analyses already done are kept."""
    stmt = sa.select(Job).where(Job.status.in_(ACTIVE))
    if kinds:
        stmt = stmt.where(Job.kind.in_(list(kinds)))
    if video_ids:
        stmt = stmt.where(Job.video_id.in_(list(video_ids)))
    if root_id:
        in_root = sa.select(Video.id).where(Video.root_id == root_id)
        stmt = stmt.where(sa.or_(Job.root_id == root_id, Job.video_id.in_(in_root)))
    cancelled = stopping = 0
    with db.write() as session:
        for job in session.execute(stmt).scalars():
            if job.status == JobStatus.QUEUED:
                cancelled += 1
            elif not job.cancel_requested:
                stopping += 1
            _cancel(session, job)
    return cancelled, stopping


def _cancel(session: Session, job: Job) -> None:
    if job.status == JobStatus.QUEUED:
        job.status = JobStatus.CANCELLED
        job.finished_at = datetime.now(UTC)
        job.message = "Annulé avant démarrage"
        if job.kind == JobKind.ANALYZE_VIDEO and job.video_id:
            session.flush()
            settle_waiting_videos(session, [job.video_id])
    else:
        job.cancel_requested = True


# What the last analysis that ran left a video as (cancelled while running: partial).
LEFT_AS = {
    JobStatus.SUCCEEDED: VideoStatus.READY,
    JobStatus.PARTIAL: VideoStatus.PARTIAL,
    JobStatus.FAILED: VideoStatus.FAILED,
    JobStatus.CANCELLED: VideoStatus.PARTIAL,
}


def settle_waiting_videos(session: Session, video_ids: Collection[str] | None = None) -> int:
    """Videos shown « queued » with no analysis left to wait for (their queued analysis was
    cancelled) go back to what their last analysis that ran left them (« ready », « partial »,
    « failed »); « new » when never analysed. ``None``: every such video (worker start:
    repairs the libraries of earlier versions). Returns how many."""
    waiting = sa.select(Job.video_id).where(
        Job.kind == JobKind.ANALYZE_VIDEO, Job.status.in_(ACTIVE), Job.video_id.is_not(None)
    )
    stmt = sa.select(Video).where(Video.status == VideoStatus.QUEUED, Video.id.not_in(waiting))
    if video_ids is not None:
        stmt = stmt.where(Video.id.in_(list(video_ids)))
    videos = list(session.execute(stmt).scalars())
    restore_status(session, videos)
    return len(videos)


def restore_status(session: Session, videos: Sequence[Video]) -> None:
    """Give these videos what their last analysis that ran left them (« ready », « partial »,
    « failed »); « new » when never analysed."""
    if not videos:
        return
    ran = session.execute(
        sa.select(Job.video_id, Job.status)
        .where(Job.kind == JobKind.ANALYZE_VIDEO, Job.status.in_(list(LEFT_AS)))
        .where(Job.started_at.is_not(None), Job.video_id.in_([v.id for v in videos]))
        .order_by(Job.started_at.desc())
    ).all()
    last: dict[str | None, JobStatus] = {}
    for video_id, status in ran:
        last.setdefault(video_id, status)
    for video in videos:
        outcome = last.get(video.id)
        if video.last_analyzed_at is None:
            video.status = VideoStatus.NEW
        else:
            video.status = LEFT_AS[outcome] if outcome else VideoStatus.PARTIAL


def retry(db: Database, job_id: str) -> Job:
    with db.write() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise NotFoundError(f"Job introuvable : {job_id}")
        if not job.status.is_terminal:
            raise ConflictError("Ce job est encore actif.")
        clone = Job(
            kind=job.kind,
            video_id=job.video_id,
            root_id=job.root_id,
            payload=job.payload,
            priority=job.priority,
        )
        session.add(clone)
        session.flush()
        return clone


def auto_scan_roots(db: Database) -> list[str]:
    """Folders updated automatically (``auto_analyze``) that no scan job is handling now."""
    with db.read() as session:
        busy = sa.select(Job.root_id).where(
            Job.kind == JobKind.SCAN_ROOT, Job.status.in_(ACTIVE), Job.root_id.is_not(None)
        )
        return list(
            session.execute(
                sa.select(LibraryRoot.id)
                .where(LibraryRoot.auto_analyze.is_(True))
                .where(LibraryRoot.id.not_in(busy))
            ).scalars()
        )
