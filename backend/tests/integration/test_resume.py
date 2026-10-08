"""Analyses left waiting for LM Studio, completed on their own once a vision model answers."""

from __future__ import annotations

import copy
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
import sqlalchemy as sa

from tests.conftest import MODELS_PAYLOAD, FakeLmStudio
from vfe_vision.db.base import utcnow
from vfe_vision.db.models import Job, LibraryRoot, StageRun, Video
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobKind, JobStatus, StageStatus, VideoStatus
from vfe_vision.jobs import queue, resume
from vfe_vision.jobs.events import DbEventSink
from vfe_vision.jobs.worker import Worker
from vfe_vision.pipeline.stage import StageOutcome

WAITING = StageOutcome.waiting_for_lmstudio("LM Studio injoignable ou aucun modèle chargé")


def _video(
    db: Database, tmp_path: Path, name: str, status: VideoStatus = VideoStatus.PARTIAL
) -> str:
    with db.write() as session:
        root = session.execute(sa.select(LibraryRoot)).scalars().first()
        if root is None:
            root = LibraryRoot(path=str(tmp_path), path_key=str(tmp_path).lower(), label="t")
            session.add(root)
            session.flush()
        video = Video(
            root_id=root.id, path=str(tmp_path / name), path_key=str(tmp_path / name),
            rel_path=name, filename=name, size_bytes=1, mtime=0.0, fingerprint=name,
            status=status,
        )  # fmt: skip
        session.add(video)
        session.flush()
        return video.id


def _run(db: Database, video_id: str, stage: str, outcome: StageOutcome, minutes: int = 0) -> str:
    """A stage run as the runner records it (status, reason, summary with « retryable »)."""
    with db.write() as session:
        run = StageRun(
            video_id=video_id, stage=stage, stage_version=1, cache_key="k", input_key="i",
            status=outcome.status, skip_reason=outcome.skip_reason,
            summary={**outcome.summary, "retryable": outcome.retryable},
            created_at=utcnow() + timedelta(minutes=minutes),
        )  # fmt: skip
        session.add(run)
        session.flush()
        return run.id


def test_a_skip_waiting_for_lm_studio_is_marked() -> None:
    assert WAITING.status == StageStatus.SKIPPED
    assert WAITING.retryable
    assert WAITING.summary == {"waits_for": "lmstudio"}


def test_the_videos_waiting_for_lm_studio(db: Database, tmp_path: Path) -> None:
    waiting = _video(db, tmp_path, "a.mp4")
    run_id = _run(db, waiting, "translation", WAITING)
    _run(db, waiting, "metadata", StageOutcome.ok())

    done = _video(db, tmp_path, "b.mp4")  # waited, then ran once LM Studio answered
    _run(db, done, "translation", WAITING)
    _run(db, done, "translation", StageOutcome.ok(texts=3), minutes=1)

    other = _video(db, tmp_path, "c.mp4")  # retryable for another reason (online service down)
    _run(db, other, "weather", StageOutcome.skipped("Open-Meteo injoignable", retryable=True))

    away = _video(db, tmp_path, "d.mp4", status=VideoStatus.OFFLINE)  # file out of reach
    _run(db, away, "translation", WAITING)

    assert resume.waiting_videos(db) == {waiting: [run_id]}
    assert resume.waiting_videos(db, besides={run_id}) == {}


def test_nothing_waits_in_an_ordinary_library(db: Database, tmp_path: Path) -> None:
    video_id = _video(db, tmp_path, "a.mp4", status=VideoStatus.READY)
    _run(db, video_id, "translation", StageOutcome.ok(texts=3))
    assert resume.waiting_videos(db) == {}


def test_an_analysis_already_queued_will_do_it(db: Database, tmp_path: Path) -> None:
    video_id = _video(db, tmp_path, "a.mp4")
    _run(db, video_id, "vision_frames", WAITING)
    queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=video_id, payload={"mode": "full"})
    assert resume.waiting_videos(db) == {}


def test_the_waiting_videos_are_completed(db: Database, tmp_path: Path) -> None:
    video_id = _video(db, tmp_path, "a.mp4")
    _run(db, video_id, "translation", WAITING)

    assert resume.queue_completions(db, [video_id]) == 1
    with db.read() as session:
        job = session.execute(sa.select(Job)).scalar_one()
        assert job.kind == JobKind.ANALYZE_VIDEO
        assert job.status == JobStatus.QUEUED
        assert job.video_id == video_id
        assert job.payload == {"mode": "complete", "force": False, "refresh": False}
        assert job.priority == resume.RESUME_PRIORITY
        assert session.get_one(Video, video_id).status == VideoStatus.QUEUED


@pytest.mark.anyio
async def test_a_vision_model_answers(db: Database, fake_lmstudio: FakeLmStudio) -> None:
    client = fake_lmstudio.client()
    try:
        assert await resume.model_answers(db, client)

        fake_lmstudio.down = True  # the other computer asleep
        assert not await resume.model_answers(db, client)

        fake_lmstudio.down = False  # LM Studio answers, but without a vision model loaded
        unloaded = copy.deepcopy(MODELS_PAYLOAD)
        unloaded["models"][0]["loaded_instances"] = []
        fake_lmstudio.models_payload = unloaded
        assert not await resume.model_answers(db, client)
    finally:
        await client.aclose()


def _jobs(db: Database) -> list[Job]:
    with db.read() as session:
        return list(session.execute(sa.select(Job)).scalars())


@pytest.mark.anyio
async def test_the_worker_completes_them_once_a_vision_model_answers(
    db: Database, tmp_path: Path, fake_lmstudio: FakeLmStudio, monkeypatch: pytest.MonkeyPatch
) -> None:
    video_id = _video(db, tmp_path, "a.mp4")
    _run(db, video_id, "translation", WAITING)
    monkeypatch.setattr(resume, "RESUME_INTERVAL_S", 0.01)
    fake_lmstudio.down = True  # a new installation still aimed at a local LM Studio
    client = fake_lmstudio.client()
    sink = DbEventSink(db)
    stop = anyio.Event()
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(Worker._resume_loop, db, sink, client, stop)
            await anyio.sleep(0.2)
            assert _jobs(db) == []

            fake_lmstudio.down = False  # its address set on the System page
            for _ in range(250):  # within 5 s
                if _jobs(db):
                    break
                await anyio.sleep(0.02)
            [job] = _jobs(db)
            assert job.video_id == video_id
            assert job.payload["mode"] == "complete"

            # The analysis ran but the stage skipped again: no second analysis for the same run.
            with db.write() as session:
                session.get_one(Job, job.id).status = JobStatus.PARTIAL
            await anyio.sleep(0.2)
            assert len(_jobs(db)) == 1
            stop.set()
    finally:
        sink.close()
        await client.aclose()
