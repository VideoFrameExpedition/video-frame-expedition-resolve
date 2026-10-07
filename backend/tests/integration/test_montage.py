"""Editing with DaVinci Resolve: exports over HTTP, match_clips, the Resolve
script built from the library and run against a fake Resolve, the MCP tools in memory and
through a real server."""

from __future__ import annotations

import csv
import io
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client

from tests.fakes.library import Library, build_library
from tests.fakes.resolve import FakeClip, FakeFolder, FakeProject, FakeResolve, run_script
from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings
from vfe_vision.core.paths import CASE_INSENSITIVE_PATHS
from vfe_vision.db.models import Job, LibraryRoot, StageRun, Video, VideoSynthesis
from vfe_vision.db.preferences import update_preferences
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import StageStatus, VideoStatus
from vfe_vision.jobs.scan import fingerprint
from vfe_vision.mcp.server import build_mcp_server
from vfe_vision.services import editing, exports
from vfe_vision.services.container import AppContainer

HEADERS = {"X-VFE-Client": "tests"}
MOMENT = {"rank": 1, "block": 3, "chapter": 1, "reason": "Le riz tombe dans la casserole."}


def _prepare(db: Database, base: Path) -> Library:
    """The search tests' library, with a highlight, a finished stage and one real file."""
    library = build_library(db, base)
    with db.write() as session:
        synthesis = session.get_one(VideoSynthesis, library.holiday)
        synthesis.data = {**synthesis.data, "moments": [MOMENT]}
        session.add(
            StageRun(video_id=library.holiday, stage="probe", stage_version=1, cache_key="k",
                     status=StageStatus.SUCCEEDED)
        )  # fmt: skip
    return library


def _video(db: Database, video_id: str) -> Video:
    with db.read() as session:
        return session.get_one(Video, video_id)


# ---------------------------------------------------------------- REST
@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


@pytest.fixture
def library(client: TestClient, tmp_path: Path) -> Library:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    return _prepare(container.db, tmp_path / "Rushs")


def test_export_options_say_what_is_missing(client: TestClient, library: Library) -> None:
    body = client.get(f"/api/v1/videos/{library.mountain}/exports").json()
    formats = {f["format"]: f for f in body["formats"]}
    assert list(formats) == ["srt", "vtt", "csv", "chapters", "edl", "json", "md", "resolve"]
    assert formats["srt"]["available"] is False
    assert formats["srt"]["reason"].startswith("Aucune parole transcrite")
    assert formats["chapters"]["reason"].startswith("Pas de chapitres")
    assert formats["json"]["reason"] == "Aucune analyse terminée : rien à exporter."
    assert formats["csv"]["available"]
    assert formats["md"]["available"]
    # What the file holds, then its language at the end of the name.
    assert formats["csv"]["filename"] == "montagne_SHOTS_FR.csv"
    assert formats["csv"]["url"] == f"/api/v1/videos/{library.mountain}/exports/csv?lang=fr"
    english = client.get(
        f"/api/v1/videos/{library.mountain}/exports", headers={"X-VFE-Language": "en"}
    ).json()
    assert {f["format"]: f["filename"] for f in english["formats"]}["md"] == (
        "montagne_MANIFEST_EN.md"
    )
    holiday = client.get(f"/api/v1/videos/{library.holiday}/exports").json()
    assert all(f["available"] for f in holiday["formats"])
    assert client.get("/api/v1/videos/inconnue/exports").status_code == 404


