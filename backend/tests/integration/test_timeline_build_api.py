"""« Create a timeline » through the REST API: the preview, the FCPXML file, and the
timeline built in the project open in Resolve (a fake builder here)."""

from __future__ import annotations

import io
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from tests.fakes.library import build_library
from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import ExternalToolError, ResolveUnavailableError, VfeError
from vfe_vision.core.paths import path_key
from vfe_vision.db.models import LibraryRoot, Video, VideoSynthesis
from vfe_vision.db.preferences import update_preferences
from vfe_vision.domain.enums import VideoStatus
from vfe_vision.domain.markers import Marker
from vfe_vision.domain.resolve_timeline import ResolveProjectRef
from vfe_vision.domain.timeline_build import BuiltTimeline, TimelineRequest
from vfe_vision.domain.timeline_files import SubtitleTrack
from vfe_vision.domain.transcript import Cue
from vfe_vision.services.container import AppContainer
from vfe_vision.services.subtitle_files import WriteStatus, write_subtitles

HEADERS = {"X-VFE-Client": "tests"}


class FakeBuilder:
    def __init__(self) -> None:
        self.requests: list[TimelineRequest] = []
        self.error: VfeError | None = None

    def build_timeline(self, request: TimelineRequest) -> BuiltTimeline:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return BuiltTimeline(
            project=ResolveProjectRef(id="prj-1", name="VFE Vision - essai"),
            timeline_id="tl-9", timeline_name=request.name + " (2)", fps=25.0,
            width=request.format.width, height=request.format.height,
            clips=len(request.clips) - 1, imported=1, reused=len(request.clips) - 2,
            missing=(request.clips[-1][1],), folder=request.folder,
            markers=sum(len(m) for m in request.markers.values()), markers_missed=0,
            subtitles=tuple(Path(path).stem for path in [
                *(path for _, path in request.subtitle_tracks), *request.subtitle_files]),
            subtitles_laid=tuple(name for name, _ in request.subtitle_tracks),
        )  # fmt: skip


@pytest.fixture
def builder() -> FakeBuilder:
    return FakeBuilder()


@pytest.fixture
def client(settings: Settings, builder: FakeBuilder) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(
        app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)
    ) as test_client:
        _container(test_client).resolve_builder = builder
        yield test_client


def _xml(data: bytes) -> ET.Element:
    return ET.fromstring(data)  # noqa: S314 - the application's own file


def _container(client: TestClient) -> AppContainer:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    return container


def _synthesis(video_id: str, chapters: list[dict[str, Any]]) -> VideoSynthesis:
    blocks = [{"no": 1, "start": 0.0, "end": 6.0}, {"no": 2, "start": 6.0, "end": 10.0}]
    return VideoSynthesis(
        video_id=video_id, schema_version=1, rules_version="1", model="m", prompt_version="1",
        language="fr", strategy="single", input_variant="V4", input_key="k",
        data={"blocks": blocks, "chapters": chapters},
    )  # fmt: skip


def _videos(client: TestClient, folder: Path) -> dict[str, str]:
    """Four videos of « D:\\cats 2026 »: two phone files (one vertical, shot first), one gone,
    one not examined yet. The later one has two chapters; the earlier one a single chapter, which
    is no chapter (the whole video)."""
    c = _container(client)
    with c.db.write() as session:
        root = LibraryRoot(path=str(folder), path_key=path_key(folder), label="cats 2026")
        session.add(root)
        session.flush()
        rows: dict[str, dict[str, Any]] = {
            "late": {"filename": "20260915_110000.mp4", "duration_s": 10.0, "width": 3840,
                     "height": 2160, "captured_at": datetime(2026, 9, 15, 9, 0, tzinfo=UTC)},
            "early": {"filename": "20260915_100000.mp4", "duration_s": 20.0, "width": 2160,
                      "height": 3840, "captured_at": datetime(2026, 9, 15, 8, 0, tzinfo=UTC)},
            "gone": {"filename": "gone.mp4", "duration_s": 5.0, "status": VideoStatus.OFFLINE},
            "new": {"filename": "new.mp4", "duration_s": None, "fps": None, "is_vfr": None},
        }  # fmt: skip
        ids = {}
        for key, fields in rows.items():
            values: dict[str, Any] = {
                "fps": 30.0, "is_vfr": True, "has_audio": True, "status": VideoStatus.READY,
                "captured_at_confidence": "high", "capture_timezone": "Europe/Paris",
            } | fields  # fmt: skip
            path = folder / values["filename"]
            video = Video(root_id=root.id, path=str(path), path_key=path_key(path),
                          rel_path=values["filename"], size_bytes=1000, mtime=0.0,
                          fingerprint=key, **values)  # fmt: skip
            session.add(video)
            session.flush()
            ids[key] = video.id
        session.add(_synthesis(ids["late"], [
            {"first": 1, "last": 1, "title": "Arrivée", "summary": "Le port"},
            {"first": 2, "last": 2, "title": "Départ", "summary": ""},
        ]))  # fmt: skip
        session.add(_synthesis(ids["early"], [{"first": 1, "last": 2, "title": "Tout"}]))
    return ids


