"""The analysis file next to each video: written after an analysis and on demand,
imported first when a video has no analysis yet, its images extracted again from the video."""

from __future__ import annotations

import errno
import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
import sqlalchemy as sa
import structlog
from fastapi.testclient import TestClient

from tests.conftest import FakeLmStudio
from tests.integration.test_scan_and_worker import _add_root, _age, _run_until_idle
from vfe_vision.adapters.ffmpeg.tools import Ffmpeg
from vfe_vision.adapters.imaging import read_image, write_jpeg
from vfe_vision.api.app import create_app
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings
from vfe_vision.core.paths import path_key
from vfe_vision.db.models import (
    AudioScene,
    AudioSegment,
    AudioStats,
    ContextPlace,
    ContextSun,
    ContextWeather,
    Detection,
    Event,
    FrameAnalysis,
    GpsPoint,
    Job,
    Keyframe,
    LibraryRoot,
    OcrText,
    SearchChunk,
    Shot,
    ShotStory,
    StageRun,
    SubjectScan,
    Transcript,
    TranscriptSegment,
    Translation,
    Video,
    VideoMetadata,
    VideoSignals,
    VideoSynthesis,
)
from vfe_vision.db.preferences import update_preferences
from vfe_vision.db.session import Database
from vfe_vision.db.translations import dictionary_for, store_entries, video_texts
from vfe_vision.domain.enums import JobStatus, Orientation, StageStatus, VideoStatus
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.sidecar import FORMAT, SidecarStatus
from vfe_vision.jobs import sidecars
from vfe_vision.jobs.scan import register_file, scan_root
from vfe_vision.pipeline.runner import latest_runs
from vfe_vision.pipeline.sidecar import writer
from vfe_vision.pipeline.sidecar.reader import find_sidecar, import_sidecar
from vfe_vision.pipeline.sidecar.writer import write_sidecar
from vfe_vision.pipeline.stage import StageContext, Toolbox, VideoRef, input_key
from vfe_vision.pipeline.stages import default_registry
from vfe_vision.services import audio_text, synthesis
from vfe_vision.services.container import AppContainer
from vfe_vision.storage.artifacts import ArtifactStore

AT = datetime(2025, 7, 14, 16, 30, tzinfo=UTC)
HEADERS = {"X-VFE-Client": "tests"}


class _Sink:
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


# ---------------------------------------------------------------- helpers
def _library(db: Database, base: Path) -> str:
    base.mkdir(parents=True, exist_ok=True)
    with db.write() as session:
        root = LibraryRoot(path=str(base), path_key=path_key(base), label="Rushs")
        session.add(root)
        session.flush()
        return root.id