def test_downloads(client: TestClient, library: Library) -> None:
    def get(fmt: str, **params: str) -> Any:
        response = client.get(f"/api/v1/videos/{library.holiday}/exports/{fmt}", params=params)
        assert response.status_code == 200, response.text
        return response

    srt = get("srt")
    assert srt.headers["content-disposition"].startswith('attachment; filename="vacances_FR.srt"')
    assert "00:00:45,000 --> " in srt.text
    assert "casserole" in srt.text
    assert get("vtt").text.startswith("WEBVTT")

    shots = get("csv")
    assert shots.headers["content-type"].startswith("text/csv")
    raw = shots.content.decode("utf-8")
    assert raw.startswith("\ufeff")
    rows = list(csv.reader(io.StringIO(raw[1:]), delimiter=";"))
    assert rows[0][:6] == ["plan", "début (s)", "fin (s)", "durée (s)", "TC entrée", "TC sortie"]
    assert rows[3][:6] == ["3", "40", "60", "20", "10:00:40:00", "10:01:00:00"]  # own start TC
    assert "riz" in rows[3][10].lower()
    assert "On met le riz" in rows[3][12]
    assert rows[1][9] == "plan d'ensemble" or rows[5][9] == "plan d'ensemble"

    assert get("chapters").text == "00:00 La sieste\n00:40 La cuisine\n01:20 Le lac\n"

    edl = get("edl").text
    assert edl.startswith("TITLE: Vacances à Hyères\r\nFCM: NON-DROP FRAME\r\n")
    assert "001  001      V     C        10:00:00:00 10:00:00:01 01:00:00:00 01:00:00:01  " in edl
    assert " |C:ResolveColorBlue |M:1. La sieste |D:1000" in edl
    assert "|C:ResolveColorGreen |M:Moment fort 1" in edl
    assert "|C:ResolveColorSand |M:Plan 6" in edl
    other = get("edl", timeline_start="00:00:00:00").text
    assert "10:00:00:00 10:00:00:01 00:00:00:00 00:00:00:01" in other
    bad = client.get(
        f"/api/v1/videos/{library.holiday}/exports/edl", params={"timeline_start": "1h"}
    )
    assert bad.status_code == 422

    document = json.loads(get("json").content)
    assert document["format"] == "vfe-vision-analysis"
    assert document["video"]["filename"] == "vacances.mp4"

    manifest = get("md").text
    assert manifest.startswith("# Vacances à Hyères\n")
    for heading in ("## Fichier", "## Contexte", "## Résumé", "## Chapitres (3)",
                    "## Moments forts (1)", "## Plans (6)", "## Parole"):  # fmt: skip
        assert heading in manifest
    assert "- **Timecode de départ** : 10:00:00:00" in manifest
    assert "Lieu** : Hyères, Var, France" in manifest

    script = get("resolve")
    assert script.headers["content-disposition"].startswith(
        'attachment; filename="vacances_RESOLVE_FR.py"'
    )
    assert script.text.isascii()

    # In English: the same files, their words and texts in English.
    rows = list(csv.reader(io.StringIO(get("csv", lang="en").content.decode()[1:]), delimiter=";"))
    assert rows[0][:3] == ["shot", "start (s)", "end (s)"]
    assert "establishing shot" in {rows[1][9], rows[5][9]}
    assert "|M:Highlight 1" in get("edl", lang="en").text
    english = get("md", lang="en")
    assert english.headers["content-disposition"].startswith(
        'attachment; filename="vacances_MANIFEST_EN.md"'
    )
    for heading in ("## File", "## Context", "## Summary", "## Chapters (3)", "## Shots (6)"):
        assert heading in english.text
    assert "- **Start timecode**: 10:00:00:00" in english.text
    assert json.loads(get("json", lang="en").content)["language"] == "en"

    missing = client.get(f"/api/v1/videos/{library.mountain}/exports/srt")
    assert missing.status_code == 404
    assert client.get(f"/api/v1/videos/{library.holiday}/exports/pdf").status_code == 422


def test_library_csv_and_resolve_script(client: TestClient, library: Library) -> None:
    response = client.post(
        "/api/v1/videos/export-csv", json={"video_ids": [library.mountain, "gone", library.holiday]},
        headers=HEADERS,
    )  # fmt: skip
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith('attachment; filename="vfe-videos-')
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8")[1:]), delimiter=";"))
    assert [r[0] for r in rows] == ["fichier", "montagne.mp4", "vacances.mp4"]
    header, mountain, holiday = rows
    column = {name: i for i, name in enumerate(header)}
    assert mountain[column["dossier"]] == "Rushs / Sommets"
    assert mountain[column["météo"]] == "Ciel dégagé, 20 °C"  # weather code 0
    assert holiday[column["lieu"]] == "Hyères, Var, France"
    assert holiday[column["lumière"]] == "heure dorée"
    assert holiday[column["titre"]] == "Vacances à Hyères"
    assert holiday[column["plans"]] == "6"
    assert holiday[column["favori"]] == "oui"
    assert holiday[column["tournage (heure locale)"]] == "01/07/2026 19:30"
    refused = client.post("/api/v1/videos/export-csv", json={"video_ids": []}, headers=HEADERS)
    assert refused.status_code == 422
    assert client.post("/api/v1/videos/export-csv", json={"video_ids": ["x"]}).status_code == 403

    script = client.post(
        "/api/v1/videos/resolve-script",
        json={"video_ids": [library.holiday], "shots": True, "speech": True}, headers=HEADERS,
    )  # fmt: skip
    assert script.status_code == 200
    namespace = run_script(script.text, FakeResolve(None))
    kinds = {m["kind"] for m in namespace["PAYLOAD"]["clips"][0]["markers"]}
    assert kinds == {"chapter", "highlight", "shot", "speech"}


