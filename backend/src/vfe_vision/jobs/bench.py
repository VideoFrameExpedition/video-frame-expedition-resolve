"""The model bench job: each vision model the user ticked is loaded alone in LM
Studio, asked the requests of the analyses on the same frames of the library, measured, then
unloaded; the model that was loaded before comes back at the end.

The only place where the application loads or unloads a model, on the user's request,
while no analysis runs (the worker gives this job the queue to itself). Nothing of the library
changes: the answers go to ``bench_runs``, never to the analyses or to their LLM cache.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anyio
import sqlalchemy as sa
from pydantic import BaseModel

from vfe_vision.adapters.imaging import encode_jpeg, read_image, resize_long_side
from vfe_vision.adapters.lmstudio.budget import TokenBudget
from vfe_vision.adapters.lmstudio.catalog import LoadedInstance, ModelInfo
from vfe_vision.adapters.lmstudio.client import (
    ChatImage,
    LmStudioClient,
    LmStudioTruncatedError,
    LmStudioUnavailableError,
    StructuredResult,
)
from vfe_vision.core.cancel import CancelToken, stopped_by
from vfe_vision.core.errors import CancelledError, NotFoundError, VfeError
from vfe_vision.core.logging import get_logger
from vfe_vision.db.models import (
    BenchRun,
    Detection,
    Job,
    Keyframe,
    LibraryRoot,
    OcrText,
    StageRun,
    SubjectScan,
    Video,
)
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.session import Database
from vfe_vision.domain.bench import (
    CONTEXT_LENGTH,
    MIN_DETECTOR_SCORE,
    PARALLEL,
    Answer,
    Batch,
    BenchBeing,
    BenchCalibration,
    BenchData,
    BenchFrame,
    BenchModelStatus,
    BenchPreviousModel,
    BenchRunStatus,
    Candidate,
    ModelRun,
    Vram,
    choose_frames,
)
from vfe_vision.domain.enums import JobKind, JobStatus, StageStatus
from vfe_vision.domain.grounding import grounding_schema, to_detections
from vfe_vision.domain.subjects import Category, Source, dedupe
from vfe_vision.domain.vision import FrameAnalysis
from vfe_vision.domain.vision_profile import BoxConvention, BoxField, prior_for
from vfe_vision.pipeline import vision_profile
from vfe_vision.pipeline.stage import ProgressFn
from vfe_vision.pipeline.stages import grounding as grounding_stage
from vfe_vision.pipeline.stages.vision_frames import (
    GROUNDING_MULTIPLE,
    context_hints,
    frame_prompt,
    sanitize,
)
from vfe_vision.ports.gpu import VramMeter
from vfe_vision.storage.artifacts import ArtifactStore

log = get_logger(__name__)

SETTLE_S = 2.0  # the driver's figures follow a load or an unload by a moment
RELEASE_TIMEOUT_S = 20.0
RELEASE_SLACK_MIB = 256
REFERENCE_KINDS = frozenset({Category.PERSON.value, Category.MAMMAL.value, Category.BIRD.value})
INTERRUPTED = "Interrompu par l'arrêt de l'application"
NEEDS_LMSTUDIO = (
    "Le banc d'essai demande LM Studio : il charge et décharge les modèles un par un, ce "
    "qu'un serveur compatible OpenAI ne permet pas."
)


@dataclass(slots=True)
class BenchTools:
    db: Database
    artifacts: ArtifactStore
    lmstudio: LmStudioClient
    vram: VramMeter | None = None
    settle_s: float = SETTLE_S  # tests wait for nothing


# ------------------------------------------------------------------------------ storage
def read_run(db: Database, run_id: str) -> tuple[BenchRun, BenchData]:
    with db.read() as session:
        row = session.get(BenchRun, run_id)
        if row is None:
            raise NotFoundError(f"Test introuvable : {run_id}")
        session.expunge(row)
        return row, BenchData.model_validate(row.data)


def save_run(
    db: Database, run_id: str, data: BenchData, status: BenchRunStatus | None = None
) -> None:
    """Store the run as it is now (the page follows it); ``status``: also its new state."""
    now = datetime.now(UTC)
    with db.write() as session:
        row = session.get(BenchRun, run_id)
        if row is None:  # deleted meanwhile: nothing to follow
            return
        row.data = data.model_dump(mode="json")
        if status is not None:
            row.status = status.value
            if status == BenchRunStatus.RUNNING:
                row.started_at = row.started_at or now
            elif not status.is_active:
                row.finished_at = now


def settle_interrupted(db: Database) -> int:
    """Runs left queued or running whose job is no longer active (the application stopped, or
    the job was cancelled before its turn): they are shown as they ended instead of running
    forever. Returns how many."""
    active = sa.select(Job.id).where(
        Job.kind == JobKind.BENCH_MODELS, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
    )
    stale = (
        sa.select(BenchRun)
        .where(BenchRun.status.in_([BenchRunStatus.QUEUED.value, BenchRunStatus.RUNNING.value]))
        .where(sa.or_(BenchRun.job_id.is_(None), BenchRun.job_id.not_in(active)))
    )
    with db.read() as session:
        if session.execute(stale.limit(1)).first() is None:
            return 0
    count = 0
    with db.write() as session:
        for row in session.execute(stale).scalars():
            job = session.get(Job, row.job_id) if row.job_id else None
            cancelled = job is not None and job.status == JobStatus.CANCELLED
            data = BenchData.model_validate(row.data)
            if not cancelled:
                data.error = data.error or (job.error if job else None) or INTERRUPTED
            for model in data.models:
                if model.status in {BenchModelStatus.PENDING, BenchModelStatus.RUNNING}:
                    model.status = BenchModelStatus.CANCELLED
            row.data = data.model_dump(mode="json")
            row.status = (BenchRunStatus.CANCELLED if cancelled else BenchRunStatus.FAILED).value
            row.finished_at = datetime.now(UTC)
            count += 1
    return count


# ------------------------------------------------------------------------------ the frames
def available_frames(db: Database) -> int:
    """How many distinct keyframes the library has to test the models on."""
    with db.read() as session:
        return int(
            session.execute(
                sa.select(sa.func.count())
                .select_from(Keyframe)
                .where(Keyframe.duplicate_of.is_(None))
            ).scalar_one()
        )


def gather_frames(db: Database, count: int) -> list[BenchFrame]:
    """The frames of a run (``domain.bench.choose_frames``), each with what the application
    already knows of it: the lines the OCR read, the beings the CPU detector boxed."""
    detected = sa.exists().where(
        Detection.keyframe_id == Keyframe.id,
        Detection.source == Source.DETECTOR.value,
        Detection.score >= MIN_DETECTOR_SCORE,
        Detection.category.in_(REFERENCE_KINDS),
    )
    with db.read() as session:
        candidates = [
            Candidate(row.id, row.video_id, bool(row.has_text), bool(row.has_beings))
            for row in session.execute(
                sa.select(
                    Keyframe.id,
                    Keyframe.video_id,
                    sa.exists().where(OcrText.keyframe_id == Keyframe.id).label("has_text"),
                    detected.label("has_beings"),
                ).where(Keyframe.duplicate_of.is_(None))
            )
        ]
    return frames_of(db, choose_frames(candidates, count))


def frames_of(db: Database, chosen: list[str]) -> list[BenchFrame]:
    """These keyframes, in this order, each with what the application already knows of it: the
    lines the OCR read, the beings the CPU detector boxed. A keyframe that no longer exists is
    left out."""
    with db.read() as session:
        rows = {
            row.id: row
            for row in session.execute(
                sa.select(
                    Keyframe.id, Keyframe.video_id, Keyframe.t_s, Keyframe.image_path,
                    Keyframe.thumb_path, Video.filename,
                )
                .join(Video, Video.id == Keyframe.video_id)
                .where(Keyframe.id.in_(chosen))
            )
        }  # fmt: skip
        videos = {row.video_id for row in rows.values()}
        read_videos = set(
            session.execute(
                sa.select(StageRun.video_id)
                .where(StageRun.video_id.in_(videos), StageRun.stage == "ocr")
                .where(StageRun.status.in_([StageStatus.SUCCEEDED, StageStatus.CACHED]))
            ).scalars()
        ) | set(
            session.execute(
                sa.select(OcrText.video_id).where(OcrText.video_id.in_(videos)).distinct()
            ).scalars()
        )
        lines: dict[str, list[str]] = {}
        for keyframe_id, text in session.execute(
            sa.select(OcrText.keyframe_id, OcrText.text)
            .where(OcrText.keyframe_id.in_(chosen))
            .order_by(OcrText.keyframe_id, OcrText.idx)
        ):
            lines.setdefault(keyframe_id, []).append(text)
        scanned = set(
            session.execute(
                sa.select(SubjectScan.keyframe_id).where(
                    SubjectScan.keyframe_id.in_(chosen),
                    SubjectScan.source == Source.DETECTOR.value,
                )
            ).scalars()
        )
        boxes: dict[str, list[BenchBeing]] = {}
        for found in session.execute(
            sa.select(Detection)
            .where(Detection.keyframe_id.in_(chosen), Detection.source == Source.DETECTOR.value)
            .where(Detection.score >= MIN_DETECTOR_SCORE, Detection.category.in_(REFERENCE_KINDS))
            .order_by(Detection.keyframe_id, Detection.idx)
        ).scalars():
            boxes.setdefault(found.keyframe_id, []).append(
                BenchBeing(category=found.category, box=[float(v) for v in found.box],
                         label=found.label)
            )  # fmt: skip
    frames = []
    for keyframe_id in chosen:
        row = rows.get(keyframe_id)
        if row is None:
            continue
        seen = keyframe_id in scanned or keyframe_id in boxes
        frames.append(
            BenchFrame(
                keyframe_id=keyframe_id, video_id=row.video_id, filename=row.filename,
                t_s=row.t_s, image_path=row.image_path, thumb_path=row.thumb_path,
                text=lines.get(keyframe_id, []) if row.video_id in read_videos else None,
                beings=boxes.get(keyframe_id, []) if seen else None,
            )
        )  # fmt: skip
    return frames


@dataclass(frozen=True, slots=True)
class _Shot:
    """One frame as the analyses would send it: the image and the description request."""

    frame: BenchFrame
    image: ChatImage
    system: str
    user: str


def _shots(tools: BenchTools, frames: list[BenchFrame], language: str) -> list[_Shot]:
    prefs = load_preferences(tools.db)
    ids = [frame.keyframe_id for frame in frames]
    videos = {frame.video_id for frame in frames}
    with tools.db.read() as session:
        index = dict(
            session.execute(sa.select(Keyframe.id, Keyframe.idx).where(Keyframe.id.in_(ids))).all()
        )
        counts = dict(
            session.execute(
                sa.select(Keyframe.video_id, sa.func.count())
                .where(Keyframe.video_id.in_(videos), Keyframe.duplicate_of.is_(None))
                .group_by(Keyframe.video_id)
            ).all()
        )
        facts = {
            row.id: row
            for row in session.execute(
                sa.select(Video.id, Video.duration_s, LibraryRoot.analysis_focus)
                .join(LibraryRoot, LibraryRoot.id == Video.root_id)
                .where(Video.id.in_(videos))
            )
        }
    hints = {video_id: context_hints(tools.db, video_id) for video_id in videos}
    shots = []
    for frame in frames:
        image = resize_long_side(
            read_image(tools.artifacts.resolve(frame.image_path)),
            prefs.vision_image_long_side,
            multiple=GROUNDING_MULTIPLE,
        )
        height, width = image.shape[:2]
        video = facts[frame.video_id]
        rendered = frame_prompt(
            frame_number=int(index[frame.keyframe_id]) + 1,
            frame_count=int(counts.get(frame.video_id, 1)),
            t_s=frame.t_s,
            filename=frame.filename,
            duration_s=video.duration_s,
            focus=video.analysis_focus or prefs.analysis_focus,
            hints=hints[frame.video_id],
            language=language,
        )
        shots.append(
            _Shot(frame, ChatImage(encode_jpeg(image, quality=90), width, height),
                  rendered.system, rendered.user)
        )  # fmt: skip
    return shots


# ------------------------------------------------------------------------------ one run
@dataclass(slots=True)
class _Run:
    tools: BenchTools
    run_id: str
    data: BenchData
    cancel: CancelToken
    progress: ProgressFn
    shots: list[_Shot] = field(default_factory=list)
    max_tokens: int = 900
    steps: int = 1
    done: int = 0

    def step(self, message: str) -> None:
        self.done += 1
        self.progress(min(0.99, self.done / self.steps), message)

    def save(self, status: BenchRunStatus | None = None) -> None:
        save_run(self.tools.db, self.run_id, self.data, status)


async def run_bench(
    tools: BenchTools, job: Job, *, cancel: CancelToken, progress: ProgressFn
) -> tuple[JobStatus, str | None]:
    """Run the bench a job asks for; the run's row follows every step."""
    run_id = str(job.payload.get("run_id") or "")
    _, data = await anyio.to_thread.run_sync(read_run, tools.db, run_id)
    run = _Run(tools, run_id, data, cancel, progress)
    try:
        return await _run(run)
    except CancelledError:
        await anyio.to_thread.run_sync(run.save, BenchRunStatus.CANCELLED)
        raise
    except VfeError as exc:
        data.error = exc.detail
        await anyio.to_thread.run_sync(run.save, BenchRunStatus.FAILED)
        raise
    except Exception as exc:
        data.error = f"{type(exc).__name__}: {exc}"
        await anyio.to_thread.run_sync(run.save, BenchRunStatus.FAILED)
        raise


