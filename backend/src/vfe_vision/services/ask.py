"""Questions on the library, for the web interface and the MCP server alike.

1. the hybrid search (``services/search.py``) finds candidate passages within the filters;
2. code keeps a varied set that fits one slot of the loaded instance (``domain/ask.py``):
   context ÷ parallel requests, the answer and the instructions reserved first;
3. the loaded instance writes the answer, streamed; it reads the passages numbered, inside an
   untrusted-content fence, and must cite them as ``[n]``;
4. code keeps the citations of real passages and maps each to its video and moment;
5. on request, 1–4 keyframes of the cited moments go to the vision model, which confirms or
   corrects the answer in a separate note;
6. the question, its filters, the answer, its citations and timings are kept (``questions``);
   each request to LM Studio is audited in ``llm_calls``.

Nothing is ever loaded: without a loaded model the question fails with the reason.
A stopped answer (stop button, page closed) closes the upstream stream, so LM Studio stops
writing, and what was written so far is kept as « cancelled ».
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from functools import partial
from typing import Any

import anyio
import sqlalchemy as sa

from vfe_vision.adapters.imaging import encode_jpeg, read_image, resize_long_side
from vfe_vision.adapters.lmstudio import prompts
from vfe_vision.adapters.lmstudio.budget import (
    estimate_image_tokens,
    estimate_text_tokens,
    slot_tokens,
)
from vfe_vision.adapters.lmstudio.catalog import LoadedInstance, ModelInfo, pick_text_instance
from vfe_vision.adapters.lmstudio.client import ChatImage
from vfe_vision.adapters.lmstudio.schema import field_guide
from vfe_vision.core.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    ServiceUnavailableError,
    VfeError,
)
from vfe_vision.core.ids import new_id
from vfe_vision.core.logging import get_logger
from vfe_vision.db.base import utcnow
from vfe_vision.db.models import Keyframe, LlmCall, Question, Video
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.ask import (
    ASK_RULES_VERSION,
    MAX_CHECK_FRAMES,
    NO_ANSWER,
    AnswerStatus,
    AskBudget,
    Candidate,
    CitedAnswer,
    VisualCheck,
    answer_status,
    ask_budget,
    check_frame_count,
    choose_passages,
    passage_line,
    read_citations,
    strip_no_answer,
)
from vfe_vision.domain.synthesis_text import LANGUAGE_HINT
from vfe_vision.domain.timecode import format_clock
from vfe_vision.domain.transcript import clean_untrusted
from vfe_vision.services import search
from vfe_vision.services.container import AppContainer
from vfe_vision.services.search import SearchFilters, SearchHit

log = get_logger(__name__)

SCHEMA_VERSION = 1  # of a stored question (citations, visual check, details)
PROMPT_VERSION = 2  # v2: only the passages that support the answer are cited or mentioned
CHECK_PROMPT_VERSION = 1
CANDIDATES = 40  # passages the search hands over, before the choice
MAX_QUESTION = 1000
TEMPERATURE = 0.2
CHECK_TEMPERATURE = 0.1
CHECK_MAX_TOKENS = 300
CHECK_LONG_SIDE = 768  # as the shot stories: ~400 tokens a 16:9 image
CHECK_MULTIPLE = 32
EXCERPT_CHARS = 300
STOPPED_CHECK = "Vérification visuelle interrompue."


class EmptyIndexError(ConflictError):
    code = "empty_index"
    title = "Aucune vidéo indexée"


class NoModelLoadedError(ServiceUnavailableError):
    code = "no_model_loaded"
    title = "Aucun modèle chargé"


# ---------------------------------------------------------------- what is shown
@dataclass(frozen=True, slots=True)
class Citation:
    """A passage the answer cites, as it was when asked; the file is read again from the
    library (a video removed since: ``available`` is false)."""

    n: int  # the number the answer cites it by
    chunk_id: int
    video_id: str
    filename: str
    title: str | None
    kind: str
    t_start: float | None
    t_end: float | None
    shot_idx: int | None
    keyframe_id: str | None
    thumb_path: str | None
    excerpt: str  # the passage, one line, cut (from the footage and the models)
    path: str | None = None
    fps: float | None = None
    start_timecode: str | None = None
    available: bool = True


@dataclass(frozen=True, slots=True)
class QuestionView:
    id: str
    question: str
    filters: dict[str, Any]
    language: str
    model: str | None
    status: AnswerStatus
    answer: str
    citations: list[Citation]
    passages: int
    visual_check: dict[str, Any] | None
    error: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    timings: dict[str, int]
    details: dict[str, Any]
    created_at: datetime

    @property
    def truncated(self) -> bool:
        return bool(self.details.get("truncated"))

    @property
    def dropped_citations(self) -> int:
        return int(self.details.get("dropped_citations") or 0)


@dataclass(frozen=True, slots=True)
class AskStarted:
    id: str
    model: str
    vision: bool  # the model reads images: the visual check is possible
    passages: int
    budget: AskBudget
    context_length: int | None
    parallel: int | None


@dataclass(frozen=True, slots=True)
class AnswerText:
    text: str  # the next piece of the answer, as written


@dataclass(frozen=True, slots=True)
class CheckStarted:
    frames: int


AskEvent = AskStarted | AnswerText | CheckStarted | QuestionView


@dataclass(slots=True)
class _Record:
    id: str
    question: str
    filters: dict[str, Any]
    language: str
    model: str | None = None
    prompt_version: str | None = None
    status: AnswerStatus = AnswerStatus.FAILED
    answer: str = ""
    hits: list[SearchHit] = field(default_factory=list)  # the passages given, in their order
    cited: tuple[int, ...] = ()
    visual_check: dict[str, Any] | None = None
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    timings: dict[str, int] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


class _Clock:
    def __init__(self) -> None:
        self.start = time.perf_counter()

    def ms(self) -> int:
        return round((time.perf_counter() - self.start) * 1000)


# ---------------------------------------------------------------- asking
def filters_json(filters: SearchFilters) -> dict[str, Any]:
    """The filters that are set, as JSON (the names of ``SearchFilters``); the language is the
    question's own."""
    out: dict[str, Any] = {}
    for item in dataclasses.fields(filters):
        value = getattr(filters, item.name)
        if value is None or value == () or item.name == "language":
            continue
        if isinstance(value, tuple):
            value = [v.value if isinstance(v, Enum) else v for v in value]
        elif isinstance(value, Enum):
            value = value.value
        elif isinstance(value, date):
            value = value.isoformat()
        out[item.name] = value
    return out