def _video(db: Database, root_id: str, source: Path, target: Path) -> str:
    """A copy of ``source`` registered in the library (real fingerprint and size)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return register_file(db, root_id, target)


def _ctx(settings: Settings, db: Database, video_id: str) -> StageContext:
    with db.read() as session:
        video = session.get_one(Video, video_id)
        ref = VideoRef(
            id=video.id, path=Path(video.path), filename=video.filename,
            fingerprint=video.fingerprint, duration_s=video.duration_s,
        )  # fmt: skip
    tools = cast(
        Toolbox,
        SimpleNamespace(
            db=db,
            artifacts=ArtifactStore(settings.artifacts_dir),
            ffmpeg=Ffmpeg(settings.ffmpeg_path, settings.ffprobe_path),
        ),
    )
    return StageContext(
        video=ref,
        prefs=AnalysisPreferences(),
        tools=tools,
        cancel=CancelToken(),
        progress=lambda _f, _m: None,
        log=structlog.get_logger("test"),
    )


def _analysed(db: Database, video_id: str) -> None:
    """Just enough for a file to be worth writing: one finished stage."""
    with db.write() as session:
        session.add(
            StageRun(
                video_id=video_id, stage="probe", stage_version=2, cache_key="k", input_key="i",
                status=StageStatus.SUCCEEDED, summary={"retryable": False},
            )
        )  # fmt: skip


def _populate(settings: Settings, db: Database, video_id: str) -> None:
    """Every kind of analysis a video can have, with rows naming each other."""
    store = ArtifactStore(settings.artifacts_dir)
    with db.write() as session:
        video = session.get_one(Video, video_id)
        path = Path(video.path)
        _video_columns(video)
        session.add_all(_context_rows(video_id, path))
        shots = [
            Shot(
                video_id=video_id, idx=i, start_s=3.0 * i, end_s=3.0 * i + 3, boundary=b,
                motion="static", motion_score=0.1, stability=0.9, metrics={"luma": 0.5 + i / 10},
            )
            for i, b in enumerate(("start", "cut"))
        ]  # fmt: skip
        session.add_all(shots)
        session.flush()
        frames = []
        for idx, (t_s, shot, reason) in enumerate(
            [(0.0, shots[0], "first"), (2.0, shots[0], "interval"), (4.0, shots[1], "scene")]
        ):
            image = store.subdir(video_id, "keyframes/g0") / f"kf_{idx:04d}.jpg"
            write_jpeg(image, np.full((240, 320, 3), 60 * idx, np.uint8))
            frames.append(
                Keyframe(
                    video_id=video_id, idx=idx, t_s=t_s, image_path=store.rel(image),
                    thumb_path=store.rel(image), width=320, height=240, selection_reason=reason,
                    phash=f"{idx:016x}", sharpness=10.0 + idx, shot_id=shot.id,
                    metrics={"exposure": {"mean": 0.4}},
                )
            )  # fmt: skip
        session.add_all(frames)
        session.flush()
        frames[1].duplicate_of = frames[0].id  # the same image as the first one
        session.add_all(_keyframe_rows(video_id, frames))
        session.add_all(_sound_rows(video_id))
        session.flush()
        session.add(_story(video_id, shots[1], frames[2]))
        session.add(_synthesis(video_id, frames))
    _runs(settings, db, video_id)


def _video_columns(video: Video) -> None:
    video.duration_s, video.width, video.height, video.fps = 6.0, 320, 240, 25.0
    video.video_codec, video.audio_codec, video.has_audio = "h264", "aac", True
    video.orientation = Orientation.HORIZONTAL
    video.captured_at, video.captured_at_source = AT, "QuickTime:CreateDate"
    video.captured_at_confidence, video.capture_timezone = "medium", "Europe/Paris"
    video.latitude, video.longitude, video.location_source = 48.8584, 2.2945, "exif"
    video.camera_make, video.camera_model = "samsung", "SM-S948B"
    video.title, video.rating, video.favorite, video.user_notes = "Tour Eiffel", 4, True, "à monter"


def _context_rows(video_id: str, path: Path) -> list[Any]:
    return [
        VideoMetadata(
            video_id=video_id,
            probe={"format": {"filename": str(path), "duration": "6.0"}},
            exif={"SourceFile": path.as_posix(), "System:Directory": path.parent.as_posix()},
            normalized={"probe": {"video_codec": "h264", "pix_fmt": "yuv420p"}},
        ),
        GpsPoint(video_id=video_id, t_s=0.0, utc=AT, latitude=48.8584, longitude=2.2945,
                 source="exif"),
        GpsPoint(video_id=video_id, t_s=3.0, utc=None, latitude=48.8585, longitude=2.2946,
                 altitude_m=35.0, speed_mps=1.2, source="gpx"),
        VideoSignals(video_id=video_id, visual={"t": [0.0, 1.0], "luma": [0.5, 0.6]},
                     audio={"t": [0.0], "lufs": [-20.0]}),
        AudioStats(video_id=video_id, integrated_lufs=-20.0, true_peak_dbfs=-1.0,
                   silences=[[1.0, 1.5]]),
        ContextPlace(video_id=video_id, source="nominatim", label="Paris, Île-de-France, France",
                     locality="Paris", country="France", country_code="fr",
                     data={"attribution": "© OpenStreetMap"}),
        ContextSun(video_id=video_id, at_utc=AT, elevation_deg=26.0, azimuth_deg=270.0,
                   light_phase="day", day_part="afternoon", data={"sunset": "21:55"}),
        ContextWeather(video_id=video_id, source="historical_forecast", at_utc=AT,
                       weather_code=1, category="clear", temperature_c=24.0,
                       data={"values": {"temperature_c": 24.0}}, fetched_at=AT),
    ]  # fmt: skip


def _keyframe_rows(video_id: str, frames: list[Keyframe]) -> list[Any]:
    first, _, last = frames
    return [
        FrameAnalysis(keyframe_id=first.id, model="qwen/qwen3-vl-8b",
                      prompt_version="frame_analysis.v2", schema_version=1, language="fr",
                      data={"caption": "Une passante devant la tour."}),
        FrameAnalysis(keyframe_id=last.id, model="qwen/qwen3-vl-8b",
                      prompt_version="frame_analysis.v2", schema_version=1, focus="la tour",
                      language="fr", data={"caption": "Un panneau « PARIS »."}),
        Detection(keyframe_id=first.id, video_id=video_id, t_s=0.0, source="detector", idx=0,
                  label="person", category="person", box=[0.1, 0.2, 0.4, 0.9], score=0.91,
                  main=True, model="dfine"),
        Detection(keyframe_id=first.id, video_id=video_id, t_s=0.0, source="faces", idx=0,
                  label="face", category="person", box=[0.2, 0.2, 0.3, 0.35], score=0.8,
                  points=[[0.22, 0.25]] * 5, model="yunet"),
        SubjectScan(keyframe_id=first.id, source="vlm", video_id=video_id, model="m", found=1),
        SubjectScan(keyframe_id=last.id, source="vlm", video_id=video_id, model="m", found=0),
        OcrText(keyframe_id=last.id, video_id=video_id, t_s=4.0, idx=0, text="PARIS",
                score=0.98, box=[[0.1, 0.1], [0.5, 0.1], [0.5, 0.2], [0.1, 0.2]],
                engine="ppocr"),
    ]  # fmt: skip


def _sound_rows(video_id: str) -> list[Any]:
    words = [[1.0, 1.4, " Bonjour", 0.9], [1.4, 1.8, " à", 0.8], [1.8, 2.5, " tous.", 0.95]]
    return [
        Transcript(video_id=video_id, source="asr", status="ok", model="whisper/large-v3-turbo",
                   language="fr", language_probability=0.99, speech_s=1.5,
                   text="Bonjour à tous.", segment_count=1, word_count=3,
                   params={"schema_version": 1}),
        TranscriptSegment(video_id=video_id, idx=0, start_s=1.0, end_s=2.5,
                          text=" Bonjour à tous.", language="fr", avg_logprob=-0.2,
                          words=words),
        AudioScene(video_id=video_id, model="yamnet", speech_s=1.5, music_s=0.0,
                   dominant="speech",
                   data={"presence": {"speech": 0.25}, "shots": [{"shot_idx": 0, "heard": []}]}),
        AudioSegment(video_id=video_id, kind="segment", category="speech", start_s=1.0,
                     end_s=2.5, score=0.8),
        AudioSegment(video_id=video_id, kind="event", category="vehicle", label="Bus",
                     start_s=4.5, end_s=5.5, score=0.6),
    ]  # fmt: skip


def _story(video_id: str, shot: Shot, frame: Keyframe) -> ShotStory:
    return ShotStory(
        video_id=video_id, shot_id=shot.id, part=1, parts=1, start_s=3.0, end_s=6.0,
        frame_times=[4.0, 5.0], keyframe_ids=[frame.id, None],
        frame_paths=[frame.thumb_path, f"{video_id[-2:]}/{video_id}/shot_frames/g/0000005.000.jpg"],
        model="qwen/qwen3-vl-8b", prompt_version="shot_story.v1", schema_version=1,
        language="fr", answer={"summary": "Un bus passe."},
        story={"summary": "Un bus passe devant la tour.", "main_action": "un bus passe",
               "notes": [{"t_s": 5.0, "what": "le bus entre"}], "possible_cut": False},
    )  # fmt: skip


def _synthesis(video_id: str, frames: list[Keyframe]) -> VideoSynthesis:
    return VideoSynthesis(
        video_id=video_id, schema_version=1, rules_version="r1", model="qwen/qwen3-vl-8b",
        prompt_version="synthesis.v2", language="fr", strategy="single", input_variant="V4",
        proofread=True, input_key="ik",
        data={
            "title": "Devant la tour Eiffel", "logline": "Une passante, un bus.", "chapters": [],
            "moments": [], "tags": [{"label": "Paris", "source": "llm"}],
            "blocks": [
                {"no": 1, "start": 0.0, "end": 3.0, "shots": [0],
                 "keyframe_ids": [frames[0].id, frames[1].id]},
                {"no": 2, "start": 3.0, "end": 6.0, "shots": [1], "keyframe_ids": [frames[2].id]},
            ],
        },
    )  # fmt: skip


def _runs(settings: Settings, db: Database, video_id: str) -> None:
    """A finished run of every stage, with the input keys the stages compute."""
    ctx = _ctx(settings, db, video_id)
    rows = [
        StageRun(
            video_id=video_id, stage=stage.name, stage_version=stage.version,
            cache_key=f"k-{stage.name}",
            input_key=input_key(stage, fingerprint=ctx.video.fingerprint,
                                facts=stage.input_facts(ctx)),
            status=StageStatus.SUCCEEDED, summary={"retryable": False}, started_at=AT,
            finished_at=AT, duration_ms=12,
        )
        for stage in default_registry().plan()
    ]  # fmt: skip
    with db.write() as session:
        session.add_all(rows)


def _by_idx(db: Database, model: Any, video_id: str) -> dict[int, Any]:
    with db.read() as session:
        rows: list[Any] = list(
            session.execute(sa.select(model).where(model.video_id == video_id)).scalars()
        )
        return {row.idx: row for row in rows}


def _count(db: Database, model: Any, video_id: str) -> int:
    with db.read() as session:
        return int(
            session.execute(
                sa.select(sa.func.count()).select_from(model).where(model.video_id == video_id)
            ).scalar_one()
        )


@pytest.fixture
def two_copies(settings: Settings, db: Database, sample_video: Path, tmp_path: Path) -> list[str]:
    """The same clip in two folders of the library: A analysed, B not yet."""
    root_id = _library(db, tmp_path / "Bibliothèque")
    first = _video(db, root_id, sample_video, tmp_path / "Bibliothèque" / "A" / "clip.mp4")
    second = _video(db, root_id, sample_video, tmp_path / "Bibliothèque" / "B" / "clip.mp4")
    return [first, second]


# ---------------------------------------------------------------- after an analysis, then first
@pytest.mark.anyio
async def test_an_analysis_is_written_next_to_the_video_then_imported_first(
    settings: Settings, db: Database, library_folder: Path, fake_lmstudio: FakeLmStudio
) -> None:
    video_file = next(library_folder.iterdir())
    _age(video_file)
    root_id = _add_root(db, library_folder)
    scan_root(db, root_id)
    await _run_until_idle(settings, db, fake_lmstudio)

    # One file per language: written in French, translated into English.
    assert sorted(p.name for p in library_folder.iterdir()) == [
        "plan séquence.mp4", "plan séquence_EN.txt", "plan séquence_FR.txt",
    ]  # fmt: skip
    english = json.loads((library_folder / "plan séquence_EN.txt").read_bytes())
    assert english["language"] == "en"
    assert english["frame_analyses"][0]["data"]["caption"].endswith(" [en]")
    sidecar = library_folder / "plan séquence_FR.txt"
    text = sidecar.read_bytes().decode("utf-8")
    document = json.loads(text)
    assert list(document)[:7] == [
        "format", "format_version", "app_version", "exported_at", "video", "user", "stage_runs",
    ]  # fmt: skip
    assert document["format"] == FORMAT
    assert document["language"] == "fr"
    assert document["exported_at"] == english["exported_at"]  # written together
    assert document["video"]["filename"] == "plan séquence.mp4"
    assert "\r" not in text
    # The search index is derived data: neither its passages nor its success are in the file.
    assert not {"search_chunks", "chunk_vectors"} & document.keys()
    assert "index" not in {run["stage"] for run in document["stage_runs"]}
    # No image, no absolute path (the folder is « Rushs été »), no library bookkeeping.
    for absent in ("Rushs été", "Rushs \\u00e9t\\u00e9", "image_path", "thumb_path", ".jpg"):
        assert absent not in text
    with db.read() as session:
        old = session.execute(sa.select(Video)).scalar_one()
    store = ArtifactStore(settings.artifacts_dir)
    before = _snapshot(db, old.id)
    images = {k[1]: read_image(store.resolve(k[-1])) for k in before["keyframes"]}

    # The database is lost: the clip is found again as a new video and analysed.
    with db.write() as session:
        session.execute(sa.delete(Video))
    shutil.rmtree(settings.artifacts_dir)
    fake_lmstudio.chat_requests.clear()
    scan_root(db, root_id)
    with db.read() as session:
        job = session.execute(sa.select(Job)).scalar_one()
    await _run_until_idle(settings, db, fake_lmstudio)

    with db.read() as session:
        done = session.get_one(Job, job.id)
        video = session.get_one(Video, job.video_id)
        redone = list(
            session.execute(sa.select(StageRun.stage).where(StageRun.job_id == job.id)).scalars()
        )
        kinds = [e.type for e in session.execute(sa.select(Event)).scalars()]
    assert done.status == JobStatus.SUCCEEDED, done.error
    assert done.message == "Analyses reprises de « plan séquence_FR.txt »"
    assert video.id != old.id
    assert video.status == VideoStatus.READY
    assert "sidecar.imported" in kinds
    # Every stage kept, nothing analysed again: only derived data that the file does not hold
    # is made again, the search index and the translations, all known already here.
    assert redone == ["translation", "index"]
    assert _count(db, SearchChunk, video.id) > 0  # searchable again
    assert fake_lmstudio.chat_requests == []
    after = _snapshot(db, video.id)
    assert {k: v for k, v in after.items() if k != "keyframes"} == {
        k: v for k, v in before.items() if k != "keyframes"
    }
    assert [k[:-1] for k in after["keyframes"]] == [k[:-1] for k in before["keyframes"]]
    # The images are extracted again at the stored times, exactly as the stage made them.
    for key in after["keyframes"]:
        again = read_image(store.resolve(key[-1]))
        assert np.array_equal(again, images[key[1]]), f"keyframe {key[1]} at {key[2]} s"
    assert video.poster_path is not None
    assert store.resolve(video.poster_path).is_file()
    view = synthesis.get_synthesis(AppContainer.create(settings), video.id)
    assert view.row is not None
    assert not view.stale  # written from the same analyses
    assert json.loads(sidecar.read_bytes())["video"]["fingerprint"] == video.fingerprint


def _snapshot(db: Database, video_id: str) -> dict[str, Any]:
    """What the video's analyses say (ids included: they are kept when free)."""
    with db.read() as session:
        video = session.get_one(Video, video_id)
        shots = session.execute(sa.select(Shot).where(Shot.video_id == video_id)).scalars()
        keyframes = session.execute(
            sa.select(Keyframe).where(Keyframe.video_id == video_id).order_by(Keyframe.idx)
        ).scalars()
        analyses = session.execute(
            sa.select(FrameAnalysis.keyframe_id, FrameAnalysis.data)
            .join(Keyframe, FrameAnalysis.keyframe_id == Keyframe.id)
            .where(Keyframe.video_id == video_id)
        ).all()
        stored = session.get(VideoSynthesis, video_id)
        place = session.get_one(ContextPlace, video_id)
        signals = session.get_one(VideoSignals, video_id)
        return {
            "video": (video.duration_s, video.width, video.captured_at, video.latitude,
                      video.capture_timezone, video.camera_make, video.orientation),
            "shots": sorted((s.id, s.idx, s.start_s, s.end_s, s.motion, s.metrics) for s in shots),
            "keyframes": [
                (k.id, k.idx, k.t_s, k.selection_reason, k.shot_id, k.phash, k.sharpness,
                 k.metrics, k.width, k.height, k.image_path)
                for k in keyframes
            ],
            "analyses": dict(analyses),
            "synthesis": stored.data if stored else None,
            # The translations are made again from the dictionary: nothing asked, other counts.
            "runs": {
                r.stage: (r.status, r.stage_version, r.cache_key, r.input_key,
                          None if r.stage == "translation" else r.summary)
                for r in latest_runs(session, video_id)
            },
            "context": (place.label, signals.visual),
        }  # fmt: skip