async def _run(run: _Run) -> tuple[JobStatus, str | None]:
    tools, data = run.tools, run.data
    data.error = None
    catalogue = {model.key: model for model in await tools.lmstudio.list_models()}
    if tools.lmstudio.serves_its_models:  # nothing to load nor unload there
        raise VfeError(NEEDS_LMSTUDIO)
    data.models = [_model_run(key, catalogue.get(key)) for key in data.model_keys]
    for previous in data.previous or []:
        previous.restored, previous.error = None, None
    if data.previous is None:  # a job tried again keeps what it found the first time
        data.previous = [
            BenchPreviousModel(
                key=model.key, instance_id=instance.id, display_name=model.display_name,
                context_length=instance.context_length, parallel=instance.parallel,
            )
            for model in catalogue.values()
            if model.type == "llm"
            for instance in model.loaded_instances
        ]  # fmt: skip
    await anyio.to_thread.run_sync(run.save, BenchRunStatus.RUNNING)
    try:
        run.progress(0.0, "Préparation des images")
        await _free_the_card(run, catalogue)
        prefs = await anyio.to_thread.run_sync(load_preferences, tools.db)
        run.max_tokens = prefs.vision_max_tokens
        data.frames = await anyio.to_thread.run_sync(gather_frames, tools.db, data.images)
        if not data.frames:
            raise VfeError("Aucune image à montrer aux modèles : analysez d'abord une vidéo.")
        run.shots = await anyio.to_thread.run_sync(_shots, tools, data.frames, data.language)
        pending = [model for model in data.models if model.status == BenchModelStatus.PENDING]
        run.steps = max(1, len(pending) * (2 * len(run.shots) + 2))
        for model in pending:
            run.cancel.raise_if_cancelled()
            await _test(run, model, catalogue[model.key])
            await anyio.to_thread.run_sync(run.save)
    finally:
        for model in data.models:
            if model.status in {BenchModelStatus.PENDING, BenchModelStatus.RUNNING}:
                model.status = BenchModelStatus.CANCELLED
        await _restore(run)
    tested = [model for model in data.models if model.status == BenchModelStatus.DONE]
    if not tested:
        raise VfeError(
            next((m.error for m in data.models if m.error), None)
            or "Aucun modèle n'a pu être testé."
        )
    status = BenchRunStatus.SUCCEEDED if len(tested) == len(data.models) else BenchRunStatus.PARTIAL
    await anyio.to_thread.run_sync(run.save, status)
    message = f"{len(tested)} modèle(s) testé(s) sur {len(data.frames)} images"
    if status == BenchRunStatus.PARTIAL:
        message += f", {len(data.models) - len(tested)} en échec"
    return JobStatus(status.value), message


