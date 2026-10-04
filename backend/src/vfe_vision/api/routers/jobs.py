"""Jobs: listing and control."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from vfe_vision.api.deps import Container
from vfe_vision.api.schemas import JobOut, JobsCancelOut, JobsCancelRequest, JobsSummaryOut
from vfe_vision.domain.enums import JobKind, JobStatus
from vfe_vision.services import jobs
from vfe_vision.services.jobs import JobOrder

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    c: Container,
    status_: Annotated[list[JobStatus] | None, Query(alias="status")] = None,
    kind: Annotated[list[JobKind] | None, Query()] = None,
    video_id: str | None = None,
    order: Annotated[
        JobOrder,
        Query(
            description="recent: created last first; queue: in the order the worker will take "
            "them; finished: finished last first."
        ),
    ] = "recent",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[JobOut]:
    return [
        JobOut.of_view(view)
        for view in jobs.list_jobs(
            c, statuses=tuple(status_ or ()), kinds=tuple(kind or ()), video_id=video_id,
            order=order, limit=limit, offset=offset,
        )
    ]  # fmt: skip


@router.get("/summary")
def jobs_summary(c: Container) -> JobsSummaryOut:
    """How many jobs are running and waiting, the record of the last 24 h, and an estimate of
    the time left for the analyses."""
    return JobsSummaryOut.of(jobs.summary(c))


@router.post("/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_jobs(c: Container, body: JobsCancelRequest) -> JobsCancelOut:
    """Stop all active jobs (or those of one kind, of one folder, of some videos): the waiting
    ones are cancelled, the running ones stop. What is already analysed stays."""
    cancelled, stopping = jobs.cancel_active(
        c, kinds=tuple(body.kinds), root_id=body.root_id, video_ids=tuple(body.video_ids)
    )
    return JobsCancelOut(cancelled=cancelled, stopping=stopping)


@router.get("/{job_id}")
def get_job(c: Container, job_id: str) -> JobOut:
    return JobOut.of(jobs.get_job(c, job_id))


@router.post("/{job_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_job(c: Container, job_id: str) -> JobOut:
    return JobOut.of(jobs.cancel_job(c, job_id))


@router.post("/{job_id}/retry", status_code=status.HTTP_201_CREATED)
def retry_job(c: Container, job_id: str) -> JobOut:
    return JobOut.of(jobs.retry_job(c, job_id))