# ---------------------------------------------------------------- every table, new ids
def test_the_same_video_twice_gets_new_ids_linked_again(
    settings: Settings, db: Database, two_copies: list[str]
) -> None:
    first, second = two_copies
    _populate(settings, db, first)
    written = write_sidecar(db, first)
    assert written.status == SidecarStatus.WRITTEN
    assert written.path is not None
    assert written.path.name == "clip_FR.txt"
    text = written.path.read_bytes().decode("utf-8")
    assert "Bibliothèque" not in text  # the absolute paths of ffprobe and ExifTool are gone
    assert '"SourceFile": "clip.mp4"' in text
    copy = written.path.parents[1] / "B" / "clip_FR.txt"  # the folder was copied with its file
    shutil.copy2(written.path, copy)

    registry = default_registry()
    ctx = _ctx(settings, db, second)
    report = import_sidecar(ctx, registry)
    assert report is not None
    assert (report.imported, report.fresh_ids, report.keyframes) == (True, True, 3)
    # Every run but those of the viewing copy, the translations and the search index: results
    # the analysis file does not hold, made again.
    assert report.stages == len(registry.names) - 3
    assert ctx.video.duration_s == 6.0

    a_frames, b_frames = _by_idx(db, Keyframe, first), _by_idx(db, Keyframe, second)
    a_shots, b_shots = _by_idx(db, Shot, first), _by_idx(db, Shot, second)
    assert not {k.id for k in a_frames.values()} & {k.id for k in b_frames.values()}
    b_shot_idx = {s.id: idx for idx, s in b_shots.items()}
    a_shot_idx = {s.id: idx for idx, s in a_shots.items()}
    for idx, frame in b_frames.items():
        assert frame.t_s == a_frames[idx].t_s
        assert b_shot_idx[frame.shot_id] == a_shot_idx[a_frames[idx].shot_id]
        assert frame.selection_reason == a_frames[idx].selection_reason
    assert b_frames[1].duplicate_of == b_frames[0].id
    _assert_linked(db, second, b_frames, b_shots)
    _assert_images(settings, db, second, b_frames)
    _assert_same_facts(settings, db, first, second)
    # The stages naming rows got their input key from the imported rows: « Complete » keeps
    # them; the others kept theirs.
    with db.read() as session:
        a_runs = {r.stage: r for r in latest_runs(session, first)}
        b_runs = {r.stage: r for r in latest_runs(session, second)}
    assert "proxy" not in b_runs
    for stage in registry.plan():
        if stage.rebuilt_after_import:
            continue
        b_run = b_runs[stage.name]
        assert b_run.cache_key == a_runs[stage.name].cache_key
        assert b_run.status == StageStatus.SUCCEEDED
        expected = input_key(stage, fingerprint=ctx.video.fingerprint, facts=stage.input_facts(ctx))
        assert b_run.input_key == expected, stage.name
        if stage.name in {"technical", "ocr", "detections", "grounding"}:  # they name rows here
            assert stage.facts_name_rows
            assert b_run.input_key != a_runs[stage.name].input_key, stage.name
    assert _count(db, Keyframe, first) == 3  # the first copy is untouched


