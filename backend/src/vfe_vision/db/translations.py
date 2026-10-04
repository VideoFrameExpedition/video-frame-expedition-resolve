"""The dictionary of translated analysis texts: what a video has to translate, what is
known, and the dictionary of a language as read by the API, the MCP and the analysis files.

Usable from every layer that reads the database.
"""

from __future__ import annotations

import threading
from collections.abc import Collection, Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from vfe_vision.db.models import (
    ContextPlace,
    Detection,
    FrameAnalysis,
    Keyframe,
    ShotStory,
    Translation,
    VideoSynthesis,
)
from vfe_vision.db.session import Database
from vfe_vision.domain.translation import (
    ANSWER_TEXTS,
    AS_WRITTEN,
    FRAME_TEXTS,
    LANGUAGES,
    PLACE_COLUMNS,
    PLACE_DATA_TEXTS,
    STORY_TEXTS,
    SYNTHESIS_TEXTS,
    Dictionary,
    SourceText,
    TextKind,
    text_key,
    texts_at,
)

LOOKUP_CHUNK = 500  # keys per query (SQLite binds)


def video_texts(session: Session, video_id: str) -> list[SourceText]:
    """Every text of a video's analyses that reads differently in French and in English, with
    the language its row says it is written in, in a stable order."""
    found: list[SourceText] = []

    def add(value: Any, spec: Any, language: str | None) -> None:
        found.extend(SourceText(text, kind, language) for kind, text in texts_at(value, spec))

    frames = session.execute(
        sa.select(FrameAnalysis.data, FrameAnalysis.language)
        .join(Keyframe, FrameAnalysis.keyframe_id == Keyframe.id)
        .where(Keyframe.video_id == video_id)
        .order_by(Keyframe.idx)
    ).all()
    for data, language in frames:
        add(data, FRAME_TEXTS, language)
    stories = session.execute(
        sa.select(ShotStory.story, ShotStory.answer, ShotStory.language)
        .where(ShotStory.video_id == video_id)
        .order_by(ShotStory.start_s, ShotStory.part)
    ).all()
    for story, answer, language in stories:
        add(story, STORY_TEXTS, language)
        add(answer, ANSWER_TEXTS, language)
    synthesis = session.get(VideoSynthesis, video_id)
    if synthesis is not None:
        add(synthesis.data, SYNTHESIS_TEXTS, synthesis.language)
    # The vision model's names of the living beings (the detectors' names are codes).
    labels = session.execute(
        sa.select(Detection.label)
        .where(Detection.video_id == video_id, Detection.source == "vlm")
        .order_by(Detection.t_s, Detection.idx)
    ).scalars()
    found.extend(SourceText(label, TextKind.LABEL, None) for label in labels if label.strip())
    place = session.get(ContextPlace, video_id)
    if place is not None:
        language = (place.data or {}).get("language")
        for column in PLACE_COLUMNS:
            value = getattr(place, column)
            if isinstance(value, str) and value.strip():
                found.append(SourceText(value, TextKind.PLACE, language))
        add(place.data or {}, PLACE_DATA_TEXTS, language)
    return found


def known_entries(session: Session, texts: Iterable[str]) -> dict[tuple[str, str], str]:
    """The entries of the dictionary for these texts: ``(text_key, target) -> text``."""
    keys = sorted({text_key(text) for text in texts})
    known: dict[tuple[str, str], str] = {}
    for start in range(0, len(keys), LOOKUP_CHUNK):
        rows = session.execute(
            sa.select(Translation.source_sha, Translation.target, Translation.text).where(
                Translation.source_sha.in_(keys[start : start + LOOKUP_CHUNK])
            )
        ).all()
        known.update({(row.source_sha, row.target): row.text for row in rows})
    return known


def store_entries(
    session: Session, entries: Iterable[tuple[str, str, str]], *, model: str | None
) -> int:
    """Add ``(source, target, text)`` entries; an entry already there is replaced (a new
    translation asked for). Returns how many were written."""
    rows = [
        {"source_sha": text_key(source), "target": target, "source": source, "text": text,
         "model": model}
        for source, target, text in entries
    ]  # fmt: skip
    if not rows:
        return 0
    statement = insert(Translation)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[Translation.source_sha, Translation.target],
            set_={
                "source": statement.excluded.source,
                "text": statement.excluded.text,
                "model": statement.excluded.model,
                "created_at": statement.excluded.created_at,
            },
        ),
        rows,
    )
    return len(rows)


def dictionary_for(session: Session, language: str | None, texts: Collection[str]) -> Dictionary:
    """The dictionary of ``language`` for these texts only (one video's file, one export)."""
    if language not in LANGUAGES:
        return Dictionary(language, {}) if language else AS_WRITTEN
    keys = sorted({text_key(text) for text in texts})
    entries: dict[str, str] = {}
    for start in range(0, len(keys), LOOKUP_CHUNK):
        rows = session.execute(
            sa.select(Translation.source_sha, Translation.text).where(
                Translation.target == language,
                Translation.source_sha.in_(keys[start : start + LOOKUP_CHUNK]),
            )
        ).all()
        entries.update({row.source_sha: row.text for row in rows})
    return Dictionary(language, entries)


class TranslationCache:
    """The whole dictionary of each language, kept by a process (API, MCP) and read again when
    the table changed, which a cheap signature tells: the number of entries and the largest
    row id (a new or replaced entry always changes one of them)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._signature: tuple[int, int] | None = None
        self._languages: dict[str, Dictionary] = {}
        self.loads = 0  # how many times the table was read (tests)

    def get(self, db: Database, language: str | None) -> Dictionary:
        if language not in LANGUAGES:
            return Dictionary(language, {}) if language else AS_WRITTEN
        with self._lock, db.read() as session:
            rowid = sa.literal_column("rowid", sa.Integer)
            count, top = session.execute(
                sa.select(sa.func.count(), sa.func.max(rowid)).select_from(Translation)
            ).one()
            signature = (int(count), int(top or 0))
            if signature != self._signature:
                self._signature = signature
                self._languages = {}
            if language not in self._languages:
                rows = session.execute(
                    sa.select(Translation.source_sha, Translation.text).where(
                        Translation.target == language
                    )
                ).all()
                self._languages[language] = Dictionary(
                    language, {row.source_sha: row.text for row in rows}
                )
                self.loads += 1
            return self._languages[language]

    def clear(self) -> None:
        with self._lock:
            self._signature = None
            self._languages = {}
