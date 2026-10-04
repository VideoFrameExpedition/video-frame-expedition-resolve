"""The Jobs page: what each job is about, the queue in the worker's order, the
history, and the summary with its estimate of the time left."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings
from vfe_vision.db.models import Job, LibraryRoot, TimelineBin, Video
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobKind, JobStatus, VideoStatus
from vfe_vision.jobs import queue
from vfe_vision.services import jobs
from vfe_vision.services.container import AppContainer

HEADERS = {"X-VFE-Client": "tests"}
NOW = datetime.now(UTC)


@pytest.fixture
def c(settings: Settings, db: Database) -> AppContainer:
    return AppContainer.create(settings)


def _root(db: Database, label: str) -> str:
    with db.write() as session:
        root = LibraryRoot(path=f"D:/{label}", path_key=f"d:/{label}".casefold(), label=label)
        session.add(root)
        session.flush()
        return root.id


def _video(db: Database, root_id: str, rel_path: str) -> str:
    with db.write() as session:
        video = Video(
            root_id=root_id, path=f"D:/x/{rel_path}", path_key=f"d:/x/{rel_path}".casefold(),
            rel_path=rel_path, filename=Path(rel_path).name, size_bytes=1, mtime=0.0,
            fingerprint=rel_path, status=VideoStatus.NEW,
        )  # fmt: skip
        session.add(video)
        session.flush()
        return video.id


def _done(db: Database, video_id: str, status: JobStatus, minutes: float, ago_h: float) -> None:
    """An analysis that took ``minutes`` and finished ``ago_h`` hours ago."""
    finished = NOW - timedelta(hours=ago_h)
    with db.write() as session:
        session.add(
            Job(
                kind=JobKind.ANALYZE_VIDEO, video_id=video_id, status=status,
                created_at=finished - timedelta(minutes=minutes + 1),
                started_at=finished - timedelta(minutes=minutes), finished_at=finished,
            )
        )  # fmt: skip


def test_jobs_say_what_they_are_about_and_the_queue_keeps_the_worker_order(
    c: AppContainer,
) -> None:
    cats = _root(c.db, "CATS")
    first, second = _video(c.db, cats, "hdr/a.mp4"), _video(c.db, cats, "b.mp4")
    later = queue.enqueue(c.db, JobKind.ANALYZE_VIDEO, video_id=first)
    urgent = queue.enqueue(c.db, JobKind.ANALYZE_VIDEO, video_id=second, priority=10)
    scan = queue.enqueue(c.db, JobKind.SCAN_ROOT, root_id=cats, priority=50)
    with c.db.write() as session:
        timeline = TimelineBin(
            label="Montage", source_key="p/t", resolve={}, auto_analyze=False, report={},
            synced_at=NOW,
        )  # fmt: skip
        session.add(timeline)
        session.flush()
        bin_id = timeline.id
    sync = queue.enqueue(c.db, JobKind.SYNC_TIMELINE, payload={"bin_id": bin_id}, priority=10)

    waiting = jobs.list_jobs(c, statuses=(JobStatus.QUEUED,), order="queue")
    assert [view.job.id for view in waiting] == [urgent.id, sync.id, scan.id, later.id]
    by_id = {view.job.id: view for view in waiting}
    assert (by_id[later.id].target, by_id[later.id].place) == ("a.mp4", "CATS › hdr")
    assert (by_id[urgent.id].target, by_id[urgent.id].place) == ("b.mp4", "CATS")
    assert by_id[scan.id].target == "CATS"
    assert by_id[sync.id].target == "Montage"
    assert [v.job.kind for v in jobs.list_jobs(c, kinds=(JobKind.SCAN_ROOT,))] == [
        JobKind.SCAN_ROOT
    ]
    assert len(jobs.list_jobs(c, limit=2, offset=3)) == 1


def test_the_summary_counts_the_last_day_and_estimates_the_time_left(c: AppContainer) -> None:
    cats = _root(c.db, "CATS")
    videos = [_video(c.db, cats, f"{n}.mp4") for n in range(6)]
    _done(c.db, videos[0], JobStatus.SUCCEEDED, minutes=4, ago_h=1)
    _done(c.db, videos[1], JobStatus.PARTIAL, minutes=6, ago_h=2)
    _done(c.db, videos[2], JobStatus.FAILED, minutes=1, ago_h=3)  # not a pace: it failed
    _done(c.db, videos[3], JobStatus.SUCCEEDED, minutes=5, ago_h=30)  # a pace, not « today »
    for video_id in videos[4:]:
        queue.enqueue(c.db, JobKind.ANALYZE_VIDEO, video_id=video_id)

    found = jobs.summary(c)
    assert (found.running, found.queued) == (0, 2)
    assert found.finished == {"succeeded": 1, "partial": 1, "failed": 1}
    assert found.analysis_s == pytest.approx(300.0)  # median of 4, 5 and 6 minutes
    assert found.parallel == c.settings.max_concurrent_videos
    assert found.eta_s == pytest.approx(2 * 300.0 / found.parallel)

    running = queue.claim_next(c.db, worker_pid=1)
    assert running is not None
    with c.db.write() as session:  # started a minute ago (not « now »: Windows' clock is coarse)
        session.get_one(Job, running.id).started_at = datetime.now(UTC) - timedelta(minutes=1)
    found = jobs.summary(c)
    assert (found.running, found.queued) == (1, 1)
    assert found.eta_s == pytest.approx((300.0 + 240.0) / found.parallel, abs=1.0)


def test_jobs_api(settings: Settings) -> None:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        video = _video(container.db, _root(container.db, "CATS"), "a.mp4")
        queue.enqueue(container.db, JobKind.ANALYZE_VIDEO, video_id=video)
        listed = client.get("/api/v1/jobs", params={"status": "queued", "order": "queue"})
        assert listed.status_code == 200, listed.text
        [job] = listed.json()
        assert (job["target"], job["place"]) == ("a.mp4", "CATS")
        summary = client.get("/api/v1/jobs/summary").json()
        assert (summary["running"], summary["queued"], summary["eta_s"]) == (0, 1, None)
        assert client.get("/api/v1/jobs", params={"order": "sideways"}).status_code == 422