def _assert_linked(
    db: Database, video_id: str, frames: dict[int, Keyframe], shots: dict[int, Shot]
) -> None:
    idx_of = {k.id: idx for idx, k in frames.items()}
    with db.read() as session:
        analyses = session.execute(
            sa.select(FrameAnalysis.keyframe_id, FrameAnalysis.focus, FrameAnalysis.data)
        ).all()
        mine = {idx_of[a.keyframe_id]: (a.focus, a.data["caption"]) for a in analyses
                if a.keyframe_id in idx_of}  # fmt: skip
        boxes = session.execute(
            sa.select(Detection).where(Detection.video_id == video_id).order_by(Detection.source)
        ).scalars()
        scans = session.execute(
            sa.select(SubjectScan).where(SubjectScan.video_id == video_id)
        ).scalars()
        lines = session.execute(sa.select(OcrText).where(OcrText.video_id == video_id)).scalars()
        story = session.execute(
            sa.select(ShotStory).where(ShotStory.video_id == video_id)
        ).scalar_one()
        stored = session.get_one(VideoSynthesis, video_id)
        assert mine == {
            0: (None, "Une passante devant la tour."),
            2: ("la tour", "Un panneau « PARIS »."),
        }
        assert [(idx_of[b.keyframe_id], b.source, b.box, b.points) for b in boxes] == [
            (0, "detector", [0.1, 0.2, 0.4, 0.9], None),
            (0, "faces", [0.2, 0.2, 0.3, 0.35], [[0.22, 0.25]] * 5),
        ]
        assert {(idx_of[s.keyframe_id], s.found) for s in scans} == {(0, 1), (2, 0)}
        assert [(idx_of[line.keyframe_id], line.text) for line in lines] == [(2, "PARIS")]
        assert story.shot_id == shots[1].id
        assert story.frame_times == [4.0, 5.0]
        assert story.keyframe_ids == [frames[2].id, None]
        assert story.frame_paths[0] == frames[2].thumb_path
        assert "/shot_frames/" in story.frame_paths[1]
        assert story.story["notes"] == [{"t_s": 5.0, "what": "le bus entre"}]
        assert [b["keyframe_ids"] for b in stored.data["blocks"]] == [
            [frames[0].id, frames[1].id],
            [frames[2].id],
        ]