def test_the_preview(client: TestClient, tmp_path: Path) -> None:
    ids = _videos(client, tmp_path / "cats 2026")
    response = client.post(
        "/api/v1/videos/timeline-preview",
        json={"video_ids": [ids["late"], ids["gone"], ids["early"], ids["new"]]},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["name"] == plan["suggested_name"] == "VFE 2026-09-15"
    assert plan["file_name"] == "VFE 2026-09-15.zip"
    assert plan["videos"] == 2
    assert plan["files"] == ["20260915_100000.mp4", "20260915_110000.mp4"]  # shooting order
    # 29.97 as Resolve reads these phone files; the vertical one has more footage.
    assert (plan["frame_rate"], plan["width"], plan["height"]) == ("29.97", 2160, 3840)
    assert plan["duration_s"] == pytest.approx(900 * 1001 / 30000)
    assert plan["rates"] == [{"frame_rate": "29.97", "fps": pytest.approx(29.97, abs=1e-3),
                              "videos": 2}]  # fmt: skip
    assert {(s["filename"], s["reason"]) for s in plan["skipped"]} == {
        ("gone.mp4", "offline"), ("new.mp4", "not_examined"),
    }  # fmt: skip
    assert plan["resolve_host"] is None
    assert (plan["chapters"], plan["chaptered"]) == (2, 1)
    assert plan["suggestions"] == {"highlights": 0, "establishing": 0, "b_roll": 0, "avoid": 0}
    assert (plan["speech_subtitles"], plan["shot_subtitles"]) == (0, 0)


def test_the_preview_with_choices(client: TestClient, tmp_path: Path) -> None:
    ids = _videos(client, tmp_path / "cats 2026")
    body = {
        "video_ids": [ids["late"], ids["early"]], "order": "selection", "name": "  Mon  montage ",
        "frame_rate": "25", "width": 1920, "height": 1080,
    }  # fmt: skip
    plan = client.post("/api/v1/videos/timeline-preview", json=body, headers=HEADERS).json()
    assert plan["name"] == "Mon montage"
    assert plan["files"] == ["20260915_110000.mp4", "20260915_100000.mp4"]
    assert (plan["frame_rate"], plan["width"], plan["height"]) == ("25", 1920, 1080)
    assert plan["suggested_frame_rate"] == "29.97"
    wrongs: list[dict[str, Any]] = [
        {"height": None}, {"frame_rate": "26"}, {"order": "random"}, {"video_ids": []},
    ]  # fmt: skip
    for wrong in wrongs:
        response = client.post(
            "/api/v1/videos/timeline-preview", json=body | wrong, headers=HEADERS
        )
        assert response.status_code == 422, wrong


def _unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        return {name: bundle.read(name) for name in bundle.namelist()}


def test_the_timeline_files(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "cats 2026"
    ids = _videos(client, folder)
    response = client.post(
        "/api/v1/videos/export-timeline",
        json={"video_ids": [ids["late"], ids["early"], ids["gone"]]},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert "VFE%202026-09-15.zip" in response.headers["content-disposition"]
    files = _unzip(response.content)
    base = "VFE 2026-09-15"
    assert sorted(files) == ["LISEZ-MOI.txt", f"{base}.fcpxml", f"{base}.otio"]  # nothing said
    assert b"Fichier \xe2\x80\xba Importer \xe2\x80\xba Timeline" in files["LISEZ-MOI.txt"]
    timeline = json.loads(files[f"{base}.otio"])
    video_track = timeline["tracks"]["children"][0]
    assert [clip["name"] for clip in video_track["children"]] == [
        "20260915_100000.mp4", "20260915_110000.mp4",
    ]  # fmt: skip
    assert [(m["name"], m["color"], m["marked_range"]["start_time"]["value"])
            for m in video_track["children"][1]["markers"]] == [
        ("Arrivée", "BLUE", 0), ("Départ", "BLUE", 179),
    ]  # fmt: skip
    root = _xml(files[f"{base}.fcpxml"])
    clips = root.findall("library/event/project/sequence/spine/asset-clip")
    assert [clip.get("name") for clip in clips] == ["20260915_100000.mp4", "20260915_110000.mp4"]
    sources = [rep.get("src") for rep in root.iter("media-rep")]
    assert sources == [(folder / "20260915_100000.mp4").as_uri(),
                       (folder / "20260915_110000.mp4").as_uri()]  # fmt: skip
    # Where its chapters start, on the later video's clip (its frames at 29.97).
    assert clips[0].findall("marker") == []
    assert [(m.get("start"), m.get("value"), m.get("note")) for m in clips[1].iter("marker")] == [
        ("0s", "Arrivée", "Le port"), ("179179/30000s", "Départ", None),
    ]  # fmt: skip
    plain = _unzip(client.post(
        "/api/v1/videos/export-timeline",
        json={"video_ids": [ids["late"]], "chapters": False},
        headers=HEADERS,
    ).content)  # fmt: skip
    assert _xml(plain[f"{base}.fcpxml"]).find(".//marker") is None
    assert (
        json.loads(plain[f"{base}.otio"])["tracks"]["children"][0]["children"][0]["markers"] == []
    )
    nothing = client.post(
        "/api/v1/videos/export-timeline", json={"video_ids": [ids["gone"]]}, headers=HEADERS
    )
    assert nothing.status_code == 422


def test_resolve_on_another_computer_gets_its_own_paths(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "cats 2026"
    ids = _videos(client, folder)
    other = _videos_elsewhere(client, tmp_path / "ailleurs")
    update_preferences(
        _container(client).db,
        {"resolve_host": "mac-studio",
         "resolve_folders": [{"here": str(folder), "there": "/Volumes/cats 2026"}]},
    )  # fmt: skip
    body = {"video_ids": [ids["early"], other]}
    plan = client.post("/api/v1/videos/timeline-preview", json=body, headers=HEADERS).json()
    assert plan["resolve_host"] == "mac-studio"
    assert [(s["filename"], s["reason"]) for s in plan["skipped"]] == [
        ("autre.mp4", "no_folder_pair")
    ]
    files = _unzip(
        client.post("/api/v1/videos/export-timeline", json=body, headers=HEADERS).content
    )
    sources = [rep.get("src") for rep in _xml(files["VFE 2026-09-15.fcpxml"]).iter("media-rep")]
    assert sources == ["file:///Volumes/cats%202026/20260915_100000.mp4"]
    timeline = json.loads(files["VFE 2026-09-15.otio"])
    clip = timeline["tracks"]["children"][0]["children"][0]
    assert clip["media_references"]["DEFAULT_MEDIA"]["target_url"] == (
        "/Volumes/cats 2026/20260915_100000.mp4"
    )


def _videos_elsewhere(client: TestClient, folder: Path) -> str:
    with _container(client).db.write() as session:
        root = LibraryRoot(path=str(folder), path_key=path_key(folder), label="ailleurs")
        session.add(root)
        session.flush()
        path = folder / "autre.mp4"
        video = Video(root_id=root.id, path=str(path), path_key=path_key(path),
                      rel_path="autre.mp4", filename="autre.mp4", size_bytes=1, mtime=0.0,
                      fingerprint="autre", duration_s=3.0, fps=25.0, status=VideoStatus.READY)  # fmt: skip
        session.add(video)
        session.flush()
        return video.id


def test_the_timeline_in_resolve(client: TestClient, builder: FakeBuilder, tmp_path: Path) -> None:
    folder = tmp_path / "cats 2026"
    ids = _videos(client, folder)
    body = {"video_ids": [ids["late"], ids["early"], ids["gone"]], "name": "Été"}
    response = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS)
    assert response.status_code == 201, response.text
    (request,) = builder.requests
    assert request.name == "Été"
    assert request.folder == "Video Frame Expedition"
    assert request.format.rate.name == "29.97"
    assert request.clips == (
        (ids["early"], str(folder / "20260915_100000.mp4")),
        (ids["late"], str(folder / "20260915_110000.mp4")),
    )
    assert request.markers == {
        ids["late"]: (
            Marker("chapter", 1, 0.0, 0.0, "Arrivée", "Le port"),
            Marker("chapter", 2, 6.0, 0.0, "Départ", ""),
        )
    }
    built = response.json()
    assert built["project"] == {"id": "prj-1", "name": "VFE Vision - essai"}
    assert (built["timeline_name"], built["fps"], built["clips"]) == ("Été (2)", 25.0, 1)
    assert (built["markers"], built["markers_missed"]) == (2, 0)
    plain = client.post(
        "/api/v1/resolve/timelines", json=body | {"chapters": False}, headers=HEADERS
    )
    assert plain.status_code == 201
    assert builder.requests[-1].markers == {}
    assert built["missing"] == [str(folder / "20260915_110000.mp4")]
    assert [s["reason"] for s in built["skipped"]] == ["offline"]


def test_the_timeline_in_resolve_when_it_cannot_be(
    client: TestClient, builder: FakeBuilder, tmp_path: Path
) -> None:
    ids = _videos(client, tmp_path / "cats 2026")
    nothing = client.post(
        "/api/v1/resolve/timelines", json={"video_ids": [ids["new"]]}, headers=HEADERS
    )
    assert nothing.status_code == 422
    assert builder.requests == []  # Resolve not even asked
    builder.error = ResolveUnavailableError("Resolve n'est pas lancé.", reason="not_running")
    response = client.post(
        "/api/v1/resolve/timelines", json={"video_ids": [ids["late"]]}, headers=HEADERS
    )
    assert response.status_code == 503
    assert response.json()["reason"] == "not_running"
    # A failure in Resolve: the user reads the message, the log keeps the script's own words.
    builder.error = ExternalToolError("Aucune vidéo.", tool="DaVinci Resolve", cause="list: 0")
    with capture_logs() as logs:
        response = client.post(
            "/api/v1/resolve/timelines", json={"video_ids": [ids["late"]]}, headers=HEADERS
        )
    assert (response.status_code, response.json()["detail"]) == (502, "Aucune vidéo.")
    failed = [entry for entry in logs if entry["event"] == "request failed"]
    assert dict(failed[0]) | {"path": ""} == {
        "event": "request failed", "log_level": "warning", "path": "", "status": 502,
        "code": "external_tool_error", "detail": "Aucune vidéo.",
        "extra_tool": "DaVinci Resolve", "extra_cause": "list: 0",
    }  # fmt: skip


MOMENT = {"rank": 1, "block": 3, "chapter": 1, "reason": "Le riz tombe dans la casserole."}


def _analysed(client: TestClient, tmp_path: Path) -> list[str]:
    """The search tests' two analysed videos (speech, described shots, a synthesis) and a
    highlight: montagne.mp4 (40 s, shot first) then vacances.mp4 (2 min)."""
    c = _container(client)
    library = build_library(c.db, tmp_path / "Rushs")
    with c.db.write() as session:
        synthesis = session.get_one(VideoSynthesis, library.holiday)
        synthesis.data = {**synthesis.data, "moments": [MOMENT]}
    return [library.holiday, library.mountain]


def test_subtitles_and_suggestions(client: TestClient, tmp_path: Path) -> None:
    ids = _analysed(client, tmp_path)
    body = {"video_ids": ids, "shots": True}
    plan = client.post("/api/v1/videos/timeline-preview", json=body, headers=HEADERS).json()
    assert plan["files"] == ["montagne.mp4", "vacances.mp4"]
    assert plan["suggestions"] == {"highlights": 1, "establishing": 2, "b_roll": 0, "avoid": 0}
    assert (plan["speech_subtitles"], plan["shot_subtitles"]) == (1, 8)
    files = _unzip(
        client.post("/api/v1/videos/export-timeline", json=body, headers=HEADERS).content
    )
    base = plan["name"]
    # What is said in vacances.mp4 at 45 s is 1:25 into the timeline (after 40 s of mountain).
    assert files[f"{base}_FR.srt"].decode() == (
        "1\n00:01:25,000 --> 00:01:28,400\nOn met le riz dans la casserole.\n"
    )
    shots = files[f"{base}_SHOTS_FR.srt"].decode()
    assert shots.startswith("1\n00:00:00,000 --> 00:00:20,000\nUn sommet enneigé")
    assert shots.count(" --> ") == 8
    read_me = files["LISEZ-MOI.txt"].decode()
    assert f"« {base}_FR.srt » (Transcription)\r\n   - « {base}_SHOTS_FR.srt » (Plans)" in read_me
    timeline = json.loads(files[f"{base}.otio"])
    holiday = timeline["tracks"]["children"][0]["children"][1]
    colors = sorted((m["color"], m["name"]) for m in holiday["markers"])
    assert ("GREEN", "Moment fort 1 — Le riz tombe dans la casserole.") in colors
    assert ("CYAN", "Plan d'ensemble") in colors
    # Only the transcript, by default; nothing but the chapters.
    default = _unzip(client.post("/api/v1/videos/export-timeline", json={"video_ids": ids},
                                 headers=HEADERS).content)  # fmt: skip
    assert [n for n in default if n.endswith(".srt")] == [f"{base}_FR.srt"]
    bare = {"video_ids": ids, "transcript": False, "suggestions": False, "chapters": False}
    only = _unzip(client.post("/api/v1/videos/export-timeline", json=bare, headers=HEADERS).content)
    assert not [n for n in only if n.endswith(".srt")]
    assert all(not clip.get("markers") for track in json.loads(only[f"{base}.otio"])["tracks"][
        "children"] for clip in track["children"])  # fmt: skip


def _on_disk(tmp_path: Path) -> tuple[Path, Path]:
    """The two videos of ``_analysed`` as files (empty), for their subtitles to go next to them."""
    mountain = tmp_path / "Rushs" / "Sommets" / "montagne.mp4"
    holiday = tmp_path / "Rushs" / "vacances.mp4"
    mountain.parent.mkdir(parents=True)
    for video in (mountain, holiday):
        video.write_bytes(b"")
    return mountain, holiday


def test_subtitles_are_written_next_to_the_videos(
    client: TestClient, builder: FakeBuilder, tmp_path: Path
) -> None:
    ids = _analysed(client, tmp_path)
    mountain, holiday = _on_disk(tmp_path)
    users = holiday.with_name("vacances_SHOTS_FR.srt")
    users.write_text("1\n00:00:01,000 --> 00:00:02,000\nÀ moi\n", encoding="utf-8")
    body = {"video_ids": ids, "shots": True, "name": "Été"}
    response = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS)
    assert response.status_code == 201, response.text
    (request,) = builder.requests
    assert request.marker_kinds == ("chapter", "highlight", "establishing", "b_roll", "avoid")
    kinds = sorted({m.kind for markers in request.markers.values() for m in markers})
    assert kinds == ["chapter", "establishing", "highlight"]
    # Laid in Resolve: a file per track, in the timeline's time, in the data folder.
    folder = _container(client).settings.data_dir / "timelines" / "Été"
    laid = folder / "Été_FR.srt"
    assert request.subtitle_tracks == (
        ("Transcription", str(laid)), ("Plans", str(folder / "Été_SHOTS_FR.srt")),
    )  # fmt: skip
    assert request.subtitle_files == ()
    assert laid.read_text(encoding="utf-8").startswith("1\n00:01:25,")  # after 40 s of mountain
    # Next to the videos, each its own files, in its own time; the user's is left.
    speech = holiday.with_name("vacances_FR.srt")
    shots = mountain.with_name("montagne_SHOTS_FR.srt")
    assert speech.read_text(encoding="utf-8").startswith("1\n00:00:45,")
    assert " --> 00:00:20,000\nUn sommet enneigé" in shots.read_text(encoding="utf-8")
    assert users.read_text(encoding="utf-8").endswith("À moi\n")
    built = response.json()
    assert built["subtitles"] == ["Été_FR", "Été_SHOTS_FR"]
    assert built["subtitles_laid"] == ["Transcription", "Plans"]
    assert [(f["file"], f["part"], f["status"], f["folder"]) for f in built["subtitle_files"]] == [
        ("montagne_SHOTS_FR.srt", "shots", "written", str(mountain.parent)),
        ("vacances_FR.srt", "transcript", "written", str(holiday.parent)),
        ("vacances_SHOTS_FR.srt", "shots", "conflict", str(holiday.parent)),
    ]
    assert "laissé tel quel" in built["subtitle_files"][2]["detail"]
    # Built again: its own files are kept (or written again); one changed by hand since is left.
    again = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS).json()
    assert [f["status"] for f in again["subtitle_files"]] == ["written", "written", "conflict"]
    speech.write_text(speech.read_text(encoding="utf-8").replace("riz", "blé"), encoding="utf-8")
    again = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS).json()
    assert [f["status"] for f in again["subtitle_files"]] == ["written", "conflict", "conflict"]
    assert "blé" in speech.read_text(encoding="utf-8")
    users.unlink()
    speech.unlink()
    again = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS).json()
    assert [f["status"] for f in again["subtitle_files"]] == ["written"] * 3
    assert users.read_text(encoding="utf-8").startswith("1\n00:00:00,000 --> 00:00:20,000\n")


