"""The model bench as its page asks for it: which vision models LM Studio holds, a
run requested, the runs with what they measured (the history the charts are drawn from), the
blind ratings and the user's note on a run."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

import anyio
import sqlalchemy as sa
from pydantic import BaseModel

from vfe_vision.adapters.gpu.nvidia_smi import NvidiaSmi
from vfe_vision.adapters.lmstudio.catalog import ModelInfo
from vfe_vision.core.errors import ConflictError, InvalidInputError, NotFoundError, VfeError
from vfe_vision.db.models import BenchRun, Job
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.bench import (
    CONTEXT_LENGTH,
    DEFAULT_IMAGES,
    IMAGE_CHOICES,
    MAX_MODELS,
    MAX_NOTE,
    MAX_RATING,
    MIN_IMAGES,
    PARALLEL,
    Answer,
    BenchBeing,
    BenchCalibration,
    BenchData,
    BenchFrame,
    BenchModelStatus,
    BenchPreviousModel,
    BenchRunStatus,
    BenchScores,
    ModelRun,
    image_set,
    score,
)
from vfe_vision.domain.enums import JobKind, JobStatus
from vfe_vision.jobs import bench as bench_job
from vfe_vision.jobs import queue
from vfe_vision.ports.gpu import VramUsage
from vfe_vision.services.container import AppContainer

BENCH_PRIORITY = 10  # asked for now: it takes its turn before the analyses that wait
RUNS_LISTED = 50
MIB = 1024 * 1024
# What a loaded model takes beyond its file (image encoder, context, buffers): 0.2 to 2.1 GiB on
# the models measured. Only a hint before the test, which measures the real figure.
LOAD_OVERHEAD_MIB = 1536
DESKTOP_MIB = 1024  # what the screen and the other applications usually hold


class BenchGpu(BaseModel):
    used_mib: int
    free_mib: int
    total_mib: int


class BenchModelFit(StrEnum):
    OK = "ok"
    TIGHT = "tight"  # may not hold on the card: the test will tell
    TOO_BIG = "too_big"  # its file alone is larger than the card


class BenchModel(BaseModel):
    """A vision model of LM Studio that can be tested."""

    key: str
    display_name: str
    publisher: str | None
    params: str | None
    quantization: str | None
    architecture: str | None
    size_bytes: int | None
    loaded: bool
    reasoning: bool
    fit: BenchModelFit | None


class BenchOverview(BaseModel):
    """What the page needs before a run."""

    lmstudio_error: str | None = None  # LM Studio unreachable: start it
    models: list[BenchModel] = []
    gpu: BenchGpu | None = None
    frames_available: int = 0  # distinct keyframes of the library
    language: str = "fr"  # the analyses' language: the one the models are asked to write in
    image_choices: list[int] = list(IMAGE_CHOICES)
    default_images: int = DEFAULT_IMAGES
    min_images: int = MIN_IMAGES
    max_models: int = MAX_MODELS
    context_length: int = CONTEXT_LENGTH
    parallel: int = PARALLEL
    loaded: list[str] = []  # the language models loaded now: unloaded for the test, then back
    running_jobs: int = 0  # jobs running now: the test waits for them to end
    active_run_id: str | None = None
    # An OpenAI-compatible server (vLLM…) serves its models itself: the bench, which loads and
    # unloads them one by one, needs LM Studio.
    needs_lmstudio: bool = False


class BenchAnswer(BaseModel):
    """What one model said of one frame."""

    ok: bool = False
    error: str | None = None
    caption: str = ""
    description: str = ""
    subjects: list[str] = []
    visible_text: str = ""
    beings: list[BenchBeing] | None = None  # None: positions not asked (disabled for this model)


class BenchFrameView(BaseModel):
    keyframe_id: str
    video_id: str
    filename: str
    t_s: float
    image_path: str
    thumb_path: str
    text: list[str] | None
    beings: list[BenchBeing] | None
    answers: dict[str, BenchAnswer]  # by model key


class BenchModelView(BaseModel):
    key: str
    display_name: str
    publisher: str | None
    params: str | None
    quantization: str | None
    size_bytes: int | None
    status: BenchModelStatus
    error: str | None
    load_s: float | None
    context_length: int | None
    parallel: int | None
    calibration: BenchCalibration | None
    scores: BenchScores


class BenchRunSummary(BaseModel):
    """A run in the history: when, on what, and what each model measured."""

    id: str
    status: BenchRunStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    job_id: str | None
    images: int
    image_set: str  # the same name: the same frames, so every measure compares
    language: str
    models: list[str]  # display names (keys before the run starts)
    model_runs: list[BenchModelView]
    note: str | None
    error: str | None


class BenchRunView(BenchRunSummary):
    context_length: int
    parallel: int
    frames: list[BenchFrameView]
    ratings: dict[str, dict[str, int]]
    previous: list[BenchPreviousModel]
    progress: float
    message: str | None


Ratings = dict[str, dict[str, int]]  # keyframe id → model key → mark


# ------------------------------------------------------------------------------ before a run
def _fit(size_bytes: int | None, gpu: VramUsage | None) -> BenchModelFit | None:
    if size_bytes is None or gpu is None:
        return None
    size = size_bytes / MIB
    if size > gpu.total:
        return BenchModelFit.TOO_BIG
    if size + LOAD_OVERHEAD_MIB > gpu.total - DESKTOP_MIB:
        return BenchModelFit.TIGHT
    return BenchModelFit.OK


def _bench_model(model: ModelInfo, gpu: VramUsage | None) -> BenchModel:
    return BenchModel(
        key=model.key,
        display_name=model.display_name,
        publisher=model.publisher,
        params=model.params,
        quantization=model.quantization,
        architecture=model.architecture,
        size_bytes=model.size_bytes,
        loaded=model.loaded,
        reasoning=bool(model.reasoning_options),
        fit=_fit(model.size_bytes, gpu),
    )


def _active_run_id(c: AppContainer) -> str | None:
    bench_job.settle_interrupted(c.db)
    with c.db.read() as session:
        return session.execute(
            sa.select(BenchRun.id)
            .where(BenchRun.status.in_([BenchRunStatus.QUEUED.value, BenchRunStatus.RUNNING.value]))
            .order_by(BenchRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()


def _running_jobs(c: AppContainer) -> int:
    with c.db.read() as session:
        return int(
            session.execute(
                sa.select(sa.func.count())
                .select_from(Job)
                .where(Job.status == JobStatus.RUNNING, Job.kind != JobKind.BENCH_MODELS)
            ).scalar_one()
        )


async def overview(c: AppContainer) -> BenchOverview:
    gpu = await anyio.to_thread.run_sync(NvidiaSmi(c.settings.nvidia_smi_path).usage)
    prefs = await anyio.to_thread.run_sync(load_preferences, c.db)
    result = BenchOverview(
        gpu=BenchGpu(used_mib=gpu.used, free_mib=gpu.free, total_mib=gpu.total) if gpu else None,
        frames_available=await anyio.to_thread.run_sync(bench_job.available_frames, c.db),
        language=prefs.language,
        running_jobs=await anyio.to_thread.run_sync(_running_jobs, c),
        active_run_id=await anyio.to_thread.run_sync(_active_run_id, c),
    )
    try:
        models = await c.lmstudio.list_models()
    except VfeError as exc:
        result.lmstudio_error = exc.detail
        return result
    if c.lmstudio.serves_its_models:
        result.needs_lmstudio = True
        return result
    vision = sorted(
        (model for model in models if model.vision),
        key=lambda model: (model.size_bytes or 0, model.key),
    )
    result.models = [_bench_model(model, gpu) for model in vision]
    result.loaded = [model.display_name for model in models if model.type == "llm" and model.loaded]
    return result


# ------------------------------------------------------------------------------ a run
async def start(c: AppContainer, keys: list[str], images: int) -> BenchRunView:
    """Queue a run on these models (their LM Studio keys), smallest first. 409 while another run
    is active, when the library has no frame to show or when the model server is not LM Studio;
    422 for a model LM Studio does not
    hold or that cannot see; 503 when LM Studio does not answer."""
    wanted = list(dict.fromkeys(key.strip() for key in keys if key.strip()))
    if not wanted:
        raise InvalidInputError("Choisissez au moins un modèle à tester.")
    if len(wanted) > MAX_MODELS:
        raise InvalidInputError(f"Au plus {MAX_MODELS} modèles par test.")
    by_key = {model.key: model for model in await c.lmstudio.list_models()}
    if c.lmstudio.serves_its_models:
        raise ConflictError(bench_job.NEEDS_LMSTUDIO)
    unknown = [key for key in wanted if key not in by_key]
    if unknown:
        raise InvalidInputError(f"Modèle introuvable dans LM Studio : {', '.join(unknown)}")
    blind = [by_key[key].display_name for key in wanted if not by_key[key].vision]
    if blind:
        raise InvalidInputError(f"Ce n'est pas un modèle de vision : {', '.join(blind)}")
    wanted.sort(key=lambda key: (by_key[key].size_bytes or 0, key))
    available = await anyio.to_thread.run_sync(bench_job.available_frames, c.db)
    if available < MIN_IMAGES:
        raise ConflictError(
            "Le banc d'essai montre aux modèles des images de votre bibliothèque : analysez "
            "d'abord une vidéo."
        )
    prefs = await anyio.to_thread.run_sync(load_preferences, c.db)
    data = BenchData(
        images=max(MIN_IMAGES, min(images, available)), language=prefs.language, model_keys=wanted
    )

    def create() -> str:
        if _active_run_id(c) is not None:
            raise ConflictError("Un test est déjà en cours : attendez sa fin, ou arrêtez-le.")
        with c.db.write() as session:
            row = BenchRun(status=BenchRunStatus.QUEUED.value, data=data.model_dump(mode="json"))
            session.add(row)
            session.flush()
            job = queue.enqueue_in(
                session, JobKind.BENCH_MODELS, payload={"run_id": row.id}, priority=BENCH_PRIORITY
            )
            row.job_id = job.id
            return row.id

    run_id = await anyio.to_thread.run_sync(create)
    return await anyio.to_thread.run_sync(get_run, c, run_id)


def _model_view(model: ModelRun, data: BenchData, ratings: Ratings) -> BenchModelView:
    return BenchModelView(
        key=model.key,
        display_name=model.display_name,
        publisher=model.publisher,
        params=model.params,
        quantization=model.quantization,
        size_bytes=model.size_bytes,
        status=model.status,
        error=model.error,
        load_s=model.load_s,
        context_length=model.context_length,
        parallel=model.parallel,
        calibration=model.calibration,
        scores=score(model, data.frames, data.language, ratings),
    )


def _summary(row: BenchRun, data: BenchData) -> BenchRunSummary:
    names = [model.display_name for model in data.models] or list(data.model_keys)
    ratings = _ratings(row)
    return BenchRunSummary(
        id=row.id,
        status=BenchRunStatus(row.status),
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        job_id=row.job_id,
        images=len(data.frames) or data.images,
        image_set=image_set(data.frames),
        language=data.language,
        models=names,
        model_runs=[_model_view(model, data, ratings) for model in data.models],
        note=data.note,
        error=data.error,
    )


def list_runs(c: AppContainer) -> list[BenchRunSummary]:
    """The history: the runs, the latest first, each with what its models measured."""
    bench_job.settle_interrupted(c.db)
    with c.db.read() as session:
        rows = session.execute(
            sa.select(BenchRun).order_by(BenchRun.created_at.desc()).limit(RUNS_LISTED)
        ).scalars()
        return [_summary(row, BenchData.model_validate(row.data)) for row in rows]


def _ratings(row: BenchRun) -> Ratings:
    return {
        str(frame): {str(model): int(mark) for model, mark in marks.items()}
        for frame, marks in (row.ratings or {}).items()
        if isinstance(marks, dict)
    }


def _answer(described: Answer | None, located: Answer | None) -> BenchAnswer:
    if described is None:
        return BenchAnswer(error="non demandé")
    data = described.data or {}
    return BenchAnswer(
        ok=described.ok,
        error=described.error,
        caption=str(data.get("caption") or ""),
        description=str(data.get("description") or ""),
        subjects=[
            " — ".join(
                part
                for part in (str(s.get("label") or ""), str(s.get("description") or ""))
                if part
            )
            for s in data.get("subjects") or []
            if isinstance(s, dict)
        ],
        visible_text=str(data.get("visible_text") or ""),
        beings=located.beings if located is not None and located.ok else None,
    )


def _frame(frame: BenchFrame, models: list[ModelRun]) -> BenchFrameView:
    answers = {}
    for model in models:
        if model.frames is None:
            continue
        answers[model.key] = _answer(
            model.frames.answers.get(frame.keyframe_id),
            model.positions.answers.get(frame.keyframe_id) if model.positions else None,
        )
    return BenchFrameView(
        keyframe_id=frame.keyframe_id,
        video_id=frame.video_id,
        filename=frame.filename,
        t_s=frame.t_s,
        image_path=frame.image_path,
        thumb_path=frame.thumb_path,
        text=frame.text,
        beings=frame.beings,
        answers=answers,
    )


def get_run(c: AppContainer, run_id: str) -> BenchRunView:
    """A run with every measure (computed now from what it stored) and every answer."""
    bench_job.settle_interrupted(c.db)
    with c.db.read() as session:
        row = session.get(BenchRun, run_id)
        if row is None:
            raise NotFoundError(f"Test introuvable : {run_id}")
        job = session.get(Job, row.job_id) if row.job_id else None
        data = BenchData.model_validate(row.data)
        active = BenchRunStatus(row.status).is_active
        return BenchRunView(
            **_summary(row, data).model_dump(),
            context_length=data.context_length,
            parallel=data.parallel,
            frames=[_frame(frame, data.models) for frame in data.frames],
            ratings=_ratings(row),
            previous=data.previous or [],
            progress=(job.progress if job else 0.0) if active else 1.0,
            message=job.message if job and active else None,
        )


def delete_run(c: AppContainer, run_id: str) -> None:
    bench_job.settle_interrupted(c.db)
    with c.db.write() as session:
        row = session.get(BenchRun, run_id)
        if row is None:
            raise NotFoundError(f"Test introuvable : {run_id}")
        if BenchRunStatus(row.status).is_active:
            raise ConflictError("Ce test est en cours : arrêtez-le avant de le supprimer.")
        session.delete(row)


def annotate(c: AppContainer, run_id: str, note: str | None) -> None:
    """Store (or clear, with None or blanks) the user's note on a run: what the history shows
    to tell this run from the others."""
    text = " ".join((note or "").split())
    if len(text) > MAX_NOTE:
        raise InvalidInputError(f"Une note fait au plus {MAX_NOTE} caractères.")
    with c.db.write() as session:
        row = session.get(BenchRun, run_id)
        if row is None:
            raise NotFoundError(f"Test introuvable : {run_id}")
        row.data = {**row.data, "note": text or None}


def rate(c: AppContainer, run_id: str, keyframe_id: str, model: str, rating: int | None) -> None:
    """Store (or clear, with None) the blind rating of one model's description of one frame."""
    if rating is not None and not 0 <= rating <= MAX_RATING:
        raise InvalidInputError(f"Une note va de 0 à {MAX_RATING}.")
    with c.db.write() as session:
        row = session.get(BenchRun, run_id)
        if row is None:
            raise NotFoundError(f"Test introuvable : {run_id}")
        data = BenchData.model_validate(row.data)
        if keyframe_id not in {frame.keyframe_id for frame in data.frames}:
            raise InvalidInputError("Cette image ne fait pas partie du test.")
        if model not in data.model_keys:
            raise InvalidInputError("Ce modèle ne fait pas partie du test.")
        ratings = _ratings(row)
        marks = ratings.setdefault(keyframe_id, {})
        if rating is None:
            marks.pop(model, None)
        else:
            marks[model] = rating
        row.ratings = {frame: marks for frame, marks in ratings.items() if marks}