def _assert_images(
    settings: Settings, db: Database, video_id: str, frames: dict[int, Keyframe]
) -> None:
    store = ArtifactStore(settings.artifacts_dir)
    for frame in frames.values():
        image = read_image(store.resolve(frame.image_path))
        assert image.shape[:2] == (frame.height, frame.width) == (960, 1280)
        assert max(read_image(store.resolve(frame.thumb_path)).shape[:2]) == 320
    with db.read() as session:
        story = session.execute(
            sa.select(ShotStory).where(ShotStory.video_id == video_id)
        ).scalar_one()
        poster = session.get_one(Video, video_id).poster_path
    extra = read_image(store.resolve(story.frame_paths[1]))  # extracted again at 5.0 s
    assert max(extra.shape[:2]) == 512
    assert poster == frames[0].thumb_path  # nearest to 10% of 6 s


def _assert_same_facts(settings: Settings, db: Database, first: str, second: str) -> None:
    with db.read() as session:
        a, b = session.get_one(Video, first), session.get_one(Video, second)
        for name in ("duration_s", "captured_at", "latitude", "capture_timezone", "orientation",
                     "camera_model", "title", "rating", "favorite", "user_notes"):  # fmt: skip
            assert getattr(b, name) == getattr(a, name), name
        for model in (VideoMetadata, VideoSignals, AudioStats, ContextPlace, ContextSun,
                      ContextWeather, AudioScene):  # fmt: skip
            row_a, row_b = session.get_one(model, first), session.get_one(model, second)
            assert getattr(row_b, "data", None) == getattr(row_a, "data", None)
        weather = session.get_one(ContextWeather, second)
        assert (weather.fetched_at, weather.category) == (AT, "clear")
        points = session.execute(
            sa.select(GpsPoint.t_s, GpsPoint.utc, GpsPoint.altitude_m, GpsPoint.source)
            .where(GpsPoint.video_id == second)
            .order_by(GpsPoint.id)
        ).all()
        assert [tuple(p) for p in points] == [(0.0, AT, None, "exif"), (3.0, None, 35.0, "gpx")]
        segments = session.execute(
            sa.select(AudioSegment.category, AudioSegment.label).where(
                AudioSegment.video_id == second
            )
        ).all()
        assert sorted(tuple(s) for s in segments) == [("speech", None), ("vehicle", "Bus")]
    transcript = audio_text.get_transcript(AppContainer.create(settings), second).transcript
    assert transcript is not None
    assert [s.words for s in transcript.segments] == [
        [[1.0, 1.4, " Bonjour", 0.9], [1.4, 1.8, " à", 0.8], [1.8, 2.5, " tous.", 0.95]]
    ]