def _question(text: str) -> str:
    question = clean_untrusted(text)
    if not question:
        raise InvalidInputError("Écrivez une question.")
    if len(question) > MAX_QUESTION:
        raise InvalidInputError(
            f"Question trop longue ({len(question)} caractères, {MAX_QUESTION} au plus)."
        )
    return question


async def loaded_model(c: AppContainer) -> tuple[ModelInfo, LoadedInstance]:
    """The instance already loaded in LM Studio that will answer: never one to load."""
    models = await c.lmstudio.list_models()
    preferred = (await anyio.to_thread.run_sync(load_preferences, c.db)).vision_model
    picked = pick_text_instance(models, preferred)
    if picked is None:
        raise NoModelLoadedError(
            "Aucun modèle n'est chargé dans LM Studio. Chargez-en un (par exemple "
            "qwen/qwen3-vl-4b) : l'application n'en charge jamais elle-même."
        )
    return picked


def _render(language: str, **context: Any) -> prompts.RenderedPrompt:
    return prompts.render(
        "ask",
        PROMPT_VERSION,
        language=language,
        language_hint=LANGUAGE_HINT.get(language, ""),
        no_answer=NO_ANSWER,
        **context,
    )


def _estimate(rendered: prompts.RenderedPrompt) -> int:
    return estimate_text_tokens(rendered.system) + estimate_text_tokens(rendered.user)


def _candidate(hit: SearchHit) -> Candidate:
    line = passage_line(
        filename=hit.filename, title=hit.title, kind=hit.kind.value, shot_idx=hit.shot_idx,
        t_start=hit.t_start, t_end=hit.t_end, text=hit.text,
    )  # fmt: skip
    return Candidate(hit.video_id, hit.kind.value, hit.t_start, hit.t_end, line)


