"""Event sink backed by the ``events`` table (read by the SSE endpoint of the API process).

Writes are flushed in batches by a background thread, so a busy SQLite writer never blocks the
worker's event loop. Progress updates are *coalesced*: only the latest value per job is written
at each flush (every ``flush_interval_s``), so the UI always ends up with the current state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from vfe_vision.core.logging import get_logger
from vfe_vision.db.models import Event, Job
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobStatus

log = get_logger(__name__)

PROGRESS_EVENT = "job.progress"


@dataclass(frozen=True, slots=True)
class _Pending:
    type: str
    job_id: str | None
    video_id: str | None
    data: dict[str, Any]


class DbEventSink:
    def __init__(self, db: Database, *, flush_interval_s: float = 0.3) -> None:
        self._db = db
        self._flush_interval_s = flush_interval_s
        self._events: list[_Pending] = []
        self._progress: dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._flush_loop, name="event-sink", daemon=True)
        self._thread.start()

    def emit(
        self,
        type_: str,
        *,
        job_id: str | None = None,
        video_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        pending = _Pending(type_, job_id, video_id, dict(data or {}))
        with self._lock:
            if type_ == PROGRESS_EVENT and job_id is not None:
                self._progress[job_id] = pending  # keep only the latest progress per job
            else:
                # A non-progress event for a job (e.g. job.finished) must come after its last
                # progress update: move the pending progress in front of it.
                if job_id is not None and job_id in self._progress:
                    self._events.append(self._progress.pop(job_id))
                self._events.append(pending)
                self._wake.set()

    def close(self, timeout_s: float = 5.0) -> None:
        self._closing.set()
        self._wake.set()
        self._thread.join(timeout_s)

    def _flush_loop(self) -> None:
        while True:
            self._wake.wait(self._flush_interval_s)
            self._wake.clear()
            with self._lock:
                batch = self._events + list(self._progress.values())
                self._events = []
                self._progress = {}
            if batch:
                self._write(batch)
            if self._closing.is_set():
                return

    def _write(self, batch: list[_Pending]) -> None:
        try:
            with self._db.write() as session:
                session.add_all(
                    Event(type=e.type, job_id=e.job_id, video_id=e.video_id, data=e.data)
                    for e in batch
                )
                for e in batch:
                    if e.type != PROGRESS_EVENT or e.job_id is None:
                        continue
                    # Only while running: a late progress flush must not overwrite the final
                    # message written when the job finished.
                    session.execute(
                        sa.update(Job)
                        .where(Job.id == e.job_id, Job.status == JobStatus.RUNNING)
                        .values(
                            progress=float(e.data.get("progress", 0.0)),
                            message=sa.func.coalesce(e.data.get("message"), Job.message),
                        )
                    )
        except Exception:
            log.exception("failed to persist events", count=len(batch))


def prune_events(db: Database, *, keep_last: int = 50_000) -> int:
    """Delete old events, keeping the most recent ``keep_last`` rows."""
    with db.write() as session:
        max_id = session.execute(sa.select(sa.func.max(Event.id))).scalar_one_or_none() or 0
        result = session.execute(sa.delete(Event).where(Event.id <= max_id - keep_last))
        return int(result.rowcount or 0)  # type: ignore[attr-defined]
