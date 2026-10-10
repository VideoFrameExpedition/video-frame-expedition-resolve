"""Stage ``vision_shots``: what happens in each shot, told by the vision model from up to four of
its frames in order.

One request per part: a shot of at most 30 s is one part, a longer one is cut into ~20 s parts
(``domain.blocks``). The frames are the shot's own distinct keyframes, taken by ``shot_id`` and
never by time (a scene keyframe sits one frame before its cut, inside the previous shot's time
range), completed by frames extracted at the part's empty slots. The model only tells the story:
the camera movement comes from the optical flow, chapters and highlights from the synthesis.
The prompt and the schema are the ones that were measured, verbatim.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import anyio
import sqlalchemy as sa

from vfe_vision.adapters.ffmpeg.tools import Ffmpeg
from vfe_vision.adapters.imaging import encode_jpeg, read_image, resize_long_side
from vfe_vision.adapters.lmstudio import prompts
from vfe_vision.adapters.lmstudio.catalog import LoadedInstance, ModelInfo, budget_of
from vfe_vision.adapters.lmstudio.client import ChatImage, LmStudioUnavailableError
from vfe_vision.adapters.lmstudio.schema import field_guide
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import CancelledError, VfeError
from vfe_vision.core.ids import new_id
from vfe_vision.db.models import (
    ContextPlace,
    Detection,
    FrameAnalysis,
    Keyframe,
    Shot,
    ShotStory,
    TranscriptSegment,
    Video,
)
from vfe_vision.domain.blocks import LONG_SHOT_S, MAX_PARTS, PART_S, speech_spans, split_shot
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.shot_story import (
    EDGE_SLACK_S,
    FRAMES,
    MAX_BLACK_RATIO,
    MAX_FROZEN_RATIO,
    MAX_PARTS_PER_VIDEO,
    MIN_SHOT_S,
    SHOT_STORY_SCHEMA_VERSION,
    TIDY_VERSION,
    answer_model,
    caption_line,
    eligible,
    fill_times,
    pick_evenly,
    tidy,
)
from vfe_vision.domain.shots import SOFT_MIN_SCORE, shown_motion
from vfe_vision.pipeline.stage import Resource, Stage, StageContext, StageFamily, StageOutcome
from vfe_vision.pipeline.stages.keyframes import SHOT_FRAMES_DIR, FrameLook
from vfe_vision.pipeline.stages.vision_frames import (
    GROUNDING_MULTIPLE,
    cached_response,
    pick_vision_model,
    run_requests,
    store_call,
)

PROMPT_NAME = "shot_story"
PROMPT_VERSION = 1
LONG_SIDE = 512  # 512 × 288 for a landscape frame: ~160 tokens per image
EXTRACT_SIDE = 768  # extra frames are decoded at this size, then resized like the keyframes
MAX_TOKENS = 600  # answers measured at 280 tokens at most


@dataclass(frozen=True, slots=True)
class _Frame:
    t_s: float
    keyframe_id: str | None  # None: extracted for the story
    image: str  # artifact path of the image the model gets (keyframe or extracted frame)
    thumb: str  # artifact path shown in the page
    caption: str | None


@dataclass(frozen=True, slots=True)
class _Part:
    shot_id: str
    shot_number: int
    part: int
    parts: int
    start_s: float
    end_s: float
    keyframes: tuple[_Frame, ...]
    fills: tuple[float, ...]  # times of the frames to extract
    living: bool  # a living being on one of its keyframes: asked first
    shot_s: float

    @property
    def key(self) -> tuple[str, int]:
        return self.shot_id, self.part


@dataclass(frozen=True, slots=True)
class _Plan:
    parts: list[_Part]
    shots: int  # eligible shots
    dropped: int  # parts beyond the cap per video
    place: str | None


class VisionShotsStage(Stage):
    name = "vision_shots"
    version = 1
    family = StageFamily.VISION
    # technical links the keyframes to their shots (Keyframe.shot_id): the frames of a shot are
    # chosen by it, so a failed or redone technical blocks or redoes the stories.
    requires = ("analysis_pass", "keyframes", "technical")
    # Captions as hints, living beings for the order, speech pauses for the cuts of long shots.
    after = ("vision_frames", "detections", "transcript")
    resource = Resource.LMSTUDIO
    optional = True
    facts_name_rows = True
    facts_read_texts = True  # the captions of its frames

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        return {
            "prompt": f"{PROMPT_NAME}.v{PROMPT_VERSION}",
            "schema": SHOT_STORY_SCHEMA_VERSION,
            "language": prefs.language,
            "long_side": LONG_SIDE,
            "frames": FRAMES,
            "blocks": [LONG_SHOT_S, PART_S, MAX_PARTS],
            "eligible": [MIN_SHOT_S, MAX_BLACK_RATIO, MAX_FROZEN_RATIO, MAX_PARTS_PER_VIDEO],
            "soft_motion": SOFT_MIN_SCORE,  # a weaker zoom, move or shake is a still camera
            "tidy": TIDY_VERSION,
            # The place is only a hint, as for the descriptions: a more precise place
            # does not re-tell the same shots unless an update is asked for.
            "place": _place_hint(ctx),
        }

    async def resolve_config(self, ctx: StageContext) -> dict[str, Any]:
        picked = await pick_vision_model(ctx)
        return {"model": picked[0].key if picked else None}

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        """The parts and what the model gets for them: their keyframes and the descriptions
        given as hints. Any change (new shots, keyframes, descriptions or pauses) redoes the
        stories; unchanged requests come back from the cache. The place is a setting."""
        plan = _plan(ctx)
        described = [
            [p.shot_id, p.part, round(p.start_s, 3), round(p.end_s, 3), list(p.fills),
             [[f.keyframe_id, f.caption] for f in p.keyframes]]
            for p in plan.parts
        ]  # fmt: skip
        payload = json.dumps(described, ensure_ascii=False)
        return {"parts": hashlib.sha1(payload.encode(), usedforsecurity=False).hexdigest()}

    def precheck(self, ctx: StageContext) -> StageOutcome | None:
        """A video with nothing to tell is settled without waiting for LM Studio: it never
        becomes a retryable skip for a model it does not need."""
        return _nothing_to_tell(ctx, _plan(ctx))

    async def execute(self, ctx: StageContext) -> StageOutcome:
        plan = await anyio.to_thread.run_sync(_plan, ctx)
        if (settled := await anyio.to_thread.run_sync(_nothing_to_tell, ctx, plan)) is not None:
            return settled
        picked = await pick_vision_model(ctx)
        if picked is None:
            return StageOutcome.waiting_for_lmstudio(
                "LM Studio injoignable ou aucun modèle de vision chargé"
            )
        model, instance = picked
        await ctx.tools.lm_budget.resize(*budget_of(instance))
        generation = new_id()
        fills: dict[tuple[str, int], list[_Frame]] = {}
        rows: list[ShotStory] = []
        counts = {"told": 0, "cached": 0, "failed": 0}
        reasoning: list[int] = []
        unavailable: list[str] = []
        total = len(plan.parts)

        async def tell(part: _Part) -> None:
            if ctx.cancel.cancelled or unavailable:
                return
            frames = sorted([*part.keyframes, *fills.get(part.key, [])], key=lambda f: f.t_s)
            try:
                row, from_cache = await _tell(
                    ctx, part, frames=frames, place=plan.place, model=model, instance=instance,
                    reasoning=reasoning,
                )  # fmt: skip
            except LmStudioUnavailableError as exc:
                unavailable.append(exc.detail)
                return
            except VfeError as exc:
                counts["failed"] += 1
                ctx.log.warning("shot story failed", shot=part.shot_number, error=exc.detail)
                return
            except (OSError, ValueError) as exc:  # a keyframe file missing or unreadable
                counts["failed"] += 1
                ctx.log.warning("shot story failed", shot=part.shot_number, error=str(exc))
                return
            rows.append(row)
            counts["cached" if from_cache else "told"] += 1
            done = sum(counts.values())
            ctx.progress(0.05 + 0.95 * done / total, f"Plans racontés : {done}/{total}")

        try:
            ctx.progress(0.02, "Images d'appoint des plans")
            fills.update(
                await anyio.to_thread.run_sync(_extract_fills, ctx, plan.parts, generation)
            )
            await run_requests(
                ctx, [partial(tell, part) for part in plan.parts], parallel=instance.parallel or 1
            )
            ctx.cancel.raise_if_cancelled()
        except BaseException:  # cancelled or failed, extracting or asking: the new frames go
            ctx.tools.artifacts.prune(ctx.video.id, SHOT_FRAMES_DIR, keep=_current_generation(ctx))
            raise
        summary: dict[str, Any] = {
            "model": model.key,
            "shots": plan.shots,
            "parts": total,
            **counts,
        }
        if plan.dropped:
            summary["dropped"] = plan.dropped
        if sum(reasoning):
            summary["reasoning_tokens"] = sum(reasoning)
        if unavailable or counts["failed"] == total:
            ctx.tools.artifacts.prune(ctx.video.id, SHOT_FRAMES_DIR, keep=_current_generation(ctx))
            if unavailable:  # the previous stories stay until LM Studio is back
                return StageOutcome.skipped(unavailable[0], retryable=True, **summary)
            raise VfeError(f"Aucun plan n'a pu être raconté par {model.key}")
        rows.sort(key=lambda r: (r.start_s, r.part))
        await anyio.to_thread.run_sync(_replace, ctx, rows, generation)
        return StageOutcome.ok(**summary)


# ---------------------------------------------------------------- plan
def _plan(ctx: StageContext) -> _Plan:
    """Eligible shots, their parts and frames, in the order they are asked (living beings first,
    then the longest shots), capped per video."""
    with ctx.tools.db.read() as session:
        shots = session.execute(
            sa.select(Shot).where(Shot.video_id == ctx.video.id).order_by(Shot.idx)
        ).scalars()
        # The camera label as it is shown: a weak zoom, move or shake is a still camera.
        shot_rows = [
            (s.id, s.idx, s.start_s, s.end_s, shown_motion(s.motion, s.motion_score),
             dict(s.metrics))
            for s in shots
        ]  # fmt: skip
        keyframes = session.execute(
            sa.select(
                Keyframe.id, Keyframe.shot_id, Keyframe.t_s, Keyframe.image_path,
                Keyframe.thumb_path, FrameAnalysis.data,
            )
            .outerjoin(FrameAnalysis, FrameAnalysis.keyframe_id == Keyframe.id)
            .where(Keyframe.video_id == ctx.video.id, Keyframe.duplicate_of.is_(None))
            .order_by(Keyframe.t_s)
        ).all()  # fmt: skip
        living = set(
            session.execute(
                sa.select(Detection.keyframe_id).where(Detection.video_id == ctx.video.id)
            ).scalars()
        )
        words: list[tuple[float, float]] = []
        for segment in session.execute(
            sa.select(TranscriptSegment).where(
                TranscriptSegment.video_id == ctx.video.id, TranscriptSegment.suspect.is_(False)
            )
        ).scalars():
            words += [(float(w[0]), float(w[1])) for w in segment.words or []] or [
                (segment.start_s, segment.end_s)
            ]
        place = _place(session.get(ContextPlace, ctx.video.id))

    speech = speech_spans(words)
    by_shot: dict[str, list[_Frame]] = {}
    living_by_shot: dict[str, bool] = {}
    for row in keyframes:
        if row.shot_id is None:
            continue
        by_shot.setdefault(row.shot_id, []).append(
            _Frame(row.t_s, row.id, row.image_path, row.thumb_path, caption_line(row.data))
        )
        living_by_shot[row.shot_id] = living_by_shot.get(row.shot_id, False) or row.id in living

    parts: list[_Part] = []
    shots_told = 0
    for shot_id, idx, start, end, motion, metrics in shot_rows:
        frames = by_shot.get(shot_id, [])
        if not eligible(end - start, metrics, motion, len(frames)):
            continue
        shots_told += 1
        bounds = split_shot(start, end, keyframe_times=[f.t_s for f in frames], speech=speech)
        for number, (a, b) in enumerate(bounds, 1):
            low = a - (EDGE_SLACK_S if number == 1 else 0.0)
            high = b + (EDGE_SLACK_S if number == len(bounds) else 0.0)
            chosen = pick_evenly([f for f in frames if low <= f.t_s < high])
            extra = fill_times(a, b, [f.t_s for f in chosen]) if len(chosen) < FRAMES else []
            parts.append(
                _Part(shot_id, idx + 1, number, len(bounds), a, b, tuple(chosen), tuple(extra),
                      living_by_shot.get(shot_id, False), end - start)
            )  # fmt: skip
    parts.sort(key=lambda p: (not p.living, -p.shot_s, p.start_s))
    kept = parts[:MAX_PARTS_PER_VIDEO]
    return _Plan(kept, shots_told, len(parts) - len(kept), place)


def _place(place: ContextPlace | None) -> str | None:
    """The place hint given to the model: locality, region and country, without repeats."""
    if place is None:
        return None
    names = [n for n in (place.locality, place.region, place.country) if n]
    return ", ".join(dict.fromkeys(names)) or None


def _place_hint(ctx: StageContext) -> str | None:
    with ctx.tools.db.read() as session:
        return _place(session.get(ContextPlace, ctx.video.id))


def _nothing_to_tell(ctx: StageContext, plan: _Plan) -> StageOutcome | None:
    """No part to tell (no shot long enough, or nothing changes): the earlier stories go, and
    LM Studio is not needed."""
    if plan.parts:
        return None
    _replace(ctx, [], None)
    return StageOutcome.ok(shots=0, parts=0)


# ---------------------------------------------------------------- frames
def extract_story_frame(
    ffmpeg: Ffmpeg,
    video: Path,
    t_s: float,
    directory: Path,
    look: FrameLook,
    *,
    cancel: CancelToken | None = None,
) -> Path:
    """An extra frame of a shot story at ``t_s``, decoded on the CPU and stored at the size and
    in the encoding it is sent with, in ``directory`` (under the artifacts, never next to the
    video). Shared by the stage and the import of an analysis file."""
    source = directory / f"{t_s:010.3f}.src.jpg"
    target = directory / f"{t_s:010.3f}.jpg"
    try:
        ffmpeg.extract_frame(
            video, t_s, source, long_side=EXTRACT_SIDE, hdr=look.hdr,
            hdr_peak_nits=look.hdr_peak_nits, log_profile=look.log_profile, cancel=cancel,
        )  # fmt: skip
        image = resize_long_side(read_image(source), LONG_SIDE, multiple=GROUNDING_MULTIPLE)
        target.write_bytes(encode_jpeg(image, quality=90))
    finally:
        source.unlink(missing_ok=True)
    return target


def _extract_fills(
    ctx: StageContext, parts: list[_Part], generation: str
) -> dict[tuple[str, int], list[_Frame]]:
    """Extract the missing frames (CPU), resized as they are sent; never next to the video."""
    wanted = [(part, t) for part in parts for t in part.fills]
    if not wanted:
        return {}
    with ctx.tools.db.read() as session:
        look = FrameLook.of_video(session.get_one(Video, ctx.video.id))
    directory = ctx.tools.artifacts.subdir(ctx.video.id, f"{SHOT_FRAMES_DIR}/{generation}")
    found: dict[tuple[str, int], list[_Frame]] = {}
    for part, t in wanted:
        ctx.cancel.raise_if_cancelled()
        try:
            target = extract_story_frame(
                ctx.tools.ffmpeg, ctx.video.path, t, directory, look, cancel=ctx.cancel
            )
        except CancelledError:
            raise
        except (VfeError, OSError, ValueError) as exc:  # the part keeps its other frames
            ctx.log.warning("extra shot frame failed", t_s=t, error=str(exc))
            continue
        rel = ctx.tools.artifacts.rel(target)
        found.setdefault(part.key, []).append(_Frame(t, None, rel, rel, None))
    return found


def _jpeg(ctx: StageContext, frame: _Frame) -> tuple[bytes, int, int]:
    path = ctx.tools.artifacts.resolve(frame.image)
    if frame.keyframe_id is None:  # already the size and encoding it is sent with
        data = path.read_bytes()
        image = read_image(path)
    else:
        image = resize_long_side(read_image(path), LONG_SIDE, multiple=GROUNDING_MULTIPLE)
        data = encode_jpeg(image, quality=90)
    height, width = image.shape[:2]
    return data, width, height


# ---------------------------------------------------------------- one request
async def _tell(
    ctx: StageContext,
    part: _Part,
    *,
    frames: list[_Frame],
    place: str | None,
    model: ModelInfo,
    instance: LoadedInstance,
    reasoning: list[int],
) -> tuple[ShotStory, bool]:
    """The story of one part, and whether it came from the cache."""
    if not frames:  # every frame of the part failed to extract: no image numbers to ask for
        raise VfeError("Aucune image de cette partie n'a pu être extraite")
    encoded = [await anyio.to_thread.run_sync(_jpeg, ctx, frame) for frame in frames]
    labels = [
        f"Image {i} - {frame.t_s - part.start_s:.1f} s into this part:"
        for i, frame in enumerate(frames, 1)
    ]
    output = answer_model(len(frames))
    rendered = prompts.render(
        PROMPT_NAME, PROMPT_VERSION, language=ctx.prefs.language, n=len(frames),
        field_guide=field_guide(output), shot_number=part.shot_number,
        filename=ctx.video.filename, part=part.part, parts=part.parts, start=part.start_s,
        end=part.end_s, place=place, captions=[f.caption for f in frames],
    )  # fmt: skip
    key = hashlib.sha256(
        b"".join(data for data, _, _ in encoded)
        + json.dumps(
            [model.key, rendered.version, SHOT_STORY_SCHEMA_VERSION, rendered.system,
             rendered.user, labels],
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()  # fmt: skip
    cached = await anyio.to_thread.run_sync(cached_response, ctx, key)
    if cached is not None:
        answer = output.model_validate(cached)
    else:
        result = await ctx.tools.lmstudio.chat_structured(
            model=instance.id,
            system=rendered.system,
            user_text=rendered.user,
            output=output,
            images=[
                ChatImage(data, width, height, label=label)
                for (data, width, height), label in zip(encoded, labels, strict=True)
            ],
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            budget=ctx.tools.lm_budget,
            purpose="shots",
            reasoning_off=bool(model.reasoning_options),
        )
        reasoning.append(result.reasoning_tokens or 0)
        answer = result.data
        await anyio.to_thread.run_sync(
            lambda: store_call(
                ctx, key, purpose="shots", model_key=model.key, prompt_version=rendered.version,
                schema_version=SHOT_STORY_SCHEMA_VERSION, result=result,
            )
        )  # fmt: skip
    times = [f.t_s for f in frames]
    story = tidy(answer, times, ctx.prefs.language)
    row = ShotStory(
        video_id=ctx.video.id, shot_id=part.shot_id, part=part.part, parts=part.parts,
        start_s=part.start_s, end_s=part.end_s, frame_times=times,
        keyframe_ids=[f.keyframe_id for f in frames], frame_paths=[f.thumb for f in frames],
        model=model.key, prompt_version=rendered.version,
        schema_version=SHOT_STORY_SCHEMA_VERSION, language=ctx.prefs.language,
        answer=answer.model_dump(mode="json"), story=story.model_dump(mode="json"),
    )  # fmt: skip
    return row, cached is not None


# ---------------------------------------------------------------- storage
def _current_generation(ctx: StageContext) -> str:
    """The frames directory the stored stories point to (kept when a run is abandoned)."""
    with ctx.tools.db.read() as session:
        stored: list[list[str]] = list(
            session.execute(
                sa.select(ShotStory.frame_paths).where(ShotStory.video_id == ctx.video.id)
            ).scalars()
        )
        for paths in stored:
            for rel in paths:
                parts = Path(rel).parts
                if SHOT_FRAMES_DIR in parts:
                    return parts[parts.index(SHOT_FRAMES_DIR) + 1]
    return ""


def _replace(ctx: StageContext, rows: list[ShotStory], generation: str | None) -> None:
    """Swap this video's stories in one transaction, then drop the older extra frames."""
    with ctx.tools.db.write() as session:
        session.execute(sa.delete(ShotStory).where(ShotStory.video_id == ctx.video.id))
        session.add_all(rows)
    ctx.tools.artifacts.prune(ctx.video.id, SHOT_FRAMES_DIR, keep=generation or "")
