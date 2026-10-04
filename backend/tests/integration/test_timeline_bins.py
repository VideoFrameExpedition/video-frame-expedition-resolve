"""Resolve timelines in the library: a timeline's files brought in as a timeline bin,
the roots of chosen files, the update job (run through its function), the links on the videos,
the analysis files written again, the migration."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import create_engine

from tests.conftest import FakeLmStudio
from tests.fakes.context import FakeGeocoder, FakeWeather
from tests.fakes.resolve_source import PROJECT, FakeResolve, clip, timeline
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    PathNotAllowedError,
)
from vfe_vision.core.paths import path_key
from vfe_vision.db.migrate import alembic_config
from vfe_vision.db.models import Job, LibraryRoot, StageRun, TimelineBin, TimelineBinItem, Video
from vfe_vision.db.preferences import update_preferences
from vfe_vision.db.session import Database
from vfe_vision.db.timeline_bins import resolve_links
from vfe_vision.domain.enums import JobKind, JobStatus, RootKind, StageStatus, VideoStatus
from vfe_vision.domain.resolve_timeline import ClipKind, ResolveProjectRef
from vfe_vision.jobs import queue, scan
from vfe_vision.jobs import timeline_bins as sync_job
from vfe_vision.jobs.scan import register_file, scan_root
from vfe_vision.jobs.sidecars import write_after as really_write
from vfe_vision.jobs.worker import Worker
from vfe_vision.services import analysis, library, timeline_bins, videos
from vfe_vision.services.container import AppContainer


class Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(
        self,
        type_: str,
        *,
        job_id: str | None = None,
        video_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.events.append((type_, data or {}))


@pytest.fixture
def resolve() -> FakeResolve:
    return FakeResolve()


@pytest.fixture
def c(settings: Settings, db: Database, resolve: FakeResolve) -> AppContainer:
    container = AppContainer.create(settings)
    container.resolve = resolve
    return container


def _file(folder: Path, name: str, content: bytes | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content if content is not None else name.encode() * 64)
    return path


def _root(c: AppContainer, folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    with c.db.write() as session:
        root = LibraryRoot(path=str(folder), path_key=path_key(folder), label=folder.name)
        session.add(root)
        session.flush()
        return root.id


def _analysed(c: AppContainer, video_id: str) -> None:
    """An analysis done: a stage run, the video ready."""
    with c.db.write() as session:
        session.add(
            StageRun(
                video_id=video_id, stage="metadata", stage_version=1, cache_key="k",
                status=StageStatus.SUCCEEDED,
            )
        )  # fmt: skip
        video = session.get_one(Video, video_id)
        video.status = VideoStatus.READY
        video.last_analyzed_at = datetime.now(UTC)


def _bring_in(c: AppContainer, timeline_id: str = "tl-1", **kwargs: Any) -> timeline_bins.BinImport:
    return timeline_bins.import_timeline(
        c, project_id=PROJECT.id, timeline_id=timeline_id, **kwargs
    )


def _run(c: AppContainer, job_id: str, sink: Sink | None = None) -> sync_job.SyncReport:
    """The update job as the worker runs it (claimed, run, finished)."""
    with c.db.write() as session:
        job = session.get_one(Job, job_id)
        job.status = JobStatus.RUNNING
        payload = dict(job.payload)
    report = sync_job.sync_timeline(
        c.db, payload, data_dir=c.settings.data_dir, events=sink or Sink(), job_id=job_id
    )
    queue.finish(c.db, job_id, JobStatus.SUCCEEDED, message=report.message)
    return report


def _video_of(c: AppContainer, path: Path) -> Video | None:
    with c.db.read() as session:
        return session.execute(
            sa.select(Video).where(Video.path_key == path_key(path))
        ).scalar_one_or_none()


def _files_roots(c: AppContainer) -> list[LibraryRoot]:
    with c.db.read() as session:
        return list(
            session.execute(
                sa.select(LibraryRoot).where(LibraryRoot.kind == RootKind.FILES)
            ).scalars()
        )


def _states(c: AppContainer, bin_id: str) -> list[str]:
    return [item.state for item in timeline_bins.bin_items(c, bin_id)]


# ---------------------------------------------------------------- bringing a timeline in
def test_a_timeline_brings_its_videos_in(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    rushes, elsewhere = tmp_path / "Rushs été", tmp_path / "Ailleurs"
    known, in_folder = _file(rushes, "a.mp4"), _file(rushes, "b.mp4")
    alone, sibling = _file(elsewhere, "x.mp4"), _file(elsewhere, "voisin.mp4")
    known_id = register_file(c.db, _root(c, rushes), known)
    resolve.put(
        timeline("tl-1", "Montage"),
        [
            clip(str(alone), 0, 4),
            clip(str(known), 4, 8),
            clip(str(known), 4, 8, track_type="audio"),  # its linked sound: not another file
            clip(str(in_folder), 8, 12, enabled=False),
            clip(str(rushes / "parti.mp4"), 12, 14),
            clip(None, 14, 16, kind=ClipKind.GRAPHICS),
            clip(str(rushes / "son.wav"), 0, 16, track_type="audio", clip_type="Audio"),
            clip(str(rushes / "camera.braw"), 16, 18, clip_type="Video"),
        ],
    )

    seen = timeline_bins.preview(c, project_id=PROJECT.id, timeline_id="tl-1")
    assert (seen.files, seen.in_library, seen.in_folder, seen.new_folder) == (4, 1, 1, 1)
    assert (seen.missing, seen.unknown, seen.other_path) == (1, 0, 0)
    assert (seen.disabled_only, seen.to_analyze) == (1, 3)  # the known one was never analysed
    assert seen.new_folders == (str(elsewhere),)
    skipped = seen.skipped
    assert (skipped.graphics, skipped.not_video, skipped.unsupported) == (1, 1, 1)
    assert seen.bin_id is None
    assert seen.snapshot_id

    brought = _bring_in(c, snapshot_id=seen.snapshot_id)
    assert resolve.timeline_reads == ["tl-1"]  # the preview's read, reused
    assert brought.created
    bin_id = brought.stats.bin.id
    assert brought.stats.bin.label == "Montage"
    assert brought.job.kind == JobKind.SYNC_TIMELINE
    assert brought.job.payload["bin_id"] == bin_id
    assert brought.preview.bin_id == bin_id
    assert _states(c, bin_id) == ["adding", "in_library", "adding", "missing"]

    sink = Sink()
    report = _run(c, brought.job.id, sink)
    assert report.message == (
        "3 vidéos liées · 2 ajoutées · 1 introuvable · 0 hors bibliothèque · 3 analyses demandées"
    )
    [chosen] = _files_roots(c)
    assert (chosen.path, chosen.files, chosen.recursive) == (str(elsewhere), ["x.mp4"], False)
    assert chosen.label == "Ailleurs"
    assert _video_of(c, sibling) is None  # only the timeline's videos join the library
    with pytest.raises(PathNotAllowedError):
        analysis.analyze_path(c, str(sibling))
    added = _video_of(c, alone)
    assert added is not None
    assert added.root_id == chosen.id
    assert added.rel_path == "x.mp4"

    [stats] = timeline_bins.list_bins(c)
    assert (stats.items, stats.videos) == (4, 3)
    assert stats.states == {
        "in_library": 3, "adding": 0, "not_processed": 0, "removed": 0, "missing": 1,
        "outside": 0, "error": 0,
    }  # fmt: skip
    assert stats.sync_job is not None
    assert stats.sync_job.status == JobStatus.SUCCEEDED
    with c.db.read() as session:
        asked = session.execute(
            sa.select(Job.video_id).where(Job.kind == JobKind.ANALYZE_VIDEO)
        ).scalars()
        assert len(set(asked)) == 3
    assert [kind for kind, _ in sink.events] == ["library.scanned", "timeline.synced"]
    assert sink.events[-1][1] == {
        "bin_id": bin_id, "linked": 3, "added": 2, "missing": 1, "outside": 0, "errors": 0,
        "queued": 3,
    }  # fmt: skip

    in_order = videos.list_videos(c, videos.VideoFilters(timeline_bin_id=bin_id))
    assert [v.filename for v in in_order.items] == ["x.mp4", "a.mp4", "b.mp4"]
    assert in_order.timeline_bins[known_id] == [bin_id]
    by_name = videos.list_videos(c, videos.VideoFilters(timeline_bin_id=bin_id, sort="name"))
    assert [v.filename for v in by_name.items] == ["a.mp4", "b.mp4", "x.mp4"]
    with pytest.raises(InvalidInputError):
        videos.list_videos(c, videos.VideoFilters(timeline_bin_id=bin_id, root_id=chosen.id))
    everything = videos.list_videos(c, videos.VideoFilters())
    assert everything.total == 3


def test_a_file_already_known_under_another_path(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    """A share and its mapped drive, or a copy: the same content, found by name and size, or
    by the id our Resolve script tagged, then checked by content."""
    rushes, elsewhere = tmp_path / "Rushs", tmp_path / "Copie"
    original = _file(rushes, "plan.mp4", b"same content" * 100)
    video_id = register_file(c.db, _root(c, rushes), original)
    copy = _file(elsewhere, "plan.mp4", b"same content" * 100)
    renamed = _file(elsewhere, "renommé.mp4", b"same content" * 100)
    lookalike = _file(elsewhere, "autre.mp4", b"other conten" * 100)  # same size only
    resolve.put(
        timeline("tl-1", "Montage"),
        [
            clip(str(copy), 0, 4),
            clip(str(renamed), 4, 8, vfe_video_id=video_id),
            clip(str(lookalike), 8, 12),
        ],
    )
    seen = timeline_bins.preview(c, project_id=PROJECT.id, timeline_id="tl-1")
    assert (seen.other_path, seen.new_folder) == (2, 1)

    brought = _bring_in(c, allow_new_folders=False)
    report = _run(c, brought.job.id)
    assert (report.linked, report.added, report.outside) == (2, 0, 1)
    with c.db.read() as session:
        items = (
            session.execute(sa.select(TimelineBinItem).order_by(TimelineBinItem.position))
            .scalars()
            .all()
        )
    assert [i.video_key for i in items] == [path_key(original), path_key(original), None]
    assert items[0].note == f"même fichier que {original}"
    assert (items[2].state, items[2].note) == ("outside", "hors bibliothèque")
    assert _files_roots(c) == []  # adding folders was not allowed
    listed = videos.list_videos(c, videos.VideoFilters(timeline_bin_id=brought.stats.bin.id))
    assert [v.id for v in listed.items] == [video_id]
    with c.db.read() as session:
        [link] = resolve_links(session, [path_key(original)])[path_key(original)]
    assert len(link.uses) == 2  # both paths of the file, one link

    again = _bring_in(c)  # the match is kept: no need to read the files again
    with c.db.read() as session:
        kept = session.execute(
            sa.select(TimelineBinItem.video_key).where(TimelineBinItem.path_key == path_key(copy))
        ).scalar_one()
    assert kept == path_key(original)
    report = _run(c, again.job.id)
    assert (report.linked, report.added) == (3, 1)  # the lookalike joins alone this time
    [chosen] = _files_roots(c)
    assert chosen.files == ["autre.mp4"]


def test_long_paths_and_shares_name_the_files_of_the_library(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    rushes = tmp_path / "Rushs"
    local = _file(rushes, "a.mp4")
    root_id = _root(c, rushes)
    register_file(c.db, root_id, local)
    share = r"\\nas-vfe-test\rushs\b.mp4"
    with c.db.write() as session:
        session.add(
            Video(
                root_id=root_id, path=share, path_key=path_key(share), rel_path="b.mp4",
                filename="b.mp4", size_bytes=1, mtime=0.0, fingerprint="b",
            )
        )  # fmt: skip
    resolve.put(
        timeline("tl-1", "Montage"),
        [
            clip("\\\\?\\" + str(local), 0, 4),
            clip(r"\\?\UNC\nas-vfe-test\rushs\b.mp4", 4, 8),
            clip("/Volumes/rushs/c.mp4", 8, 12),  # a path of another computer
        ],
    )
    started = time.monotonic()
    seen = timeline_bins.preview(c, project_id=PROJECT.id, timeline_id="tl-1")
    assert time.monotonic() - started < 3  # the share is never asked
    assert (seen.files, seen.in_library, seen.skipped.elsewhere) == (2, 2, 1)


def test_an_unreachable_share_is_not_waited_for(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here, dead, broken = tmp_path / "Ici", tmp_path / "Partage", tmp_path / "Cassé"
    _file(here, "a.mp4")
    real = sync_job.folder_files

    def listing(folder: str) -> sync_job.FolderFiles | None:
        if folder == str(dead):
            time.sleep(1.5)  # a share that does not answer
        if folder == str(broken):
            raise OSError("réseau injoignable")
        return real(folder)

    monkeypatch.setattr(timeline_bins, "folder_files", listing)
    monkeypatch.setattr(timeline_bins, "LOOK_DEADLINE_S", 0.3)
    resolve.put(
        timeline("tl-1", "Montage"),
        [clip(str(here / "a.mp4"), 0, 4), clip(str(dead / "b.mp4"), 4, 8),
         clip(str(broken / "c.mp4"), 8, 12)],
    )  # fmt: skip
    started = time.monotonic()
    seen = timeline_bins.preview(c, project_id=PROJECT.id, timeline_id="tl-1")
    assert time.monotonic() - started < 1.2
    assert (seen.new_folder, seen.unknown) == (1, 2)
    brought = _bring_in(c, snapshot_id=seen.snapshot_id)
    notes = [i.note for i in timeline_bins.bin_items(c, brought.stats.bin.id)]
    assert notes == [None, timeline_bins.UNREACHABLE_NOTE, timeline_bins.UNREACHABLE_NOTE]


def test_a_onedrive_file_not_on_this_computer_is_not_read(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def in_the_cloud(path: Path) -> bool:
        return path.name == "nuage.mp4"

    monkeypatch.setattr(timeline_bins, "is_cloud_placeholder", in_the_cloud)
    monkeypatch.setattr(sync_job, "is_cloud_placeholder", in_the_cloud)
    folder = tmp_path / "OneDrive"
    cloud, here = _file(folder, "nuage.mp4"), _file(folder, "ici.mp4")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(cloud), 0, 4), clip(str(here), 4, 8)])
    brought = _bring_in(c)
    items = timeline_bins.bin_items(c, brought.stats.bin.id)
    assert [(i.state, i.note) for i in items] == [
        ("error", sync_job.CLOUD_NOTE), ("adding", None)
    ]  # fmt: skip
    report = _run(c, brought.job.id)
    assert (report.linked, report.errors) == (1, 1)
    assert report.message.endswith("· 1 en erreur")
    assert _video_of(c, cloud) is None
    assert _states(c, brought.stats.bin.id) == ["error", "in_library"]


def test_the_application_data_folder_stays_outside(c: AppContainer, resolve: FakeResolve) -> None:
    inside = _file(c.settings.data_dir / "exports", "clip.mp4")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(inside), 0, 4)])
    report = _run(c, _bring_in(c).job.id)
    assert report.outside == 1
    assert _files_roots(c) == []


# ---------------------------------------------------------------- updating a timeline bin
def test_updating_a_timeline_mirrors_it(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    rushes = tmp_path / "Rushs"
    a, b, e = _file(rushes, "a.mp4"), _file(rushes, "b.mp4"), _file(rushes, "e.mp4")
    _root(c, rushes)
    resolve.put(timeline("tl-1", "Montage"), [clip(str(a), 0, 4), clip(str(b), 4, 8)])
    first = _bring_in(c)
    _run(c, first.job.id)

    resolve.put(timeline("tl-1", "Montage final"), [clip(str(e), 0, 4), clip(str(a), 4, 8)])
    again = _bring_in(c)  # no preview: Resolve is read again
    assert resolve.timeline_reads == ["tl-1", "tl-1"]
    assert not again.created
    bin_id = again.stats.bin.id
    assert bin_id == first.stats.bin.id
    assert again.stats.bin.label == "Montage final"  # named after its timeline: follows it
    assert again.preview.bin_id == bin_id
    _run(c, again.job.id)
    listed = videos.list_videos(c, videos.VideoFilters(timeline_bin_id=bin_id))
    assert [v.filename for v in listed.items] == ["e.mp4", "a.mp4"]
    assert _video_of(c, b) is not None  # out of the timeline, still in the library

    timeline_bins.update_bin(c, bin_id, {"label": "Chats"})
    resolve.put(timeline("tl-1", "Montage v3"), [clip(str(a), 0, 4)])
    assert _bring_in(c).stats.bin.label == "Chats"  # a name of the user's stays
    assert _bring_in(c, label="Chats 2026").stats.bin.label == "Chats 2026"
    with pytest.raises(InvalidInputError):
        timeline_bins.update_bin(c, bin_id, {"label": "  "})


def test_a_timeline_with_a_new_id_finds_its_bin_again(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    a = _file(tmp_path / "Rushs", "a.mp4")
    _root(c, tmp_path / "Rushs")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(a), 0, 4)])
    first = _bring_in(c)
    bin_id = first.stats.bin.id
    _run(c, first.job.id)

    resolve.timelines.clear()  # duplicated, or the project imported again: another id
    resolve.put(timeline("tl-2", "Montage"), [clip(str(a), 0, 4)])
    seen = timeline_bins.preview(c, project_id=PROJECT.id, timeline_id="tl-2")
    assert seen.bin_id == bin_id
    [view] = timeline_bins.project_view(c).timelines
    assert view.bin is not None
    assert view.bin.id == bin_id
    again = _bring_in(c, "tl-2", snapshot_id=seen.snapshot_id)
    assert (again.created, again.stats.bin.id) == (False, bin_id)
    assert again.stats.bin.source_key == f"{PROJECT.id}/tl-2"

    resolve.timelines.clear()
    resolve.put(timeline("tl-3", "Montage"), [clip(str(a), 0, 4)])
    synced = timeline_bins.sync_bin(c, bin_id)  # « Update from Resolve »
    assert synced.stats.bin.source_key == f"{PROJECT.id}/tl-3"
    assert synced.job.id == again.job.id  # merged into the update still queued

    resolve.put(timeline("tl-4", "Autre montage"), [clip(str(a), 0, 4)])
    other = _bring_in(c, "tl-4")
    assert other.created  # another name: another timeline


def test_updating_needs_the_timeline_s_project(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    a = _file(tmp_path / "Rushs", "a.mp4")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(a), 0, 4)])
    bin_id = _bring_in(c).stats.bin.id
    with pytest.raises(ConflictError, match="a changé") as changed:
        timeline_bins.preview(c, project_id="prj-before", timeline_id="tl-1")
    assert changed.value.extra == {"project": "cats 2026"}

    resolve.open_project(ResolveProjectRef(id="prj-other", name="autre"))
    with pytest.raises(ConflictError, match="Ouvrez le projet « cats 2026 »"):
        timeline_bins.sync_bin(c, bin_id)
    resolve.open_project(ResolveProjectRef(id="prj-copy", name="cats 2026"))
    with pytest.raises(ConflictError, match=r"même nom \(base « Local Database »\)"):
        timeline_bins.sync_bin(c, bin_id)
    resolve.open_project(PROJECT)  # the project, without the timeline
    with pytest.raises(NotFoundError, match="La timeline « Montage » n'existe plus"):
        timeline_bins.sync_bin(c, bin_id)
    with pytest.raises(NotFoundError):
        timeline_bins.sync_bin(c, "unknown-bin")


def test_one_update_per_timeline_bin(c: AppContainer, resolve: FakeResolve, tmp_path: Path) -> None:
    a = _file(tmp_path / "Rushs", "a.mp4")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(a), 0, 4)])
    resolve.put(timeline("tl-2", "Bande-annonce"), [clip(str(a), 0, 4)])
    one, two = _bring_in(c, "tl-1"), _bring_in(c, "tl-2")
    assert one.job.id != two.job.id  # two bins, never one job
    assert _bring_in(c, "tl-1").job.id == one.job.id  # still queued: the same update

    first = queue.enqueue(
        c.db, JobKind.SYNC_TIMELINE,
        payload={"bin_id": "b1", "allow_new_folders": False, "analyze": True,
                 "videos": ["v1", "v2"]},
    )  # fmt: skip
    merged = queue.enqueue(
        c.db, JobKind.SYNC_TIMELINE,
        payload={"bin_id": "b1", "allow_new_folders": True, "analyze": False,
                 "videos": ["v2", "v3"]},
    )  # fmt: skip
    assert merged.id == first.id
    assert merged.payload == {
        "bin_id": "b1", "allow_new_folders": True, "analyze": True, "videos": ["v1", "v2", "v3"]
    }  # fmt: skip
    other = queue.enqueue(c.db, JobKind.SYNC_TIMELINE, payload={"bin_id": "b2"})
    assert other.id != first.id

    with c.db.write() as session:
        session.get_one(Job, one.job.id).status = JobStatus.RUNNING
    with pytest.raises(ConflictError, match="déjà en cours"):
        _bring_in(c, "tl-1")


def test_what_the_page_shows_of_each_file(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "Ailleurs"
    x = _file(elsewhere, "x.mp4")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(x), 0, 4)])
    brought = _bring_in(c)
    bin_id = brought.stats.bin.id
    assert _states(c, bin_id) == ["adding"]
    queue.request_cancel(c.db, brought.job.id)  # « Stop all » before it ran
    assert _states(c, bin_id) == ["not_processed"]
    [stats] = timeline_bins.list_bins(c)
    assert stats.states["not_processed"] == 1
    assert stats.sync_job is not None
    assert stats.sync_job.status == JobStatus.CANCELLED

    _run(c, _bring_in(c).job.id)
    assert _states(c, bin_id) == ["in_library"]
    [chosen] = _files_roots(c)
    library.remove_root(c, chosen.id)  # « Remove from library »: its videos are forgotten
    assert _states(c, bin_id) == ["removed"]

    report = _run(c, _bring_in(c, allow_new_folders=False).job.id)
    assert report.outside == 1
    assert _states(c, bin_id) == ["outside"]
    x.unlink()
    _run(c, _bring_in(c).job.id)
    assert _states(c, bin_id) == ["missing"]


def test_removing_a_timeline_forgets_no_video(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    x = _file(tmp_path / "Ailleurs", "x.mp4")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(x), 0, 4)])
    brought = _bring_in(c)
    _run(c, brought.job.id)
    timeline_bins.remove_bin(c, brought.stats.bin.id)
    assert timeline_bins.list_bins(c) == []
    assert _video_of(c, x) is not None
    with c.db.read() as session:
        assert (
            session.execute(sa.select(sa.func.count()).select_from(TimelineBinItem)).scalar() == 0
        )
    with pytest.raises(NotFoundError):
        timeline_bins.remove_bin(c, brought.stats.bin.id)
    queued = _bring_in(c)
    timeline_bins.remove_bin(c, queued.stats.bin.id)
    report = _run(c, queued.job.id)  # removed before its update ran
    assert report.gone
    assert report.message == sync_job.GONE


# ---------------------------------------------------------------- roots of chosen files
def _one_file_brought_in(c: AppContainer, resolve: FakeResolve, file: Path) -> tuple[str, str, str]:
    """A timeline using one file outside the library: (bin id, video id, its root id)."""
    resolve.put(timeline("tl-1", "Montage"), [clip(str(file), 0, 4)])
    brought = _bring_in(c)
    _run(c, brought.job.id)
    video = _video_of(c, file)
    assert video is not None
    _analysed(c, video.id)
    return brought.stats.bin.id, video.id, video.root_id


def _runs(c: AppContainer, video_id: str) -> int:
    with c.db.read() as session:
        return session.execute(
            sa.select(sa.func.count()).where(StageRun.video_id == video_id)
        ).scalar_one()


def test_adding_the_folder_of_chosen_files_keeps_their_analyses(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    folder = tmp_path / "Ailleurs"
    bin_id, video_id, chosen_id = _one_file_brought_in(c, resolve, _file(folder, "x.mp4"))
    _file(folder, "voisin.mp4")
    root, job = library.add_root(c, str(folder), label="Tout Ailleurs", recursive=True)
    assert root.id == chosen_id  # the same root, now the whole folder
    assert (root.kind, root.files, root.label, root.recursive) == (
        RootKind.FOLDER, None, "Tout Ailleurs", True,
    )  # fmt: skip
    assert (job.kind, job.root_id) == (JobKind.SCAN_ROOT, chosen_id)
    video = _video_of(c, folder / "x.mp4")
    assert video is not None
    assert (video.id, video.root_id) == (video_id, chosen_id)
    assert _runs(c, video_id) == 1
    assert _states(c, bin_id) == ["in_library"]


def test_adding_a_folder_above_chosen_files_takes_them_in(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    shoot = tmp_path / "Tournage"
    bin_id, video_id, chosen_id = _one_file_brought_in(c, resolve, _file(shoot / "jour 1", "x.mp4"))
    queue.enqueue(c.db, JobKind.SCAN_ROOT, root_id=chosen_id)  # a scan of the chosen files
    with pytest.raises(ConflictError, match="mise à jour d'un dossier concerné"):
        library.add_root(c, str(shoot))
    assert [r.id for r in _files_roots(c)] == [chosen_id]  # nothing changed
    queue.cancel_active(c.db, kinds=[JobKind.SCAN_ROOT])

    root, _job = library.add_root(c, str(shoot))
    video = _video_of(c, shoot / "jour 1" / "x.mp4")
    assert video is not None
    assert (video.id, video.root_id, video.rel_path) == (video_id, root.id, "jour 1/x.mp4")
    assert _files_roots(c) == []
    assert _runs(c, video_id) == 1  # its analyses stay
    assert _states(c, bin_id) == ["in_library"]
    [tree] = library.folder_tree(c)
    assert [child.name for child in tree.tree.children] == ["jour 1"]


def test_folders_of_chosen_files_are_analysed_and_scanned_for_those_files(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "Disque" / "Ailleurs"
    x = _file(folder, "x.mp4")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(x), 0, 4)])
    _run(c, _bring_in(c, auto_analyze=False).job.id)
    [chosen] = _files_roots(c)
    sibling = _file(folder, "voisin.mp4")

    result = analysis.analyze_folder(c, str(folder))
    assert (result.root_id, result.found, result.queued) == (chosen.id, 1, 1)
    assert _video_of(c, sibling) is None
    with pytest.raises(ConflictError, match="ajoutées depuis une timeline"):
        analysis.analyze_folder(c, str(tmp_path / "Disque"))

    with pytest.raises(InvalidInputError, match="ni sous-dossiers ni exclusions"):
        library.update_root(c, chosen.id, {"recursive": True})
    with pytest.raises(InvalidInputError, match="ni sous-dossiers ni exclusions"):
        library.update_root(c, chosen.id, {"exclude_globs": ["*.mov"]})
    assert library.update_root(c, chosen.id, {"label": "Chats", "recursive": False}).label == (
        "Chats"
    )
    [stats] = [s for s in library.list_roots(c) if s.root.id == chosen.id]
    assert stats.video_count == 1

    monkeypatch.setattr(scan, "still_copying", lambda *_args, **_kwargs: False)
    with c.db.write() as session:  # Resolve spelled it otherwise: the disk's spelling wins
        session.get_one(LibraryRoot, chosen.id).files = ["X.MP4"]
        session.execute(sa.delete(Video))
    report = scan_root(c.db, chosen.id)
    assert (report.found, report.new) == (1, 1)
    video = _video_of(c, x)
    assert video is not None
    assert video.filename == "x.mp4"
    x.unlink()
    assert scan_root(c.db, chosen.id).offline == 1


def test_a_second_file_of_a_folder_joins_its_root_of_chosen_files(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    folder = tmp_path / "Ailleurs"
    x, y = _file(folder, "x.mp4"), _file(folder, "y.mp4")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(x), 0, 4)])
    _run(c, _bring_in(c, auto_analyze=False).job.id)
    with c.db.write() as session:
        session.get_one(LibraryRoot, _files_roots(c)[0].id).analysis_focus = "les chats"
    resolve.put(timeline("tl-2", "Autre"), [clip(str(y), 0, 4), clip(str(x), 4, 8)])
    _run(c, _bring_in(c, "tl-2").job.id)
    [chosen] = _files_roots(c)
    assert chosen.files == ["x.mp4", "y.mp4"]
    assert (chosen.analysis_focus, chosen.auto_analyze) == ("les chats", False)  # kept


# ---------------------------------------------------------------- links and analysis files
def test_analysis_files_are_written_again_only_when_links_change(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[str] = []

    def write_after(db: Database, video_id: str, events: Any, **kwargs: Any) -> None:
        written.append(video_id)
        really_write(db, video_id, events, **kwargs)

    monkeypatch.setattr(sync_job, "write_after", write_after)
    rushes = tmp_path / "Rushs"
    a, b = _file(rushes, "a.mp4"), _file(rushes, "b.mp4")
    root_id = _root(c, rushes)
    a_id, b_id = register_file(c.db, root_id, a), register_file(c.db, root_id, b)
    _analysed(c, a_id)
    _analysed(c, b_id)

    resolve.put(timeline("tl-1", "Montage"), [clip(str(a), 0, 4), clip(str(b), 4, 8)])
    _run(c, _bring_in(c).job.id)
    assert sorted(written) == sorted([a_id, b_id])  # linked to a timeline: written

    written.clear()
    _run(c, _bring_in(c).job.id)
    assert written == []  # read again, nothing changed: left as they are

    resolve.put(timeline("tl-1", "Montage"), [clip(str(a), 0, 4), clip(str(b), 10, 14)])
    _run(c, _bring_in(c).job.id)
    assert written == [b_id]  # moved on the timeline

    written.clear()
    queue.enqueue(c.db, JobKind.ANALYZE_VIDEO, video_id=a_id)  # it writes the file itself
    resolve.put(timeline("tl-1", "Montage"), [clip(str(b), 0, 4)])
    _run(c, _bring_in(c).job.id)
    assert written == [b_id]  # a left the timeline, but its analysis is on its way

    written.clear()
    [stats] = timeline_bins.list_bins(c)
    removal = timeline_bins.remove_bin(c, stats.bin.id)  # the files are written by a job
    assert written == []
    assert removal is not None
    assert _run(c, removal.id).message == sync_job.GONE
    assert written == [b_id]
    assert '"resolve": []' in (rushes / "b_FR.txt").read_text(encoding="utf-8")

    written.clear()
    update_preferences(c.db, {"sidecar_files": False})
    resolve.put(timeline("tl-1", "Montage"), [clip(str(b), 0, 4)])
    _run(c, _bring_in(c).job.id)
    assert written == []


def test_an_update_stopped_halfway_is_caught_up(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    rushes = tmp_path / "Rushs"
    a = _file(rushes, "a.mp4")
    a_id = register_file(c.db, _root(c, rushes), a)
    _analysed(c, a_id)
    resolve.put(timeline("tl-1", "Montage"), [clip(str(a), 0, 4)])
    first = _bring_in(c)
    queue.request_cancel(c.db, first.job.id)  # « Stop all » before it wrote anything
    assert not (rushes / "a_FR.txt").exists()

    _run(c, _bring_in(c).job.id)  # nothing changed in Resolve since: the file is still due
    assert "tl-1" in (rushes / "a_FR.txt").read_text(encoding="utf-8")
    with c.db.read() as session:
        assert session.get_one(Video, a_id).sidecar_links not in (None, sync_job.NO_LINKS)

    with c.db.write() as session:
        session.get_one(Job, _bring_in(c).job.id).status = JobStatus.RUNNING
    [stats] = timeline_bins.list_bins(c)
    with pytest.raises(ConflictError, match="déjà en cours"):
        timeline_bins.remove_bin(c, stats.bin.id)


def test_the_links_of_a_video(c: AppContainer, resolve: FakeResolve, tmp_path: Path) -> None:
    a = _file(tmp_path / "Rushs", "a.mp4")
    register_file(c.db, _root(c, tmp_path / "Rushs"), a)
    resolve.put(
        timeline("tl-1", "Montage"),
        [clip(str(a), 4, 8, source=(1.0, 5.0)), clip(str(a), 20, 22, enabled=False)],
    )
    resolve.put(timeline("tl-2", "Bande-annonce"), [clip(str(a), 0, 2)])
    _run(c, _bring_in(c, "tl-1").job.id)
    _run(c, _bring_in(c, "tl-2").job.id)
    video = _video_of(c, a)
    assert video is not None
    detail = videos.get_video(c, video.id)
    assert [link.bin_label for link in detail.resolve] == ["Bande-annonce", "Montage"]
    montage = detail.resolve[1]
    assert (montage.project, montage.database) == (PROJECT, resolve.database)
    assert montage.timeline.id == "tl-1"
    assert [(u.record_start_frame, u.enabled) for u in montage.uses] == [
        (90100, True), (90500, False)
    ]  # fmt: skip


# ---------------------------------------------------------------- the worker, the migration
@pytest.mark.anyio
async def test_the_worker_runs_the_update(
    settings: Settings, c: AppContainer, resolve: FakeResolve, tmp_path: Path,
    fake_lmstudio: FakeLmStudio,
) -> None:  # fmt: skip
    x = _file(tmp_path / "Ailleurs", "x.mp4")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(x), 0, 4)])
    job_id = _bring_in(c, auto_analyze=False).job.id
    worker = Worker(
        settings, lmstudio=fake_lmstudio.client(), weather=FakeWeather(), geocoder=FakeGeocoder()
    )
    stop = anyio.Event()

    async def stop_when_done() -> None:
        while True:
            await anyio.sleep(0.2)
            with c.db.read() as session:
                if session.get_one(Job, job_id).status.is_terminal:
                    stop.set()
                    return

    with anyio.fail_after(60):
        async with anyio.create_task_group() as tg:
            tg.start_soon(worker.run, stop)
            tg.start_soon(stop_when_done)
    with c.db.read() as session:
        job = session.get_one(Job, job_id)
    assert job.status == JobStatus.SUCCEEDED
    assert job.message == (
        "1 vidéo liée · 1 ajoutée · 0 introuvable · 0 hors bibliothèque · 0 analyse demandée"
    )
    assert _video_of(c, x) is not None


def test_the_migration_keeps_every_root_a_folder(tmp_path: Path) -> None:
    db_path = tmp_path / "timeline_bins.sqlite3"
    config = alembic_config(db_path)
    command.upgrade(config, "0014")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO library_roots (id, path, path_key, label, recursive, "
                    "exclude_globs, auto_analyze, created_at) VALUES ('r1', 'D:\\cats 2026', "
                    "'d:/cats 2026', 'cats 2026', 1, '[]', 1, '2026-09-28')"
                )
            )
        command.upgrade(config, "head")
        with engine.begin() as conn:
            assert conn.execute(sa.text("SELECT kind, files FROM library_roots")).one() == (
                "folder", None,
            )  # fmt: skip
            for job_id, kind in (("j1", "sync_timeline"), ("j2", "scan_root")):
                conn.execute(
                    sa.text(
                        "INSERT INTO jobs (id, kind, payload, status, priority, progress, "
                        "attempts, max_attempts, cancel_requested, created_at) VALUES "
                        "(:id, :kind, '{}', 'succeeded', 10, 1.0, 1, 3, 0, '2026-09-28')"
                    ),
                    {"id": job_id, "kind": kind},
                )
        command.downgrade(config, "0014")
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT id FROM jobs")).scalars().all() == ["j2"]
            tables = conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE name LIKE 'timeline_bin%'")
            ).all()
            assert tables == []
            columns = [row[1] for row in conn.execute(sa.text("PRAGMA table_info(library_roots)"))]
            assert "kind" not in columns
            assert "files" not in columns
    finally:
        engine.dispose()


def test_items_reference_no_video(c: AppContainer, resolve: FakeResolve, tmp_path: Path) -> None:
    """Forgetting a video's folder leaves the timeline's items (they name files, not videos)."""
    rushes = tmp_path / "Rushs"
    a = _file(rushes, "a.mp4")
    root_id = _root(c, rushes)
    register_file(c.db, root_id, a)
    resolve.put(timeline("tl-1", "Montage"), [clip(str(a), 0, 4)])
    bin_id = _bring_in(c).stats.bin.id
    library.remove_root(c, root_id)
    with c.db.read() as session:
        assert session.get(TimelineBin, bin_id) is not None
        items = session.execute(sa.select(TimelineBinItem.path)).scalars().all()
    assert items == [str(a)]