def test_no_subtitles_asked_none_written(
    client: TestClient, builder: FakeBuilder, tmp_path: Path
) -> None:
    ids = _analysed(client, tmp_path)
    _, holiday = _on_disk(tmp_path)
    body = {"video_ids": ids, "transcript": False, "shots": False}
    built = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS).json()
    assert built["subtitle_files"] == []
    assert (builder.requests[0].subtitle_tracks, builder.requests[0].subtitle_files) == ((), ())
    assert sorted(p.name for p in holiday.parent.rglob("*") if p.is_file()) == [
        "montagne.mp4", "vacances.mp4",
    ]  # fmt: skip


def _on_another_computer(client: TestClient, tmp_path: Path) -> None:
    update_preferences(
        _container(client).db,
        {"resolve_host": "mac-studio",
         "resolve_folders": [{"here": str(tmp_path / "Rushs"), "there": "/Volumes/Rushs"}]},
    )  # fmt: skip


def test_subtitles_for_resolve_on_another_computer(
    client: TestClient, builder: FakeBuilder, tmp_path: Path
) -> None:
    ids = _analysed(client, tmp_path)
    mountain, holiday = _on_disk(tmp_path)
    _on_another_computer(client, tmp_path)
    body = {"video_ids": ids, "shots": True, "name": "Été"}
    built = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS).json()
    # Its computer does not see the data folder: the tracks are written next to the timeline's
    # first video, opened through the folder pair as the videos, and laid as here.
    (request,) = builder.requests
    assert request.subtitle_tracks == (
        ("Transcription", "/Volumes/Rushs/Sommets/Été_TIMELINE_FR.srt"),
        ("Plans", "/Volumes/Rushs/Sommets/Été_TIMELINE_SHOTS_FR.srt"),
    )
    assert request.subtitle_files == ()
    assert built["subtitles_laid"] == ["Transcription", "Plans"]
    laid = mountain.with_name("Été_TIMELINE_FR.srt")
    assert laid.read_text(encoding="utf-8").startswith("1\n00:01:25,")  # after 40 s of mountain
    assert holiday.with_name("vacances_FR.srt").is_file()  # each video's own, as here
    assert [(f["file"], f["status"]) for f in built["subtitle_files"]] == [
        ("montagne_SHOTS_FR.srt", "written"), ("vacances_FR.srt", "written"),
        ("vacances_SHOTS_FR.srt", "written"), ("Été_TIMELINE_FR.srt", "written"),
        ("Été_TIMELINE_SHOTS_FR.srt", "written"),
    ]  # fmt: skip
    # Built again, its files are written again; one the user changed is left, and the track goes
    # to the next folder of the videos.
    shots = mountain.with_name("Été_TIMELINE_SHOTS_FR.srt")
    shots.write_text("1\n00:00:01,000 --> 00:00:02,000\nÀ moi\n", encoding="utf-8")
    again = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS).json()
    assert builder.requests[-1].subtitle_tracks == (
        ("Transcription", "/Volumes/Rushs/Sommets/Été_TIMELINE_FR.srt"),
        ("Plans", "/Volumes/Rushs/Été_TIMELINE_SHOTS_FR.srt"),
    )
    assert shots.read_text(encoding="utf-8").endswith("À moi\n")
    assert [f["status"] for f in again["subtitle_files"]] == ["written"] * 5


