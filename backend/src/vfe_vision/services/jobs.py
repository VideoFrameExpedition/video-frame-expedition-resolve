"""Job listing and control."""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.core.errors import NotFoundError
from vfe_vision.db.models import Job, LibraryRoot, TimelineBin, Video
from vfe_vision.domain.enums import JobKind, JobStatus
from vfe_vision.jobs import queue
from vfe_vision.services.container import AppContainer

JobOrder = Literal["recent", "queue", "finished"]
FINISHED = (JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELLED)
RECENT = timedelta(hours=24)  # the summary counts the jobs finished over this span
PACE_SAMPLE = 20  # the latest finished analyses whose duration sets the pace


@dataclass(frozen=True, slots=True)
class JobView:
    job: Job
    target: str | None = None  # the video's file name, the folder's or the timeline's name
    place: str | None = None  # where the video is: its library folder › sub-folder


@dataclass(frozen=True, slots=True)
class JobsSummary:
    running: int
    queued: int
    finished: dict[str, int] = field(default_factory=dict)  # by status, over RECENT
    analysis_s: float | None = None  # median duration of the latest analyses
    eta_s: float | None = None  # the analyses still to run, at that pace
    parallel: int = 1  # analyses run side by side


def list_jobs(
    c: AppContainer,
    *,
    statuses: tuple[JobStatus, ...] = (),
    kinds: tuple[JobKind, ...] = (),
    video_id: str | None = None,
    order: JobOrder = "recent",
    limit: int = 50,
    offset: int = 0,
) -> list[JobView]:
    """Jobs with what they are about. ``order``: recent (created last first), queue (the
    order the worker takes queued jobs in), finished (finished last first)."""
    stmt = sa.select(Job)
    if statuses:
        stmt = stmt.where(Job.status.in_(statuses))
    if kinds:
        stmt = stmt.where(Job.kind.in_(kinds))
    if video_id:
        stmt = stmt.where(Job.video_id == video_id)
    if order == "queue":
        stmt = stmt.order_by(Job.priority, Job.created_at)
    elif order == "finished":
        stmt = stmt.order_by(Job.finished_at.desc().nulls_last(), Job.created_at.desc())
    else:
        stmt = stmt.order_by(Job.created_at.desc())
    with c.db.read() as session:
        found = list(session.execute(stmt.limit(limit).offset(offset)).scalars())
        return _views(session, found)


def _views(session: Session, found: list[Job]) -> list[JobView]:
    """Name each job's target in three queries at most (videos, folders, timelines)."""
    video_ids = {job.video_id for job in found if job.video_id}
    videos = {
        row.id: row
        for row in session.execute(
            sa.select(Video.id, Video.filename, Video.rel_path, LibraryRoot.label)
            .join(LibraryRoot, LibraryRoot.id == Video.root_id)
            .where(Video.id.in_(video_ids))
        )
    }
    root_ids = {job.root_id for job in found if job.root_id}
    roots = dict(
        session.execute(
            sa.select(LibraryRoot.id, LibraryRoot.label).where(LibraryRoot.id.in_(root_ids))
        ).all()
    )
    bin_ids = {str(job.payload.get("bin_id")) for job in found if job.kind == JobKind.SYNC_TIMELINE}
    bins = dict(
        session.execute(
            sa.select(TimelineBin.id, TimelineBin.label).where(TimelineBin.id.in_(bin_ids))
        ).all()
    )
    views = []
    for job in found:
        video = videos.get(job.video_id) if job.video_id else None
        if video is not None:
            views.append(JobView(job, video.filename, _place(video.label, video.rel_path)))
        elif job.root_id:
            views.append(JobView(job, roots.get(job.root_id)))
        elif job.kind == JobKind.SYNC_TIMELINE:
            views.append(JobView(job, bins.get(str(job.payload.get("bin_id")))))
        elif job.kind == JobKind.RELINK_VIDEOS:
            folder = str(job.payload.get("folder") or "")
            views.append(JobView(job, PurePath(folder).name or folder or None, folder or None))
        else:
            views.append(JobView(job))
    return views


def _place(root_label: str, rel_path: str) -> str:
    """« CATS › hdr » for a video at hdr/x.mp4 of the folder CATS."""
    parent = PurePath(rel_path.replace("\\", "/")).parent.as_posix()
    return root_label if parent in ("", ".") else f"{root_label} › {parent.replace('/', ' › ')}"


def summary(c: AppContainer) -> JobsSummary:
    """How many jobs run and wait, how the last day went, and when the analyses still to run
    should be done at the pace of the latest ones (an estimate: a « Complete » of a video
    already analysed takes seconds, a first analysis minutes)."""
    now = datetime.now(UTC)
    with c.db.read() as session:
        active = dict(
            session.execute(
                sa.select(Job.status, sa.func.count())
                .where(Job.status.in_(queue.ACTIVE))
                .group_by(Job.status)
            ).all()
        )
        finished = {
            str(status.value): int(count)
            for status, count in session.execute(
                sa.select(Job.status, sa.func.count())
                .where(Job.status.in_(FINISHED), Job.finished_at >= now - RECENT)
                .group_by(Job.status)
            ).all()
        }
        latest = session.execute(
            sa.select(Job.started_at, Job.finished_at)
            .where(Job.kind == JobKind.ANALYZE_VIDEO)
            .where(Job.status.in_((JobStatus.SUCCEEDED, JobStatus.PARTIAL)))
            .where(Job.started_at.is_not(None), Job.finished_at.is_not(None))
            .order_by(Job.finished_at.desc())
            .limit(PACE_SAMPLE)
        ).all()
        waiting = session.scalar(
            sa.select(sa.func.count())
            .select_from(Job)
            .where(Job.kind == JobKind.ANALYZE_VIDEO, Job.status == JobStatus.QUEUED)
        )
        started = list(
            session.execute(
                sa.select(Job.started_at).where(
                    Job.kind == JobKind.ANALYZE_VIDEO, Job.status == JobStatus.RUNNING
                )
            ).scalars()
        )
    parallel = max(1, c.settings.max_concurrent_videos)
    pace = _median_s((row.finished_at - row.started_at).total_seconds() for row in latest)
    eta = None
    if pace is not None and (waiting or started):
        left = sum(max(0.0, pace - (now - at).total_seconds()) for at in started if at)
        eta = (int(waiting or 0) * pace + left) / parallel
    return JobsSummary(
        running=int(active.get(JobStatus.RUNNING, 0)),
        queued=int(active.get(JobStatus.QUEUED, 0)),
        finished=finished,
        analysis_s=pace,
        eta_s=eta,
        parallel=parallel,
    )


def _median_s(durations: Iterable[float]) -> float | None:
    kept = [d for d in durations if d > 0]
    return statistics.median(kept) if kept else None


def get_job(c: AppContainer, job_id: str) -> Job:
    with c.db.read() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise NotFoundError(f"Job introuvable : {job_id}")
        return job


def cancel_job(c: AppContainer, job_id: str) -> Job:
    return queue.request_cancel(c.db, job_id)


def cancel_active(
    c: AppContainer,
    *,
    kinds: tuple[JobKind, ...] = (),
    root_id: str | None = None,
    video_ids: tuple[str, ...] = (),
) -> tuple[int, int]:
    """« Stop all »: (cancelled before starting, asked to stop while running)."""
    return queue.cancel_active(c.db, kinds=kinds, root_id=root_id, video_ids=video_ids)


def retry_job(c: AppContainer, job_id: str) -> Job:
    return queue.retry(c.db, job_id)
