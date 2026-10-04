"""Offline videos: the scans leave hidden and empty files aside, offline videos are
taken out of the library, or relinked to their files moved elsewhere (« Relink… »)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import InvalidInputError, NotFoundError, PathNotAllowedError
from vfe_vision.core.paths import path_key
from vfe_vision.db.models import Job, LibraryRoot, StageRun, Video
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobKind, JobStatus, RootKind, StageStatus, VideoStatus
from vfe_vision.jobs import queue
from vfe_vision.jobs.relink import relink_videos
from vfe_vision.jobs.scan import register_file, scan_root
from vfe_vision.services import offline
from vfe_vision.services.container import AppContainer

HEADERS = {"X-VFE-Client": "tests"}


class Sink:
    def __init__(self) -> None:
        self.events: list[str] = []

    def emit(self, type_: str, **_kwargs: Any) -> None:
        self.events.append(type_)


@pytest.fixture
def c(settings: Settings, db: Database) -> AppContainer:
    return AppContainer.create(settings)


def _file(folder: Path, name: str, content: bytes | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(name.encode() * 64 if content is None else content)
    return path


def _root(c: AppContainer, folder: Path, **extra: Any) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    with c.db.write() as session:
        root = LibraryRoot(path=str(folder), path_key=path_key(folder), label=folder.name, **extra)
        session.add(root)
        session.flush()
        return root.id


def _analysed(c: AppContainer, video_id: str) -> None:
    """An analysis that ran and succeeded."""
    now = datetime.now(UTC)
    with c.db.write() as session:
        session.add(
            StageRun(video_id=video_id, stage="probe", stage_version=1, cache_key="k",
                     status=StageStatus.SUCCEEDED)
        )  # fmt: skip
        session.add(
            Job(kind=JobKind.ANALYZE_VIDEO, video_id=video_id, status=JobStatus.SUCCEEDED,
                started_at=now, finished_at=now)
        )  # fmt: skip
        video = session.get_one(Video, video_id)
        video.status, video.last_analyzed_at = VideoStatus.READY, now


def _gone(c: AppContainer, video_id: str, path: Path, moved_to: Path | None = None) -> None:
    """The file left its place (moved, or deleted): the video is offline."""
    if moved_to is None:
        path.unlink()
    else:
        moved_to.parent.mkdir(parents=True, exist_ok=True)
        path.rename(moved_to)
    with c.db.write() as session:
        session.get_one(Video, video_id).status = VideoStatus.OFFLINE


def _offline(c: AppContainer, video_id: str) -> None:
    with c.db.write() as session:
        session.get_one(Video, video_id).status = VideoStatus.OFFLINE


def _video(c: AppContainer, video_id: str) -> Video | None:
    with c.db.read() as session:
        return session.get(Video, video_id)


def _relink(c: AppContainer, job: Job) -> Any:
    return relink_videos(c.db, job.payload, data_dir=c.settings.data_dir, events=Sink())


def test_scans_leave_hidden_and_empty_files_aside(c: AppContainer, tmp_path: Path) -> None:
    rushes = tmp_path / "Rushs"
    _file(rushes, "20260622_201056.mp4")
    _file(rushes, ".temp-20260622_201056811_BACK_SECOND_TELE.mp4")  # a Samsung camera's
    _file(rushes, "._20260622_201056.mp4")  # macOS's companion file
    _file(rushes, "vide.mp4", b"")
    report = scan_root(c.db, _root(c, rushes))
    assert (report.found, report.new, report.skipped_empty) == (2, 1, 1)
    with c.db.read() as session:
        assert list(session.execute(sa.select(Video.filename)).scalars()) == ["20260622_201056.mp4"]


def test_offline_videos_are_taken_out_with_their_analyses(c: AppContainer, tmp_path: Path) -> None:
    rushes = tmp_path / "Rushs"
    root_id = _root(c, rushes)
    gone_path, kept_path = _file(rushes, "parti.mp4"), _file(rushes, "la.mp4")
    busy_path = _file(rushes, "occupe.mp4")
    gone, kept = register_file(c.db, root_id, gone_path), register_file(c.db, root_id, kept_path)
    busy = register_file(c.db, root_id, busy_path)
    _analysed(c, gone)
    _gone(c, gone, gone_path)
    _gone(c, busy, busy_path)
    queue.enqueue(c.db, JobKind.ANALYZE_VIDEO, video_id=busy)
    assert queue.claim_next(c.db, worker_pid=1) is not None  # an analysis runs on it
    with c.db.write() as session:
        session.get_one(Video, busy).status = VideoStatus.OFFLINE  # its file went meanwhile

    assert offline.forget_videos(c, [gone, kept, busy]) == (1, 2)
    assert _video(c, gone) is None
    assert _video(c, kept) is not None  # its file is there: left alone
    assert _video(c, busy) is not None  # being analysed: left alone
    with c.db.read() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(StageRun)) == 0
    with pytest.raises(InvalidInputError):
        offline.forget_videos(c, [])


def test_a_root_of_chosen_files_goes_with_its_last_video(c: AppContainer, tmp_path: Path) -> None:
    elsewhere = tmp_path / "Ailleurs"
    x, y = _file(elsewhere, "x.mp4"), _file(elsewhere, "y.mp4")
    root_id = _root(c, elsewhere, kind=RootKind.FILES, files=["x.mp4", "y.mp4"], recursive=False)
    x_id, y_id = register_file(c.db, root_id, x), register_file(c.db, root_id, y)
    _gone(c, x_id, x)
    assert offline.forget_videos(c, [x_id]) == (1, 0)
    with c.db.read() as session:
        assert session.get_one(LibraryRoot, root_id).files == ["y.mp4"]
    _gone(c, y_id, y)
    offline.forget_videos(c, [y_id])
    with c.db.read() as session:
        assert session.get(LibraryRoot, root_id) is None


def test_relink_finds_moved_files_by_content(c: AppContainer, tmp_path: Path) -> None:
    rushes, disk = tmp_path / "Rushs", tmp_path / "Autre disque"
    root_id = _root(c, rushes)
    a, b, lost = _file(rushes, "a.mp4"), _file(rushes, "b.mp4"), _file(rushes, "perdu.mp4")
    a_id, b_id = register_file(c.db, root_id, a), register_file(c.db, root_id, b)
    lost_id = register_file(c.db, root_id, lost)
    _analysed(c, a_id)
    _gone(c, a_id, a, disk / "2026" / "a.mp4")
    _gone(c, b_id, b, disk / "renommée.mp4")  # renamed: found by its size and content
    _gone(c, lost_id, lost)
    _file(disk, "leurre.mp4", b"x" * len(b"b.mp4" * 64))  # same size, other content

    job = offline.request_relink(c, [a_id, b_id, lost_id], str(disk))
    assert (job.kind, job.payload["folder"]) == (JobKind.RELINK_VIDEOS, str(disk))
    report = _relink(c, job)
    assert (report.linked, report.missing, report.conflicts) == (2, 1, 0)
    assert report.message == "2 vidéos reliées · 1 introuvable"
    moved = _video(c, a_id)
    assert moved is not None
    assert (moved.path, moved.status) == (str(disk / "2026" / "a.mp4"), VideoStatus.READY)
    with c.db.read() as session:
        chosen = {
            Path(root.path).name: root.files
            for root in session.execute(
                sa.select(LibraryRoot).where(LibraryRoot.kind == RootKind.FILES)
            ).scalars()
        }
    assert chosen == {"2026": ["a.mp4"], "Autre disque": ["renommée.mp4"]}  # only these files
    renamed = _video(c, b_id)
    assert renamed is not None
    assert (renamed.filename, renamed.status) == ("renommée.mp4", VideoStatus.NEW)
    assert _video(c, lost_id) is not None  # not found: still offline, still there


def test_relink_takes_the_place_of_a_copy_registered_anew(c: AppContainer, tmp_path: Path) -> None:
    rushes, other = tmp_path / "Rushs", tmp_path / "Autre"
    root_id, other_root = _root(c, rushes), _root(c, other)
    a = _file(rushes, "a.mp4")
    a_id = register_file(c.db, root_id, a)
    _analysed(c, a_id)
    a.rename(other / "a.mp4")
    # The folder it went to was scanned before its own: seen as new (nothing offline yet).
    copy_id = register_file(c.db, other_root, other / "a.mp4")
    _offline(c, a_id)

    assert _relink(c, offline.request_relink(c, [a_id], str(other))).linked == 1
    assert _video(c, copy_id) is None
    moved = _video(c, a_id)
    assert moved is not None
    assert (moved.root_id, moved.rel_path) == (other_root, "a.mp4")

    b = _file(rushes, "b.mp4")
    b_id = register_file(c.db, root_id, b)
    b.rename(other / "b.mp4")
    rival = register_file(c.db, other_root, other / "b.mp4")
    _offline(c, b_id)
    _analysed(c, rival)  # analysed on its own since: never overwritten
    assert _relink(c, offline.request_relink(c, [b_id], str(other))).conflicts == 1
    assert _video(c, rival) is not None


def test_relink_requests_are_checked(c: AppContainer, tmp_path: Path) -> None:
    rushes = tmp_path / "Rushs"
    a_id = register_file(c.db, _root(c, rushes), _file(rushes, "a.mp4"))
    with pytest.raises(InvalidInputError, match="hors ligne"):
        offline.request_relink(c, [a_id], str(tmp_path))
    with pytest.raises(NotFoundError):
        offline.request_relink(c, [a_id], str(tmp_path / "nulle part"))
    c.settings.data_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PathNotAllowedError):
        offline.request_relink(c, [a_id], str(c.settings.data_dir))


def test_offline_api(settings: Settings, tmp_path: Path) -> None:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        rushes = tmp_path / "Rushs"
        a = _file(rushes, "a.mp4")
        a_id = register_file(container.db, _root(container, rushes), a)
        _gone(container, a_id, a, tmp_path / "Ailleurs" / "a.mp4")
        relink = client.post(
            "/api/v1/videos/relink",
            json={"video_ids": [a_id], "folder": str(tmp_path / "Ailleurs")},
            headers=HEADERS,
        )
        assert relink.status_code == 202, relink.text
        assert relink.json()["kind"] == "relink_videos"
        listed = client.get("/api/v1/jobs", params={"kind": "relink_videos"}).json()
        assert listed[0]["target"] == "Ailleurs"
        forget = client.post("/api/v1/videos/forget", json={"video_ids": [a_id]}, headers=HEADERS)
        assert forget.json() == {"forgotten": 1, "left": 0}
        assert (
            client.post(
                "/api/v1/videos/forget", json={"video_ids": []}, headers=HEADERS
            ).status_code
            == 422
        )