def _model_run(key: str, model: ModelInfo | None) -> ModelRun:
    if model is None:
        return ModelRun(
            key=key, display_name=key, status=BenchModelStatus.LOAD_FAILED,
            error="Modèle introuvable dans LM Studio.",
        )  # fmt: skip
    return ModelRun(
        key=key, display_name=model.display_name, publisher=model.publisher,
        architecture=model.architecture, params=model.params, quantization=model.quantization,
        size_bytes=model.size_bytes, reasoning_capable=bool(model.reasoning_options),
    )  # fmt: skip


async def _free_the_card(run: _Run, catalogue: dict[str, ModelInfo]) -> None:
    """Unload every language model: each one is measured alone on the card."""
    loaded = [
        instance.id
        for model in catalogue.values()
        if model.type == "llm"
        for instance in model.loaded_instances
    ]
    for instance_id in loaded:
        await run.tools.lmstudio.unload_model(instance_id)
    if loaded:
        await anyio.sleep(run.tools.settle_s)


async def _restore(run: _Run) -> None:
    """Load again what was loaded before the run, with its settings. Best effort: a failure is
    told in the run, the user loads the model in LM Studio."""
    for previous in run.data.previous or []:
        if previous.restored:
            continue
        try:
            await run.tools.lmstudio.load_model(
                previous.key, context_length=previous.context_length, parallel=previous.parallel
            )
        except VfeError as exc:
            previous.restored, previous.error = False, exc.detail
            log.warning(
                "model not loaded again after the bench", model=previous.key, error=exc.detail
            )
        else:
            previous.restored, previous.error = True, None


