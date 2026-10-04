"""Reframing plans for an edit: the subject's boxes of each item, its head asked to the
vision model already loaded when needed, the plan of ``domain/reframe_plan``, and contact sheets.

**Heads.** A subject bigger than the crop must be aimed at by its head, which the analyses do not
store (the detectors box whole bodies; people have their face, YuNet). For those keyframes only,
the vision model already loaded in LM Studio is asked for the head of the framed being (prompt
``head.v1``, never a model to load), with its box written in the model's own convention
— only for models whose convention is verified. Answers are cached in
``llm_calls`` like every vision answer (``purpose = "head"``): asked once per keyframe and being,
reused by every later plan, and never shown in the interface. A call asks at most
``MAX_NEW_HEADS`` new heads within ``HEADS_DEADLINE_S``: the others are « pending » (top of the
box meanwhile), a second call finishes them from the cache.

**Contact sheets.** Every keyframe of a piece with its crop drawn over it (outside dimmed; yellow
frame, red below the plan's ``flag``; the head in cyan), JPEG, for the assistant to look at: no
still is exported from Resolve (exporting one froze it).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio
import cv2
import numpy as np
import sqlalchemy as sa

from vfe_vision.adapters.imaging import encode_jpeg, read_image, resize_long_side
from vfe_vision.adapters.lmstudio import prompts
from vfe_vision.adapters.lmstudio.catalog import LoadedInstance, ModelInfo, pick_vision_instance
from vfe_vision.adapters.lmstudio.client import ChatImage, LmStudioUnavailableError
from vfe_vision.adapters.lmstudio.schema import field_guide
from vfe_vision.core.errors import InvalidInputError, VfeError
from vfe_vision.core.logging import get_logger
from vfe_vision.db.models import Keyframe, LlmCall
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.grounding import (
    HEAD_CONVENTIONS,
    HEAD_SCHEMA_VERSION,
    Head,
    HeadYFirst,
    box_for_prompt,
    head_box,
    head_schema,
)
from vfe_vision.domain.reframe_plan import (
    EDGE_S,
    Anchor,
    Geometry,
    Piece,
    PlanFrame,
    PlanOptions,
    plan_item,
)
from vfe_vision.domain.subjects import Box, Category
from vfe_vision.domain.transcript import clean_untrusted
from vfe_vision.domain.vision_profile import BOX_SENTENCES, BoxConvention, BoxField
from vfe_vision.pipeline.stages.vision_frames import GROUNDING_MULTIPLE
from vfe_vision.pipeline.vision_profile import known_setup
from vfe_vision.services import editing, videos
from vfe_vision.services import subjects as subject_service
from vfe_vision.services.container import AppContainer
from vfe_vision.services.resolve_edit import ensure_allowed

log = get_logger(__name__)
MAX_ITEMS = 200
MAX_NEW_HEADS = 160
HEADS_DEADLINE_S = 150.0
HEAD_PROMPT = "head"
HEAD_PROMPT_VERSION = 1
HEAD_MAX_TOKENS = 120
SHEET_ROW_H = 176
SHEET_LABEL_W = 92
SHEET_ROWS = 6
SHEET_MAX = 3
SHEET_PER_ROW = 6
_YELLOW, _RED, _CYAN, _WHITE = (0, 220, 255), (40, 40, 235), (255, 230, 0), (240, 240, 240)


@dataclass(frozen=True, slots=True)
class ReframeInput:
    video_id: str
    in_s: float
    out_s: float
    anchor: Anchor = "auto"
    rotation: int = 0
    subject: str | None = None  # overrides the plan's


@dataclass(slots=True)
class _Frame:
    t_s: float
    keyframe_id: str
    image: str  # relative to the artifacts directory
    box: Box
    label: str
    head: Box | None = None
    needs_head: bool = False
    pending: bool = False  # its head not asked yet (budget, deadline, LM Studio gone)


@dataclass(slots=True)
class ItemPlan:
    n: int
    video_id: str
    filename: str
    width: int
    height: int
    in_s: float
    out_s: float
    subject: str | None
    pieces: list[Piece]
    frames: list[_Frame]
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HeadStats:
    model: str | None = None
    wanted: int = 0  # keyframes whose subject is bigger than the crop
    from_faces: int = 0
    cached: int = 0
    asked: int = 0
    found: int = 0
    failed: int = 0
    pending: int = 0
    note: str | None = None


@dataclass(slots=True)
class ReframePlan:
    timeline_width: int
    timeline_height: int
    items: list[ItemPlan]
    heads: HeadStats

    def pieces(self) -> list[tuple[ItemPlan, Piece]]:
        return [(item, piece) for item in self.items for piece in item.pieces]


async def plan_reframe(
    c: AppContainer,
    inputs: Sequence[ReframeInput],
    *,
    timeline_width: int,
    timeline_height: int,
    subject: str | None = None,
    heads: bool = True,
    options: PlanOptions = PlanOptions(),  # noqa: B008 - frozen
) -> ReframePlan:
    """The plan of every item, in order (its pieces are the items of ``build_timeline``)."""
    await anyio.to_thread.run_sync(ensure_allowed, c)
    if not inputs:
        raise InvalidInputError("Aucun plan à recadrer.")
    if len(inputs) > MAX_ITEMS:
        raise InvalidInputError(f"Au plus {MAX_ITEMS} plans à la fois.")
    for n, item in enumerate(inputs, 1):
        if item.out_s <= item.in_s or item.in_s < 0:
            raise InvalidInputError(f"Plan {n} : la sortie doit suivre l'entrée.")
        if item.rotation not in (0, 90, -90, 180):
            raise InvalidInputError(f"Plan {n} : rotation 0, 90, -90 ou 180.")
    gathered = await anyio.to_thread.run_sync(
        lambda: _gather(c, inputs, subject, timeline_width, timeline_height)
    )
    stats = HeadStats(wanted=sum(f.needs_head for item in gathered for f in item.frames))
    stats.from_faces = sum(1 for item in gathered for f in item.frames if f.needs_head and f.head)
    if heads and stats.wanted > stats.from_faces:
        await _ask_heads(c, [f for item in gathered for f in item.frames if f.needs_head
                             and f.head is None], stats)  # fmt: skip
    for plan, source in zip(gathered, inputs, strict=True):
        geo = Geometry(plan.width, plan.height, timeline_width, timeline_height)
        frames = [PlanFrame(f.t_s, (f.box.x1, f.box.y1, f.box.x2, f.box.y2),
                            (f.head.x1, f.head.y1, f.head.x2, f.head.y2) if f.head else None)
                  for f in plan.frames]  # fmt: skip
        item_options = PlanOptions(
            anchor=source.anchor, rotation=source.rotation, min_piece_s=options.min_piece_s,
            keep=options.keep, flag=options.flag,
        )  # fmt: skip
        plan.pieces = plan_item(frames, geo, source.in_s, source.out_s, item_options)
        waiting = {round(f.t_s, 3) for f in plan.frames if f.pending}
        for piece in plan.pieces:
            if any(round(s.t_s, 3) in waiting for s in piece.frames):
                piece.flags.append("head_pending")
    return ReframePlan(timeline_width, timeline_height, gathered, stats)


# ---------------------------------------------------------------- the boxes of each item
def _gather(
    c: AppContainer,
    inputs: Sequence[ReframeInput],
    subject: str | None,
    timeline_width: int,
    timeline_height: int,
) -> list[ItemPlan]:
    plans: list[ItemPlan] = []
    for n, item in enumerate(inputs, 1):
        video = videos.get_video(c, item.video_id).video
        if not video.width or not video.height:
            raise InvalidInputError(f"Plan {n} : dimensions de la vidéo inconnues (étape probe).")
        geo = Geometry(video.width, video.height, timeline_width, timeline_height)
        cw, ch = geo.crop_size
        view = subject_service.get_subjects(
            c, item.video_id, start_s=max(0.0, item.in_s - EDGE_S), end_s=item.out_s + EDGE_S
        )
        wanted = item.subject or subject
        frames: list[_Frame] = []
        for frame in view.frames:
            being = editing.pick_subject(frame.subjects, wanted)
            if being is None:
                continue
            box = being.box
            big = (box.x2 - box.x1) * video.width > cw + 1 or (
                box.y2 - box.y1
            ) * video.height > ch + 1
            face = being.face if being.category == Category.PERSON else None
            frames.append(
                _Frame(t_s=frame.t_s, keyframe_id=frame.keyframe_id, image="", box=box,
                       label=clean_untrusted(being.label)[:40], head=face if big else None,
                       needs_head=big and item.anchor == "auto" and item.rotation in (0, 180))
            )  # fmt: skip
        images = _images(c, [f.keyframe_id for f in frames])
        for f in frames:
            f.image = images.get(f.keyframe_id, "")
        label = (
            max({f.label for f in frames}, key=[f.label for f in frames].count) if frames else None
        )
        plans.append(
            ItemPlan(n=n, video_id=video.id, filename=video.filename, width=video.width,
                     height=video.height, in_s=item.in_s, out_s=item.out_s, subject=label,
                     pieces=[], frames=frames)
        )  # fmt: skip
    return plans


def _images(c: AppContainer, keyframe_ids: list[str]) -> dict[str, str]:
    if not keyframe_ids:
        return {}
    with c.db.read() as session:
        rows = session.execute(
            sa.select(Keyframe.id, Keyframe.image_path).where(Keyframe.id.in_(keyframe_ids))
        ).all()
    return {row[0]: row[1] for row in rows}


# ---------------------------------------------------------------- heads (vision model)
def _none_asked(frames: list[_Frame], stats: HeadStats, note: str) -> None:
    stats.note = note
    stats.pending = len(frames)
    for frame in frames:
        frame.pending = True


async def _ask_heads(c: AppContainer, frames: list[_Frame], stats: HeadStats) -> None:
    try:
        models = await c.lmstudio.list_models()
    except VfeError:
        _none_asked(frames, stats, "LM Studio injoignable : têtes non cherchées (haut du sujet).")
        return
    prefs = await anyio.to_thread.run_sync(load_preferences, c.db)
    picked = pick_vision_instance(models, prefs.vision_model)
    if picked is None:
        _none_asked(frames, stats, "Aucun modèle de vision chargé dans LM Studio : têtes non "
                    "cherchées (haut du sujet).")  # fmt: skip
        return
    model, instance = picked
    setup = await anyio.to_thread.run_sync(known_setup, c.db, model)
    if setup is None or not setup.enabled or setup.convention not in HEAD_CONVENTIONS:
        _none_asked(frames, stats, f"{model.key} : positions non calibrées, têtes non cherchées.")
        return
    stats.model = model.key
    convention, box_field = setup.convention, setup.box_field
    long_side = prefs.vision_image_long_side
    slots = anyio.Semaphore(max(1, instance.parallel or 1))
    gone: list[str] = []
    done: set[int] = set()

    async def one(index: int, frame: _Frame) -> None:
        async with slots:
            if gone or not frame.image:
                return
            try:
                answer, cached = await _head(
                    c, frame, (model, instance), (convention, box_field, long_side), stats
                )
            except LmStudioUnavailableError as exc:
                gone.append(exc.detail)
                return
            except (VfeError, OSError, ValueError) as exc:
                stats.failed += 1
                done.add(index)
                log.warning("head not located", keyframe=frame.keyframe_id, error=str(exc))
                return
            done.add(index)
            if answer is None:  # over the budget of new requests
                done.discard(index)
                return
            stats.cached += cached
            frame.head = head_box(answer, convention)
            stats.found += frame.head is not None

    with anyio.move_on_after(HEADS_DEADLINE_S):
        async with anyio.create_task_group() as group:
            for index, frame in enumerate(frames):
                group.start_soon(one, index, frame)
    stats.pending = len(frames) - len(done)
    for index, frame in enumerate(frames):
        frame.pending = index not in done
    if gone:
        stats.note = f"LM Studio a cessé de répondre : {gone[0]}"


async def _head(
    c: AppContainer,
    frame: _Frame,
    loaded: tuple[ModelInfo, LoadedInstance],
    how: tuple[BoxConvention, BoxField, int],
    stats: HeadStats,
) -> tuple[Head | HeadYFirst | None, bool]:
    """The model's answer for one keyframe (from the cache when asked before), whether it came
    from the cache; None when the budget of new requests is spent."""
    model, instance = loaded
    convention, box_field, long_side = how
    output = head_schema(box_field)
    image = await anyio.to_thread.run_sync(read_image, c.artifacts.resolve(frame.image))
    image = resize_long_side(image, long_side, multiple=GROUNDING_MULTIPLE)
    jpeg = encode_jpeg(image, quality=90)
    height, width = image.shape[:2]
    rendered = prompts.render(
        HEAD_PROMPT, HEAD_PROMPT_VERSION, label=frame.label,
        subject_box=box_for_prompt(frame.box, convention), box_sentence=BOX_SENTENCES[box_field],
        field_guide=field_guide(output),
    )  # fmt: skip
    key = hashlib.sha256(
        jpeg + json.dumps([model.key, rendered.version, HEAD_SCHEMA_VERSION, rendered.system,
                           rendered.user], ensure_ascii=False).encode("utf-8")
    ).hexdigest()  # fmt: skip
    cached = await anyio.to_thread.run_sync(_cached, c, key)
    if cached is not None:
        return output.model_validate(cached), True
    if stats.asked >= MAX_NEW_HEADS:
        return None, False
    stats.asked += 1
    result = await c.lmstudio.chat_structured(
        model=instance.id, system=rendered.system, user_text=rendered.user, output=output,
        images=[ChatImage(jpeg, width, height)], max_tokens=HEAD_MAX_TOKENS, temperature=0.0,
        purpose=HEAD_PROMPT, reasoning_off=bool(model.reasoning_options),
    )  # fmt: skip
    await anyio.to_thread.run_sync(
        lambda: _store(c, key, model.key, version=rendered.version,
                       response=result.data.model_dump(mode="json"),
                       costs=(result.prompt_tokens, result.completion_tokens, result.latency_ms))
    )  # fmt: skip
    return result.data, False


def _cached(c: AppContainer, key: str) -> dict[str, Any] | None:
    with c.db.read() as session:
        response: dict[str, Any] | None = session.execute(
            sa.select(LlmCall.response).where(LlmCall.cache_key == key)
        ).scalar_one_or_none()
        return response


def _store(
    c: AppContainer,
    key: str,
    model_key: str,
    *,
    version: str,
    response: dict[str, Any],
    costs: tuple[int | None, int | None, int | None],
) -> None:
    with c.db.write() as session:
        if (
            session.execute(
                sa.select(LlmCall.id).where(LlmCall.cache_key == key)
            ).scalar_one_or_none()
            is None
        ):
            session.add(
                LlmCall(cache_key=key, purpose=HEAD_PROMPT, model=model_key, prompt_version=version,
                        schema_version=HEAD_SCHEMA_VERSION, prompt_tokens=costs[0],
                        completion_tokens=costs[1], latency_ms=costs[2], response=response)
            )  # fmt: skip


# ---------------------------------------------------------------- contact sheets
def render_sheets(
    c: AppContainer, plan: ReframePlan, *, flagged_only: bool, flag: float
) -> tuple[list[bytes], int]:
    """JPEG contact sheets of the pieces (flagged ones only, or all reframed ones), and how many
    pieces did not fit on them."""
    rows: list[np.ndarray[Any, Any]] = []
    shown = 0
    number = 0
    wanted = []
    for item in plan.items:
        for piece in item.pieces:
            number += 1
            if piece.props.get("RotationAngle") in (90.0, -90.0) or not piece.frames:
                continue
            if flagged_only and not piece.flags:
                continue
            wanted.append((number, item, piece))
    for number, item, piece in wanted[: SHEET_ROWS * SHEET_MAX]:
        row = _row(c, number, item, piece, flag)
        if row is not None:
            rows.append(row)
            shown += 1
    sheets = []
    for start in range(0, len(rows), SHEET_ROWS):
        chunk = rows[start : start + SHEET_ROWS]
        width = max(r.shape[1] for r in chunk)
        padded = [np.pad(r, ((0, 0), (0, width - r.shape[1]), (0, 0)), constant_values=40)
                  for r in chunk]  # fmt: skip
        sheets.append(encode_jpeg(np.vstack(padded).astype(np.uint8), quality=80))
    return sheets, len(wanted) - shown


def _row(
    c: AppContainer, number: int, item: ItemPlan, piece: Piece, flag: float
) -> np.ndarray[Any, Any] | None:
    by_time = {round(f.t_s, 3): f for f in item.frames}
    scores = piece.frames
    if len(scores) > SHEET_PER_ROW:
        scores = [scores[round(i * (len(scores) - 1) / (SHEET_PER_ROW - 1))]
                  for i in range(SHEET_PER_ROW)]  # fmt: skip
    thumbs = []
    for score in scores:
        frame = by_time.get(round(score.t_s, 3))
        if frame is None or not frame.image:
            continue
        try:
            image = read_image(c.artifacts.resolve(frame.image))
        except (OSError, ValueError, VfeError):
            continue
        thumbs.append(_thumb(image, item, piece, frame, cov=score.cov, low=score.cov < flag))
    if not thumbs:
        return None
    body = np.hstack([np.pad(t, ((0, SHEET_ROW_H - t.shape[0]), (0, 4), (0, 0)), constant_values=40)
                      for t in thumbs])  # fmt: skip
    label = np.full((SHEET_ROW_H, SHEET_LABEL_W, 3), 40, dtype=np.uint8)
    cv2.putText(label, str(number), (6, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, _YELLOW, 2)
    for i, text in enumerate(piece.flags[:6]):
        cv2.putText(label, text[:14], (4, 46 + 16 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.36, _WHITE, 1)
    return np.hstack([label, body])


def _thumb(
    image: np.ndarray[Any, Any],
    item: ItemPlan,
    piece: Piece,
    frame: _Frame,
    *,
    cov: float,
    low: bool,
) -> np.ndarray[Any, Any]:
    h0, w0 = image.shape[:2]
    scale = (SHEET_ROW_H - 4) / h0
    thumb = cv2.resize(
        image, (max(1, round(w0 * scale)), SHEET_ROW_H - 4), interpolation=cv2.INTER_AREA
    )
    th, tw = thumb.shape[:2]
    sx, sy = tw / item.width, th / item.height
    x1, y1 = round(piece.crop.x * sx), round(piece.crop.y * sy)
    x2, y2 = (
        round((piece.crop.x + piece.crop.width) * sx),
        round((piece.crop.y + piece.crop.height) * sy),
    )
    dimmed = (thumb * 0.35).astype(np.uint8)
    dimmed[y1:y2, x1:x2] = thumb[y1:y2, x1:x2]
    cv2.rectangle(dimmed, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)), _RED if low else _YELLOW, 2)
    if frame.head is not None:
        hx1, hy1 = round(frame.head.x1 * tw), round(frame.head.y1 * th)
        hx2, hy2 = round(frame.head.x2 * tw), round(frame.head.y2 * th)
        cv2.rectangle(dimmed, (hx1, hy1), (hx2, hy2), _CYAN, 1)
    cv2.putText(
        dimmed,
        f"{cov:.0%}",
        (4, th - 6),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        _RED if low else _WHITE,
        1,
    )
    return dimmed