def test_tracks_no_folder_takes_on_another_computer(
    client: TestClient, builder: FakeBuilder, tmp_path: Path
) -> None:
    ids = _analysed(client, tmp_path)
    mountain, holiday = _on_disk(tmp_path)
    for folder in (mountain.parent, holiday.parent):  # a file of the user's of that name in each
        folder.joinpath("Été_TIMELINE_FR.srt").write_text("À moi\n", encoding="utf-8")
    _on_another_computer(client, tmp_path)
    body = {"video_ids": ids, "name": "Été"}
    built = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS).json()
    # The videos' own files go into the bin instead, to lay by hand.
    (request,) = builder.requests
    assert request.subtitle_tracks == ()
    assert request.subtitle_files == ("/Volumes/Rushs/vacances_FR.srt",)
    assert built["subtitles_laid"] == []
    assert [(f["file"], f["status"], f["folder"]) for f in built["subtitle_files"]] == [
        ("vacances_FR.srt", "written", str(holiday.parent)),
        ("Été_TIMELINE_FR.srt", "conflict", str(holiday.parent)),
    ]
    # Their disk gone, nothing is written, and all is said.
    shutil.rmtree(holiday.parent)
    built = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS).json()
    gone = builder.requests[-1]
    assert (gone.subtitle_tracks, gone.subtitle_files) == ((), ())
    assert [(f["file"], f["status"]) for f in built["subtitle_files"]] == [
        ("vacances_FR.srt", "failed"), ("Été_TIMELINE_FR.srt", "failed"),
    ]  # fmt: skip