def _vram(meter: VramMeter | None) -> Vram | None:
    usage = meter.usage() if meter is not None else None
    return None if usage is None else Vram(used=usage.used, free=usage.free, total=usage.total)


async def _measure(run: _Run) -> Vram | None:
    return await anyio.to_thread.run_sync(_vram, run.tools.vram)


async def _released(run: _Run, before: Vram | None) -> None:
    """Wait until the card is back to what it held before the load (or give up quietly)."""
    await anyio.sleep(run.tools.settle_s)
    if before is None or run.tools.settle_s <= 0:
        return
    deadline = time.monotonic() + RELEASE_TIMEOUT_S
    while time.monotonic() < deadline:
        now = await _measure(run)
        if now is None or now.used <= before.used + RELEASE_SLACK_MIB:
            return
        await anyio.sleep(1.0)


# ------------------------------------------------------------------------------ one model
@dataclass(frozen=True, slots=True)
class _Loaded:
    """The model under test, as LM Studio holds it now."""

    name: str
    model: ModelInfo
    instance: LoadedInstance
    budget: TokenBudget

    @property
    def reasoning_off(self) -> bool:
        return bool(self.model.reasoning_options)


async def _test(run: _Run, model_run: ModelRun, listed: ModelInfo) -> None:
    lm = run.tools.lmstudio
    model_run.status = BenchModelStatus.RUNNING
    run.step(f"{model_run.display_name} : chargement")
    await anyio.to_thread.run_sync(run.save)
    model_run.vram_before = await _measure(run)
    context = min(CONTEXT_LENGTH, listed.max_context_length or CONTEXT_LENGTH)
    try:
        loaded = await lm.load_model(model_run.key, context_length=context, parallel=PARALLEL)
    except LmStudioUnavailableError:
        raise
    except VfeError as exc:  # refused: not enough memory, a guardrail of LM Studio…
        model_run.status, model_run.error = BenchModelStatus.LOAD_FAILED, exc.detail
        return
    try:
        model_run.load_s = loaded.load_time_s
        await anyio.sleep(run.tools.settle_s)
        model_run.vram_loaded = await _measure(run)
        model, instance = await _instance(lm, listed, loaded.instance_id, context)
        model_run.context_length, model_run.parallel = instance.context_length, instance.parallel
        budget = TokenBudget.for_context(instance.context_length or context, instance.parallel or 1)
        target = _Loaded(model_run.display_name, model, instance, budget)
        run.step(f"{target.name} : calibrage des positions")
        setup = await _calibrate(run, model_run, target)
        model_run.frames = await _describe(run, target)
        await _peak(run, model_run)
        await anyio.to_thread.run_sync(run.save)
        if setup is not None:
            model_run.positions = await _locate(run, target, setup)
            await _peak(run, model_run)
        model_run.status = BenchModelStatus.DONE
    except CancelledError:
        model_run.status = BenchModelStatus.CANCELLED
        raise
    except LmStudioUnavailableError as exc:  # LM Studio is gone: the run stops there
        model_run.status, model_run.error = BenchModelStatus.FAILED, exc.detail
        raise
    except VfeError as exc:
        model_run.status, model_run.error = BenchModelStatus.FAILED, exc.detail
    finally:
        with contextlib.suppress(VfeError):
            await lm.unload_model(loaded.instance_id)
        await _released(run, model_run.vram_before)