# ---------------------------------------------------------------- left aside
def test_a_file_for_another_content_is_left_aside(
    settings: Settings, db: Database, two_copies: list[str]
) -> None:
    first, second = two_copies
    _populate(settings, db, first)
    written = write_sidecar(db, first)
    assert written.path is not None
    document = json.loads(written.path.read_bytes())
    document["video"]["fingerprint"] = "0" * 32  # another take under the same name
    target = written.path.parents[1] / "B" / "clip_FR.txt"
    target.write_bytes(json.dumps(document).encode("utf-8"))

    sink = _Sink()
    report = sidecars.import_first(
        _ctx(settings, db, second), default_registry(), sink, job_id=None
    )
    assert report is not None
    assert not report.imported
    [(kind, data)] = sink.events
    assert kind == "sidecar.ignored"
    assert data["file"] == "clip_FR.txt"
    assert "autre contenu" in data["reason"]
    assert _count(db, Keyframe, second) == _count(db, StageRun, second) == 0


def test_an_unusable_file_leaves_nothing_behind(
    settings: Settings, db: Database, two_copies: list[str]
) -> None:
    first, second = two_copies
    _populate(settings, db, first)
    written = write_sidecar(db, first)
    assert written.path is not None
    document = json.loads(written.path.read_bytes())
    document["keyframes"][2]["t_s"] = 999.0  # after the end: no image to extract there
    target = written.path.parents[1] / "B" / "clip_FR.txt"
    target.write_bytes(json.dumps(document).encode("utf-8"))
    store = ArtifactStore(settings.artifacts_dir)

    report = import_sidecar(_ctx(settings, db, second), default_registry())
    assert report is not None
    assert not report.imported
    assert report.reason is not None
    assert report.reason == "l'image clé à 999.000 s ne peut pas être extraite de la vidéo"
    assert _count(db, Keyframe, second) == _count(db, StageRun, second) == 0
    assert not any(store.video_dir(second).rglob("*.jpg"))  # the images extracted went too

    document["keyframes"] = "illisible"
    target.write_bytes(json.dumps(document).encode("utf-8"))
    report = import_sidecar(_ctx(settings, db, second), default_registry())
    assert report is not None
    assert report.reason == "contenu illisible : « keyframes »"


def test_analyses_already_there_are_never_replaced(
    settings: Settings, db: Database, two_copies: list[str]
) -> None:
    first, second = two_copies
    _populate(settings, db, first)
    written = write_sidecar(db, first)
    assert written.path is not None
    shutil.copy2(written.path, written.path.parents[1] / "B" / "clip_FR.txt")
    _analysed(db, second)
    assert import_sidecar(_ctx(settings, db, second), default_registry()) is None
    assert _count(db, Keyframe, second) == 0