def test_a_folder_that_cannot_be_written(
    client: TestClient, builder: FakeBuilder, tmp_path: Path
) -> None:
    ids = _analysed(client, tmp_path)  # the videos' folders do not exist: their disk is gone
    body = {"video_ids": ids, "shots": True}
    response = client.post("/api/v1/resolve/timelines", json=body, headers=HEADERS)
    assert response.status_code == 201, response.text
    files = response.json()["subtitle_files"]
    assert [(f["status"], f["detail"]) for f in files] == [
        ("failed", "dossier introuvable (déplacé ou disque déconnecté)")
    ] * 3
    # The timeline's own tracks are laid all the same (they are in the data folder).
    assert [name for name, _ in builder.requests[0].subtitle_tracks] == ["Transcription", "Plans"]


def test_its_own_file_is_brought_up_to_date(client: TestClient, tmp_path: Path) -> None:
    db = _container(client).db
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    said = SubtitleTrack("transcript", "Transcription", (Cue(1.0, 2.0, ("Bonjour",)),), "fr")
    later = SubtitleTrack("transcript", "Transcription", (Cue(1.0, 2.0, ("Bonsoir",)),), "fr")
    assert write_subtitles(db, "v1", video, said).status == WriteStatus.WRITTEN
    done = write_subtitles(db, "v1", video, later)
    assert (done.status, done.path) == (WriteStatus.WRITTEN, tmp_path / "clip_FR.srt")
    assert done.path.read_bytes() == b"1\n00:00:01,000 --> 00:00:02,000\nBonsoir\n"
    # Another video of the folder with the same stem: each its own name, as the analysis file.
    (tmp_path / "clip.mov").write_bytes(b"")
    shots = SubtitleTrack("shots", "Shots", (Cue(0.0, 5.0, ("A harbour",)),), "en")
    assert write_subtitles(db, "v1", video, shots).path.name == "clip.mp4_SHOTS_EN.srt"
    assert write_subtitles(db, "v1", video, said).path.name == "clip.mp4_FR.srt"