async def _peak(run: _Run, model_run: ModelRun) -> None:
    now = await _measure(run)
    if now is not None:
        model_run.vram_peak_mib = max(model_run.vram_peak_mib or 0, now.used)


async def _instance(
    lm: LmStudioClient, listed: ModelInfo, instance_id: str, context: int
) -> tuple[ModelInfo, LoadedInstance]:
    """The instance just loaded, as LM Studio describes it (its real context and slots)."""
    for model in await lm.list_models():
        for instance in model.loaded_instances:
            if instance.id == instance_id:
                return model, instance
    # Not listed (a variant that is not the selected one): what was asked for stands.
    return listed, LoadedInstance(id=instance_id, context_length=context, parallel=PARALLEL)


async def _calibrate(
    run: _Run, model_run: ModelRun, target: _Loaded
) -> tuple[BoxConvention, BoxField] | None:
    """How the model writes boxes, measured here without storing a profile: the
    calibration of the analyses stays theirs. Returns the convention to read its boxes with,
    or None when the grounding stage would skip this model."""
    prior = prior_for(target.model.key, target.model.architecture)
    try:
        profile = await vision_profile.probe(
            run.tools.lmstudio, target.budget, target.model, target.instance, cancel=run.cancel
        )
    except (CancelledError, LmStudioUnavailableError):
        raise
    except VfeError as exc:
        model_run.calibration = BenchCalibration(
            source="prior" if prior else "none",
            enabled=prior is not None,
            reason=exc.detail,
            convention=prior[0].value if prior else None,
            box_field=prior[1].value if prior else None,
        )
        return prior
    measured = profile.grounding
    calibration = BenchCalibration(
        source="prior" if prior else "profile",
        enabled=measured.enabled,
        convention=measured.convention.value if measured.convention else None,
        box_field=measured.box_field.value if measured.box_field else None,
        mean_iou=measured.mean_iou,
        reason=measured.reason,
        reasoning_tokens=profile.reasoning_tokens_seen,
        truncated=profile.truncated,
    )
    model_run.calibration = calibration
    if prior is not None:  # Qwen3-VL: its verified convention, whatever the probe says
        calibration.enabled = True
        calibration.convention, calibration.box_field = prior[0].value, prior[1].value
        return prior
    if not measured.enabled or measured.convention is None:
        return None
    return measured.convention, measured.box_field or BoxField.BBOX_2D


