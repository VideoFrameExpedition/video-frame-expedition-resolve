"""Questions on the library: answers written by the loaded model from the passages the search
finds, streamed as Server-Sent Events, and the questions asked before."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Query, status
from sse_starlette.sse import EventSourceResponse

from vfe_vision.api.deps import Container, DisplayLanguage
from vfe_vision.api.schemas import (
    AskCheckingOut,
    AskErrorOut,
    AskOut,
    AskRequest,
    AskStartOut,
    AskStreamEvent,
    AskSummaryOut,
    AskTokenOut,
    Page,
)
from vfe_vision.core.errors import VfeError
from vfe_vision.core.logging import get_logger
from vfe_vision.services import ask

router = APIRouter(prefix="/ask", tags=["ask"])
log = get_logger(__name__)

STREAM_DOC = (
    "Server-Sent Events, in this order: « start » (AskStartOut: the loaded model and the "
    "passages it reads), « token » (AskTokenOut: the next part of the answer), « checking » "
    "(AskCheckingOut: visual check in progress), then « done » (AskOut: the question as kept, "
    "citations included). On failure, « error » (AskErrorOut) instead."
)


class EventStream(EventSourceResponse):
    """Server-Sent Events, declared as such in the OpenAPI document."""

    media_type = "text/event-stream"


def _event(event: ask.AskEvent) -> dict[str, str]:
    match event:
        case ask.AskStarted():
            name, data = "start", AskStartOut.of(event).model_dump_json()
        case ask.AnswerText():
            name, data = "token", AskTokenOut(text=event.text).model_dump_json()
        case ask.CheckStarted():
            name, data = "checking", AskCheckingOut(frames=event.frames).model_dump_json()
        case _:
            name, data = "done", AskOut.of(event).model_dump_json()
    return {"event": name, "data": data}


def _error(code: str, title: str, detail: str, status_code: int) -> dict[str, str]:
    problem = AskErrorOut(code=code, title=title, detail=detail, status=status_code)
    return {"event": "error", "data": problem.model_dump_json()}


@router.post(
    "",
    response_class=EventStream,
    responses={200: {"model": AskStreamEvent, "description": STREAM_DOC}},
)
async def ask_question(c: Container, language: DisplayLanguage, body: AskRequest) -> EventStream:
    """Ask a question about the library: the model already loaded in LM Studio answers from
    the passages the search finds (same filters), citing them [n].

    Closing the connection (« Stop » button, page closed) also closes the request to LM Studio,
    which stops writing; the partial answer is kept (« cancelled »). No model is ever loaded:
    without a loaded model, the « error » event says so."""
    events = ask.ask(
        c, body.question, body.filters.to_filters(), visual_check=body.visual_check,
        language=language,
    )  # fmt: skip

    async def stream() -> AsyncIterator[dict[str, str]]:
        async with aclosing(events):
            try:
                async for event in events:
                    yield _event(event)
            except VfeError as exc:
                yield _error(exc.code, exc.title, exc.detail, exc.status)
            except Exception:
                log.exception("question failed")
                yield _error(
                    "internal_error", "Erreur interne",
                    "Une erreur inattendue est survenue ; consultez les journaux.", 500,
                )  # fmt: skip

    return EventStream(stream(), ping=15)


@router.get("/history")
def question_history(
    c: Container,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AskSummaryOut]:
    """The questions asked, most recent first."""
    views, total = ask.list_questions(c, limit=limit, offset=offset)
    return Page[AskSummaryOut](
        items=[AskSummaryOut.of(view) for view in views], total=total, limit=limit, offset=offset
    )


@router.get("/history/{question_id}")
def get_question(c: Container, question_id: str) -> AskOut:
    """A question asked, its answer and its citations (the videos removed since are
    flagged)."""
    return AskOut.of(ask.get_question(c, question_id))


@router.delete("/history/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(c: Container, question_id: str) -> None:
    ask.delete_question(c, question_id)
