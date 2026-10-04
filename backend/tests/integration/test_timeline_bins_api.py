"""The REST API of the Resolve timelines: the open project, the preview, the import,
the timeline bins, the videos of one, the links on a video; with a fake Resolve."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes.resolve_source import PROJECT, START, FakeResolve, clip, timeline
from vfe_vision.adapters.resolve.reader import NOT_RUNNING
from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import ResolveUnavailableError
from vfe_vision.db.models import Job
from vfe_vision.domain.enums import JobStatus
from vfe_vision.jobs import queue
from vfe_vision.jobs.timeline_bins import sync_timeline
from vfe_vision.services.container import AppContainer

HEADERS = {"X-VFE-Client": "tests"}


class _Sink:
    def emit(self, type_: str, **_fields: Any) -> None:
        pass


@pytest.fixture
def resolve() -> FakeResolve:
    return FakeResolve()


@pytest.fixture
def client(settings: Settings, resolve: FakeResolve) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(
        app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)
    ) as test_client:
        _container(test_client).resolve = resolve
        yield test_client


def _container(client: TestClient) -> AppContainer:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    return container


def _run(client: TestClient, job_id: str) -> None:
    c = _container(client)
    with c.db.write() as session:
        job = session.get_one(Job, job_id)
        job.status = JobStatus.RUNNING
        payload = dict(job.payload)
    report = sync_timeline(c.db, payload, data_dir=c.settings.data_dir, events=_Sink())
    queue.finish(c.db, job_id, JobStatus.SUCCEEDED, message=report.message)


def _file(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(name.encode() * 64)
    return path


def test_a_timeline_through_the_api(
    client: TestClient, resolve: FakeResolve, tmp_path: Path
) -> None:
    x = _file(tmp_path / "Ailleurs", "x.mp4")
    resolve.put(timeline("tl-z", "Zèbre", current=True), [clip(str(x), 4, 8, source=(1, 5))])
    resolve.put(timeline("tl-a", "alpha"), [clip(str(x), 0, 2)])

    project = client.get("/api/v1/resolve/project")
    assert project.status_code == 200
    body = project.json()
    assert (body["project"], body["database"]) == (
        {"id": PROJECT.id, "name": "cats 2026"}, {"type": "Disk", "name": "Local Database"}
    )  # fmt: skip
    assert body["current_timeline_id"] == "tl-z"
    assert [t["name"] for t in body["timelines"]] == ["Zèbre", "alpha"]  # the current first
    first = body["timelines"][0]
    assert (first["is_current"], first["video_clips"], first["duration_s"]) == (True, 1, 100.0)
    assert (first["bin_id"], first["synced_at"], first["changed_since_sync"]) == (None, None, None)

    preview = client.get(
        "/api/v1/resolve/timelines/tl-z/preview", params={"project_id": PROJECT.id}
    )
    assert preview.status_code == 200
    seen = preview.json()
    assert (seen["files"], seen["new_folder"], seen["to_analyze"]) == (1, 1, 1)
    assert seen["new_folders"] == [str(tmp_path / "Ailleurs")]
    assert seen["skipped"]["graphics"] == 0
    assert seen["timeline"]["start_timecode"] == "01:00:00:00"
    assert client.get("/api/v1/resolve/timelines/tl%20z/preview").status_code == 422

    brought = client.post(
        "/api/v1/resolve/timelines/import",
        json={"project_id": PROJECT.id, "timeline_id": "tl-z", "snapshot_id": seen["snapshot_id"]},
        headers=HEADERS,
    )
    assert brought.status_code == 201
    answer = brought.json()
    assert answer["created"] is True
    assert answer["job"]["kind"] == "sync_timeline"
    bin_id = answer["bin"]["id"]
    assert answer["bin"]["states"]["adding"] == 1
    assert answer["preview"]["bin_id"] == bin_id
    assert resolve.timeline_reads == ["tl-z"]
    _run(client, answer["job"]["id"])


def _brought_in(client: TestClient, resolve: FakeResolve, x: Path) -> str:
    resolve.put(timeline("tl-z", "Zèbre"), [clip(str(x), 4, 8, source=(1, 5))])
    answer = client.post(
        "/api/v1/resolve/timelines/import",
        json={"project_id": PROJECT.id, "timeline_id": "tl-z"},
        headers=HEADERS,
    ).json()
    _run(client, answer["job"]["id"])
    bin_id: str = answer["bin"]["id"]
    return bin_id


def test_a_timeline_bin_through_the_api(
    client: TestClient, resolve: FakeResolve, tmp_path: Path
) -> None:
    x = _file(tmp_path / "Ailleurs", "x.mp4")
    bin_id = _brought_in(client, resolve, x)
    [listed] = client.get("/api/v1/library/timelines").json()
    assert (listed["label"], listed["items"], listed["videos"]) == ("Zèbre", 1, 1)
    assert listed["states"]["in_library"] == 1
    assert listed["timeline"] == {
        "id": "tl-z", "name": "Zèbre", "fps": 25.0, "drop_frame": False,
        "start_timecode": "01:00:00:00", "duration_s": 100.0,
    }  # fmt: skip
    assert listed["sync_job"]["status"] == "succeeded"
    assert listed["report"]["skipped"]["elsewhere"] == 0
    [item] = client.get(f"/api/v1/library/timelines/{bin_id}/items").json()
    assert (item["state"], item["path"], item["position"]) == ("in_library", str(x), 0)
    video_id = item["video_id"]

    page = client.get("/api/v1/videos", params={"timeline_bin_id": bin_id}).json()
    assert [v["id"] for v in page["items"]] == [video_id]
    assert page["items"][0]["timeline_bins"] == [bin_id]
    both = client.get("/api/v1/videos", params={"timeline_bin_id": bin_id, "root_id": "r"})
    assert both.status_code == 422

    detail = client.get(f"/api/v1/videos/{video_id}").json()
    assert detail["timeline_bins"] == [bin_id]
    [link] = detail["resolve"]
    assert (link["bin_id"], link["bin_label"], link["project"]["id"]) == (
        bin_id,
        "Zèbre",
        PROJECT.id,
    )
    assert link["timeline"]["id"] == "tl-z"
    assert link["uses"] == [
        {
            "track_type": "video", "track": 1, "track_name": None, "track_enabled": True,
            "enabled": True, "nested_in": None, "record_in_tc": "01:00:04:00",
            "record_out_tc": "01:00:08:00", "record_in_s": 4.0, "record_out_s": 8.0,
            "source_in_s": 1.0, "source_out_s": 5.0, "media_pool_item_id": "pool-x-mp4",
            "timeline_item_id": f"item-video-{START + 100}",
        }
    ]  # fmt: skip

    roots = client.get("/api/v1/library/roots").json()
    assert [(r["kind"], r["files_count"]) for r in roots] == [("files", 1)]
    assert [f["kind"] for f in client.get("/api/v1/library/folders").json()] == ["files"]


def test_changing_a_timeline_bin_through_the_api(
    client: TestClient, resolve: FakeResolve, tmp_path: Path
) -> None:
    x = _file(tmp_path / "Ailleurs", "x.mp4")
    bin_id = _brought_in(client, resolve, x)
    [item] = client.get(f"/api/v1/library/timelines/{bin_id}/items").json()
    video_id = item["video_id"]
    info, clips = resolve.timelines["tl-z"]
    resolve.put(replace(info, end_frame=info.end_frame + 250), clips)
    changed = client.get("/api/v1/resolve/project").json()["timelines"][0]
    assert (changed["bin_id"], changed["bin_label"], changed["changed_since_sync"]) == (
        bin_id, "Zèbre", True,
    )  # fmt: skip

    renamed = client.patch(
        f"/api/v1/library/timelines/{bin_id}", json={"label": "Chats"}, headers=HEADERS
    )
    assert renamed.status_code == 200
    assert renamed.json()["label"] == "Chats"
    empty = client.patch(f"/api/v1/library/timelines/{bin_id}", json={"label": ""}, headers=HEADERS)
    assert empty.status_code == 422

    synced = client.post(f"/api/v1/library/timelines/{bin_id}/sync", headers=HEADERS)
    assert synced.status_code == 202
    assert (synced.json()["created"], synced.json()["bin"]["label"]) == (False, "Chats")

    assert client.delete(f"/api/v1/library/timelines/{bin_id}", headers=HEADERS).status_code == 204
    assert client.get("/api/v1/library/timelines").json() == []
    assert client.get(f"/api/v1/library/timelines/{bin_id}/items").status_code == 404
    assert client.get(f"/api/v1/videos/{video_id}").json()["resolve"] == []


def test_resolve_errors_through_the_api(
    client: TestClient, resolve: FakeResolve, tmp_path: Path
) -> None:
    x = _file(tmp_path / "Ailleurs", "x.mp4")
    resolve.put(timeline("tl-1", "Montage"), [clip(str(x), 0, 4)])
    changed = client.post(
        "/api/v1/resolve/timelines/import",
        json={"project_id": "prj-before", "timeline_id": "tl-1"},
        headers=HEADERS,
    )
    assert changed.status_code == 409
    assert changed.json()["project"] == "cats 2026"
    gone = client.get("/api/v1/resolve/timelines/tl-9/preview")
    assert gone.status_code == 404

    resolve.error = ResolveUnavailableError(NOT_RUNNING, reason="not_running")
    down = client.get("/api/v1/resolve/project")
    assert down.status_code == 503
    problem = down.json()
    assert (problem["code"], problem["reason"], problem["detail"]) == (
        "resolve_unavailable", "not_running", NOT_RUNNING,
    )  # fmt: skip
    unknown = client.post("/api/v1/library/timelines/unknown/sync", headers=HEADERS)
    assert unknown.status_code == 404

    refused = client.post(
        "/api/v1/resolve/timelines/import",
        json={"project_id": PROJECT.id, "timeline_id": "tl-1", "unexpected": 1},
        headers=HEADERS,
    )
    assert refused.status_code == 422
    no_header = client.post(
        "/api/v1/resolve/timelines/import", json={"project_id": PROJECT.id, "timeline_id": "tl-1"}
    )
    assert no_header.status_code == 403