class _MarkerGate:
    """Holds the start of the answer back until it is known whether it is the ``NO_ANSWER``
    marker, which the reader never sees."""

    def __init__(self) -> None:
        self._head = ""
        self._open = False

    def feed(self, piece: str) -> str:
        if self._open:
            return piece
        self._head += piece
        probe = self._head.lstrip(" \t\n*").upper()
        if len(probe) <= len(NO_ANSWER) and NO_ANSWER.startswith(probe):
            return ""  # may still become the marker
        self._open = True
        return strip_no_answer(self._head)[0]

    def flush(self) -> str:
        if self._open:
            return ""
        self._open = True
        return strip_no_answer(self._head)[0]


async def ask(  # noqa: PLR0915 - one question, told in order
    c: AppContainer,
    question: str,
    filters: SearchFilters | None = None,
    *,
    visual_check: bool = False,
    language: str | None = None,
) -> AsyncGenerator[AskEvent]:
    """Answer ``question`` from the passages of the library, as events: ``AskStarted``, the
    answer's pieces (``AnswerText``), ``CheckStarted`` when the images are being looked at, and
    the stored ``QuestionView`` last.

    Raises before ``AskStarted`` when nothing can be asked (empty index, LM Studio off, no model
    loaded), and a ``VfeError`` after it when LM Studio fails while answering (the question is
    kept, « failed »). ``language``: the one the answer is written in, and the passages read in
    (the interface's; default: the analysis language).
    """
    text = _question(question)
    filters = filters or SearchFilters()
    clock = _Clock()
    state = await anyio.to_thread.run_sync(search.index_state, c)
    if state.indexed == 0:
        raise EmptyIndexError(
            "Aucune vidéo n'est encore indexée : les questions portent sur l'index de recherche, "
            "construit à la fin de chaque analyse (« Compléter » dans la bibliothèque)."
        )
    model, instance = await loaded_model(c)
    language = language or (await anyio.to_thread.run_sync(load_preferences, c.db)).language
    filters = replace(filters, language=language)
    found = await anyio.to_thread.run_sync(
        partial(search.search, c, text, filters, limit=CANDIDATES)
    )
    candidates = [_candidate(hit) for hit in found.hits]
    slot = slot_tokens(instance.context_length, instance.parallel)
    budget = ask_budget(slot, _estimate(_render(language, question=text, passages=[])))
    chosen = choose_passages(candidates, budget.passages, estimate_text_tokens)
    rendered = _render(language, question=text, passages=[candidates[i].line for i in chosen])
    record = _Record(
        id=new_id(), question=text, filters=filters_json(filters), language=language,
        model=model.key, prompt_version=rendered.version, hits=[found.hits[i] for i in chosen],
        timings={"retrieval_ms": clock.ms()},
        details={
            "rules": ASK_RULES_VERSION, "slot_tokens": slot, "answer_tokens": budget.answer,
            "passage_tokens": budget.passages, "prompt_tokens": _estimate(rendered),
            "candidates": len(candidates), "retrievers": list(found.retrievers),
            **({"search_note": found.note} if found.note else {}),
        },
    )  # fmt: skip
    yield AskStarted(
        id=record.id, model=model.key, vision=model.vision, passages=len(record.hits),
        budget=budget, context_length=instance.context_length, parallel=instance.parallel,
    )  # fmt: skip
    if not record.hits:
        record.status = AnswerStatus.NO_PASSAGES
        record.timings["total_ms"] = clock.ms()
        yield await anyio.to_thread.run_sync(_store, c, record)
        return
    pieces: list[str] = []
    gate = _MarkerGate()
    try:
        async with c.lmstudio.chat_stream(
            model=instance.id, system=rendered.system, user_text=rendered.user,
            max_tokens=budget.answer, temperature=TEMPERATURE,
            reasoning_off=bool(model.reasoning_options),
        ) as stream:  # fmt: skip
            async for piece in stream:
                pieces.append(piece)
                if shown := gate.feed(piece):
                    yield AnswerText(shown)
            if rest := gate.flush():
                yield AnswerText(rest)
    except VfeError as exc:
        _written(record, pieces, len(record.hits))
        record.status, record.error = AnswerStatus.FAILED, exc.detail
        record.timings["total_ms"] = clock.ms()
        await anyio.to_thread.run_sync(_store, c, record)
        raise
    except (anyio.get_cancelled_exc_class(), GeneratorExit):
        _written(record, pieces, len(record.hits))
        record.status = AnswerStatus.CANCELLED
        record.timings["total_ms"] = clock.ms()
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(_store, c, record)
        raise
    stats = stream.stats
    cited = _written(record, pieces, len(record.hits))
    record.status = answer_status(cited)
    record.prompt_tokens, record.completion_tokens = stats.prompt_tokens, stats.completion_tokens
    record.timings |= {"first_token_ms": stats.first_token_ms or 0, "answer_ms": stats.latency_ms}
    record.details |= {
        "finish_reason": stats.finish_reason, "truncated": stats.finish_reason == "length",
        "dropped_citations": cited.dropped,
    }  # fmt: skip
    if stats.reasoning_tokens:
        record.details["reasoning_tokens"] = stats.reasoning_tokens
    await anyio.to_thread.run_sync(
        partial(_audit, c, record, "ask", rendered, response={"text": "".join(pieces)},
                latency_ms=stats.latency_ms, prompt_tokens=stats.prompt_tokens,
                completion_tokens=stats.completion_tokens)
    )  # fmt: skip
    if visual_check:
        started = _Clock()
        try:
            frames, reason = await anyio.to_thread.run_sync(
                partial(_check_frames, c, record, model, slot)
            )
            if frames:
                yield CheckStarted(len(frames))
                record.visual_check = await _visual_check(c, record, model, instance, frames)
            else:
                record.visual_check = {"status": "skipped", "note": reason, "frames": []}
        except (anyio.get_cancelled_exc_class(), GeneratorExit):  # the answer is whole: kept
            record.visual_check = {"status": "skipped", "note": STOPPED_CHECK, "frames": []}
            record.timings["total_ms"] = clock.ms()
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(_store, c, record)
            raise
        record.timings["check_ms"] = started.ms()
    record.timings["total_ms"] = clock.ms()
    yield await anyio.to_thread.run_sync(_store, c, record)


