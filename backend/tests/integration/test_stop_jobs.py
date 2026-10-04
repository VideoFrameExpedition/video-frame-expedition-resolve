"""« Stop all »: every active job stopped at once, and the videos whose queued
analysis was cancelled no longer shown « queued » forever."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings
from vfe_vision.db.models import Job, LibraryRoot, Video
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobKind, JobStatus, VideoStatus
from vfe_vision.jobs import queue

HEADERS = {"X-VFE-Client": "tests"}


def _root(db: Database, path: str) -> str:
    with db.write() as session:
        root = LibraryRoot(path=path, path_key=path.casefold(), label=Path(path).name)
        session.add(root)
        session.flush()
        return root.id


def _video(db: Database, root_id: str, name: str, *, analysed: bool = False) -> str:
    with db.write() as session:
        video = Video(
            root_id=root_id,
            path=f"D:/r/{name}",
            path_key=f"d:/r/{root_id}/{name}",
            rel_path=name,
            filename=name,
            size_bytes=1,
            mtime=0.0,
            fingerprint=name,
            last_analyzed_at=datetime.now(UTC) if analysed else None,
            status=VideoStatus.READY if analysed else VideoStatus.NEW,
        )
        session.add(video)
        session.flush()
        return video.id


def _status(db: Database, model: type[Job] | type[Video], id_: str) -> str:
    with db.read() as session:
        found = session.scalar(sa.select(model.status).where(model.id == id_))
        assert found is not None
        return str(found)


def test_everything_active_is_stopped(db: Database) -> None:
    root = _root(db, "D:/cats 2026")
    new, done = _video(db, root, "a.mp4"), _video(db, root, "b.mp4", analysed=True)
    queued_new = queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=new)
    queued_done = queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=done)
    scan = queue.enqueue(db, JobKind.SCAN_ROOT, root_id=root, priority=10)
    running = queue.claim_next(db, worker_pid=1)  # the scan: lowest priority number first
    assert running is not None
    assert running.id == scan.id
    finished = queue.enqueue(db, JobKind.PROBE_VISION)
    queue.finish(db, finished.id, JobStatus.SUCCEEDED)

    assert queue.cancel_active(db) == (2, 1)
    assert _status(db, Job, queued_new.id) == "cancelled"
    assert _status(db, Job, queued_done.id) == "cancelled"
    assert _status(db, Job, finished.id) == "succeeded"  # finished jobs are left alone
    with db.read() as session:
        scan_row = session.get_one(Job, scan.id)
        assert scan_row.status == JobStatus.RUNNING
        assert scan_row.cancel_requested  # the worker stops it
    # Nothing left to wait for: back to what they were, not « queued ».
    assert _status(db, Video, new) == "new"
    assert _status(db, Video, done) == "partial"
    assert queue.cancel_active(db) == (0, 0)  # asking twice changes nothing


def test_stop_only_some(db: Database) -> None:
    root, other = _root(db, "D:/cats 2026"), _root(db, "D:/autre")
    mine, theirs = _video(db, root, "a.mp4"), _video(db, other, "b.mp4")
    job_mine = queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=mine)
    job_theirs = queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=theirs)
    scan = queue.enqueue(db, JobKind.SCAN_ROOT, root_id=other)

    assert queue.cancel_active(db, root_id=root) == (1, 0)
    assert _status(db, Job, job_mine.id) == "cancelled"
    assert _status(db, Job, job_theirs.id) == "queued"
    assert queue.cancel_active(db, kinds=[JobKind.SCAN_ROOT]) == (1, 0)
    assert _status(db, Job, scan.id) == "cancelled"
    assert queue.cancel_active(db, video_ids=[theirs]) == (1, 0)


def test_one_cancelled_analysis_settles_its_video(db: Database) -> None:
    root = _root(db, "D:/cats 2026")
    video = _video(db, root, "a.mp4")
    job = queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=video)
    assert _status(db, Video, video) == "queued"
    queue.request_cancel(db, job.id)
    assert _status(db, Video, video) == "new"


def test_videos_left_waiting_by_an_older_version_are_repaired(db: Database) -> None:
    root = _root(db, "D:/cats 2026")
    stuck, waiting = _video(db, root, "a.mp4"), _video(db, root, "b.mp4")
    queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=waiting)
    with db.write() as session:  # cancelled the old way: the video kept « queued »
        session.get_one(Video, stuck).status = VideoStatus.QUEUED
    with db.write() as session:
        assert queue.settle_waiting_videos(session) == 1
    assert _status(db, Video, stuck) == "new"
    assert _status(db, Video, waiting) == "queued"  # its analysis still waits


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


def test_route(client: TestClient) -> None:
    db: Database = client.app.state.container.db  # type: ignore[attr-defined]
    root = _root(db, "D:/cats 2026")
    queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=_video(db, root, "a.mp4"))
    response = client.post("/api/v1/jobs/cancel", json={}, headers=HEADERS)
    assert response.status_code == 202
    assert response.json() == {"cancelled": 1, "stopping": 0}
    refused = client.post("/api/v1/jobs/cancel", json={"everything": True}, headers=HEADERS)
    assert refused.status_code == 422
    assert client.post("/api/v1/jobs/cancel", json={}).status_code == 403  # no client header


def test_a_stopped_request_leaves_the_video_as_its_last_analysis_did(db: Database) -> None:
    root = _root(db, "D:/cats 2026")
    ready, failed = (
        _video(db, root, "a.mp4", analysed=True),
        _video(db, root, "b.mp4", analysed=True),
    )
    for video_id, outcome in ((ready, JobStatus.SUCCEEDED), (failed, JobStatus.FAILED)):
        with db.write() as session:
            session.add(
                Job(kind=JobKind.ANALYZE_VIDEO, video_id=video_id, status=outcome,
                    started_at=datetime.now(UTC), finished_at=datetime.now(UTC))
            )  # fmt: skip
        queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=video_id, payload={"stages": ["ocr"]})
    assert _status(db, Video, ready) == VideoStatus.QUEUED
    assert queue.cancel_active(db) == (2, 0)
    assert _status(db, Video, ready) == VideoStatus.READY  # not « partial »: nothing to redo
    assert _status(db, Video, failed) == VideoStatus.FAILED