@dataclass(frozen=True, slots=True)
class _Request:
    keyframe_id: str
    label: str
    kwargs: dict[str, Any]


_Reader = Callable[[_Request, StructuredResult[Any]], Answer]


async def _batch(run: _Run, target: _Loaded, requests: list[_Request], read: _Reader) -> Batch:
    """The requests, as many at a time as the instance has slots (as the stages do)."""
    slots = anyio.Semaphore(max(1, target.instance.parallel or 1))
    answers: dict[str, Answer] = {}
    gone: list[str] = []  # LM Studio stopped answering: the rest is not asked

    async def one(request: _Request) -> None:
        async with slots:
            if gone:
                answers[request.keyframe_id] = Answer(error=gone[0])
                return
            answers[request.keyframe_id] = await _ask(run, target, request, read, gone)
            run.step(request.label)

    started = time.perf_counter()
    async with stopped_by(run.cancel), anyio.create_task_group() as group:
        for request in requests:
            group.start_soon(one, request)
    wall = time.perf_counter() - started
    if gone:
        raise LmStudioUnavailableError(gone[0])
    return Batch(
        wall_s=round(wall, 3),
        answers={request.keyframe_id: answers[request.keyframe_id] for request in requests},
    )


async def _ask(
    run: _Run, target: _Loaded, request: _Request, read: _Reader, gone: list[str]
) -> Answer:
    try:
        result = await run.tools.lmstudio.chat_structured(
            model=target.instance.id,
            budget=target.budget,
            reasoning_off=target.reasoning_off,
            **request.kwargs,
        )
    except LmStudioTruncatedError as exc:
        return Answer(
            error=exc.detail,
            truncated=True,
            prompt_tokens=_count(exc.extra.get("prompt_tokens")),
            completion_tokens=_count(exc.extra.get("completion_tokens")),
            reasoning_tokens=_count(exc.extra.get("reasoning_tokens")),
        )
    except LmStudioUnavailableError as exc:
        gone.append(exc.detail)
        return Answer(error=exc.detail)
    except VfeError as exc:
        return Answer(error=exc.detail)
    answer = read(request, result)
    answer.ok = True
    answer.latency_ms = result.latency_ms
    answer.prompt_tokens = result.prompt_tokens
    answer.completion_tokens = result.completion_tokens
    answer.reasoning_tokens = result.reasoning_tokens
    answer.repaired = result.repaired
    return answer


