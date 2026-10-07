"""Library scan and the worker running the real pipeline (ffmpeg) with a fake LM Studio."""

from __future__ import annotations

import contextlib
import os
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
import sqlalchemy as sa

from tests.conftest import FakeLmStudio
from tests.fakes.context import FakeGeocoder, FakeWeather
from vfe_vision.api.schemas import (
    AudioCurveOut,
    ExifSummaryOut,
    KeyframeMetricsOut,
    ShotMetricsOut,
    VisualSignalsOut,
)
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import ConflictError, NotFoundError
from vfe_vision.core.paths import CASE_INSENSITIVE_PATHS
from vfe_vision.db.models import (
    AudioStats,
    ContextPlace,
    ContextSun,
    ContextWeather,
    FrameAnalysis,
    Job,
    Keyframe,
    LibraryRoot,
    Shot,
    StageRun,
    Video,
    VideoMetadata,
    VideoSignals,
)
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobKind, JobStatus, StageStatus, VideoStatus
from vfe_vision.jobs import queue, scan, worker
from vfe_vision.jobs.scan import ScanReport, scan_root
from vfe_vision.jobs.worker import Worker


def _add_root(db: Database, folder: Path, *, auto: bool = True) -> str:
    with db.write() as session:
        root = LibraryRoot(
            path=str(folder), path_key=str(folder).lower(), label="t", auto_analyze=auto
        )
        session.add(root)
        session.flush()
        return root.id


def _age(path: Path, seconds: float = 60) -> None:
    past = time.time() - seconds
    os.utime(path, (past, past))