def test_a_file_holding_what_would_be_written_is_taken_as_ours(
    client: TestClient, tmp_path: Path
) -> None:
    db = _container(client).db
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    said = SubtitleTrack("transcript", "Transcription", (Cue(1.0, 2.0, ("Bonjour",)),), "fr")
    later = SubtitleTrack("transcript", "Transcription", (Cue(1.0, 2.0, ("Bonsoir",)),), "fr")
    # Written by the application on another computer sharing the folder: no record here.
    there = tmp_path / "clip_FR.srt"
    there.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
    assert write_subtitles(db, "v1", video, said).status == WriteStatus.WRITTEN
    # Ours from then on: brought up to date.
    assert write_subtitles(db, "v1", video, later).status == WriteStatus.WRITTEN
    assert there.read_bytes().endswith(b"Bonsoir\n")
    # One holding something else is left as it is.
    other = SubtitleTrack("shots", "Plans", (Cue(0.0, 5.0, ("Un port",)),), "fr")
    users = tmp_path / "clip_SHOTS_FR.srt"
    users.write_bytes(b"1\n00:00:00,000 --> 00:00:05,000\nA moi\n")
    assert write_subtitles(db, "v1", video, other).status == WriteStatus.CONFLICT
    assert users.read_bytes().endswith(b"A moi\n")


