"""Server-Sent Events fed by the ``events`` table (works across the API and worker processes)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

import anyio
import sqlalchemy as sa
from fastapi import APIRouter, Header, Query, Request
from sse_starlette.sse import EventSourceResponse

from vfe_vision.api.deps import Container
from vfe_vision.db.models import Event
from vfe_vision.db.session import Database

router = APIRouter(tags=["events"])

POLL_INTERVAL_S = 0.5
BATCH = 200
STOP_GRACE_S = 1.0  # well within uvicorn's graceful shutdown (5 s, ``vfe serve``)


def _latest_id(db: Database) -> int:
    with db.read() as session:
        return int(session.execute(sa.select(sa.func.max(Event.id))).scalar_one_or_none() or 0)


def _fetch(db: Database, after: int) -> list[dict[str, Any]]:
    with db.read() as session:
        rows = session.execute(
            sa.select(Event).where(Event.id > after).order_by(Event.id).limit(BATCH)
        ).scalars()
        return [
            {
                "id": row.id,
                "type": row.type,
                "job_id": row.job_id,
                "video_id": row.video_id,
                "data": row.data,
                "at": row.created_at.isoformat(),
            }
            for row in rows
        ]


@router.get("/events", response_class=EventSourceResponse)
async def events(
    request: Request,
    c: Container,
    since: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> EventSourceResponse:
    """Live events. Resumes after ``Last-Event-ID`` (or ``since``) on reconnection."""
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else since
    if start is None:
        start = await anyio.to_thread.run_sync(_latest_id, c.db)
    # Set when the server stops: the stream then ends by itself and the response is complete
    # (cut short, uvicorn reports "ASGI callable returned without completing response").
    stopping = anyio.Event()

    async def stream() -> AsyncIterator[dict[str, str]]:
        cursor = start
        while not stopping.is_set() and not await request.is_disconnected():
            batch = await anyio.to_thread.run_sync(_fetch, c.db, cursor)
            for event in batch:
                cursor = event["id"]
                yield {
                    "id": str(event["id"]),
                    "event": event["type"],
                    "data": json.dumps(event, ensure_ascii=False, default=str),
                }
            if len(batch) < BATCH:
                with anyio.move_on_after(POLL_INTERVAL_S):
                    await stopping.wait()

    return EventSourceResponse(
        stream(), ping=15, shutdown_event=stopping, shutdown_grace_period=STOP_GRACE_S
    )