def _written(record: _Record, pieces: Sequence[str], passages: int) -> CitedAnswer:
    """What the model wrote so far, its citations checked."""
    cited = read_citations("".join(pieces), passages)
    record.answer, record.cited = cited.text, cited.cited
    return cited


async def ask_once(
    c: AppContainer,
    question: str,
    filters: SearchFilters | None = None,
    *,
    visual_check: bool = False,
) -> QuestionView:
    """The whole answer at once (the MCP server)."""
    async with aclosing(ask(c, question, filters, visual_check=visual_check)) as events:
        async for event in events:
            if isinstance(event, QuestionView):
                return event
    raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------- visual check
@dataclass(frozen=True, slots=True)
class _Frame:
    n: int
    keyframe_id: str
    t_s: float
    thumb_path: str | None
    image: ChatImage


def _check_frames(
    c: AppContainer, record: _Record, model: ModelInfo, slot: int
) -> tuple[list[_Frame], str]:
    """1–4 keyframes of the cited moments, in the order of the citations, that fit one slot
    next to the question and the answer; or why there are none."""
    if not model.vision:
        return [], "Le modèle chargé ne lit pas les images : pas de vérification visuelle."
    if record.status is not AnswerStatus.ANSWERED:
        return [], "Rien à vérifier : la réponse ne cite aucun moment."
    wanted: dict[str, int] = {}
    for n in record.cited:
        keyframe_id = record.hits[n - 1].keyframe_id
        if keyframe_id and keyframe_id not in wanted:
            wanted[keyframe_id] = n
    if not wanted:
        return [], "Les moments cités n'ont pas d'image clé à montrer."
    with c.db.read() as session:
        rows = {
            row.id: row
            for row in session.execute(
                sa.select(Keyframe).where(Keyframe.id.in_(list(wanted)))
            ).scalars()
        }
    text_tokens = _estimate(_check_prompt(record, MAX_CHECK_FRAMES))
    frames: list[_Frame] = []
    for keyframe_id, n in wanted.items():
        row = rows.get(keyframe_id)
        if row is None:
            continue
        try:
            image = resize_long_side(
                read_image(c.artifacts.resolve(row.image_path)), CHECK_LONG_SIDE,
                multiple=CHECK_MULTIPLE,
            )  # fmt: skip
        except (OSError, ValueError, VfeError) as exc:
            log.warning("keyframe unreadable for the visual check", id=keyframe_id, error=str(exc))
            continue
        height, width = image.shape[:2]
        count = check_frame_count(slot, text_tokens, estimate_image_tokens(width, height),
                                  CHECK_MAX_TOKENS)  # fmt: skip
        if len(frames) >= count:
            break
        label = f"Image for passage [{n}] ({format_clock(row.t_s)})"
        frames.append(
            _Frame(n, keyframe_id, row.t_s, row.thumb_path,
                   ChatImage(encode_jpeg(image, quality=85), width, height, label=label))
        )  # fmt: skip
    if not frames:
        return [], "Le contexte du modèle chargé est trop court pour y joindre des images."
    return frames, ""


