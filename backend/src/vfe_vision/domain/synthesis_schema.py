"""What the synthesis asks the language model for: built per video, so that the grammar
holds exactly the chapters and the highlight moments the application chose.

The model writes texts only. Chapters are cut and highlights ranked by code; a chapter or moment
field only names the blocks it is about (in its description), never a time. Moment fields have
neutral names (``moment_1``…): named after their block, the model wrote « Le bloc montre… ».
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

SYNTHESIS_SCHEMA_VERSION = 1
_CLOSED = ConfigDict(extra="forbid")


@dataclass(frozen=True, slots=True)
class ChapterPlan:
    """A chapter cut by the application, and the blocks it chose as highlights in it."""

    first: int  # block numbers, 1-based, inclusive
    last: int
    moments: tuple[int, ...] = ()


def moment_field(index: int) -> str:
    return f"moment_{index}"


def _span(plan: ChapterPlan) -> str:
    return f"B{plan.first}" if plan.first == plan.last else f"B{plan.first}–B{plan.last}"


def _moments(plan: ChapterPlan) -> dict[str, Any]:
    return {
        moment_field(k): (
            str,
            Field(
                description=f"Block {block} (chosen by the application): one sentence on what "
                "makes it worth using, without naming the block."
            ),
        )
        for k, block in enumerate(plan.moments, 1)
    }


def draft_model(chapters: Sequence[ChapterPlan]) -> type[BaseModel]:
    """The single-pass answer: title, logline, summary, one entry per chapter (its title and
    sentence when there are several, the reasons of its moments), tags."""
    fields: dict[str, Any] = {
        "title": (
            str,
            Field(
                description="Evocative title in sentence case, 3 to 8 words; not the file name, "
                "no date."
            ),
        ),
        "logline": (str, Field(description="One sentence, at most 25 words.")),
        "summary": (
            str,
            Field(description="3 to 5 sentences: what happens, where, what is seen and heard."),
        ),
    }
    several = len(chapters) >= 2
    entries: dict[str, Any] = {}
    for index, plan in enumerate(chapters, 1):
        own: dict[str, Any] = {}
        if several:
            own["title"] = (str, Field(description="2 to 6 words."))
            own["summary"] = (
                str,
                Field(description=f"One sentence about blocks {_span(plan)} only."),
            )
        own |= _moments(plan)
        if own:
            chapter = create_model(f"Chapter{index}", __config__=_CLOSED, **own)
            entries[f"C{index}"] = (chapter, Field(description=f"Blocks {_span(plan)}."))
    if entries:
        chapters_model = create_model("Chapters", __config__=_CLOSED, **entries)
        fields["chapters"] = (chapters_model, Field(description="One entry per chapter."))
    fields["tags"] = (
        list[str],
        Field(max_length=12, description="Search keywords: lowercase, no duplicates."),
    )
    model: type[BaseModel] = create_model("SynthesisDraft", __config__=_CLOSED, **fields)
    return model


def map_model(plan: ChapterPlan) -> type[BaseModel]:
    """One chapter of a long video (map step)."""
    fields: dict[str, Any] = {
        "title": (str, Field(description="2 to 6 words.")),
        "summary": (str, Field(description="2 to 3 sentences about this part only.")),
        **_moments(plan),
        "tags": (list[str], Field(max_length=8, description="Search keywords: lowercase.")),
    }
    model: type[BaseModel] = create_model("ChapterDigest", __config__=_CLOSED, **fields)
    return model


def reduce_model() -> type[BaseModel]:
    """The whole long video from its chapter digests (reduce step): no highlights."""
    model: type[BaseModel] = create_model(
        "VideoDigest",
        __config__=_CLOSED,
        title=(str, Field(description="Evocative title in sentence case, 3 to 8 words.")),
        logline=(str, Field(description="One sentence, at most 25 words.")),
        summary=(str, Field(description="4 to 6 sentences covering the whole video.")),
        tags=(list[str], Field(max_length=12, description="Search keywords: lowercase.")),
    )
    return model


def proof_model(count: int) -> type[BaseModel]:
    """The proofread texts: exactly as many as given, in the same order."""
    model: type[BaseModel] = create_model(
        "Proofread",
        __config__=_CLOSED,
        texts=(
            list[str],
            Field(
                min_length=count,
                max_length=count,
                description="The same texts, corrected, in the same order.",
            ),
        ),
    )
    return model
