"""Stage ``vision_frames``: structured description of each keyframe by the local vision model."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as fixed_zone
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
import sqlalchemy as sa

from vfe_vision.adapters.imaging import encode_jpeg, read_image, resize_long_side
from vfe_vision.adapters.lmstudio import prompts
from vfe_vision.adapters.lmstudio.catalog import LoadedInstance, ModelInfo, pick_vision_instance
from vfe_vision.adapters.lmstudio.client import (
    ChatImage,
    LmStudioUnavailableError,
    StructuredResult,
)
from vfe_vision.adapters.lmstudio.schema import field_guide
from vfe_vision.core.cancel import CANCEL_POLL_S
from vfe_vision.core.errors import ServiceUnavailableError, VfeError
from vfe_vision.db.models import ContextPlace, Keyframe, LlmCall, Video
from vfe_vision.db.models import FrameAnalysis as FrameAnalysisRow
from vfe_vision.db.session import Database
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.timecode import format_clock
from vfe_vision.domain.vision import FRAME_ANALYSIS_SCHEMA_VERSION, FrameAnalysis
from vfe_vision.pipeline.stage import Resource, Stage, StageContext, StageFamily, StageOutcome

PROMPT_NAME = "frame_analysis"
PROMPT_VERSION = 2
GROUNDING_MULTIPLE = 32
_TAG_RE = re.compile(r"</?\s*untrusted[^>]*>", re.IGNORECASE)


@dataclass(slots=True)
class _Frame:
    id: str
    idx: int
    t_s: float
    image_path: str


@dataclass(slots=True)
class _Counters:
    analysed: int = 0
    cached: int = 0
    failed: int = 0
    reasoning_tokens: int = 0


def capture_hint(utc: datetime, timezone: str | None, utc_offset_min: int | None = None) -> str:
    """Date and hour only: enough for season and daylight, and stable when a rule is refined
    by a few seconds (the hint is part of the per-frame LLM cache key)."""
    local, label = utc, "UTC"
    if timezone:
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
            local, label = utc.astimezone(ZoneInfo(timezone)), "local time"
    if label == "UTC" and utc_offset_min is not None:
        local = utc.astimezone(fixed_zone(timedelta(minutes=utc_offset_min)))
        label = "local time"
    return f"Capture date: {local:%Y-%m-%d}, around {local:%H}:00 {label}"


def frame_prompt(
    *,
    frame_number: int,
    frame_count: int,
    t_s: float,
    filename: str,
    duration_s: float | None,
    focus: str | None,
    hints: list[str],
    language: str,
) -> prompts.RenderedPrompt:
    """What the model is asked about one frame (shared with the model bench: the same
    text, to the character, as the analyses send)."""
    return prompts.render(
        PROMPT_NAME,
        PROMPT_VERSION,
        frame_number=frame_number,
        frame_count=frame_count,
        timecode=format_clock(t_s, millis=True),
        filename=filename,
        duration=format_clock(duration_s or 0.0),
        focus=focus,
        hints=hints,
        transcript=None,
        language=language,
        field_guide=field_guide(FrameAnalysis),
    )


def context_hints(db: Database, video_id: str) -> list[str]:
    """Capture time, camera, orientation and place of a video, as hints for its frames."""
    with db.read() as session:
        video = session.get_one(Video, video_id)
        hints: list[str] = []
        if video.captured_at and video.captured_at_confidence in {"high", "medium"}:
            hints.append(
                capture_hint(
                    video.captured_at, video.capture_timezone, video.capture_utc_offset_min
                )
            )
        if video.camera_make or video.camera_model:
            hints.append(f"Camera: {video.camera_make or ''} {video.camera_model or ''}".strip())
        if video.orientation:
            hints.append(f"Video orientation: {video.orientation.value}")
        place = session.get(ContextPlace, video_id)
        if place is not None:
            # Stable wording (no source, no distance): an online/offline switch or a
            # transient outage must not re-describe every frame.
            parts = [place.locality, place.region, place.country]
            unique = [p for i, p in enumerate(parts) if p and p not in parts[:i]]
            if unique:
                hints.append("Place: " + ", ".join(unique))
            feature = place.data.get("feature")
            if isinstance(feature, dict) and feature.get("name"):
                hints.append(f"Nearby: {feature['name']} ({feature.get('type', 'feature')})")
    return hints


def sanitize(analysis: FrameAnalysis, filename: str) -> FrameAnalysis:
    """Drop echoes of the prompt (untrusted tags, file name) from the visible text."""
    text = _TAG_RE.sub("", analysis.visible_text).strip()
    stem = filename.rsplit(".", 1)[0].casefold()
    if text.casefold() in {filename.casefold(), stem} or text.casefold().startswith(stem + "."):
        text = ""
    return analysis.model_copy(update={"visible_text": text})


async def run_requests(
    ctx: StageContext, tasks: Sequence[Callable[[], Awaitable[None]]], *, parallel: int
) -> None:
    """Run one LM Studio request per task, at most ``parallel`` at a time (the loaded slots;
    the token budget still bounds them across videos). A cancelled job stops at once: requests
    waiting for their turn never start, and those in flight are abandoned."""
    slots = anyio.Semaphore(max(1, parallel))

    async def guarded(task: Callable[[], Awaitable[None]]) -> None:
        async with slots:
            if not ctx.cancel.cancelled:
                await task()

    with anyio.CancelScope() as scope:
        async with anyio.create_task_group() as group:

            async def watch() -> None:
                # The token is a threading.Event set by the worker's heartbeat thread: polled.
                while not ctx.cancel.cancelled:  # noqa: ASYNC110
                    await anyio.sleep(CANCEL_POLL_S)
                scope.cancel()

            group.start_soon(watch)
            async with anyio.create_task_group() as work:
                for task in tasks:
                    work.start_soon(guarded, task)
            group.cancel_scope.cancel()  # every request answered: stop watching


async def pick_vision_model(ctx: StageContext) -> tuple[ModelInfo, LoadedInstance] | None:
    """The vision instance already loaded in LM Studio, or None when unreachable."""
    try:
        models = await ctx.tools.lmstudio.list_models()
    except (ServiceUnavailableError, VfeError):  # unreachable, or an unexpected answer
        return None
    return pick_vision_instance(models, ctx.prefs.vision_model)


def cached_response(ctx: StageContext, key: str) -> dict[str, Any] | None:
    """A structured answer already obtained for exactly this request (``llm_calls``)."""
    with ctx.tools.db.read() as session:
        response: dict[str, Any] | None = session.execute(
            sa.select(LlmCall.response).where(LlmCall.cache_key == key)
        ).scalar_one_or_none()
        return response


def store_call(
    ctx: StageContext,
    key: str,
    *,
    purpose: str,
    model_key: str,
    prompt_version: str,
    schema_version: int,
    result: StructuredResult[Any],
) -> None:
    """Cache and audit one structured answer (latency, tokens)."""
    with ctx.tools.db.write() as session:
        exists = session.execute(
            sa.select(LlmCall.id).where(LlmCall.cache_key == key)
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                LlmCall(
                    cache_key=key,
                    purpose=purpose,
                    model=model_key,
                    prompt_version=prompt_version,
                    schema_version=schema_version,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    latency_ms=result.latency_ms,
                    response=result.data.model_dump(mode="json"),
                )
            )


class VisionFramesStage(Stage):
    name = "vision_frames"
    version = 1
    family = StageFamily.VISION
    requires = ("keyframes",)
    # Capture time, camera and place name are prompt hints when available. The model weather
    # is deliberately NOT a hint: the visual weather must stay an independent opinion.
    after = ("metadata", "place")
    resource = Resource.LMSTUDIO
    optional = True
    uses_focus = True

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        return {
            "prompt": f"{PROMPT_NAME}.v{PROMPT_VERSION}",
            "schema": FRAME_ANALYSIS_SCHEMA_VERSION,
            "language": prefs.language,
            "focus": ctx.focus,
            "long_side": prefs.vision_image_long_side,
            # Capture date, camera and place are only hints: a more precise place or a corrected
            # time does not redo descriptions of the same images unless an update is asked for.
            "hints": self._context_hints(ctx),
        }

    async def resolve_config(self, ctx: StageContext) -> dict[str, Any]:
        picked = await pick_vision_model(ctx)
        return {"model": picked[0].key if picked else None}

    async def execute(self, ctx: StageContext) -> StageOutcome:
        picked = await pick_vision_model(ctx)
        if picked is None:
            return StageOutcome.waiting_for_lmstudio(
                "LM Studio injoignable ou aucun modèle de vision chargé"
            )
        model, instance = picked
        await ctx.tools.lm_budget.resize(
            int((instance.context_length or 8192) * 0.9), instance.parallel or 1
        )
        frames = await anyio.to_thread.run_sync(self._load_frames, ctx)
        hints = await anyio.to_thread.run_sync(self._context_hints, ctx)
        counters = _Counters()
        total = max(1, len(frames))
        unavailable: list[str] = []

        async def analyse(frame: _Frame) -> None:
            if ctx.cancel.cancelled or unavailable:
                return
            try:
                cached = await self._analyse_frame(
                    ctx,
                    frame,
                    frame_count=len(frames),
                    model=model,
                    instance=instance,
                    hints=hints,
                    counters=counters,
                )
            except LmStudioUnavailableError as exc:
                unavailable.append(exc.detail)
                return
            except VfeError as exc:
                counters.failed += 1
                ctx.log.warning("frame analysis failed", frame=frame.idx, error=exc.detail)
                return
            counters.cached += int(cached)
            counters.analysed += int(not cached)
            done = counters.analysed + counters.cached + counters.failed
            ctx.progress(done / total, f"Image {done}/{total} décrite")

        # Concurrency is bounded by the token budget and the loaded slots.
        await run_requests(
            ctx, [partial(analyse, frame) for frame in frames], parallel=instance.parallel or 1
        )
        ctx.cancel.raise_if_cancelled()
        summary: dict[str, Any] = {
            "model": model.key,
            "frames": len(frames),
            "analysed": counters.analysed,
            "cached": counters.cached,
            "failed": counters.failed,
        }
        if counters.reasoning_tokens:  # thinking despite reasoning_off
            summary["reasoning_tokens"] = counters.reasoning_tokens
        if unavailable:
            return StageOutcome.skipped(unavailable[0], retryable=True, **summary)
        if frames and counters.failed == len(frames):
            raise VfeError(f"Aucune image n'a pu être décrite par {model.key}")
        return StageOutcome.ok(**summary)

    # ------------------------------------------------------------------ helpers
    def _load_frames(self, ctx: StageContext) -> list[_Frame]:
        with ctx.tools.db.read() as session:
            rows = session.execute(
                sa.select(Keyframe.id, Keyframe.idx, Keyframe.t_s, Keyframe.image_path)
                .where(Keyframe.video_id == ctx.video.id, Keyframe.duplicate_of.is_(None))
                .order_by(Keyframe.idx)
            ).all()
        return [_Frame(r.id, r.idx, r.t_s, r.image_path) for r in rows]

    def _context_hints(self, ctx: StageContext) -> list[str]:
        return context_hints(ctx.tools.db, ctx.video.id)

    async def _analyse_frame(
        self,
        ctx: StageContext,
        frame: _Frame,
        *,
        frame_count: int,
        model: ModelInfo,
        instance: LoadedInstance,
        hints: list[str],
        counters: _Counters,
    ) -> bool:
        """Describe one frame; return True when the answer came from the cache."""
        path = ctx.tools.artifacts.resolve(frame.image_path)
        image = await anyio.to_thread.run_sync(read_image, path)
        image = resize_long_side(
            image, ctx.prefs.vision_image_long_side, multiple=GROUNDING_MULTIPLE
        )
        jpeg = encode_jpeg(image, quality=90)
        height, width = image.shape[:2]
        rendered = frame_prompt(
            frame_number=frame.idx + 1,
            frame_count=frame_count,
            t_s=frame.t_s,
            filename=ctx.video.filename,
            duration_s=ctx.video.duration_s,
            focus=ctx.focus,
            hints=hints,
            language=ctx.prefs.language,
        )
        key = hashlib.sha256(
            jpeg
            + json.dumps(
                [model.key, rendered.version, FRAME_ANALYSIS_SCHEMA_VERSION, rendered.system,
                 rendered.user],
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()  # fmt: skip

        cached = await anyio.to_thread.run_sync(cached_response, ctx, key)
        if cached is not None:
            analysis = FrameAnalysis.model_validate(cached)
            from_cache = True
        else:
            result = await ctx.tools.lmstudio.chat_structured(
                model=instance.id,
                system=rendered.system,
                user_text=rendered.user,
                output=FrameAnalysis,
                images=[ChatImage(jpeg, width, height)],
                max_tokens=ctx.prefs.vision_max_tokens,
                budget=ctx.tools.lm_budget,
                reasoning_off=bool(model.reasoning_options),
            )
            analysis = result.data
            from_cache = False
            counters.reasoning_tokens += result.reasoning_tokens or 0
            await anyio.to_thread.run_sync(
                lambda: store_call(
                    ctx, key, purpose=PROMPT_NAME, model_key=model.key,
                    prompt_version=rendered.version, schema_version=FRAME_ANALYSIS_SCHEMA_VERSION,
                    result=result,
                )
            )  # fmt: skip
        analysis = sanitize(analysis, ctx.video.filename)
        await anyio.to_thread.run_sync(self._store_analysis, ctx, frame, model.key, analysis)
        return from_cache

    @staticmethod
    def _store_analysis(
        ctx: StageContext, frame: _Frame, model_key: str, analysis: FrameAnalysis
    ) -> None:
        with ctx.tools.db.write() as session:
            session.execute(
                sa.delete(FrameAnalysisRow).where(FrameAnalysisRow.keyframe_id == frame.id)
            )
            session.add(
                FrameAnalysisRow(
                    keyframe_id=frame.id,
                    model=model_key,
                    prompt_version=f"{PROMPT_NAME}.v{PROMPT_VERSION}",
                    schema_version=FRAME_ANALYSIS_SCHEMA_VERSION,
                    focus=ctx.focus,
                    language=ctx.prefs.language,
                    data=analysis.model_dump(mode="json"),
                )
            )