def test_match_clips_endpoint(client: TestClient, library: Library, tmp_path: Path) -> None:
    holiday = _video(client.app.state.container.db, library.holiday)  # type: ignore[attr-defined]
    response = client.post(
        "/api/v1/videos/match-clips",
        json={"items": [
            _other_case(holiday.path),
            {"file_path": holiday.path, "source_start_frame": 1100, "source_end_frame": 1250,
             "fps": 25},
            str(tmp_path / "inconnu.mov"),
        ]},
        headers=HEADERS,
    )  # fmt: skip
    assert response.status_code == 200, response.text
    whole, ranged, unknown = response.json()["items"]
    assert (whole["method"], whole["status"], whole["video_id"]) == (
        "path",
        "ready",
        library.holiday,
    )
    assert whole["range"] is None
    assert ranged["range"]["start_s"] == 44.0
    assert ranged["range"]["end_s"] == 50.0
    assert [s["idx"] for s in ranged["range"]["shots"]] == [2]
    assert "casserole" in ranged["range"]["speech"]
    assert ranged["range"]["cut"]["clip"]["picture_in_s"] == 44.0
    assert (unknown["status"], unknown["method"], unknown["video_id"]) == ("unknown", "none", None)
    # The range in seconds of the file, with the media pool id.
    seconds = client.post(
        "/api/v1/videos/match-clips",
        json={"items": [{"file_path": holiday.path, "source_start_s": 44.0, "source_end_s": 50.0,
                         "clip_uid": "72234098-3d88-4f48-aec1-cdaf3f79f5df"}]},
        headers=HEADERS,
    )  # fmt: skip
    assert seconds.status_code == 200, seconds.text
    [same] = seconds.json()["items"]
    assert (same["range"]["start_s"], same["range"]["end_s"]) == (44.0, 50.0)
    refused = client.post(
        "/api/v1/videos/match-clips",
        json={"items": [{"file_path": holiday.path, "clip_uid": "x'); drop"}]},
        headers=HEADERS,
    )
    assert refused.status_code == 422


# ---------------------------------------------------------------- services
@pytest.fixture
def container(settings: Settings, db: Database) -> Iterator[AppContainer]:
    c = AppContainer.create(settings)
    yield c
    c.db.dispose()


