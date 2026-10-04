"""What an analysis file holds, table by table, and how a row goes in and comes back.

Rows are copied column by column from the models, so a column added later travels without a
change here. Left out: the owner id (the video is the file's own), artifact paths (images are
rebuilt on import) and the ids of rows nobody refers to. Shots and keyframes keep theirs as
local references: the rows that point at them are linked again on import.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.db.base import Base, UTCDateTime
from vfe_vision.db.models import (
    AudioScene,
    AudioSegment,
    AudioStats,
    ContextPlace,
    ContextSun,
    ContextWeather,
    Detection,
    FrameAnalysis,
    GpsPoint,
    Keyframe,
    OcrText,
    Shot,
    ShotStory,
    StageRun,
    SubjectScan,
    Transcript,
    TranscriptSegment,
    Video,
    VideoMetadata,
    VideoSignals,
    VideoSynthesis,
)
from vfe_vision.domain.sidecar import iso_utc, parse_utc

OWNER = "video_id"


@dataclass(frozen=True, slots=True)
class Table:
    key: str  # its name in the file
    model: type[Base]
    many: bool = True  # a list of rows, or at most one row
    left_out: frozenset[str] = frozenset()  # besides the owner: row ids, artifact paths
    order: tuple[str, ...] = ()  # stable order of the rows in the file


def _out(*names: str) -> frozenset[str]:
    return frozenset(names)


# In the order they are inserted: a row comes after the rows it refers to.
TABLES: tuple[Table, ...] = (
    Table("video_metadata", VideoMetadata, many=False),
    Table("gps_points", GpsPoint, left_out=_out("id"), order=("id",)),
    Table("video_signals", VideoSignals, many=False),
    Table("audio_stats", AudioStats, many=False),
    Table("context_place", ContextPlace, many=False),
    Table("context_sun", ContextSun, many=False),
    Table("context_weather", ContextWeather, many=False),
    Table("shots", Shot, order=("idx",)),
    Table("keyframes", Keyframe, left_out=_out("image_path", "thumb_path"), order=("idx",)),
    Table("frame_analyses", FrameAnalysis, left_out=_out("id")),
    Table("detections", Detection, left_out=_out("id"), order=("t_s", "source", "idx")),
    Table("subject_scans", SubjectScan, order=("keyframe_id", "source")),
    Table("ocr_texts", OcrText, left_out=_out("id"), order=("t_s", "idx")),
    Table("transcript", Transcript, many=False),
    Table("transcript_segments", TranscriptSegment, left_out=_out("id"), order=("idx",)),
    Table("audio_scene", AudioScene, many=False),
    Table("audio_segments", AudioSegment, left_out=_out("id"), order=("id",)),
    Table("shot_stories", ShotStory, left_out=_out("id", "frame_paths"), order=("start_s", "part")),
    Table("video_synthesis", VideoSynthesis, many=False),
)

# The video row: the file's identity and what the analyses found in it (technical, capture).
# The user's own fields go in their own block; the library's bookkeeping stays out.
VIDEO_LEFT_OUT = _out(
    "id", "root_id", "path", "path_key", "rel_path", "status", "poster_path", "created_at",
    "updated_at", "sidecar_links",
)  # fmt: skip
USER_FIELDS = (
    "title", "summary", "rating", "favorite", "user_notes", "transcript_mode",
    "transcript_language",
)  # fmt: skip
FILE_FIELDS = _out("filename", "size_bytes", "fingerprint", "mtime", "last_analyzed_at")
RUN_LEFT_OUT = _out("id", "job_id", OWNER)


def columns(model: type[Base]) -> dict[str, sa.Column[Any]]:
    return {column.key: column for column in sa.inspect(model).columns}


def to_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, Enum):
        return value.value
    return value


def from_json(column: sa.Column[Any], value: Any) -> Any:
    """A value of the file in the column's type (dates in UTC, enumerations)."""
    if value is None:
        return None
    if isinstance(column.type, UTCDateTime):
        return parse_utc(str(value))
    if isinstance(column.type, sa.Enum) and column.type.enum_class is not None:
        return column.type.enum_class(value)
    return value


def dump(model: type[Base], row: Base, left_out: frozenset[str]) -> dict[str, Any]:
    return {name: to_json(getattr(row, name)) for name in columns(model) if name not in left_out}


def load[M: Base](
    model: type[M], data: Mapping[str, Any], left_out: frozenset[str], /, **fixed: Any
) -> M:
    """A new row from the file's values (unknown keys ignored, missing ones by default) and
    the ``fixed`` ones (owner, linked ids, rebuilt paths), which may be named like any column
    (``data``, ``model``…)."""
    values = {
        name: from_json(column, data[name])
        for name, column in columns(model).items()
        if name in data and name not in left_out and name not in fixed
    }
    return model(**values, **fixed)


def rows_of(session: Session, table: Table, video_id: str) -> list[Base]:
    model = table.model
    stmt: sa.Select[Any]
    if model is FrameAnalysis:  # owned through its keyframe
        stmt = (
            sa.select(FrameAnalysis)
            .join(Keyframe, FrameAnalysis.keyframe_id == Keyframe.id)
            .where(Keyframe.video_id == video_id)
            .order_by(Keyframe.idx)
        )
    else:
        table_columns = columns(model)
        stmt = (
            sa.select(model)
            .where(table_columns[OWNER] == video_id)
            .order_by(*(table_columns[name] for name in table.order))
        )
    return list(session.execute(stmt).scalars())


def dump_video(video: Video) -> tuple[dict[str, Any], dict[str, Any]]:
    """The video block and the user's block."""
    facts = dump(Video, video, VIDEO_LEFT_OUT | frozenset(USER_FIELDS))
    user = {name: to_json(getattr(video, name)) for name in USER_FIELDS}
    return facts, user


def dump_run(run: StageRun) -> dict[str, Any]:
    return dump(StageRun, run, RUN_LEFT_OUT)
