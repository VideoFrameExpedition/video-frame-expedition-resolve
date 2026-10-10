"""The model bench: vision models loaded one at a time in LM Studio, asked the
requests of the analyses on frames of the library, measured, then unloaded."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import numpy as np
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from tests.conftest import FakeVllm
from tests.fakes.bench import CAT, GEMMA, LARGE, SMALL, TEXT_ONLY, BenchLmStudio, FakeVram
from tests.fakes.context import FakeGeocoder, FakeWeather
from tests.fakes.library import Library, build_library
from vfe_vision.adapters.ffmpeg.tools import Ffmpeg
from vfe_vision.adapters.imaging import encode_jpeg
from vfe_vision.adapters.lmstudio.client import LmStudioLoadError, LmStudioUnavailableError
from vfe_vision.api.app import create_app
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import (
    CancelledError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    VfeError,
)
from vfe_vision.db.models import (
    BenchRun,
    Detection,
    FrameAnalysis,
    Job,
    Keyframe,
    LlmCall,
    ServiceCache,
    SubjectScan,
)
from vfe_vision.db.session import Database
from vfe_vision.domain.bench import BenchModelStatus, BenchRunStatus
from vfe_vision.domain.enums import JobKind, JobStatus
from vfe_vision.jobs import queue
from vfe_vision.jobs.bench import BenchTools, run_bench, settle_interrupted
from vfe_vision.jobs.worker import ALONE_KINDS, Worker
from vfe_vision.services import bench
from vfe_vision.services.container import AppContainer
from vfe_vision.storage.artifacts import ArtifactStore

pytestmark = pytest.mark.anyio
HEADERS = {"X-VFE-Client": "tests"}


def _container(settings: Settings, db: Database, lm: BenchLmStudio) -> AppContainer:
    return AppContainer(
        settings=settings,
        db=db,
        artifacts=ArtifactStore(settings.artifacts_dir),
        ffmpeg=Ffmpeg(settings.ffmpeg_path, settings.ffprobe_path),
        lmstudio=lm.client(),
    )


def _library(settings: Settings, db: Database, base: Path) -> Library:
    """The analysed library of the search tests, with the files of its eight keyframes, and a
    cat the CPU detector boxed on the first one."""
    library = build_library(db, base)
    image = np.full((360, 640, 3), 90, np.uint8)
    image[100:300, 150:450] = (40, 160, 220)
    jpeg = encode_jpeg(image)
    with db.write() as session:
        frames = list(session.execute(sa.select(Keyframe).order_by(Keyframe.t_s)).scalars())
        for frame in frames:
            for rel in (frame.image_path, frame.thumb_path):
                target = settings.artifacts_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(jpeg)
        first = next(f for f in frames if f.video_id == library.holiday and f.idx == 0)
        session.add_all(
            [
                Detection(
                    keyframe_id=first.id, video_id=first.video_id, t_s=first.t_s,
                    source="detector", idx=0, label="cat", category="mammal", box=CAT,
                    score=0.9, model="dfine",
                ),
                SubjectScan(
                    keyframe_id=first.id, source="detector", video_id=first.video_id,
                    model="dfine", found=1,
                ),
            ]
        )  # fmt: skip
    return library


async def _queued(c: AppContainer, keys: list[str], images: int = 8) -> tuple[str, Job]:
    view = await bench.start(c, keys, images)
    job = queue.claim_next(c.db, 1)
    assert job is not None
    assert job.kind == JobKind.BENCH_MODELS
    return view.id, job


def _tools(c: AppContainer, lm: BenchLmStudio) -> BenchTools:
    return BenchTools(c.db, c.artifacts, lm.client(), FakeVram(lm), settle_s=0.0)


# ------------------------------------------------------------------------------ LM Studio
async def test_the_client_loads_and_unloads_a_model() -> None:
    lm = BenchLmStudio(loaded=())
    client = lm.client()
    loaded = await client.load_model(SMALL, context_length=12288, parallel=4)
    assert (loaded.instance_id, loaded.load_time_s) == (SMALL, 1.5)
    assert (loaded.context_length, loaded.parallel) == (12288, 4)
    assert lm.loads == [
        {"model": SMALL, "echo_load_config": True, "context_length": 12288, "parallel": 4}
    ]
    await client.unload_model(SMALL)
    assert lm.loaded == {}

    with pytest.raises(LmStudioLoadError, match="is not loaded"):
        await client.unload_model(SMALL)
    lm.refused.add(GEMMA)
    with pytest.raises(LmStudioLoadError, match="Not enough memory"):
        await client.load_model(GEMMA)
    lm.down = True
    with pytest.raises(LmStudioUnavailableError):
        await client.load_model(SMALL)


# ------------------------------------------------------------------------------ a run
async def test_each_model_is_tested_alone_and_the_loaded_one_comes_back(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    lm = BenchLmStudio()
    c = _container(settings, db, lm)
    library = _library(settings, db, tmp_path / "rushs")
    run_id, job = await _queued(c, [GEMMA, SMALL])
    messages: list[str | None] = []

    status, message = await run_bench(
        _tools(c, lm), job, cancel=CancelToken(), progress=lambda _f, m: messages.append(m)
    )

    assert (status, message) == (JobStatus.SUCCEEDED, "2 modèle(s) testé(s) sur 8 images")
    # The loaded model leaves the card, each model has it alone (the smaller first), with the
    # same settings (Gemma cannot hold more than its own context), then the first one is back.
    assert lm.unloads == [LARGE, SMALL, GEMMA]
    assert [(b["model"], b["context_length"], b["parallel"]) for b in lm.loads] == [
        (SMALL, 12288, 4),
        (GEMMA, 8192, 4),
        (LARGE, 10496, 4),
    ]
    assert lm.loaded == {LARGE: {"context_length": 10496, "parallel": 4}}
    assert "Qwen3 VL 4B : descriptions (8/8)" in messages
    assert "Gemma 4 12B : positions (8/8)" in messages

    view = bench.get_run(c, run_id)
    assert view.status == BenchRunStatus.SUCCEEDED
    assert (view.progress, view.message, view.error) == (1.0, None, None)
    assert [(p.key, p.restored) for p in view.previous] == [(LARGE, True)]
    assert len(view.frames) == 8
    small, gemma = view.model_runs
    assert (small.key, small.status, small.quantization) == (SMALL, BenchModelStatus.DONE, "Q4_K_M")
    assert (small.context_length, small.parallel, small.load_s) == (12288, 4, 1.5)
    # What the model takes on the card: its file, on top of the 1 GiB of the screen.
    assert small.scores.vram_mib == 3_300_000_000 // (1024 * 1024)
    assert small.scores.vram_free_mib == 12288 - 1024 - small.scores.vram_mib
    assert (small.scores.requests, small.scores.valid) == (16, 16)
    assert small.scores.seconds_per_image is not None
    assert (small.scores.language_checked, small.scores.wrong_language) == (8, 0)
    # The OCR read « RIZ BASMATI » on one frame of the holiday video and nothing on its five
    # others; it never ran on the mountain video. The fake model reads that text everywhere.
    assert (small.scores.text_frames, small.scores.text_recall) == (1, 1.0)
    assert (small.scores.blank_frames, small.scores.unconfirmed_text) == (5, 5)
    # The detector boxed a cat on one frame: both models locate it, each in its own convention.
    assert small.calibration is not None
    assert (small.calibration.source, small.calibration.convention) == ("prior", "xyxy_1000")
    assert gemma.calibration is not None
    assert (gemma.calibration.source, gemma.calibration.convention, gemma.calibration.box_field) == (
        "profile", "yxyx_1000", "box_2d",
    )  # fmt: skip
    for model in (small, gemma):
        assert (model.scores.position_beings, model.scores.position_recall) == (1, 1.0)
        assert model.scores.position_iou is not None
        assert model.scores.position_iou > 0.99
    # Gemma can reason: it was asked not to.
    asked = [r for r in lm.chat_requests if r["model"] == GEMMA]
    assert all(r.get("reasoning_effort") == "none" for r in asked)
    assert not any("reasoning_effort" in r for r in lm.chat_requests if r["model"] == SMALL)

    frame = next(f for f in view.frames if f.text == ["RIZ BASMATI"])
    assert frame.video_id == library.holiday
    assert frame.answers[SMALL].caption == "Un chat noir dort sur le canapé."
    assert frame.answers[SMALL].subjects == ["mire — motif de test"]
    assert [b.label for b in frame.answers[GEMMA].beings or []] == ["chat"]
    boxed = next(f for f in view.frames if f.beings)
    assert [b.category for b in boxed.beings or []] == ["mammal"]

    # Nothing of the library changed: no analysis, no cached answer, no stored calibration.
    with db.read() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(LlmCall)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(ServiceCache)) == 0
        models = set(session.execute(sa.select(FrameAnalysis.model)).scalars())
    assert models == {"m"}


async def test_the_same_requests_as_the_analyses(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    lm = BenchLmStudio(loaded=())
    c = _container(settings, db, lm)
    _library(settings, db, tmp_path / "rushs")
    _, job = await _queued(c, [SMALL], images=4)
    await run_bench(_tools(c, lm), job, cancel=CancelToken(), progress=lambda _f, _m: None)

    described = [
        r for r in lm.chat_requests
        if str(r["messages"][0]["content"]).startswith("You assist a professional video editor")
    ]  # fmt: skip
    assert len(described) == 4
    request = described[0]
    assert request["max_tokens"] == 900
    assert request["temperature"] == 0.1
    assert "Write every free-text value" in request["messages"][0]["content"]
    user = request["messages"][1]["content"]
    assert user[0]["text"].startswith("Frame ")
    assert "Context hints (may be incomplete or wrong):" in user[0]["text"]
    assert user[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    located = [
        r for r in lm.chat_requests if "You locate living beings" in r["messages"][0]["content"]
    ]
    assert len(located) == 4
    assert (located[0]["max_tokens"], located[0]["temperature"]) == (1200, 0.0)
    assert lm.loaded == {}  # nothing was loaded before: nothing is loaded after


async def test_a_model_lm_studio_refuses_does_not_stop_the_others(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    lm = BenchLmStudio()
    lm.refused.add(GEMMA)
    lm.english.add(SMALL)
    c = _container(settings, db, lm)
    _library(settings, db, tmp_path / "rushs")
    run_id, job = await _queued(c, [SMALL, GEMMA])

    status, message = await run_bench(
        _tools(c, lm), job, cancel=CancelToken(), progress=lambda _f, _m: None
    )

    assert status == JobStatus.PARTIAL
    assert message == "1 modèle(s) testé(s) sur 8 images, 1 en échec"
    view = bench.get_run(c, run_id)
    assert view.status == BenchRunStatus.PARTIAL
    small, gemma = view.model_runs
    assert (gemma.status, gemma.error) == (
        BenchModelStatus.LOAD_FAILED, "Not enough memory to load the model.",
    )  # fmt: skip
    assert gemma.scores.requests == 0
    # Asked for French, the small model wrote its descriptions in English.
    assert (small.scores.language_checked, small.scores.wrong_language) == (8, 8)
    assert list(lm.loaded) == [LARGE]


async def test_stopping_a_run_unloads_the_model_and_restores_the_first_one(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    lm = BenchLmStudio()
    c = _container(settings, db, lm)
    _library(settings, db, tmp_path / "rushs")
    run_id, job = await _queued(c, [SMALL, GEMMA])
    token = CancelToken()

    def progress(_fraction: float, message: str | None) -> None:
        if message and "positions (" in message:
            token.cancel()

    with pytest.raises(CancelledError):
        await run_bench(_tools(c, lm), job, cancel=token, progress=progress)

    assert list(lm.loaded) == [LARGE]
    assert GEMMA not in [b["model"] for b in lm.loads]
    view = bench.get_run(c, run_id)
    assert view.status == BenchRunStatus.CANCELLED
    assert [m.status for m in view.model_runs] == [
        BenchModelStatus.CANCELLED,
        BenchModelStatus.CANCELLED,
    ]
    # What the first model had answered is kept.
    assert view.model_runs[0].scores.valid >= 8


async def test_lm_studio_going_away_ends_the_run(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    lm = BenchLmStudio()
    c = _container(settings, db, lm)
    _library(settings, db, tmp_path / "rushs")
    run_id, job = await _queued(c, [SMALL])

    def progress(_fraction: float, message: str | None) -> None:
        if message and message.endswith("calibrage des positions"):
            lm.down = True

    with pytest.raises(VfeError):
        await run_bench(_tools(c, lm), job, cancel=CancelToken(), progress=progress)

    view = bench.get_run(c, run_id)
    assert view.status == BenchRunStatus.FAILED
    assert view.error
    assert view.model_runs[0].status == BenchModelStatus.FAILED
    assert [(p.key, p.restored) for p in view.previous] == [(LARGE, False)]
    assert view.previous[0].error


async def test_blind_ratings_join_the_measures(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    lm = BenchLmStudio(loaded=())
    c = _container(settings, db, lm)
    _library(settings, db, tmp_path / "rushs")
    run_id, job = await _queued(c, [SMALL, GEMMA], images=4)
    await run_bench(_tools(c, lm), job, cancel=CancelToken(), progress=lambda _f, _m: None)
    first, second = (frame.keyframe_id for frame in bench.get_run(c, run_id).frames[:2])

    bench.rate(c, run_id, first, SMALL, 3)
    bench.rate(c, run_id, second, SMALL, 2)
    bench.rate(c, run_id, first, GEMMA, 0)
    view = bench.get_run(c, run_id)
    assert view.ratings == {first: {SMALL: 3, GEMMA: 0}, second: {SMALL: 2}}
    small, gemma = view.model_runs
    assert (small.scores.rated, small.scores.rating) == (2, 2.5)
    assert (gemma.scores.rated, gemma.scores.rating) == (1, 0.0)

    bench.rate(c, run_id, first, GEMMA, None)  # a note taken back
    assert bench.get_run(c, run_id).model_runs[1].scores.rated == 0
    for frame, model, mark in ((first, SMALL, 4), ("nope", SMALL, 1), (first, LARGE, 1)):
        with pytest.raises(InvalidInputError):
            bench.rate(c, run_id, frame, model, mark)


async def test_the_history_keeps_what_each_run_measured(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    lm = BenchLmStudio(loaded=())
    c = _container(settings, db, lm)
    _library(settings, db, tmp_path / "rushs")
    first, job = await _queued(c, [SMALL], images=4)
    await run_bench(_tools(c, lm), job, cancel=CancelToken(), progress=lambda _f, _m: None)
    second, job = await _queued(c, [GEMMA, SMALL], images=4)
    await run_bench(_tools(c, lm), job, cancel=CancelToken(), progress=lambda _f, _m: None)
    wider, job = await _queued(c, [SMALL], images=8)
    await run_bench(_tools(c, lm), job, cancel=CancelToken(), progress=lambda _f, _m: None)
    frame = bench.get_run(c, first).frames[0].keyframe_id
    bench.rate(c, first, frame, SMALL, 2)

    history = {run.id: run for run in bench.list_runs(c)}
    assert list(history) == [wider, second, first]  # the latest first
    # What each model measured travels with the history: the charts need nothing else.
    assert [m.key for m in history[second].model_runs] == [SMALL, GEMMA]
    measured = history[first].model_runs[0].scores
    assert measured.vram_mib == 3147  # what the fake card held more once the model was loaded
    assert measured.seconds_per_image is not None
    assert (measured.rated, measured.rating) == (1, 2.0)
    # The same frames: every measure compares; another choice of frames: memory and speed only.
    assert history[first].image_set == history[second].image_set != ""
    assert history[wider].image_set != history[first].image_set

    assert history[first].note is None
    bench.annotate(c, first, "  avant la mise à jour\n de LM Studio ")
    assert bench.list_runs(c)[-1].note == "avant la mise à jour de LM Studio"
    assert bench.get_run(c, first).note == "avant la mise à jour de LM Studio"
    assert bench.get_run(c, first).model_runs[0].scores.rated == 1  # the run itself is untouched
    bench.annotate(c, first, "   ")
    assert bench.get_run(c, first).note is None
    with pytest.raises(InvalidInputError):
        bench.annotate(c, first, "x" * 301)
    with pytest.raises(NotFoundError):
        bench.annotate(c, "nope", "a")


# ------------------------------------------------------------------------------ asking for one
async def test_what_can_be_tested(settings: Settings, db: Database, tmp_path: Path) -> None:
    lm = BenchLmStudio()
    c = _container(settings, db, lm)
    empty = await bench.overview(c)
    assert empty.frames_available == 0
    assert [m.key for m in empty.models] == [SMALL, GEMMA, LARGE]  # vision only, smallest first
    assert [m.loaded for m in empty.models] == [False, False, True]
    assert (empty.loaded, empty.language, empty.gpu) == (["Qwen3 VL 8B"], "fr", None)
    assert empty.models[1].reasoning
    with pytest.raises(ConflictError, match="analysez d'abord une vidéo"):
        await bench.start(c, [SMALL], 24)

    _library(settings, db, tmp_path / "rushs")
    assert (await bench.overview(c)).frames_available == 8
    with pytest.raises(InvalidInputError, match="introuvable"):
        await bench.start(c, ["nope/model"], 24)
    with pytest.raises(InvalidInputError, match="Text Only"):
        await bench.start(c, [TEXT_ONLY], 24)
    with pytest.raises(InvalidInputError):
        await bench.start(c, [" "], 24)

    view = await bench.start(c, [LARGE, SMALL, SMALL], 24)
    assert (view.status, view.images, view.models) == (BenchRunStatus.QUEUED, 8, [SMALL, LARGE])
    assert (await bench.overview(c)).active_run_id == view.id
    with pytest.raises(ConflictError, match="déjà en cours"):
        await bench.start(c, [SMALL], 24)
    with pytest.raises(ConflictError):
        bench.delete_run(c, view.id)

    lm.down = True
    assert (await bench.overview(c)).lmstudio_error


async def test_the_bench_needs_lm_studio(
    settings: Settings, db: Database, tmp_path: Path, fake_vllm: FakeVllm
) -> None:
    """An OpenAI-compatible server (vLLM) serves its models itself: the bench, which loads and
    unloads them one by one, says it needs LM Studio and starts nothing."""
    c = AppContainer(
        settings=settings,
        db=db,
        artifacts=ArtifactStore(settings.artifacts_dir),
        ffmpeg=Ffmpeg(settings.ffmpeg_path, settings.ffprobe_path),
        lmstudio=fake_vllm.client(),
    )
    _library(settings, db, tmp_path / "rushs")
    overview = await bench.overview(c)
    assert overview.needs_lmstudio
    assert (overview.models, overview.lmstudio_error) == ([], None)
    with pytest.raises(ConflictError, match="demande LM Studio"):
        await bench.start(c, ["Qwen/Qwen3-VL-8B-Instruct"], 8)
    assert queue.claim_next(c.db, 1) is None

    # A run queued while LM Studio answered, run after the switch: it fails with the same words.
    lm = BenchLmStudio(loaded=())
    run_id, job = await _queued(_container(settings, db, lm), [SMALL])
    tools = BenchTools(c.db, c.artifacts, fake_vllm.client(), FakeVram(lm), settle_s=0.0)
    with pytest.raises(VfeError, match="demande LM Studio"):
        await run_bench(tools, job, cancel=CancelToken(), progress=lambda _f, _m: None)
    view = bench.get_run(c, run_id)
    assert view.status == BenchRunStatus.FAILED
    assert "demande LM Studio" in (view.error or "")
    assert (lm.loads, lm.unloads) == ([], [])
    assert not any("load" in path for path in fake_vllm.requested_paths)


async def test_a_run_whose_job_ended_without_it_is_settled(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    lm = BenchLmStudio()
    c = _container(settings, db, lm)
    _library(settings, db, tmp_path / "rushs")
    cancelled = await bench.start(c, [SMALL], 8)
    assert cancelled.job_id is not None
    queue.request_cancel(db, cancelled.job_id)  # stopped before its turn
    assert bench.get_run(c, cancelled.id).status == BenchRunStatus.CANCELLED

    await anyio.sleep(0.05)  # Windows' clock ticks every 16 ms: the history is ordered by date
    run_id, job = await _queued(c, [SMALL])  # running when the application stops
    assert queue.requeue_interrupted(db) == 0  # a bench is not taken up again at the next start
    with db.read() as session:
        assert session.get_one(Job, job.id).status == JobStatus.FAILED
    assert settle_interrupted(db) == 1
    view = bench.get_run(c, run_id)
    assert (view.status, view.error) == (
        BenchRunStatus.FAILED,
        "Interrompu par l'arrêt de l'application",
    )
    assert [r.id for r in bench.list_runs(c)] == [run_id, cancelled.id]
    bench.delete_run(c, run_id)
    assert [r.id for r in bench.list_runs(c)] == [cancelled.id]


# ------------------------------------------------------------------------------ the worker
def test_a_bench_waits_for_the_running_jobs_then_runs_alone(db: Database) -> None:
    with db.write() as session:
        session.add(BenchRun(id="r1"))
    probe = queue.enqueue(db, JobKind.PROBE_VISION, priority=5)
    waiting = queue.enqueue(db, JobKind.BENCH_MODELS, payload={"run_id": "r1"}, priority=10)

    first = queue.claim_next(db, 1, alone=ALONE_KINDS, busy=False)
    assert first is not None
    assert first.id == probe.id
    # The bench is next, but a job runs: nothing is claimed until the worker is idle.
    assert queue.claim_next(db, 1, alone=ALONE_KINDS, busy=True) is None
    queue.finish(db, probe.id, JobStatus.SUCCEEDED)
    claimed = queue.claim_next(db, 1, alone=ALONE_KINDS, busy=False)
    assert claimed is not None
    assert claimed.id == waiting.id


async def test_the_worker_runs_a_bench(settings: Settings, db: Database, tmp_path: Path) -> None:
    lm = BenchLmStudio()
    c = _container(settings, db, lm)
    _library(settings, db, tmp_path / "rushs")
    view = await bench.start(c, [SMALL], 4)
    probe = queue.enqueue(db, JobKind.PROBE_VISION, priority=50)
    worker = Worker(
        settings, lmstudio=lm.client(), weather=FakeWeather(), geocoder=FakeGeocoder(),
        vram=FakeVram(lm), bench_settle_s=0.0,
    )  # fmt: skip
    stop = anyio.Event()

    async def stop_when_idle() -> None:
        while True:
            await anyio.sleep(0.2)
            with db.read() as session:
                statuses = set(session.execute(sa.select(Job.status)).scalars())
            if all(s.is_terminal for s in statuses):
                stop.set()
                return

    with anyio.fail_after(60):
        async with anyio.create_task_group() as group:
            group.start_soon(worker.run, stop)
            group.start_soon(stop_when_idle)

    done = bench.get_run(c, view.id)
    assert done.status == BenchRunStatus.SUCCEEDED
    assert done.model_runs[0].scores.valid == 8
    with db.read() as session:
        bench_job = session.get_one(Job, view.job_id)
        probe_job = session.get_one(Job, probe.id)
    assert (bench_job.status, bench_job.progress) == (JobStatus.SUCCEEDED, 1.0)
    assert bench_job.message == "1 modèle(s) testé(s) sur 4 images"
    # The calibration asked for meanwhile waited for the bench to end (and found the model
    # loaded before it, back in place).
    assert probe_job.status == JobStatus.SUCCEEDED
    assert bench_job.finished_at is not None
    assert probe_job.started_at is not None
    assert probe_job.started_at >= bench_job.finished_at


# ------------------------------------------------------------------------------ the API
@pytest.fixture
def api(
    settings: Settings, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, BenchLmStudio]]:
    lm = BenchLmStudio()
    _library(settings, db, tmp_path / "rushs")
    monkeypatch.setattr(
        AppContainer, "create", staticmethod(lambda _s: _container(settings, db, lm))
    )
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        yield client, lm


def test_the_api_of_the_bench(api: tuple[TestClient, BenchLmStudio]) -> None:
    client, _ = api
    overview: dict[str, Any] = client.get("/api/v1/bench").json()
    assert [m["key"] for m in overview["models"]] == [SMALL, GEMMA, LARGE]
    assert (overview["frames_available"], overview["default_images"]) == (8, 24)
    assert (overview["context_length"], overview["parallel"]) == (12288, 4)
    assert client.get("/api/v1/bench/runs").json() == []

    refused: list[dict[str, Any]] = [{"models": []}, {"models": ["nope"]}, {"models": [TEXT_ONLY]}]
    for body in refused:
        assert client.post("/api/v1/bench/runs", json=body, headers=HEADERS).status_code == 422
    started = client.post(
        "/api/v1/bench/runs", json={"models": [SMALL], "images": 12}, headers=HEADERS
    )
    assert started.status_code == 202
    run: dict[str, Any] = started.json()
    assert (run["status"], run["images"], run["models"]) == ("queued", 8, [SMALL])
    assert run["frames"] == []
    again = client.post("/api/v1/bench/runs", json={"models": [SMALL]}, headers=HEADERS)
    assert again.status_code == 409

    path = f"/api/v1/bench/runs/{run['id']}"
    assert [r["id"] for r in client.get("/api/v1/bench/runs").json()] == [run["id"]]
    assert client.delete(path, headers=HEADERS).status_code == 409  # active
    rating = {"keyframe_id": "nope", "model": SMALL, "rating": 2}
    assert client.put(f"{path}/ratings", json=rating, headers=HEADERS).status_code == 422
    assert client.patch(path, json={"note": "essai"}, headers=HEADERS).status_code == 204
    listed: dict[str, Any] = client.get("/api/v1/bench/runs").json()[0]
    assert (listed["note"], listed["image_set"], listed["model_runs"]) == ("essai", "", [])
    assert client.patch(path, json={"note": "x" * 301}, headers=HEADERS).status_code == 422
    assert client.patch(path, json={"note": None}, headers=HEADERS).status_code == 204
    assert client.get(path).json()["note"] is None
    stopped = client.post(f"/api/v1/jobs/{run['job_id']}/cancel", headers=HEADERS)
    assert stopped.status_code == 202
    assert client.get(path).json()["status"] == "cancelled"
    assert client.delete(path, headers=HEADERS).status_code == 204
    assert client.get(path).status_code == 404