def test_match_methods(container: AppContainer, tmp_path: Path) -> None:
    library = _prepare(container.db, tmp_path / "Rushs")
    elsewhere = tmp_path / "Resolve" / "Media"
    elsewhere.mkdir(parents=True)
    copy = elsewhere / "vacances.mp4"
    copy.write_bytes(b"x")  # same name and size (1 byte) as the library's
    renamed = elsewhere / "C0042.MP4"
    renamed.write_bytes(b"\x00" * 64)
    with container.db.write() as session:
        mountain = session.get_one(Video, library.mountain)
        mountain.fingerprint = fingerprint(renamed, 64)  # same content, another name
        mountain.status = VideoStatus.OFFLINE
        root = session.get_one(LibraryRoot, library.root_id)
        other = Video(root_id=root.id, path=str(tmp_path / "Rushs/B/vacances.mp4"),
                      path_key=str(tmp_path / "Rushs/B/vacances.mp4").lower(),
                      rel_path="B/vacances.mp4", filename="vacances.mp4", size_bytes=99,
                      mtime=0.0, fingerprint="fp-b", status=VideoStatus.NEW)  # fmt: skip
        session.add(other)
    queries = [
        editing.ClipQuery("\\\\?\\" + _other_case(str(tmp_path / "Rushs" / "vacances.mp4"))),
        editing.ClipQuery(str(copy)),
        editing.ClipQuery(str(renamed)),
        editing.ClipQuery(str(tmp_path / "ailleurs" / "vacances.mp4")),
        editing.ClipQuery(str(tmp_path / "ailleurs" / "montagne.mp4")),
        editing.ClipQuery(str(tmp_path / "ailleurs" / "rien.mp4")),
    ]
    path, name_size, content, ambiguous, name, unknown = editing.match_clips(container, queries)
    assert (path.method, path.video.id if path.video else None) == ("path", library.holiday)
    assert (name_size.method, name_size.confidence) == ("name_size", 0.9)
    assert name_size.note is not None
    assert name_size.note.startswith("Même nom et même taille")
    assert (content.method, content.status) == ("fingerprint", "offline")
    assert content.note is not None
    assert "Rescanner" in content.note
    assert (ambiguous.status, len(ambiguous.candidates)) == ("ambiguous", 2)
    assert (name.method, name.confidence, name.status) == ("name", 0.5, "offline")
    assert unknown.status == "unknown"
    assert unknown.video is None
    with pytest.raises(Exception, match="Au plus"):
        editing.match_clips(container, [editing.ClipQuery("x")] * (editing.MAX_ITEMS + 1))


def _other_case(path: str) -> str:
    """The same file spelled otherwise where file names ignore case (Windows, macOS)."""
    return path.upper() if CASE_INSENSITIVE_PATHS else path


def test_resolve_payload_applied_to_a_fake_resolve(container: AppContainer, tmp_path: Path) -> None:
    library = _prepare(container.db, tmp_path / "Rushs")
    holiday = _video(container.db, library.holiday)
    from vfe_vision.services import resolve

    payload = resolve.build_payload(container, [library.holiday, "gone"], resolve.MarkerOptions())
    assert payload.unknown == ["gone"]
    assert payload.script.isascii()
    [clip] = payload.data["clips"]
    assert clip["metadata"]["Keywords"] == ["vacances", "chat"]
    assert clip["metadata"]["Description"] == "Une journée d'été entre la maison et le lac."
    assert clip["metadata"]["Comments"].startswith(
        "Lieu : Hyères, Var, France · Tournage : 01/07/2026"
    )
    assert [m["custom_data"] for m in clip["markers"]] == [
        "vfe:chapter:1", "vfe:chapter:2", "vfe:highlight:1", "vfe:chapter:3"]  # fmt: skip
    media = FakeClip(_other_case(holiday.path.replace("\\", "/")), fps=25.0, frames=3000)
    resolve_app = FakeResolve(FakeProject(FakeFolder(folders=[FakeFolder(clips=[media])])))
    result = run_script(payload.script, resolve_app)["result"]
    assert result["errors"] == []
    assert result["not_found"] == []
    assert result["applied"][0]["markers_added"] == 4
    assert media.metadata["Keywords"] == "vacances, chat"
    assert "Hyères" in media.metadata["Comments"]  # decoded from the ASCII script
    again = run_script(payload.script, resolve_app)["result"]
    assert again["applied"][0]["markers_removed"] == 4
    assert len(media.markers) == 4


def test_write_export_goes_to_the_data_folder(container: AppContainer, tmp_path: Path) -> None:
    library = _prepare(container.db, tmp_path / "Rushs")
    path = exports.write_export(container, library.holiday, exports.ExportFormat.SRT)
    assert path == container.settings.data_dir / "exports" / library.holiday / "vacances_FR.srt"
    assert "casserole" in path.read_text(encoding="utf-8")
    assert not (tmp_path / "Rushs").exists()  # nothing next to the videos