def _check_prompt(record: _Record, frames: int) -> prompts.RenderedPrompt:
    return prompts.render(
        "ask_check", CHECK_PROMPT_VERSION, language=record.language,
        language_hint=LANGUAGE_HINT.get(record.language, ""), field_guide=field_guide(VisualCheck),
        n=frames, question=record.question, answer=clean_untrusted(record.answer),
    )  # fmt: skip


async def _visual_check(
    c: AppContainer,
    record: _Record,
    model: ModelInfo,
    instance: LoadedInstance,
    frames: list[_Frame],
) -> dict[str, Any]:
    shown = [
        {"n": f.n, "keyframe_id": f.keyframe_id, "t_s": f.t_s, "thumb_path": f.thumb_path}
        for f in frames
    ]
    rendered = _check_prompt(record, len(frames))
    try:
        result = await c.lmstudio.chat_structured(
            model=instance.id, system=rendered.system, user_text=rendered.user,
            output=VisualCheck, images=[f.image for f in frames], max_tokens=CHECK_MAX_TOKENS,
            temperature=CHECK_TEMPERATURE, purpose="ask_check",
            reasoning_off=bool(model.reasoning_options),
        )  # fmt: skip
    except VfeError as exc:  # the answer stands without its check
        log.warning("visual check failed", question_id=record.id, error=exc.detail)
        return {"status": "failed", "note": exc.detail, "frames": shown}
    check = result.data
    await anyio.to_thread.run_sync(
        partial(_audit, c, record, "ask_check", rendered, response=check.model_dump(mode="json"),
                latency_ms=result.latency_ms, prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens)
    )  # fmt: skip
    return {
        "status": "done", "verdict": check.verdict.value,
        "note": clean_untrusted(check.note)[:600], "frames": shown, "model": model.key,
    }  # fmt: skip


