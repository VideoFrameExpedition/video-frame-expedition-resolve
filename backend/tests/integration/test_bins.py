"""Folders as bins: the tree, the videos of one bin, automatic update, the picker."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from vfe_vision.api.app import create_app
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings
from vfe_vision.db.models import Job, LibraryRoot, Video
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobKind, JobStatus
from vfe_vision.jobs import queue
from vfe_vision.services import library, videos
from vfe_vision.services.container import AppContainer

HEADERS = {"X-VFE-Client": "test"}
PATHS = [
    "RIZ.mp4",
    "buzz.mp4",
    "with data/a.mp4",
    "with data/b.mp4",
    "with data/2020/c.mp4",
    "apv/d.mp4",
    "a_b/e.mp4",  # "_" is a LIKE wildcard: "axb/" must not match
    "axb/f.mp4",
]


@pytest.fixture
def container(settings: Settings, db: Database) -> AppContainer:
    return AppContainer.create(settings)


def _library(c: AppContainer, tmp_path: Path, *, auto: bool = True) -> str:
    with c.db.write() as session:
        root = LibraryRoot(
            path=str(tmp_path), path_key=str(tmp_path).lower(), label="Sample IA",
            auto_analyze=auto,
        )  # fmt: skip
        session.add(root)
        session.flush()
        for rel in PATHS:
            session.add(
                Video(
                    root_id=root.id, path=str(tmp_path / rel), path_key=rel, rel_path=rel,
                    filename=rel.rsplit("/", 1)[-1], size_bytes=1, mtime=0.0, fingerprint=rel,
                )
            )  # fmt: skip
        return root.id


def test_the_folders_become_bins(container: AppContainer, tmp_path: Path) -> None:
    _library(container, tmp_path)
    [item] = library.folder_tree(container)
    tree = item.tree
    assert (tree.name, tree.path, tree.count, tree.total) == ("Sample IA", "", 2, 8)
    assert [child.name for child in tree.children] == ["a_b", "apv", "axb", "with data"]
    data = tree.children[3]
    assert (data.path, data.count, data.total) == ("with data", 2, 3)
    assert [(c.name, c.path, c.count) for c in data.children] == [("2020", "with data/2020", 1)]


@pytest.mark.parametrize(
    ("folder", "names"),
    [
        ("", ["RIZ.mp4", "buzz.mp4"]),  # the root's own videos only
        ("with data", ["a.mp4", "b.mp4"]),  # not those of with data/2020
        ("with data/2020", ["c.mp4"]),
        ("a_b", ["e.mp4"]),
    ],
)
def test_a_bin_shows_its_own_videos(
    container: AppContainer, tmp_path: Path, folder: str, names: list[str]
) -> None:
    root_id = _library(container, tmp_path)
    page = videos.list_videos(
        container, videos.VideoFilters(root_id=root_id, folder=folder, sort="name")
    )
    assert [v.filename for v in page.items] == names


def test_folders_with_automatic_update_are_looked_at(
    container: AppContainer, tmp_path: Path
) -> None:
    root_id = _library(container, tmp_path)
    assert queue.auto_scan_roots(container.db) == [root_id]
    queue.enqueue(container.db, JobKind.SCAN_ROOT, root_id=root_id)
    assert queue.auto_scan_roots(container.db) == []  # a scan is already on its way
    with container.db.write() as session:
        session.execute(sa.update(Job).values(status=JobStatus.SUCCEEDED))
        session.execute(sa.update(LibraryRoot).values(auto_analyze=False))
    assert queue.auto_scan_roots(container.db) == []  # switched off


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(
        app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)
    ) as test_client:
        yield test_client


def test_the_api_serves_bins_and_the_picker(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vfe_vision.api.routers import system

    assert client.get("/api/v1/library/folders").json() == []
    refused = client.get("/api/v1/videos", params={"folder": "with data"})
    assert refused.status_code == 422  # a folder needs its root

    picked: list[str] = []

    def pick(title: str, *, cancel: CancelToken) -> Path:
        picked.append(title)
        assert not cancel.cancelled
        return settings.data_dir

    monkeypatch.setattr(system, "pick_folder", pick)
    answer = client.post("/api/v1/system/pick-folder", headers=HEADERS).json()
    assert answer == {"path": str(settings.data_dir)}
    assert picked == ["Choisir un dossier de vidéos"]
    monkeypatch.setattr(system, "pick_folder", lambda title, **_: None)
    assert client.post("/api/v1/system/pick-folder", headers=HEADERS).json() == {"path": None}

    other = create_app(settings, start_worker=False)
    with TestClient(other, base_url="http://127.0.0.1:8765") as remote:  # not this computer
        denied = remote.post("/api/v1/system/pick-folder", headers=HEADERS)
    assert denied.status_code == 403


@pytest.mark.anyio
async def test_closing_the_page_closes_the_folder_dialog() -> None:
    from vfe_vision.api.routers import system

    class Gone:
        async def is_disconnected(self) -> bool:
            return True

    token = CancelToken()
    await system._close_when_gone(Gone(), token)  # type: ignore[arg-type]
    assert token.cancelled


def test_the_folder_dialog_dies_with_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    from vfe_vision.adapters import folder_picker
    from vfe_vision.core.procs import ProcessResult

    seen: dict[str, Any] = {}

    def run(args: list[str], **kwargs: Any) -> ProcessResult:
        seen.update(kwargs)
        return ProcessResult(0, b"C:\\Videos\\Rushes", b"", 0.1)

    monkeypatch.setattr(folder_picker, "run_process", run)
    token = CancelToken()
    assert folder_picker.pick_folder("t", cancel=token) == Path("C:/Videos/Rushes")
    assert seen["cancel"] is token
    assert seen["job"] is folder_picker._app_job()  # the app's job: killed with it