# ---------------------------------------------------------------- MCP in memory
@pytest.mark.anyio
async def test_editing_tools(container: AppContainer, tmp_path: Path) -> None:
    library = _prepare(container.db, tmp_path / "Rushs")
    holiday = _video(container.db, library.holiday)
    async with Client(build_mcp_server(lambda: container)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        for name in ("analyze_folder", "match_clips", "get_cut_points", "get_reframe",
                     "get_resolve_payload", "export_video"):  # fmt: skip
            assert tools[name].output_schema is not None, name
            assert tools[name].description
        matched = await client.call_tool("match_clips", {"items": [
            {"file_path": holiday.path, "source_start_frame": 1000, "source_end_frame": 1210,
             "fps": 25}]})  # fmt: skip
        # The same range in seconds of the file (GetLeftOffset and GetDuration / the timeline's
        # fps), as Claude reads it whatever the timeline's frame rate, with the media pool uid.
        in_seconds = await client.call_tool("match_clips", {"items": [
            {"file_path": holiday.path, "clip_uid": "72234098-3d88-4f48-aec1-cdaf3f79f5df",
             "source_start_s": 40.0, "source_end_s": 48.4}]})  # fmt: skip
        bad_uid = await client.call_tool(
            "match_clips", {"items": [{"file_path": holiday.path, "clip_uid": "x'); drop"}]}
        )
        cut = await client.call_tool(
            "get_cut_points", {"video_id": library.holiday, "t_start": 46.2, "t_end": 59.8}
        )
        frame = await client.call_tool(
            "get_reframe",
            {"video_id": library.holiday, "t_start": 0, "t_end": 20, "timeline_width": 1080,
             "timeline_height": 1920},
        )  # fmt: skip
        payload = await client.call_tool("get_resolve_payload", {"video_ids": [library.holiday]})
        written = await client.call_tool(
            "export_video", {"video_id": library.holiday, "format": "chapters"}
        )
        empty = await client.call_tool(
            "export_video", {"video_id": library.mountain, "format": "srt"}
        )
    data = matched.structured_content or {}
    [item] = data["clips"]
    assert (item["method"], item["confidence"], item["status"]) == ("path", 1.0, "ready")
    info = item["range"]
    assert (info["start_s"], info["end_s"]) == (40.0, 48.4)
    assert [s["shot"] for s in info["shots"]] == [3]
    assert info["highlights"][0]["rank"] == 1
    assert info["safe_cut"]["timecode_in"] == "10:00:40:00"
    [by_seconds] = (in_seconds.structured_content or {})["clips"]
    assert (by_seconds["range"]["start_s"], by_seconds["range"]["end_s"]) == (40.0, 48.4)
    assert [s["shot"] for s in by_seconds["range"]["shots"]] == [3]
    assert bad_uid.is_error
    text = matched.content[0].text  # type: ignore[union-attr]
    listing, fence = text.split("BEGIN UNTRUSTED VIDEO CONTENT", 1)
    assert "casserole" not in listing
    assert "casserole" in fence

    points = cut.structured_content or {}
    assert points["cut"]["picture_in_s"] == 44.85  # « riz » not cut: before the sentence
    assert points["cut"]["picture_out_s"] == 60.0  # 0.2 s before a cut: to it
    assert points["cut"]["in_frame"] == 1121
    assert points["cut"]["timecode_out"] == "10:01:00:00"
    assert points["shots"] == [3]
    assert points["cut"]["notes"] == ["sortie calée sur la fin du plan 3",
                                      "entrée déplacée entre deux mots"]  # fmt: skip

    framing = frame.structured_content or {}
    assert framing["subject"] == "chat"
    assert len(framing["keyframes"]) == 1
    props = framing["framing"]["properties"]
    assert props["ZoomX"] == props["ZoomY"] == pytest.approx(3.1605, abs=1e-3)
    assert props["Pan"] == pytest.approx(170.7, abs=0.1)
    assert props["Tilt"] == 0.0
    assert framing["framing"]["crop_px"][2:] == [607.5, 1080.0]
    assert "Scaling" in framing["convention"]

    result = payload.structured_content or {}
    assert result["clips"][0]["markers"] == {"chapter": 3, "highlight": 1}
    script = result["script"]
    assert script.isascii()
    assert script in payload.content[0].text  # type: ignore[union-attr]

    exported = written.structured_content or {}
    assert Path(exported["path"]).read_text(encoding="utf-8").startswith("00:00 La sieste")
    assert empty.is_error
    assert "Aucune parole" in empty.content[0].text  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_manifest_resource_and_prompts(container: AppContainer, tmp_path: Path) -> None:
    library = _prepare(container.db, tmp_path / "Rushs")
    async with Client(build_mcp_server(lambda: container)) as client:
        templates = (await client.list_resource_templates()).resource_templates
        assert [t.uri_template for t in templates] == ["vfe://videos/{video_id}/manifest"]
        read = await client.read_resource(f"vfe://videos/{library.holiday}/manifest")
        prompts = {p.name: p for p in (await client.list_prompts()).prompts}
        plan = await client.get_prompt(
            "plan_edit", {"goal": "un film de 1 min", "target_duration": "60 s"}
        )
        review = await client.get_prompt("review_rushes", {"folder": "D:\\Rushs"})
    text = read.contents[0].text  # type: ignore[union-attr]
    assert text.startswith("MANIFEST généré par vfe-vision.")
    assert "BEGIN UNTRUSTED VIDEO CONTENT" in text
    assert "# Vacances à Hyères" in text
    assert set(prompts) == {"plan_edit", "review_rushes"}
    assert [a.name for a in prompts["plan_edit"].arguments or []] == [
        "goal",
        "target_duration",
        "style",
    ]
    body = plan.messages[0].content.text  # type: ignore[union-attr]
    assert "Durée visée : 60 s." in body
    assert "get_cut_points" in body
    assert "Cross Dissolve" in body
    assert "analyze_folder" in review.messages[0].content.text  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_analyze_folder(container: AppContainer, tmp_path: Path) -> None:
    library = _prepare(container.db, tmp_path / "Rushs")
    inside = tmp_path / "Rushs" / "Sommets"
    inside.mkdir(parents=True)
    (inside / "nouveau.mp4").write_bytes(b"\x00" * 16)
    (inside / "notes.txt").write_text("pas une vidéo")
    outside = tmp_path / "Ailleurs"
    outside.mkdir()
    async with Client(build_mcp_server(lambda: container)) as client:
        queued = await client.call_tool("analyze_folder", {"path": str(inside)})
        refused = await client.call_tool("analyze_folder", {"path": str(outside)})
        update_preferences(container.db, {"mcp_add_folders": True})
        added = await client.call_tool("analyze_folder", {"path": str(outside), "recursive": False})
    data = queued.structured_content or {}
    assert (data["root_id"], data["found"], data["queued"], data["root_added"]) == (
        library.root_id, 1, 1, False)  # fmt: skip
    assert refused.is_error
    assert "page Système" in refused.content[0].text  # type: ignore[union-attr]
    grown = added.structured_content or {}
    assert grown["root_added"] is True
    assert grown["scan_job_id"]
    with container.db.read() as session:
        roots = session.execute(sa.select(LibraryRoot.path)).scalars().all()
        jobs = session.execute(sa.select(sa.func.count()).select_from(Job)).scalar_one()
    assert str(outside) in roots
    assert jobs == 2


# ---------------------------------------------------------------- MCP over real HTTP
def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture
def server(settings: Settings) -> Iterator[tuple[str, FastAPI]]:
    port = _free_port()
    app = create_app(settings.model_copy(update={"port": port}), start_worker=False)
    runner = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not runner.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert runner.started
    try:
        yield f"http://127.0.0.1:{port}/mcp", app
    finally:
        runner.should_exit = True
        thread.join(timeout=10)


@pytest.mark.anyio
async def test_editing_tools_over_real_http(server: tuple[str, FastAPI], tmp_path: Path) -> None:
    url, app = server
    container: AppContainer = app.state.container
    library = _prepare(container.db, tmp_path / "Rushs")
    holiday = _video(container.db, library.holiday)
    async with Client(url) as client:
        matched = await client.call_tool("match_clips", {"items": [holiday.path]})
        payload = await client.call_tool(
            "get_resolve_payload", {"video_ids": [library.holiday], "include_shots": True}
        )
        manifest = await client.read_resource(f"vfe://videos/{library.holiday}/manifest")
    assert not matched.is_error
    assert not payload.is_error
    assert (matched.structured_content or {})["clips"][0]["video_id"] == library.holiday
    counts = (payload.structured_content or {})["clips"][0]["markers"]
    assert counts == {"chapter": 3, "highlight": 1, "shot": 6}
    assert "## Plans (6)" in manifest.contents[0].text  # type: ignore[union-attr]
