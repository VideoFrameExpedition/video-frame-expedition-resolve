"""The database as a whole: export, import and reset (carried out at the next start), and the
restart asked from the interface."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from vfe_vision.api.app import create_app
from vfe_vision.api.server import RESTART_EXIT
from vfe_vision.core.config import Settings
from vfe_vision.db.models import LibraryRoot
from vfe_vision.services.container import AppContainer

HEADERS = {"X-VFE-Client": "tests"}
FRAME = "ab/cd/frame-0001.jpg"
ROOT = Path(__file__).resolve().parents[3]


@contextmanager
def running(settings: Settings) -> Iterator[TestClient]:
    """The application started on these data (an import or a reset prepared is done then)."""
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        yield client


def _computer(tmp_path: Path, settings: Settings, name: str) -> Settings:
    return settings.model_copy(update={"data_dir": tmp_path / name})


def _interval(client: TestClient, seconds: float | None = None) -> float:
    if seconds is not None:
        client.patch(
            "/api/v1/settings/analysis", json={"keyframe_interval_s": seconds}, headers=HEADERS
        ).raise_for_status()
    value: float = client.get("/api/v1/settings/analysis").json()["keyframe_interval_s"]
    return value


def _add_folder(client: TestClient, label: str) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    with container.db.write() as session:
        session.add(LibraryRoot(path=f"D:/{label}", path_key=f"d:/{label}", label=label))


def _folders(client: TestClient) -> list[str]:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    with container.db.read() as session:
        return sorted(session.execute(sa.select(LibraryRoot.label)).scalars())


def _export(client: TestClient, *, images: bool = True) -> bytes:
    prepared = client.post("/api/v1/system/data/export", json={"images": images}, headers=HEADERS)
    prepared.raise_for_status()
    token = prepared.json()["token"]
    download = client.get(f"/api/v1/system/data/export/{token}")
    download.raise_for_status()
    assert download.headers["content-type"] == "application/zip"
    assert "video-frame-expedition-" in download.headers["content-disposition"]
    assert client.get(f"/api/v1/system/data/export/{token}").status_code == 404  # once
    return download.content


def _import(client: TestClient, body: bytes, *, keep_settings: bool = True) -> dict[str, object]:
    response = client.post(
        "/api/v1/system/data/import",
        params={"name": "library.zip", "keep_settings": keep_settings},
        content=body,
        headers={**HEADERS, "Content-Type": "application/octet-stream"},
    )
    data: dict[str, object] = response.json()
    if response.status_code != 200:
        data["status"] = response.status_code
    return data


def test_a_library_moves_to_another_computer(tmp_path: Path, settings: Settings) -> None:
    first = _computer(tmp_path, settings, "first")
    second = _computer(tmp_path, settings, "second")
    with running(first) as client:
        _interval(client, 3.5)
        _add_folder(client, "Rushes")
        frame = first.artifacts_dir / FRAME
        frame.parent.mkdir(parents=True)
        frame.write_bytes(b"\xff\xd8 a frame \xff\xd9")
        archive = _export(client)
    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        assert {"manifest.json", "vfe.sqlite3", f"media/{FRAME}"} <= set(opened.namelist())
        assert json.loads(opened.read("manifest.json"))["images"] is True

    with running(second) as client:
        _interval(client, 5.0)
        prepared = _import(client, archive, keep_settings=False)
        assert prepared["action"] == "import"
        assert (prepared["images"], prepared["settings"], prepared["videos"]) == (True, True, 0)
        assert client.get("/api/v1/system/data").json()["pending"]["source"] == "library.zip"
        assert _folders(client) == []  # nothing changes before the restart

    with running(second) as client:
        state = client.get("/api/v1/system/data").json()
        assert state["pending"] is None
        assert state["last"]["action"] == "import"
        assert state["last"]["error"] is None
        assert state["last"]["backup"].endswith("-before-import.sqlite3")
        assert (second.backups_dir / state["last"]["backup"]).is_file()
        assert _folders(client) == ["Rushes"]
        assert _interval(client) == 3.5  # the settings came with the library
        assert (second.artifacts_dir / FRAME).read_bytes() == b"\xff\xd8 a frame \xff\xd9"


def test_an_import_can_keep_this_computers_settings(tmp_path: Path, settings: Settings) -> None:
    first = _computer(tmp_path, settings, "first")
    second = _computer(tmp_path, settings, "second")
    with running(first) as client:
        _interval(client, 3.5)
        _add_folder(client, "Rushes")
        archive = _export(client, images=False)
    with running(second) as client:
        _interval(client, 5.0)
        _add_folder(client, "Here")
        (second.artifacts_dir / "kept.jpg").write_bytes(b"kept")
        assert _import(client, archive)["settings"] is False  # keep_settings by default
    with running(second) as client:
        assert _folders(client) == ["Rushes"]
        assert _interval(client) == 5.0
        assert (second.artifacts_dir / "kept.jpg").is_file()  # no frames in the archive


def test_a_database_from_the_backups_can_be_imported(tmp_path: Path, settings: Settings) -> None:
    with running(settings) as client:
        _add_folder(client, "Before")
        client.post(
            "/api/v1/system/data/reset", json={"library": True}, headers=HEADERS
        ).raise_for_status()
    with running(settings) as client:
        assert _folders(client) == []
        backup = settings.backups_dir / client.get("/api/v1/system/data").json()["last"]["backup"]
        prepared = _import(client, backup.read_bytes())
        assert (prepared["action"], prepared["images"]) == ("import", False)
    with running(settings) as client:
        assert _folders(client) == ["Before"]


@pytest.mark.parametrize(
    ("library", "erase_settings"), [(True, False), (False, True), (True, True)]
)
def test_reset(settings: Settings, library: bool, erase_settings: bool) -> None:
    with running(settings) as client:
        _interval(client, 3.5)
        _add_folder(client, "Rushes")
        (settings.artifacts_dir / "frame.jpg").write_bytes(b"frame")
        prepared = client.post(
            "/api/v1/system/data/reset",
            json={"library": library, "settings": erase_settings},
            headers=HEADERS,
        ).json()
        assert (prepared["action"], prepared["library"]) == ("reset", library)
    with running(settings) as client:
        last = client.get("/api/v1/system/data").json()["last"]
        assert (last["action"], last["error"]) == ("reset", None)
        assert _folders(client) == ([] if library else ["Rushes"])
        assert (settings.artifacts_dir / "frame.jpg").exists() is not library
        assert _interval(client) == (2.0 if erase_settings else 3.5)  # 2.0: the default


def test_a_reset_needs_something_to_erase(settings: Settings) -> None:
    with running(settings) as client:
        refused = client.post(
            "/api/v1/system/data/reset",
            json={"library": False, "settings": False},
            headers=HEADERS,
        )
        assert refused.status_code == 422


def test_what_is_prepared_can_be_cancelled(settings: Settings) -> None:
    with running(settings) as client:
        _add_folder(client, "Rushes")
        client.post("/api/v1/system/data/reset", json={}, headers=HEADERS).raise_for_status()
        cancelled = client.delete("/api/v1/system/data/pending", headers=HEADERS)
        assert cancelled.status_code == 204
        assert client.get("/api/v1/system/data").json()["pending"] is None
    with running(settings) as client:
        assert _folders(client) == ["Rushes"]
        assert client.get("/api/v1/system/data").json()["last"] is None


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _database(tmp_path: Path, sql: str = "") -> bytes:
    path = tmp_path / "other.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(sql or "CREATE TABLE notes (text TEXT);")
    return path.read_bytes()


def test_what_is_not_a_library_is_refused(tmp_path: Path, settings: Settings) -> None:
    with running(settings) as client:
        library = _export(client, images=False)
        with zipfile.ZipFile(io.BytesIO(library)) as opened:
            database = opened.read("vfe.sqlite3")
        newer = tmp_path / "newer.sqlite3"
        newer.write_bytes(database)
        with closing(sqlite3.connect(newer)) as conn, conn:
            conn.execute("UPDATE alembic_version SET version_num = '9999'")
        cases = {
            b"plain text": "not_a_library",
            _zip({"notes.txt": b"hello"}): "not_a_library",
            _database(tmp_path): "not_a_library",
            _zip({"vfe.sqlite3": database, "media/../../escaped.jpg": b"x"}): "not_a_library",
            newer.read_bytes(): "newer_library",
        }
        for body, code in cases.items():
            refused = _import(client, body)
            assert (refused["status"], refused["code"]) == (422, code)
            assert client.get("/api/v1/system/data").json()["pending"] is None
        assert not (settings.data_dir / "escaped.jpg").exists()
        assert not (settings.data_dir.parent / "escaped.jpg").exists()


def test_writes_need_the_client_header(settings: Settings) -> None:
    with running(settings) as client:
        assert client.post("/api/v1/system/data/export", json={}).status_code == 403
        assert client.post("/api/v1/system/data/import", content=b"x").status_code == 403
        assert client.post("/api/v1/system/data/reset", json={}).status_code == 403
        assert client.post("/api/v1/system/restart").status_code == 403


class _FakeServer:
    def __init__(self) -> None:
        self.restarted = False

    def restart(self) -> None:
        self.restarted = True


def test_restart(settings: Settings) -> None:
    with running(settings) as client:
        assert client.get("/api/v1/system/data").json()["can_restart"] is False
        alone = client.post("/api/v1/system/restart", headers=HEADERS)
        assert alone.status_code == 409  # not started by vfe serve: nothing would start it again
        server = _FakeServer()
        client.app.state.server = server  # type: ignore[attr-defined]
        assert client.get("/api/v1/system/data").json()["can_restart"] is True
        asked = client.post("/api/v1/system/restart", headers=HEADERS)
        assert (asked.status_code, asked.json()) == (202, {"restarting": True})
        assert server.restarted


def test_the_launchers_start_the_application_again() -> None:
    """``vfe serve`` exits with 75 for a restart: run.bat and run.command start it again."""
    assert RESTART_EXIT == 75
    bat = (ROOT / "run.bat").read_bytes().decode("utf-8")
    assert "if errorlevel 76 goto :failed\r\nif errorlevel 75 goto :serve\r\n" in bat
    command = (ROOT / "run.command").read_bytes().decode("utf-8")
    assert '[ "$status" -eq 75 ] || break' in command