def _count(value: object) -> int | None:
    return value if isinstance(value, int) else None


async def _describe(run: _Run, target: _Loaded) -> Batch:
    """The frame descriptions, as ``vision_frames`` asks for them."""
    total = len(run.shots)
    filenames = {shot.frame.keyframe_id: shot.frame.filename for shot in run.shots}
    requests = [
        _Request(
            shot.frame.keyframe_id,
            f"{target.name} : descriptions ({index}/{total})",
            {
                "system": shot.system,
                "user_text": shot.user,
                "output": FrameAnalysis,
                "images": [shot.image],
                "max_tokens": run.max_tokens,
            },
        )
        for index, shot in enumerate(run.shots, 1)
    ]

    def read(request: _Request, result: StructuredResult[Any]) -> Answer:
        analysis = sanitize(result.data, filenames[request.keyframe_id])
        return Answer(data=analysis.model_dump(mode="json"))

    return await _batch(run, target, requests, read)


async def _locate(run: _Run, target: _Loaded, setup: tuple[BoxConvention, BoxField]) -> Batch:
    """The living beings and their boxes, as ``grounding`` asks for them."""
    convention, box_field = setup
    output: type[BaseModel] = grounding_schema(box_field)
    rendered = grounding_stage.subjects_prompt(run.data.language, box_field)
    total = len(run.shots)
    sizes = {shot.frame.keyframe_id: (shot.image.width, shot.image.height) for shot in run.shots}
    requests = [
        _Request(
            shot.frame.keyframe_id,
            f"{target.name} : positions ({index}/{total})",
            {
                "system": rendered.system,
                "user_text": rendered.user,
                "output": output,
                "images": [shot.image],
                "max_tokens": grounding_stage.MAX_TOKENS,
                "temperature": 0.0,
                "purpose": grounding_stage.PROMPT_NAME,
            },
        )
        for index, shot in enumerate(run.shots, 1)
    ]

    def read(request: _Request, result: StructuredResult[Any]) -> Answer:
        found = dedupe(to_detections(result.data, convention, *sizes[request.keyframe_id]))
        return Answer(
            data=result.data.model_dump(mode="json"),
            beings=[
                BenchBeing(
                    category=being.category.value,
                    box=being.box.rounded(),
                    label=being.label,
                    main=being.main,
                )
                for being in found
            ],
        )

    return await _batch(run, target, requests, read)