class TestScan:
    def test_new_moved_and_offline(
        self, db: Database, library_folder: Path, tmp_path: Path
    ) -> None:
        video_file = next(library_folder.iterdir())
        _age(video_file)
        root_id = _add_root(db, library_folder)

        first = scan_root(db, root_id)
        assert (first.found, first.new, first.queued) == (1, 1, 1)
        assert scan_root(db, root_id).unchanged == 1

        # The file disappears: offline. Then it reappears under another name: relinked.
        parked = tmp_path / "parked.mp4"
        shutil.move(video_file, parked)
        assert scan_root(db, root_id).offline == 1
        renamed = library_folder / "renommé.mp4"
        shutil.move(parked, renamed)
        _age(renamed)
        report = scan_root(db, root_id)
        assert (report.moved, report.new) == (1, 0)
        with db.read() as session:
            videos = session.execute(sa.select(Video)).scalars().all()
        assert len(videos) == 1
        assert videos[0].filename == "renommé.mp4"

    def test_a_clip_moved_into_a_bin_keeps_its_analyses(
        self, db: Database, library_folder: Path
    ) -> None:
        video_file = next(library_folder.iterdir())
        _age(video_file)
        root_id = _add_root(db, library_folder)
        scan_root(db, root_id)
        with db.write() as session:
            video = session.execute(sa.select(Video)).scalar_one()
            video.status = VideoStatus.READY
            video.last_analyzed_at = datetime.now(UTC)
            video_id = video.id
            session.execute(sa.delete(Job))  # the analysis the first scan queued

        (library_folder / "Jour 1").mkdir()
        shutil.move(video_file, library_folder / "Jour 1" / video_file.name)
        report = scan_root(db, root_id)  # one pass sees it gone and elsewhere
        assert (report.moved, report.new, report.offline, report.queued) == (1, 0, 0, 0)
        with db.read() as session:
            [video] = session.execute(sa.select(Video)).scalars().all()
            assert session.execute(sa.select(Job)).scalars().all() == []
        assert (video.id, video.status) == (video_id, VideoStatus.READY)
        assert video.rel_path == f"Jour 1/{video_file.name}"

    @pytest.mark.skipif(not CASE_INSENSITIVE_PATHS, reason="names differing by case: two folders")
    def test_a_folder_renamed_by_its_case_keeps_one_spelling(
        self, db: Database, library_folder: Path
    ) -> None:
        video_file = next(library_folder.iterdir())
        (library_folder / "été").mkdir()
        inside = library_folder / "été" / video_file.name
        shutil.move(video_file, inside)
        _age(inside)
        root_id = _add_root(db, library_folder)
        scan_root(db, root_id)
        (library_folder / "été").rename(library_folder / "Été")
        assert scan_root(db, root_id).unchanged == 1
        with db.read() as session:
            video = session.execute(sa.select(Video)).scalar_one()
        assert video.rel_path == f"Été/{video_file.name}"  # the bin filter finds it again

    def test_an_automatic_pass_leaves_another_disk_alone(
        self, db: Database, library_folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _age(next(library_folder.iterdir()))
        root_id = _add_root(db, library_folder)
        with db.write() as session:
            session.get_one(LibraryRoot, root_id).volume_serial = "CARD-A"
        monkeypatch.setattr(scan, "volume_serial", lambda _path: "CARD-B")
        report = scan_root(db, root_id, automatic=True)
        assert report.other_volume
        assert (report.found, report.new) == (0, 0)
        # « Rescan » is asked for this disk: it becomes the root's.
        assert scan_root(db, root_id).new == 1
        with db.read() as session:
            assert session.get_one(LibraryRoot, root_id).volume_serial == "CARD-B"

    def test_one_scan_of_a_root_at_a_time(self, db: Database, library_folder: Path) -> None:
        root_id = _add_root(db, library_folder)
        lock = scan._root_locks.setdefault(root_id, threading.Lock())
        with lock, pytest.raises(ConflictError):  # « Rescan » is running
            scan_root(db, root_id, automatic=True)

    def test_a_root_removed_during_a_scan(
        self, db: Database, library_folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _age(next(library_folder.iterdir()))
        root_id = _add_root(db, library_folder)
        mark_missing = scan._mark_missing

        def removed_meanwhile(db_: Database, root_id_: str, seen: set[str]) -> set[str]:
            with db_.write() as session:
                session.delete(session.get_one(LibraryRoot, root_id_))
            return mark_missing(db_, root_id_, seen)

        monkeypatch.setattr(scan, "_mark_missing", removed_meanwhile)
        with contextlib.suppress(NotFoundError):  # a clear error at worst, never a crash
            scan_root(db, root_id)
        with db.read() as session:
            assert session.execute(sa.select(Job)).scalars().all() == []

    def test_files_being_copied_are_skipped(
        self, db: Database, library_folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        video_file = next(library_folder.iterdir())

        def keep_growing(_seconds: float) -> None:  # the copy is still writing
            with video_file.open("ab") as handle:
                handle.write(b"\x00" * 1024)

        monkeypatch.setattr("vfe_vision.jobs.scan.time.sleep", keep_growing)
        report = scan_root(db, _add_root(db, library_folder))
        assert (report.skipped_in_progress, report.new) == (1, 0)

    def test_recent_but_complete_copies_are_accepted(
        self, db: Database, library_folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vfe_vision.jobs.scan.time.sleep", lambda _s: None)
        report = scan_root(db, _add_root(db, library_folder))
        assert (report.skipped_in_progress, report.new) == (0, 1)


async def _run_until_idle(
    settings: Settings, db: Database, fake: FakeLmStudio, timeout_s: float = 120
) -> None:
    """Run a worker until every queued job has reached a terminal status."""
    worker = Worker(
        settings, lmstudio=fake.client(), weather=FakeWeather(), geocoder=FakeGeocoder()
    )
    stop = anyio.Event()

    async def stop_when_idle() -> None:
        while True:
            await anyio.sleep(0.3)
            with db.read() as session:
                statuses = set(session.execute(sa.select(Job.status)).scalars())
            if statuses and all(s.is_terminal for s in statuses):
                stop.set()
                return

    with anyio.fail_after(timeout_s):
        async with anyio.create_task_group() as tg:
            tg.start_soon(worker.run, stop)
            tg.start_soon(stop_when_idle)


def _assert_stored_json_matches_api(db: Database, video_id: str) -> None:
    """Stored JSON must match the typed API contract, otherwise the API would hide it."""
    with db.read() as session:
        shots = session.execute(sa.select(Shot).where(Shot.video_id == video_id)).scalars().all()
        frames = (
            session.execute(sa.select(Keyframe).where(Keyframe.video_id == video_id))
            .scalars()
            .all()
        )
        signals = session.get_one(VideoSignals, video_id)
        meta = session.get_one(VideoMetadata, video_id)
    visual = VisualSignalsOut.model_validate(signals.visual)
    assert len(visual.luma) == len(visual.t) >= 10
    curve = AudioCurveOut.model_validate(signals.audio)
    assert len(curve.lufs) == len(curve.t) > 0
    for shot in shots:
        ShotMetricsOut.model_validate(shot.metrics)
    assert all(KeyframeMetricsOut.model_validate(f.metrics).colors for f in frames)
    assert meta.normalized is not None
    exif = ExifSummaryOut.model_validate(meta.normalized["exif"])
    assert exif.capture is not None
    assert exif.dates


@pytest.mark.anyio
async def test_worker_analyses_a_video_end_to_end(
    settings: Settings, db: Database, library_folder: Path, fake_lmstudio: FakeLmStudio
) -> None:
    video_file = next(library_folder.iterdir())
    _age(video_file)
    root_id = _add_root(db, library_folder)
    scan_root(db, root_id)  # queues the analysis
    with db.read() as session:
        job = session.execute(sa.select(Job).where(Job.kind == JobKind.ANALYZE_VIDEO)).scalar_one()

    await _run_until_idle(settings, db, fake_lmstudio)

    with db.read() as session:
        done = session.get_one(Job, job.id)
        video = session.get_one(Video, job.video_id)
        frames = (
            session.execute(sa.select(Keyframe).where(Keyframe.video_id == video.id))
            .scalars()
            .all()
        )
        analyses = session.execute(
            sa.select(sa.func.count()).select_from(FrameAnalysis)
        ).scalar_one()
        runs = {r.stage: r.status for r in session.execute(sa.select(StageRun)).scalars()}
        shots = session.execute(sa.select(Shot).order_by(Shot.idx)).scalars().all()
        audio = session.get(AudioStats, video.id)
    assert done.status == JobStatus.SUCCEEDED, done.error
    assert video.status == VideoStatus.READY
    assert video.width == 320
    assert video.poster_path is not None
    assert 3 <= len(frames) <= 6  # three shots, 2 s density floor, duplicates removed
    assert any(f.selection_reason == "scene" for f in frames)
    assert analyses == len(frames)
    stages = (
        "probe",
        "metadata",
        "place",
        "weather",
        "sun",
        "analysis_pass",
        "keyframes",
        "technical",
        "audio_levels",
        "vision_frames",
        "grounding",
        "vision_shots",
        "synthesis",
        "translation",  # nothing described in the test pattern's frames beyond the fake's
        "index",  # words only: no embedding model in the test data folder (provisional)
    )  # fmt: skip  (three 2 s shots: nothing to tell, no request)
    # Browser-playable (no viewing copy); no audio/OCR/detector models in the test data folder.
    skipped = ("proxy", "audio_events", "ocr", "detections", "transcript")
    assert runs == dict.fromkeys(stages, StageStatus.SUCCEEDED) | dict.fromkeys(
        skipped, StageStatus.SKIPPED
    )
    # Three synthetic shots (test pattern, colour bars, flat blue) cut at 2 s and 4 s.
    assert [round(s.start_s) for s in shots] == [0, 2, 4]
    assert all(f.shot_id is not None and f.metrics for f in frames)
    # GPS position → time zone → capture time (QuickTime CreateDate is UTC for this encoder).
    assert video.latitude == pytest.approx(48.8584, abs=1e-4)
    assert video.capture_timezone == "Europe/Paris"
    assert video.captured_at == datetime(2025, 7, 14, 16, 30, tzinfo=UTC)
    assert (video.captured_at_source, video.captured_at_confidence) == (
        "QuickTime:CreateDate",
        "medium",
    )
    assert audio is not None
    assert audio.integrated_lufs is not None
    # Context: place (Nominatim fake), model weather (Open-Meteo fake), sun (local).
    with db.read() as session:
        place = session.get_one(ContextPlace, video.id)
        weather = session.get_one(ContextWeather, video.id)
        sun = session.get_one(ContextSun, video.id)
    assert place.label == "Paris, Île-de-France, France"
    assert place.data["attribution"].startswith("© OpenStreetMap")
    assert (weather.source, weather.weather_code, weather.category) == (
        "historical_forecast",
        1,
        "clear",
    )
    assert weather.data["values"]["temperature_c"] == 24.0
    assert weather.data["time_confidence"] == "medium"
    # 18:30 in Paris in mid-July: the sun is ~26° high, the same phase over ± 30 min.
    assert (sun.light_phase, sun.day_part) == ("day", "afternoon")
    assert sun.data["light"]["regime"] == "sunlit"
    assert sun.theoretical_cct_k is not None
    _assert_stored_json_matches_api(db, video.id)

    # A second analysis is fully cached: no new LM Studio calls.
    calls = len(fake_lmstudio.chat_requests)
    second = queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=video.id)
    await _run_until_idle(settings, db, fake_lmstudio, timeout_s=60)
    with db.read() as session:
        assert session.get_one(Job, second.id).status == JobStatus.SUCCEEDED
    assert len(fake_lmstudio.chat_requests) == calls


@pytest.mark.anyio
async def test_lmstudio_down_gives_a_partial_retryable_result(
    settings: Settings, db: Database, library_folder: Path, fake_lmstudio: FakeLmStudio
) -> None:
    video_file = next(library_folder.iterdir())
    _age(video_file)
    scan_root(db, _add_root(db, library_folder))
    fake_lmstudio.down = True
    await _run_until_idle(settings, db, fake_lmstudio)
    with db.read() as session:
        job = session.execute(sa.select(Job)).scalar_one()
        vision = session.execute(
            sa.select(StageRun).where(StageRun.stage == "vision_frames")
        ).scalar_one()
    assert job.status == JobStatus.PARTIAL
    assert vision.status == StageStatus.SKIPPED
    assert vision.summary["retryable"] is True


def test_a_video_with_another_analysis_queued_is_not_settled(
    db: Database, library_folder: Path
) -> None:
    """A job finishing while another one waits for the same video (a folder « Complete »
    during an analysis) must not show the video as analysed: it waits for that job."""
    scan_root(db, _add_root(db, library_folder, auto=False))
    with db.read() as session:
        video_id = session.execute(sa.select(Video.id)).scalar_one()
    first = queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=video_id, payload={})
    claimed = queue.claim_next(db, os.getpid())
    assert claimed is not None
    assert claimed.id == first.id
    with db.read() as session:
        assert not queue.has_queued_analysis(session, video_id, besides=first.id)
    second = queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=video_id, payload={})
    assert second.id != first.id  # a running job is never merged into
    with db.read() as session:
        assert queue.has_queued_analysis(session, video_id, besides=first.id)
        assert not queue.has_queued_analysis(session, video_id, besides=second.id)


class _Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, kind: str, **fields: Any) -> None:
        self.events.append((kind, fields.get("data") or {}))


@pytest.mark.anyio
async def test_the_automatic_pass_survives_a_failing_scan(
    db: Database, library_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_id = _add_root(db, library_folder)
    calls: list[dict[str, Any]] = []
    stop = anyio.Event()

    def flaky(_db: Database, rid: str, **kwargs: Any) -> ScanReport:
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("unexpected")  # e.g. the folder removed meanwhile
        if len(calls) == 3:
            stop.set()
        return ScanReport(offline=1)  # only a clip gone: still worth telling the page

    monkeypatch.setattr(worker, "scan_root", flaky)
    monkeypatch.setattr(worker, "AUTO_SCAN_FIRST_S", 0.01)
    monkeypatch.setattr(worker, "AUTO_SCAN_INTERVAL_S", 0.01)
    sink = _Sink()
    token = CancelToken()
    with anyio.fail_after(10):
        await Worker._auto_scan_loop(db, sink, stop, token)  # type: ignore[arg-type]
    assert len(calls) == 3
    assert all(call == {"cancel": token, "automatic": True} for call in calls)
    assert sink.events == [("library.scanned", {"root_id": root_id, "automatic": True})] * 2
