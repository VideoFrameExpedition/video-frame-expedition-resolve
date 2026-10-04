"""The Resolve timelines through the MCP server: list_resolve_timelines,
import_resolve_timeline (and the « add folders » rule), the timeline_id filters, the link on the
manifests, the analysis file and the exports, match_clips by media pool id."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from mcp import Client

from tests.fakes.resolve_source import PROJECT, FakeResolve, clip, timeline
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import ResolveUnavailableError
from vfe_vision.core.paths import path_key
from vfe_vision.db.models import Job, LibraryRoot, StageRun, Video
from vfe_vision.db.preferences import update_preferences
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobStatus, StageStatus
from vfe_vision.jobs import queue
from vfe_vision.jobs import timeline_bins as sync_job
from vfe_vision.jobs.scan import register_file
from vfe_vision.mcp.server import build_mcp_server
from vfe_vision.pipeline.sidecar.writer import build_document
from vfe_vision.services import exports, timeline_bins
from vfe_vision.services import resolve as resolve_markers
from vfe_vision.services.container import AppContainer

pytestmark = pytest.mark.anyio


class _Sink:
    def emit(self, type_: str, **_kwargs: Any) -> None:
        pass


@pytest.fixture
def resolve() -> FakeResolve:
    return FakeResolve()


@pytest.fixture
def c(settings: Settings, db: Database, resolve: FakeResolve) -> Iterator[AppContainer]:
    container = AppContainer.create(settings)
    container.resolve = resolve
    yield container
    container.db.dispose()


def _file(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(name.encode() * 64)
    return path


def _root(c: AppContainer, folder: Path) -> str:
    with c.db.write() as session:
        root = LibraryRoot(path=str(folder), path_key=path_key(folder), label=folder.name)
        session.add(root)
        session.flush()
        return root.id


def _run_sync(c: AppContainer, job_id: str) -> None:
    """The update job, as the worker runs it."""
    with c.db.write() as session:
        job = session.get_one(Job, job_id)
        job.status = JobStatus.RUNNING
        payload = dict(job.payload)
    report = sync_job.sync_timeline(
        c.db, payload, data_dir=c.settings.data_dir, events=_Sink(), job_id=job_id
    )
    queue.finish(c.db, job_id, JobStatus.SUCCEEDED, message=report.message)


def _video(c: AppContainer, path: Path) -> Video:
    with c.db.read() as session:
        return session.execute(
            sa.select(Video).where(Video.path_key == path_key(path))
        ).scalar_one()


@dataclass(frozen=True)
class Edit:
    first: Path  # in a folder of the library, registered
    second: Path  # in that folder, not registered yet
    alone: Path  # in no folder of the library


@pytest.fixture
def edit(c: AppContainer, resolve: FakeResolve, tmp_path: Path) -> Edit:
    rushes, elsewhere = tmp_path / "Rushs", tmp_path / "Ailleurs"
    first, second = _file(rushes, "b.mp4"), _file(rushes, "a.mp4")
    alone = _file(elsewhere, "x.mp4")
    register_file(c.db, _root(c, rushes), first)
    resolve.put(
        timeline("tl-1", "Montage", current=True),
        [
            clip(str(first), 0, 4, source=(2.0, 6.0)),
            clip(str(second), 4, 8),
            clip(str(alone), 8, 9),
        ],
    )
    resolve.put(timeline("tl-2", "Brouillon"), [clip(str(first), 0, 2)])
    return Edit(first, second, alone)


async def test_timelines_listed_and_added(c: AppContainer, edit: Edit) -> None:
    alone = edit.alone
    async with Client(build_mcp_server(lambda: c)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        for name in ("list_resolve_timelines", "import_resolve_timeline"):
            assert tools[name].output_schema is not None, name
            assert tools[name].description

        listed = await client.call_tool("list_resolve_timelines", {})
        project = listed.structured_content
        assert project is not None
        assert project["project"] == {"id": PROJECT.id, "name": "cats 2026"}
        assert [t["timeline_id"] for t in project["timelines"]] == ["tl-1", "tl-2"]  # current 1st
        assert not project["timelines"][0]["in_library"]
        assert "« Montage » (actuelle) [timeline_id tl-1]" in listed.content[0].text  # type: ignore[union-attr]

        # Not added yet: the filters say how to add it.
        missing = await client.call_tool("list_watched", {"timeline_id": "tl-1"})
        assert missing.is_error
        assert "import_resolve_timeline" in missing.content[0].text  # type: ignore[union-attr]

        # Claude may not add folders (the default): the file in no folder stays outside.
        imported = await client.call_tool("import_resolve_timeline", {"timeline_id": "tl-1"})
        result = imported.structured_content
        assert result is not None
        assert result["created"]
        assert (result["files"], result["in_library"], result["in_folder"]) == (3, 1, 1)
        assert (result["new_folder"], result["new_folders_allowed"]) == (1, False)
        assert result["analyze"]
        assert "Importer depuis Resolve" in result["note"]
        _run_sync(c, result["job_id"])
        with pytest.raises(sa.exc.NoResultFound):
            _video(c, alone)

        # Allowed: updated in place (same bin), the lone file joins on its own.
        update_preferences(c.db, {"mcp_add_folders": True})
        again = await client.call_tool(
            "import_resolve_timeline", {"timeline_id": "tl-1", "analyze": False}
        )
        updated = again.structured_content
        assert updated is not None
        assert not updated["created"]
        assert updated["bin_id"] == result["bin_id"]
        assert (updated["new_folders_allowed"], updated["analyze"], updated["to_analyze"]) == (
            True, False, 0,
        )  # fmt: skip
        assert "note" not in updated
        _run_sync(c, updated["job_id"])
        assert _video(c, alone).filename == "x.mp4"

        listed = await client.call_tool("list_resolve_timelines", {})
        assert listed.structured_content is not None
        entry = listed.structured_content["timelines"][0]
        assert (entry["in_library"], entry["bin_id"], entry["videos"]) == (
            True, result["bin_id"], 3,
        )  # fmt: skip
        assert entry["states"]["in_library"] == 3
        assert entry["changed_since_sync"] is False


async def test_links_of_the_timeline_videos(c: AppContainer, edit: Edit, tmp_path: Path) -> None:
    brought = timeline_bins.import_timeline(c, project_id=PROJECT.id, timeline_id="tl-1")
    _run_sync(c, brought.job.id)
    async with Client(build_mcp_server(lambda: c)) as client:
        # The timeline's videos in its order, each with its link.
        watched = await client.call_tool("list_watched", {"timeline_id": "tl-1"})
        assert watched.structured_content is not None
        rows = watched.structured_content["result"]
        assert [row["filename"] for row in rows] == ["b.mp4", "a.mp4", "x.mp4"]
        [ref] = rows[0]["resolve"]
        assert (ref["project_id"], ref["timeline_id"], ref["timeline"]) == (
            PROJECT.id, "tl-1", "Montage",
        )  # fmt: skip
        assert ref["media_pool_item_id"] == "pool-b-mp4"
        everything = await client.call_tool("list_watched", {})
        assert everything.structured_content is not None
        assert len(everything.structured_content["result"]) == 3

        # The manifest says where the video is used.
        video = _video(c, edit.first)
        manifest = await client.call_tool("get_video", {"video_id": video.id})
        text = manifest.content[0].text  # type: ignore[union-attr]
        assert "DaVinci Resolve" in text
        assert "tl-1" in text
        assert "01:00:00:00" in text  # where it sits in the timeline

        # The media pool id finds the video although Resolve's path differs.
        moved = str(tmp_path / "Autre disque" / "b.mp4")
        matched = await client.call_tool(
            "match_clips", {"items": [{"file_path": moved, "clip_uid": "pool-b-mp4"}]}
        )
        assert matched.structured_content is not None
        [match] = matched.structured_content["clips"]
        assert (match["method"], match["video_id"]) == ("resolve_id", video.id)
        assert match["resolve"][0]["timeline_id"] == "tl-1"
        assert matched.structured_content["timelines"][0]["timeline"] == "Montage"

    # The analysis file and the exports carry the link too.
    with c.db.write() as session:
        session.add(
            StageRun(video_id=video.id, stage="probe", stage_version=1, cache_key="k",
                     status=StageStatus.SUCCEEDED)
        )  # fmt: skip
    with c.db.read() as session:
        document = build_document(session, session.get_one(Video, video.id))
    assert document is not None
    [link] = document["resolve"]
    assert link["timeline"]["id"] == "tl-1"
    assert link["uses"][0]["source_start_s"] == pytest.approx(2.0)
    assert "note" in link
    table = exports.library_csv(c, [video.id]).data.decode("utf-8-sig")
    header, row = table.splitlines()[:2]
    assert header.endswith("timelines Resolve")
    assert row.endswith("cats 2026 › Montage")


async def test_resolve_out_of_reach_is_a_tool_error(c: AppContainer, resolve: FakeResolve) -> None:
    resolve.error = ResolveUnavailableError(
        "DaVinci Resolve n'est pas lancé.", reason="not_running"
    )
    async with Client(build_mcp_server(lambda: c)) as client:
        listed = await client.call_tool("list_resolve_timelines", {})
        imported = await client.call_tool("import_resolve_timeline", {})
    assert listed.is_error
    assert imported.is_error
    assert "pas lancé" in listed.content[0].text  # type: ignore[union-attr]


async def test_resolve_on_another_computer_sees_its_own_paths(
    c: AppContainer, resolve: FakeResolve, tmp_path: Path
) -> None:
    rushes = tmp_path / "Rushs"
    a = _file(rushes, "a.mp4")
    video_id = register_file(c.db, _root(c, rushes), a)
    resolve.put(timeline("tl-1", "Montage", current=True), [clip("/Volumes/Rushs/a.mp4", 0, 4)])
    alone = timeline_bins.preview(c, project_id=PROJECT.id, timeline_id="tl-1")
    assert (alone.files, alone.skipped.elsewhere) == (0, 1)  # a Mac path: not this computer's

    update_preferences(
        c.db, {"resolve_folders": [{"here": str(rushes), "there": "/Volumes/Rushs"}]}
    )
    seen = timeline_bins.preview(c, project_id=PROJECT.id, timeline_id="tl-1")
    assert (seen.files, seen.in_library, seen.skipped.elsewhere) == (1, 1, 0)
    _run_sync(c, timeline_bins.import_timeline(c, project_id=PROJECT.id, timeline_id="tl-1").job.id)

    async with Client(build_mcp_server(lambda: c)) as client:
        watched = await client.call_tool("list_watched", {"timeline_id": "tl-1"})
        assert watched.structured_content is not None
        [row] = watched.structured_content["result"]
        assert row["resolve_path"] == "/Volumes/Rushs/a.mp4"
        manifest = await client.call_tool("get_video", {"video_id": video_id})
        text = manifest.content[0].text  # type: ignore[union-attr]
        assert "fichier vu par DaVinci Resolve : /Volumes/Rushs/a.mp4" in text
        matched = await client.call_tool("match_clips", {"items": ["/Volumes/Rushs/a.mp4"]})
        assert matched.structured_content is not None
        [found] = matched.structured_content["clips"]
        assert (found["method"], found["video_id"]) == ("path", video_id)

    with c.db.write() as session:  # an analysis: the markers script gets the video
        session.add(
            StageRun(video_id=video_id, stage="probe", stage_version=1, cache_key="k",
                     status=StageStatus.SUCCEEDED)
        )  # fmt: skip
    payload = resolve_markers.build_payload(c, [video_id], resolve_markers.MarkerOptions())
    assert payload.data["clips"][0]["path"] == "/Volumes/Rushs/a.mp4"