# ---------------------------------------------------------------- writing
def test_a_foreign_txt_is_never_replaced(
    settings: Settings, db: Database, two_copies: list[str]
) -> None:
    first, _ = two_copies
    _analysed(db, first)
    with db.read() as session:
        target = Path(session.get_one(Video, first).path).with_name("clip_FR.txt")
    for foreign in (b"Notes de tournage : refaire le plan 3.\r\n", b'{"notes": ["plan 3"]}'):
        target.write_bytes(foreign)
        result = write_sidecar(db, first)
        assert result.status == SidecarStatus.CONFLICT
        assert result.path == target
        assert target.read_bytes() == foreign
    target.unlink()
    target.mkdir()  # a folder of that name is not ours either
    assert write_sidecar(db, first).status == SidecarStatus.CONFLICT
    target.rmdir()
    assert write_sidecar(db, first).status == SidecarStatus.WRITTEN
    ours = target.read_bytes()
    assert write_sidecar(db, first).status == SidecarStatus.WRITTEN  # ours: refreshed
    assert json.loads(target.read_bytes())["format"] == json.loads(ours)["format"] == FORMAT


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (PermissionError(errno.EACCES, "Accès refusé"), "écriture refusée"),
        (OSError(errno.EROFS, "Read-only file system"), "écriture refusée"),
        (OSError(errno.ENOSPC, "No space left on device"), "disque plein"),
    ],
)
def test_a_folder_that_cannot_be_written_is_reported(
    db: Database,
    two_copies: list[str],
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    detail: str,
) -> None:
    first, _ = two_copies
    _analysed(db, first)

    def refuse(path: Path, data: bytes, *, create_parents: bool = True) -> None:
        assert not create_parents  # never a folder created in the user's space
        raise error

    monkeypatch.setattr(writer, "atomic_write_bytes", refuse)
    sink = _Sink()
    result = sidecars.write_after(db, first, sink, job_id=None, log=structlog.get_logger("test"))
    assert result is not None
    assert result.status == SidecarStatus.FAILED
    assert result.detail is not None
    assert detail in result.detail
    assert sink.events == [("sidecar.failed", {"file": "clip_FR.txt", "error": result.detail})]


def test_nothing_is_written_when_turned_off_or_not_analysed(
    db: Database, two_copies: list[str]
) -> None:
    first, second = two_copies
    sink = _Sink()
    log = structlog.get_logger("test")
    result = sidecars.write_after(db, first, sink, job_id=None, log=log)
    assert result is not None
    assert result.status == SidecarStatus.NOT_ANALYZED
    _analysed(db, second)
    update_preferences(db, {"sidecar_files": False})
    assert sidecars.write_after(db, second, sink, job_id=None, log=log) is None
    assert sink.events == []
    with db.read() as session:
        folder = Path(session.get_one(Video, second).path).parent
    assert [p.name for p in folder.iterdir()] == ["clip.mp4"]


def test_two_videos_with_one_name_keep_their_extension(
    db: Database, sample_video: Path, tmp_path: Path
) -> None:
    root_id = _library(db, tmp_path / "Rushs")
    mp4 = _video(db, root_id, sample_video, tmp_path / "Rushs" / "clip.mp4")
    mov = _video(db, root_id, sample_video, tmp_path / "Rushs" / "clip.mov")
    for video_id in (mp4, mov):
        _analysed(db, video_id)
        assert write_sidecar(db, video_id).status == SidecarStatus.WRITTEN
    names = sorted(p.name for p in (tmp_path / "Rushs").iterdir())
    assert names == ["clip.mov", "clip.mov_FR.txt", "clip.mp4", "clip.mp4_FR.txt"]
    found = tmp_path / "Rushs" / "clip.mp4_FR.txt"
    assert find_sidecar(tmp_path / "Rushs" / "clip.mp4") == found
    (tmp_path / "Rushs" / "clip.mov").unlink()  # alone again: its file is still found
    assert find_sidecar(tmp_path / "Rushs" / "clip.mp4") == found