# ---------------------------------------------------------------- storage
def _audit(
    c: AppContainer,
    record: _Record,
    purpose: str,
    rendered: prompts.RenderedPrompt,
    *,
    response: dict[str, Any],
    latency_ms: int,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    """One request in ``llm_calls``, like every other call to LM Studio (never read back as a
    cache: the same question asked again is answered again)."""
    material = [record.id, purpose, record.model, rendered.version, rendered.system, rendered.user]
    key = hashlib.sha256(json.dumps(material, ensure_ascii=False).encode("utf-8")).hexdigest()
    with c.db.write() as session:
        session.add(
            LlmCall(
                cache_key=key, purpose=purpose, model=record.model or "",
                prompt_version=rendered.version, schema_version=SCHEMA_VERSION,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                latency_ms=latency_ms, response={**response, "question_id": record.id},
            )
        )  # fmt: skip


def _excerpt(text: str) -> str:
    line = clean_untrusted(" · ".join(part for part in text.splitlines() if part.strip()))
    return line if len(line) <= EXCERPT_CHARS else line[: EXCERPT_CHARS - 1].rstrip() + "…"


def _citation_json(n: int, hit: SearchHit) -> dict[str, Any]:
    return {
        "n": n, "chunk_id": hit.chunk_id, "video_id": hit.video_id, "filename": hit.filename,
        "title": hit.title, "kind": hit.kind.value, "t_start": hit.t_start, "t_end": hit.t_end,
        "shot_idx": hit.shot_idx, "keyframe_id": hit.keyframe_id, "thumb_path": hit.thumb_path,
        "excerpt": _excerpt(hit.text),
    }  # fmt: skip


def _store(c: AppContainer, record: _Record) -> QuestionView:
    citations = [_citation_json(n, record.hits[n - 1]) for n in record.cited]
    row = Question(
        id=record.id, schema_version=SCHEMA_VERSION, question=record.question,
        filters=record.filters, language=record.language, model=record.model,
        prompt_version=record.prompt_version, status=record.status.value, answer=record.answer,
        citations=citations, passages=len(record.hits), visual_check=record.visual_check,
        error=record.error, prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens, timings=record.timings,
        details=record.details, created_at=utcnow(),
    )  # fmt: skip
    with c.db.write() as session:
        session.add(row)
    return _views(c, [row])[0]


def _views(c: AppContainer, rows: Sequence[Question]) -> list[QuestionView]:
    """Stored questions, their citations completed with the files as they are now."""
    ids = {str(item.get("video_id")) for row in rows for item in row.citations or []}
    with c.db.read() as session:
        videos = {
            v.id: v for v in session.execute(sa.select(Video).where(Video.id.in_(ids))).scalars()
        }
    return [_view(row, videos) for row in rows]


def _view(row: Question, videos: dict[str, Video]) -> QuestionView:
    citations: list[Citation] = []
    for item in row.citations or []:
        video = videos.get(str(item.get("video_id")))
        citations.append(
            Citation(
                n=int(item["n"]), chunk_id=int(item.get("chunk_id") or 0),
                video_id=str(item["video_id"]), filename=str(item.get("filename") or ""),
                title=item.get("title"), kind=str(item.get("kind") or "video"),
                t_start=item.get("t_start"), t_end=item.get("t_end"),
                shot_idx=item.get("shot_idx"), keyframe_id=item.get("keyframe_id"),
                thumb_path=item.get("thumb_path") if video is not None else None,
                excerpt=str(item.get("excerpt") or ""),
                path=video.path if video is not None else None,
                fps=video.fps if video is not None else None,
                start_timecode=video.start_timecode if video is not None else None,
                available=video is not None,
            )
        )  # fmt: skip
    return QuestionView(
        id=row.id, question=row.question, filters=dict(row.filters or {}), language=row.language,
        model=row.model, status=AnswerStatus(row.status), answer=row.answer,
        citations=citations, passages=row.passages, visual_check=row.visual_check,
        error=row.error, prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        timings={k: int(v) for k, v in (row.timings or {}).items() if isinstance(v, int | float)},
        details=dict(row.details or {}), created_at=row.created_at,
    )  # fmt: skip


# ---------------------------------------------------------------- history
def list_questions(
    c: AppContainer, *, limit: int = 50, offset: int = 0
) -> tuple[list[QuestionView], int]:
    """The questions asked, the latest first, and how many there are."""
    with c.db.read() as session:
        total = session.execute(sa.select(sa.func.count()).select_from(Question)).scalar_one()
        rows = list(
            session.execute(
                sa.select(Question)
                .order_by(Question.created_at.desc(), Question.id.desc())
                .limit(limit)
                .offset(offset)
            ).scalars()
        )
    return _views(c, rows), int(total)


def get_question(c: AppContainer, question_id: str) -> QuestionView:
    with c.db.read() as session:
        row = session.get(Question, question_id)
    if row is None:
        raise NotFoundError(f"Question introuvable : {question_id}")
    return _views(c, [row])[0]


def delete_question(c: AppContainer, question_id: str) -> None:
    with c.db.write() as session:
        row = session.get(Question, question_id)
        if row is None:
            raise NotFoundError(f"Question introuvable : {question_id}")
        session.delete(row)
