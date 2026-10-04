"""Read what a video's search passages are written from, once, into plain data
(``domain.search_chunks.IndexFacts``).

Used by the ``index`` stage, and by the search service when the user edits a video's own
fields (only the passage about the whole video is written again then).
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from datetime import UTC, date, timedelta
from datetime import timezone as fixed_zone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.db.models import Detection as DetectionRow
from vfe_vision.db.models import OcrText, Shot, ShotStory, SubjectScan, Video, VideoSynthesis
from vfe_vision.db.session import Database
from vfe_vision.domain.search_chunks import Chapter, IndexFacts, ScreenText, Story
from vfe_vision.domain.subjects import Box, Category, Detection, Source, fuse
from vfe_vision.domain.synthesis_input import Video as VideoFacts
from vfe_vision.domain.translation import (
    AS_WRITTEN,
    FRAME_TEXTS,
    SYNTHESIS_TEXTS,
    Dictionary,
    translated,
)
from vfe_vision.pipeline.synthesis_facts import load_facts

EXPORT_DATE = "export_date"  # the date an editor exported the file, not a shooting date


def load_index_facts(
    db: Database, video_id: str, language: str, tr: Dictionary = AS_WRITTEN
) -> IndexFacts:
    """``tr``: the texts the models wrote, read in ``language`` (the passages are
    written in French and in English)."""
    video_facts = _translated_facts(load_facts(db, video_id), tr)
    with db.read() as session:
        video = session.get_one(Video, video_id)
        shots = {
            row.id: row.idx
            for row in session.execute(
                sa.select(Shot.id, Shot.idx).where(Shot.video_id == video_id)
            )
        }
        stories = tuple(
            Story(
                shot=shots[row.shot_id], start=row.start_s, end=row.end_s,
                summary=tr(str((row.story or {}).get("summary", ""))),
                main_action=tr(str((row.story or {}).get("main_action", ""))),
            )
            for row in session.execute(
                sa.select(ShotStory)
                .where(ShotStory.video_id == video_id)
                .order_by(ShotStory.start_s, ShotStory.part)
            ).scalars()
            if row.shot_id in shots
        )  # fmt: skip
        screen = tuple(
            ScreenText(t=row.t_s, text=row.text)
            for row in session.execute(
                sa.select(OcrText.t_s, OcrText.text)
                .where(OcrText.video_id == video_id)
                .order_by(OcrText.t_s, OcrText.idx)
            )
        )
        beings = _beings(session, video_id, language, tr)
        synthesis = session.get(VideoSynthesis, video_id)
        device = " ".join(p.strip() for p in (video.camera_make, video.camera_model) if p)
        capture = _capture_date(video)
        user = (video.title, video.summary, video.user_notes)
    data: dict[str, Any] = (
        translated(synthesis.data, SYNTHESIS_TEXTS, tr) if synthesis is not None else {}
    )
    return IndexFacts(
        video=video_facts,
        language=language,
        user_title=user[0],
        user_summary=user[1],
        user_notes=user[2],
        device=device or None,
        capture_date=capture,
        shot_ids={idx: shot_id for shot_id, idx in shots.items()},
        stories=stories,
        screen=screen,
        beings=beings,
        synthesis_title=str(data.get("title") or "") or None,
        logline=str(data.get("logline") or "") or None,
        synthesis_summary=str(data.get("summary") or "") or None,
        tags=tuple(str(t.get("label", "")) for t in data.get("tags") or [] if isinstance(t, dict)),
        chapters=_chapters(data),
    )


def _translated_facts(facts: VideoFacts, tr: Dictionary) -> VideoFacts:
    """The facts with the frames' descriptions and the place in the language of ``tr``."""
    if tr.language is None:
        return facts
    frames = tuple(
        replace(frame, data=translated(frame.data, FRAME_TEXTS, tr)) if frame.data else frame
        for frame in facts.frames
    )
    return replace(facts, frames=frames, place_label=tr.maybe(facts.place_label))


def _capture_date(video: Video) -> date | None:
    """The local date of shooting: in the place's zone, else with the recorded offset, else in
    UTC; none for an editor's export date."""
    if video.captured_at is None or video.captured_at_source == EXPORT_DATE:
        return None
    utc = video.captured_at
    utc = utc.replace(tzinfo=UTC) if utc.tzinfo is None else utc.astimezone(UTC)
    if video.capture_timezone:
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
            return utc.astimezone(ZoneInfo(video.capture_timezone)).date()
    if video.capture_utc_offset_min is not None:
        return utc.astimezone(fixed_zone(timedelta(minutes=video.capture_utc_offset_min))).date()
    return utc.date()


def _chapters(data: dict[str, Any]) -> tuple[Chapter, ...]:
    """The synthesis' chapters with their times (from the blocks they span)."""
    blocks = {
        int(b["no"]): (float(b["start"]), float(b["end"]))
        for b in data.get("blocks") or []
        if isinstance(b, dict) and "no" in b
    }
    chapters: list[Chapter] = []
    for chapter in data.get("chapters") or []:
        first, last = blocks.get(chapter.get("first")), blocks.get(chapter.get("last"))
        if first is None or last is None:
            continue
        chapters.append(
            Chapter(
                first[0], last[1], str(chapter.get("title", "")), str(chapter.get("summary", ""))
            )
        )
    return tuple(chapters)


def _beings(
    session: Session, video_id: str, language: str, tr: Dictionary = AS_WRITTEN
) -> dict[str, tuple[str, ...]]:
    """Labels of the beings located in each keyframe, the three sources fused as when read."""
    by_frame: dict[str, list[Detection]] = {}
    for row in session.execute(
        sa.select(DetectionRow)
        .where(DetectionRow.video_id == video_id)
        .order_by(DetectionRow.t_s, DetectionRow.source, DetectionRow.idx)
    ).scalars():
        box = Box.of(row.box)
        try:
            source, category = Source(row.source), Category(row.category)
        except ValueError:
            continue
        if box is not None:
            label = tr(row.label) if source == Source.VLM else row.label
            by_frame.setdefault(row.keyframe_id, []).append(
                Detection(source, label, category, box, row.score, row.main)
            )
    scanned = {
        frame_id
        for frame_id, source in session.execute(
            sa.select(SubjectScan.keyframe_id, SubjectScan.source).where(
                SubjectScan.video_id == video_id
            )
        )
        if source == Source.VLM.value
    }
    return {
        frame_id: tuple(
            s.label
            for s in fuse(found, language, vlm_scanned=True if frame_id in scanned else None)
        )
        for frame_id, found in by_frame.items()
    }