@pytest.mark.anyio
async def test_a_file_that_cannot_be_written_never_fails_the_analysis(
    settings: Settings,
    db: Database,
    library_folder: Path,
    fake_lmstudio: FakeLmStudio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(path: Path, data: bytes, *, create_parents: bool = True) -> None:
        raise PermissionError(errno.EACCES, "Accès refusé")

    monkeypatch.setattr(writer, "atomic_write_bytes", refuse)
    video_file = next(library_folder.iterdir())
    _age(video_file)
    scan_root(db, _add_root(db, library_folder))
    await _run_until_idle(settings, db, fake_lmstudio)
    with db.read() as session:
        job = session.execute(sa.select(Job)).scalar_one()
        video = session.get_one(Video, job.video_id)
        failed: dict[str, Any] = session.execute(
            sa.select(Event.data).where(Event.type == "sidecar.failed")
        ).scalar_one()
    assert job.status == JobStatus.SUCCEEDED, job.error
    assert video.status == VideoStatus.READY
    assert "écriture refusée" in failed["error"]
    assert [p.name for p in library_folder.iterdir()] == [video_file.name]


# ---------------------------------------------------------------- the « Export » button
@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


def test_the_export_endpoint(client: TestClient, sample_video: Path, tmp_path: Path) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    db = container.db
    root_id = _library(db, tmp_path / "Rushs")
    ready = _video(db, root_id, sample_video, tmp_path / "Rushs" / "prêt.mp4")
    new = _video(db, root_id, sample_video, tmp_path / "Rushs" / "nouveau.mp4")
    gone = _video(db, root_id, sample_video, tmp_path / "Rushs" / "parti.mp4")
    taken = _video(db, root_id, sample_video, tmp_path / "Rushs" / "notes.mp4")
    for video_id in (ready, gone, taken):
        _analysed(db, video_id)
    (tmp_path / "Rushs" / "parti.mp4").unlink()
    (tmp_path / "Rushs" / "notes_FR.txt").write_bytes(b"mes notes")
    body = {"video_ids": [ready, new, gone, taken, "inconnue", ready]}

    refused = client.post("/api/v1/videos/export-sidecars", json=body)
    assert refused.status_code == 403  # a write: the client header is required
    response = client.post("/api/v1/videos/export-sidecars", json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [(r["video_id"], r["status"], r["file"]) for r in results] == [
        (ready, "written", "prêt_FR.txt"),
        (new, "not_analyzed", None),
        (gone, "offline", None),
        (taken, "conflict", "notes_FR.txt"),
        ("inconnue", "unknown", None),
    ]
    assert results[0]["path"] == str(tmp_path / "Rushs" / "prêt_FR.txt")
    assert results[0]["files"] == ["prêt_FR.txt"]  # not translated: French only
    assert results[3]["detail"]
    assert json.loads((tmp_path / "Rushs" / "prêt_FR.txt").read_bytes())["format"] == FORMAT
    assert (tmp_path / "Rushs" / "notes_FR.txt").read_bytes() == b"mes notes"
    empty = client.post("/api/v1/videos/export-sidecars", json={"video_ids": []}, headers=HEADERS)
    assert empty.status_code == 422


# ---------------------------------------------------------------- one file per language
def _translate(db: Database, video_id: str) -> None:
    """The video's texts in English, as the translation stage leaves them."""
    with db.read() as session:
        texts = [source.text for source in video_texts(session, video_id)]
    with db.write() as session:
        store_entries(session, [(text, "en", f"{text} (en)") for text in texts], model="m")


def test_each_language_has_its_file_and_the_single_file_goes(
    settings: Settings, db: Database, two_copies: list[str]
) -> None:
    first, _ = two_copies
    _populate(settings, db, first)
    _translate(db, first)
    with db.read() as session:
        folder = Path(session.get_one(Video, first).path).parent
    single = folder / "clip.txt"  # what an earlier version wrote
    single.write_bytes(json.dumps({"format": FORMAT, "format_version": 1}).encode())
    result = write_sidecar(db, first)
    assert result.status == SidecarStatus.WRITTEN
    assert [p.name for p in result.paths] == ["clip_FR.txt", "clip_EN.txt"]
    assert sorted(p.name for p in folder.iterdir()) == ["clip.mp4", "clip_EN.txt", "clip_FR.txt"]
    french = json.loads((folder / "clip_FR.txt").read_bytes())
    english = json.loads((folder / "clip_EN.txt").read_bytes())
    assert french["frame_analyses"][0]["data"]["caption"] == "Une passante devant la tour."
    assert english["frame_analyses"][0]["data"]["caption"] == "Une passante devant la tour. (en)"
    assert english["frame_analyses"][0]["language"] == "en"
    assert english["video_synthesis"]["data"]["title"] == "Devant la tour Eiffel (en)"
    assert english["shot_stories"][0]["story"]["notes"][0]["what"] == "le bus entre (en)"
    assert english["context_place"]["label"] == "Paris, Île-de-France, France (en)"
    assert english["ocr_texts"][0]["text"] == "PARIS"  # as seen
    assert english["transcript_segments"][0]["text"] == " Bonjour à tous."  # as spoken
    # A « clip.txt » the application did not write stays where it is.
    (folder / "clip.txt").write_bytes(b"mes notes")
    assert write_sidecar(db, first).status == SidecarStatus.WRITTEN
    assert (folder / "clip.txt").read_bytes() == b"mes notes"


def test_the_file_of_the_other_language_gives_its_translations_back(
    settings: Settings, db: Database, two_copies: list[str]
) -> None:
    first, second = two_copies
    _populate(settings, db, first)
    _translate(db, first)
    result = write_sidecar(db, first)
    for path in result.paths:  # the folder was copied with its files
        shutil.copy2(path, path.parents[1] / "B" / path.name)
    with db.write() as session:
        session.execute(sa.delete(Translation))  # another computer: an empty dictionary

    report = import_sidecar(_ctx(settings, db, second), default_registry())
    assert report is not None
    assert report.imported, report.reason
    assert report.path.name == "clip_FR.txt"
    assert report.translations > 0
    with db.read() as session:
        texts = [source.text for source in video_texts(session, second)]
        english = dictionary_for(session, "en", texts)
    assert english("Devant la tour Eiffel") == "Devant la tour Eiffel (en)"
    assert english("le bus entre") == "le bus entre (en)"
    view = synthesis.get_synthesis(AppContainer.create(settings), second)
    assert not view.stale  # its input key read from the imported texts