def test_a_file_named_without_its_language_goes_when_replaced(
    client: TestClient, tmp_path: Path
) -> None:
    db = _container(client).db
    folder = tmp_path / "Rushs"
    folder.mkdir()
    video = folder / "clip.mp4"
    video.write_bytes(b"")
    cues = (Cue(1.0, 2.0, ("Bonjour",)),)
    # What an earlier version wrote: « clip.srt », « clip_SHOTS.srt ».
    for part in ("transcript", "shots"):
        assert write_subtitles(db, "v1", video, SubtitleTrack(part, "x", cues)).status == (
            WriteStatus.WRITTEN
        )
    (folder / "clip_SHOTS.srt").write_text("changé à la main", encoding="utf-8")
    for part, language in (("transcript", "fr"), ("shots", "en")):
        write_subtitles(db, "v1", video, SubtitleTrack(part, "x", cues, language))
    names = sorted(p.name for p in folder.iterdir())
    # Ours unchanged: replaced by the named one. Changed by hand since: left.
    assert names == ["clip.mp4", "clip_FR.srt", "clip_SHOTS.srt", "clip_SHOTS_EN.srt"]


def test_a_timeline_in_the_language_of_the_interface(
    client: TestClient, builder: FakeBuilder, tmp_path: Path
) -> None:
    ids = _analysed(client, tmp_path)
    _on_disk(tmp_path)
    english = {**HEADERS, "X-VFE-Language": "en"}
    body = {"video_ids": ids, "shots": True, "name": "Summer"}
    files = _unzip(
        client.post("/api/v1/videos/export-timeline", json=body, headers=english).content
    )
    assert {"README.txt", "Summer_FR.srt", "Summer_SHOTS_EN.srt"} <= set(files)
    timeline = json.loads(files["Summer.otio"])
    names = {m["name"] for clip in timeline["tracks"]["children"][0]["children"]
             for m in clip["markers"]}  # fmt: skip
    assert "Establishing shot" in names
    assert any(name.startswith("Highlight 1 — ") for name in names)
    built = client.post("/api/v1/resolve/timelines", json=body, headers=english).json()
    assert built["subtitles_laid"] == ["Transcript", "Shots"]
    assert [name for name, _ in builder.requests[0].subtitle_tracks] == ["Transcript", "Shots"]
    assert [f["file"] for f in built["subtitle_files"]] == [
        "montagne_SHOTS_EN.srt", "vacances_FR.srt", "vacances_SHOTS_EN.srt",
    ]  # fmt: skip
