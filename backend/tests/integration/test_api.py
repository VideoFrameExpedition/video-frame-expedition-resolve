"""REST API with an in-process app (no worker): security, library, videos, media, streaming."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings, platform_name
from vfe_vision.jobs.scan import scan_root
from vfe_vision.services.container import AppContainer

HEADERS = {"X-VFE-Client": "tests"}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


def _container(client: TestClient) -> AppContainer:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    return container


def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["platform"] == platform_name()  # the interface's texts follow it


class TestSecurity:
    def test_write_requires_client_header(self, client: TestClient) -> None:
        response = client.post("/api/v1/library/roots", json={"path": "C:/x"})
        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["code"] == "missing_client_header"

    def test_foreign_origin_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/library/roots",
            json={"path": "C:/x"},
            headers={**HEADERS, "Origin": "https://evil.example"},
        )
        assert response.status_code == 403

    def test_foreign_host_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/v1/system/health", headers={"Host": "evil.example"})
        assert response.status_code == 400

    def test_media_traversal_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/v1/media/..%2F..%2Fvfe.sqlite3")
        assert response.status_code in {403, 404}

    def test_media_network_path_is_never_looked_at(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        looked = _record_resolve(monkeypatch)
        response = client.get("/api/v1/media/%5C%5Cattacker.invalid%5Cshare%5Cx.jpg")
        assert response.status_code == 403
        assert not [path for path in looked if "attacker" in path]

    def test_no_page_can_be_framed(self, client: TestClient) -> None:
        response = client.get("/api/v1/system/health")
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
        refused = client.get("/api/v1/system/health", headers={"Host": "evil.example"})
        assert refused.headers["x-frame-options"] == "DENY"

    def test_unknown_api_route_is_a_problem_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/nope")
        assert response.status_code == 404
        assert response.json()["status"] == 404


def _record_resolve(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every path the application resolves (resolving a network path logs in to its host)."""
    looked: list[str] = []
    real = Path.resolve

    def resolve(self: Path, strict: bool = False) -> Path:
        looked.append(str(self))
        return real(self, strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    return looked


class TestWebInterface:
    """The built interface's files (a stand-in folder): its pages, never a path outside it."""

    @pytest.fixture
    def page_client(
        self, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[TestClient]:
        dist = (tmp_path / "dist").resolve()
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><title>index</title>", encoding="utf-8")
        (dist / "logo.svg").write_text("<svg/>", encoding="utf-8")
        monkeypatch.setattr("vfe_vision.api.app.WEB_DIST", dist)
        app = create_app(settings, start_worker=False)
        with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
            yield test_client

    def test_files_and_routes(self, page_client: TestClient) -> None:
        assert page_client.get("/logo.svg").text == "<svg/>"
        page = page_client.get("/library/some-folder")
        assert "<title>index</title>" in page.text
        assert page.headers["x-frame-options"] == "DENY"

    @pytest.mark.parametrize(
        "path",
        [
            "/%5C%5Cattacker.invalid%5Cshare%5Cx",
            "/C:%5CWindows%5Cwin.ini",
            "/..%2F..%2Fvfe.sqlite3",
        ],
    )
    def test_outside_paths_get_the_page_without_being_looked_at(
        self, page_client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        looked = _record_resolve(monkeypatch)
        response = page_client.get(path)
        assert response.status_code == 200
        assert "<title>index</title>" in response.text
        assert not [p for p in looked if "attacker" in p or "win.ini" in p or "sqlite" in p]


def test_library_and_videos_flow(client: TestClient, library_folder: Path) -> None:
    for file in library_folder.iterdir():
        past = time.time() - 60
        os.utime(file, (past, past))

    created = client.post(
        "/api/v1/library/roots",
        json={"path": str(library_folder), "label": "Rushs"},
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["scan_job"]["kind"] == "scan_root"

    overlapping = client.post(
        "/api/v1/library/roots", json={"path": str(library_folder / "sub")}, headers=HEADERS
    )
    assert overlapping.status_code in {409, 422}

    scan_root(_container(client).db, body["root"]["id"])  # what the worker would do
    page = client.get("/api/v1/videos").json()
    assert page["total"] == 1
    video = page["items"][0]
    assert video["filename"] == "plan séquence.mp4"
    assert video["status"] == "queued"

    detail = client.get(f"/api/v1/videos/{video['id']}").json()
    assert detail["root_label"] == "Rushs"
    assert client.get(f"/api/v1/videos/{video['id']}/keyframes").json() == []
    # Media endpoints answer with empty shapes before the analysis has run.
    for sub, empty in (
        ("shots", []),
        ("track", []),
        ("signals", {"visual": None, "audio": None, "audio_stats": None}),
        ("metadata", {"exif": None, "probe": None, "raw_tags": None}),
    ):
        assert client.get(f"/api/v1/videos/{video['id']}/{sub}").json() == empty
    ctx = client.get(f"/api/v1/videos/{video['id']}/context").json()
    assert (ctx["place"], ctx["weather"], ctx["sun"], ctx["notes"]) == (None, None, None, {})
    assert ctx["online_services"] is True
    assert client.get("/api/v1/videos/inconnue/context").status_code == 404
    assert client.get("/api/v1/videos/inconnue/shots").status_code == 404
    assert client.get("/api/v1/videos/inconnue/metadata").status_code == 404
    assert client.get("/api/v1/videos/inconnue/track").status_code == 404

    root_id = body["root"]["id"]
    for bad in ("Europe/Paris/", "Nowhere/City", "../etc"):
        rejected = client.patch(
            f"/api/v1/library/roots/{root_id}", json={"default_timezone": bad}, headers=HEADERS
        )
        assert rejected.status_code == 422, bad
    cleared = client.patch(
        f"/api/v1/library/roots/{root_id}", json={"default_timezone": " "}, headers=HEADERS
    )
    assert cleared.status_code == 200
    assert cleared.json()["default_timezone"] is None

    patched = client.patch(
        f"/api/v1/videos/{video['id']}", json={"rating": 4, "favorite": True}, headers=HEADERS
    )
    assert patched.json()["rating"] == 4

    ranged = client.get(f"/api/v1/videos/{video['id']}/stream", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206
    assert len(ranged.content) == 100

    stale_client = client.post(
        f"/api/v1/videos/{video['id']}/analyze", json={"force": True}, headers=HEADERS
    )
    assert stale_client.status_code == 422  # an unknown option is refused, never ignored

    job = client.post(
        f"/api/v1/videos/{video['id']}/analyze",
        json={"mode": "complete", "focus": "la mire"},
        headers=HEADERS,
    ).json()
    # A new focus redoes the stages that use it, and only them.
    assert job["payload"] == {
        "mode": "complete", "force": False, "refresh": ["vision_frames"], "focus": "la mire"
    }  # fmt: skip
    merged = client.post(
        f"/api/v1/videos/{video['id']}/analyze",
        json={"mode": "full", "stages": ["technical"]},
        headers=HEADERS,
    ).json()
    assert merged["id"] == job["id"]  # merged into the queued request
    assert merged["payload"] == {
        "mode": "full", "force": ["technical"], "refresh": ["vision_frames"], "focus": "la mire"
    }  # fmt: skip
    cancelled = client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=HEADERS).json()
    assert cancelled["status"] == "cancelled"

    detail = client.get(f"/api/v1/videos/{video['id']}").json()
    assert "probe" in detail["missing_stages"]  # never analysed: everything is to do
    assert detail["outdated_stages"] == []

    root_id = video["root_id"]
    queued = client.post(
        f"/api/v1/library/roots/{root_id}/analyze", json={"mode": "complete"}, headers=HEADERS
    ).json()
    assert queued == {"queued": 1}

    batch = client.post(
        "/api/v1/videos/analyze",
        json={"video_ids": [video["id"]], "stages": ["place"], "mode": "full"},
        headers=HEADERS,
    )
    assert batch.status_code == 202
    assert batch.json() == {"queued": 1, "up_to_date": 0, "offline": 0, "unknown": []}
    for body in (
        {"video_ids": []},  # nothing to analyse
        {"video_ids": [video["id"]], "stages": []},  # no stage is not every stage
    ):
        refused = client.post("/api/v1/videos/analyze", json=body, headers=HEADERS)
        assert refused.status_code == 422
    unknown = client.post(
        "/api/v1/videos/analyze", json={"video_ids": ["inconnue"]}, headers=HEADERS
    )
    assert unknown.json() == {"queued": 0, "up_to_date": 0, "offline": 0, "unknown": ["inconnue"]}
    bad_stage = client.post(
        "/api/v1/videos/analyze",
        json={"video_ids": [video["id"]], "stages": ["montage"]},
        headers=HEADERS,
    )
    assert bad_stage.status_code == 422


def test_preferences_roundtrip(client: TestClient) -> None:
    assert client.get("/api/v1/settings/analysis").json()["language"] == "fr"
    updated = client.patch(
        "/api/v1/settings/analysis", json={"keyframe_interval_s": 3.5}, headers=HEADERS
    ).json()
    assert updated["keyframe_interval_s"] == 3.5
    invalid = client.patch(
        "/api/v1/settings/analysis", json={"language": "français"}, headers=HEADERS
    )
    assert invalid.status_code == 422


def test_stage_catalogue(client: TestClient) -> None:
    stages = client.get("/api/v1/system/stages").json()
    assert stages[0] == {
        "name": "probe", "family": "file", "requires": [], "after": [], "resource": "cpu",
        "optional": False,
    }  # fmt: skip
    names = [stage["name"] for stage in stages]
    grounding = stages[names.index("grounding")]
    assert grounding["family"] == "vision"
    assert grounding["requires"] == ["keyframes"]
    assert names.index("keyframes") < names.index("grounding")  # execution order


def test_openapi_is_exposed(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/videos/{video_id}/keyframes" in schema["paths"]
