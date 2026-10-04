"""ORM models. Every JSON payload stored here carries a ``schema_version``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vfe_vision.core.ids import new_id
from vfe_vision.db.base import Base, utcnow
from vfe_vision.domain.enums import (
    JobKind,
    JobStatus,
    Orientation,
    RootKind,
    StageStatus,
    VideoStatus,
)

_ID = sa.String(32)


class LibraryRoot(Base):
    """A folder of videos declared by the user. The application writes nothing inside it but
    the analysis file of each video, unless that is turned off.

    A root of kind ``files`` covers only the files it lists, directly in its folder: it is made
    for the videos of a Resolve timeline that no folder of the library holds.
    """

    __tablename__ = "library_roots"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    kind: Mapped[RootKind] = mapped_column(default=RootKind.FOLDER, server_default="folder")
    path: Mapped[str] = mapped_column(sa.Text)
    path_key: Mapped[str] = mapped_column(sa.Text, unique=True)
    files: Mapped[list[str] | None]  # kind files: the names listed, as found on disk
    volume_serial: Mapped[str | None] = mapped_column(sa.String(32))
    label: Mapped[str] = mapped_column(sa.String(200))
    recursive: Mapped[bool] = mapped_column(default=True)
    exclude_globs: Mapped[list[str]] = mapped_column(default=list)
    analysis_focus: Mapped[str | None] = mapped_column(sa.Text)
    clock_offset_s: Mapped[int | None]
    default_latitude: Mapped[float | None]
    default_longitude: Mapped[float | None]
    default_timezone: Mapped[str | None] = mapped_column(sa.String(64))
    auto_analyze: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_scan_at: Mapped[datetime | None]

    videos: Mapped[list[Video]] = relationship(back_populates="root", passive_deletes=True)


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    root_id: Mapped[str] = mapped_column(
        sa.ForeignKey("library_roots.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(sa.Text)
    path_key: Mapped[str] = mapped_column(sa.Text, unique=True)
    rel_path: Mapped[str] = mapped_column(sa.Text)
    filename: Mapped[str] = mapped_column(sa.String(512))
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger)
    mtime: Mapped[float]
    fingerprint: Mapped[str] = mapped_column(sa.String(64), index=True)
    status: Mapped[VideoStatus] = mapped_column(default=VideoStatus.NEW, index=True)

    # --- technical (probe) ---
    duration_s: Mapped[float | None]
    width: Mapped[int | None]  # display width, rotation applied
    height: Mapped[int | None]
    fps: Mapped[float | None]
    video_codec: Mapped[str | None] = mapped_column(sa.String(32))
    audio_codec: Mapped[str | None] = mapped_column(sa.String(32))
    has_audio: Mapped[bool | None]
    orientation: Mapped[Orientation | None]
    is_hdr: Mapped[bool | None]
    color_transfer: Mapped[str | None] = mapped_column(sa.String(32))
    color_profile: Mapped[str | None] = mapped_column(sa.String(32))  # e.g. "d-log", "s-log3"
    hdr_format: Mapped[str | None] = mapped_column(sa.String(16))  # HDR10, HDR10+, HLG, DV
    hdr_peak_nits: Mapped[float | None]  # tone-mapping peak (MaxCLL or mastering maximum)
    is_vfr: Mapped[bool | None]  # variable frame rate (average differs from the nominal rate)
    capture_fps: Mapped[float | None]  # frames captured per second (slow motion when > fps)
    start_timecode: Mapped[str | None] = mapped_column(sa.String(16))
    encoder: Mapped[str | None] = mapped_column(sa.String(200))

    # --- capture context (metadata stage) ---
    captured_at: Mapped[datetime | None]
    captured_at_source: Mapped[str | None] = mapped_column(sa.String(64))
    captured_at_confidence: Mapped[str | None] = mapped_column(sa.String(16))
    capture_timezone: Mapped[str | None] = mapped_column(sa.String(64))
    capture_utc_offset_min: Mapped[int | None]  # local UTC offset at capture, when known
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    altitude_m: Mapped[float | None]
    location_source: Mapped[str | None] = mapped_column(sa.String(64))
    camera_make: Mapped[str | None] = mapped_column(sa.String(100))
    camera_model: Mapped[str | None] = mapped_column(sa.String(100))
    camera_os: Mapped[str | None] = mapped_column(sa.String(32))  # e.g. "Android 16"

    # --- synthesis & user annotations ---
    title: Mapped[str | None] = mapped_column(sa.Text)
    summary: Mapped[str | None] = mapped_column(sa.Text)
    poster_path: Mapped[str | None] = mapped_column(sa.Text)
    rating: Mapped[int | None]
    favorite: Mapped[bool] = mapped_column(default=False)
    user_notes: Mapped[str | None] = mapped_column(sa.Text)
    # Transcription choices of the user (kept across analyses): auto (None), always, never.
    transcript_mode: Mapped[str | None] = mapped_column(sa.String(8))
    transcript_language: Mapped[str | None] = mapped_column(sa.String(8))

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    last_analyzed_at: Mapped[datetime | None]
    # What the analysis file last written says of the video's DaVinci Resolve timelines (a
    # digest): the file is written again when the links differ. None: none said.
    sidecar_links: Mapped[str | None] = mapped_column(sa.String(16))

    root: Mapped[LibraryRoot] = relationship(back_populates="videos")
    keyframes: Mapped[list[Keyframe]] = relationship(
        back_populates="video", order_by="Keyframe.idx", passive_deletes=True
    )


class VideoMetadata(Base):
    """Raw tool outputs (ffprobe, exiftool) and their normalised form."""

    __tablename__ = "video_metadata"

    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    probe: Mapped[dict[str, Any] | None]
    exif: Mapped[dict[str, Any] | None]
    normalized: Mapped[dict[str, Any] | None]
    schema_version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Keyframe(Base):
    __tablename__ = "keyframes"
    __table_args__ = (sa.UniqueConstraint("video_id", "idx"),)

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int]
    t_s: Mapped[float]
    image_path: Mapped[str] = mapped_column(sa.Text)  # relative to the artifacts directory
    thumb_path: Mapped[str] = mapped_column(sa.Text)
    width: Mapped[int]
    height: Mapped[int]
    selection_reason: Mapped[str] = mapped_column(sa.String(32))
    phash: Mapped[str | None] = mapped_column(sa.String(16))
    duplicate_of: Mapped[str | None] = mapped_column(_ID)
    sharpness: Mapped[float | None]
    shot_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("shots.id", ondelete="SET NULL"), index=True
    )
    metrics: Mapped[dict[str, Any] | None]  # exposure, colours, measured CCT…
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    video: Mapped[Video] = relationship(back_populates="keyframes")
    analysis: Mapped[FrameAnalysis | None] = relationship(
        back_populates="keyframe", uselist=False, passive_deletes=True
    )


class Shot(Base):
    """A continuous shot between two cuts, with camera motion and technical averages."""

    __tablename__ = "shots"
    __table_args__ = (sa.UniqueConstraint("video_id", "idx"),)

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int]
    start_s: Mapped[float]
    end_s: Mapped[float]
    boundary: Mapped[str] = mapped_column(sa.String(16))  # start | cut | fade
    motion: Mapped[str] = mapped_column(sa.String(16))  # static | pan | tilt | zoom | handheld…
    motion_score: Mapped[float]
    stability: Mapped[float]  # 0 (shaky) … 1 (locked off)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class GpsPoint(Base):
    """GPS fix: a single point or one sample of a timed track (GoPro, DJI, GPX…)."""

    __tablename__ = "gps_points"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    t_s: Mapped[float | None]  # offset from the start of the video
    utc: Mapped[datetime | None]
    latitude: Mapped[float]
    longitude: Mapped[float]
    altitude_m: Mapped[float | None]
    speed_mps: Mapped[float | None]
    source: Mapped[str] = mapped_column(sa.String(32))


class VideoSignals(Base):
    """Time series for charts and the timeline (schema-versioned JSON)."""

    __tablename__ = "video_signals"

    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    schema_version: Mapped[int] = mapped_column(default=1)
    visual: Mapped[dict[str, Any] | None]  # {"t": [...], "luma": [...], "motion": [...], …}
    audio: Mapped[dict[str, Any] | None]  # {"t": [...], "momentary_lufs": [...]}
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AudioStats(Base):
    __tablename__ = "audio_stats"

    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    integrated_lufs: Mapped[float | None]
    loudness_range_lu: Mapped[float | None]
    true_peak_dbfs: Mapped[float | None]
    silence_ratio: Mapped[float | None]
    silences: Mapped[list[Any]] = mapped_column(default=list)  # [[start, end], …]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ContextPlace(Base):
    """Where the video was shot: reverse geocoding of its position."""

    __tablename__ = "context_place"

    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(sa.String(32))  # nominatim | offline
    label: Mapped[str | None] = mapped_column(sa.String(300))  # "Giverny, Eure, France"
    locality: Mapped[str | None] = mapped_column(sa.String(200))
    region: Mapped[str | None] = mapped_column(sa.String(200))
    country: Mapped[str | None] = mapped_column(sa.String(100))
    country_code: Mapped[str | None] = mapped_column(sa.String(2), index=True)
    data: Mapped[dict[str, Any]]  # address, OSM reference, distance, licence…
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ContextSun(Base):
    """Sun position and light phase at the capture instant (computed locally)."""

    __tablename__ = "context_sun"

    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    at_utc: Mapped[datetime]
    elevation_deg: Mapped[float]
    azimuth_deg: Mapped[float]
    # None when the capture time is only probable and the phase changes within ± 30 min.
    light_phase: Mapped[str | None] = mapped_column(sa.String(24), index=True)
    twilight_phase: Mapped[str | None] = mapped_column(sa.String(24))
    day_part: Mapped[str | None] = mapped_column(sa.String(16))
    theoretical_cct_k: Mapped[int | None]
    data: Mapped[dict[str, Any]]  # sunrise/sunset, golden/blue hours, moon, spans…
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ContextWeather(Base):
    """Model weather at the capture instant (Open-Meteo), with its source and grid cell."""

    __tablename__ = "context_weather"

    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(sa.String(32))  # historical_forecast | archive
    at_utc: Mapped[datetime]
    weather_code: Mapped[int | None]
    category: Mapped[str | None] = mapped_column(sa.String(16), index=True)
    temperature_c: Mapped[float | None]
    cloud_cover_pct: Mapped[float | None]
    provisional: Mapped[bool] = mapped_column(default=False)
    data: Mapped[dict[str, Any]]  # every value, units, surrounding hours, grid cell…
    fetched_at: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ServiceCache(Base):
    """Raw answers of online services, shared by every video (Nominatim, Open-Meteo)."""

    __tablename__ = "service_cache"

    key: Mapped[str] = mapped_column(sa.String(200), primary_key=True)
    service: Mapped[str] = mapped_column(sa.String(32), index=True)
    response: Mapped[dict[str, Any]]
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)


class FrameAnalysis(Base):
    """Structured description of one keyframe by the vision model."""

    __tablename__ = "frame_analyses"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    keyframe_id: Mapped[str] = mapped_column(
        sa.ForeignKey("keyframes.id", ondelete="CASCADE"), unique=True
    )
    model: Mapped[str] = mapped_column(sa.String(200))
    prompt_version: Mapped[str] = mapped_column(sa.String(32))
    schema_version: Mapped[int]
    focus: Mapped[str | None] = mapped_column(sa.Text)
    language: Mapped[str] = mapped_column(sa.String(8))
    data: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    keyframe: Mapped[Keyframe] = relationship(back_populates="analysis")


class ShotStory(Base):
    """What happens in one shot (or one ~20 s part of a long shot), told by the vision model from
    up to four of its frames. Replaced as a whole when the stage runs again."""

    __tablename__ = "shot_stories"
    __table_args__ = (sa.UniqueConstraint("shot_id", "part"),)

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    shot_id: Mapped[str] = mapped_column(sa.ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    part: Mapped[int]  # 1-based
    parts: Mapped[int]
    start_s: Mapped[float]
    end_s: Mapped[float]
    frame_times: Mapped[list[Any]]  # seconds in the video, one per image sent
    keyframe_ids: Mapped[list[Any]]  # null for an extra frame extracted for the story
    frame_paths: Mapped[list[Any]]  # artifact paths of the images sent (512 px)
    model: Mapped[str] = mapped_column(sa.String(200))
    prompt_version: Mapped[str] = mapped_column(sa.String(32))
    schema_version: Mapped[int]
    language: Mapped[str] = mapped_column(sa.String(8))
    answer: Mapped[dict[str, Any]]  # the model's answer as received (validated)
    story: Mapped[dict[str, Any]]  # what is shown: summary, main action, notes, possible cut
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class VideoSynthesis(Base):
    """The synthesis of a video: texts written by the language model (title, logline,
    summary, chapter titles, reasons of the highlight moments, tags) and the blocks they refer
    to. Chapters and highlights are chosen by code; usability, suggestions and in/out points are
    computed again when read, from the current facts."""

    __tablename__ = "video_synthesis"

    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    schema_version: Mapped[int]
    rules_version: Mapped[str] = mapped_column(sa.String(64))
    model: Mapped[str] = mapped_column(sa.String(200))
    prompt_version: Mapped[str] = mapped_column(sa.String(64))
    language: Mapped[str] = mapped_column(sa.String(8))
    strategy: Mapped[str] = mapped_column(sa.String(16))  # single | map_reduce
    input_variant: Mapped[str] = mapped_column(sa.String(8))  # V4 (full) | V4c (compact)
    proofread: Mapped[bool] = mapped_column(default=False)
    input_key: Mapped[str] = mapped_column(sa.String(64))  # hash of the input written from
    data: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class SearchChunk(Base):
    """A passage of the search index: the whole video, a chapter, a shot, a keyframe
    or ~30 s of speech, as plain text, with the facets the filters read.

    Derived data, rebuilt by the ``index`` stage (never in the analysis file). The full-text
    table ``search_fts`` (FTS5, external content) follows it through triggers (migration 0013).
    Ids only grow (AUTOINCREMENT): a rebuilt index never reuses one, so a reader can tell that
    its copy of the vectors is out of date.
    """

    __tablename__ = "search_chunks"
    __table_args__ = {"sqlite_autoincrement": True}  # noqa: RUF012 - SQLAlchemy reads it

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(
        sa.String(16)
    )  # video | chapter | shot | keyframe | transcript
    t_start: Mapped[float | None]  # seconds of the source file; None for the whole video
    t_end: Mapped[float | None]
    shot_id: Mapped[str | None] = mapped_column(_ID)
    keyframe_id: Mapped[str | None] = mapped_column(_ID)  # its keyframe, or the one shown for it
    language: Mapped[str | None] = mapped_column(sa.String(8))
    text: Mapped[str] = mapped_column(sa.Text)
    facets: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ChunkVector(Base):
    """The embedding of a passage (256 float32, unit length) by the model it names."""

    __tablename__ = "chunk_vectors"

    chunk_id: Mapped[int] = mapped_column(
        sa.ForeignKey("search_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(sa.String(200), index=True)
    dim: Mapped[int]
    vector: Mapped[bytes] = mapped_column(sa.LargeBinary)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (sa.Index("ix_jobs_status_priority", "status", "priority", "created_at"),)

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    kind: Mapped[JobKind]
    video_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    root_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("library_roots.id", ondelete="CASCADE")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[JobStatus] = mapped_column(default=JobStatus.QUEUED)
    priority: Mapped[int] = mapped_column(default=100)  # lower runs first
    progress: Mapped[float] = mapped_column(default=0.0)
    message: Mapped[str | None] = mapped_column(sa.Text)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    error: Mapped[str | None] = mapped_column(sa.Text)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    worker_pid: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    heartbeat_at: Mapped[datetime | None]


class StageRun(Base):
    """One execution (or cache hit) of a pipeline stage for a video."""

    __tablename__ = "stage_runs"
    __table_args__ = (sa.Index("ix_stage_runs_video_stage", "video_id", "stage", "created_at"),)

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    job_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    video_id: Mapped[str] = mapped_column(sa.ForeignKey("videos.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(sa.String(64))
    stage_version: Mapped[int]
    cache_key: Mapped[str] = mapped_column(sa.String(64))
    # Key of the video data the result was computed from; NULL before migration 0005.
    input_key: Mapped[str | None] = mapped_column(sa.String(64))
    status: Mapped[StageStatus] = mapped_column(default=StageStatus.PENDING)
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(sa.Text)
    skip_reason: Mapped[str | None] = mapped_column(sa.Text)
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    duration_ms: Mapped[int | None]


class Event(Base):
    """Append-only event log read by the SSE endpoint (``Last-Event-ID`` = ``id``)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    type: Mapped[str] = mapped_column(sa.String(64))
    job_id: Mapped[str | None] = mapped_column(_ID)
    video_id: Mapped[str | None] = mapped_column(_ID)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


class LlmCall(Base):
    """Cache and audit trail of LM Studio calls (key = hash of image + model + prompt…)."""

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    cache_key: Mapped[str] = mapped_column(sa.String(64), unique=True)
    purpose: Mapped[str] = mapped_column(sa.String(64))
    model: Mapped[str] = mapped_column(sa.String(200))
    prompt_version: Mapped[str] = mapped_column(sa.String(32))
    schema_version: Mapped[int]
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    latency_ms: Mapped[int | None]
    response: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Question(Base):
    """A question asked about the library and its answer: the filters it was
    asked with, the text the model wrote (its citations checked), the passages it cites, the
    optional visual check, and what it cost. Citations name their video by id only: a video
    removed since leaves them in place, marked unavailable when read."""

    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    schema_version: Mapped[int]
    question: Mapped[str] = mapped_column(sa.Text)
    filters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    language: Mapped[str] = mapped_column(sa.String(8))
    model: Mapped[str | None] = mapped_column(sa.String(200))
    prompt_version: Mapped[str | None] = mapped_column(sa.String(32))
    # answered | no_answer | uncited | no_passages | cancelled | failed
    status: Mapped[str] = mapped_column(sa.String(16))
    answer: Mapped[str] = mapped_column(sa.Text, default="")
    citations: Mapped[list[Any]] = mapped_column(default=list)
    passages: Mapped[int] = mapped_column(default=0)  # given to the model
    visual_check: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(sa.Text)
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    timings: Mapped[dict[str, Any]] = mapped_column(default=dict)  # milliseconds per step
    # budget of the slot, candidates, cut answer, citations of passages that do not exist…
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class Setting(Base):
    """User preferences editable from the UI (JSON value per key)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(sa.String(100), primary_key=True)
    value: Mapped[dict[str, Any]]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Transcript(Base):
    """What is said in a video (Whisper, or subtitles found in the file)."""

    __tablename__ = "transcripts"

    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(sa.String(16))  # asr | embedded | sidecar
    status: Mapped[str] = mapped_column(sa.String(16))  # ok | no_speech
    model: Mapped[str | None] = mapped_column(sa.String(64))
    language: Mapped[str | None] = mapped_column(sa.String(8), index=True)
    language_probability: Mapped[float | None]
    languages: Mapped[list[Any]] = mapped_column(default=list)  # [{language, speech_s, p}]
    duration_s: Mapped[float | None]
    speech_s: Mapped[float | None]
    text: Mapped[str] = mapped_column(sa.Text, default="")  # reliable segments, for search
    segment_count: Mapped[int] = mapped_column(default=0)
    word_count: Mapped[int] = mapped_column(default=0)
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)  # schema_version, settings…
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    segments: Mapped[list[TranscriptSegment]] = relationship(
        order_by="TranscriptSegment.idx", cascade="all, delete-orphan", passive_deletes=True
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (sa.UniqueConstraint("video_id", "idx"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("transcripts.video_id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int]
    start_s: Mapped[float]
    end_s: Mapped[float]
    text: Mapped[str] = mapped_column(sa.Text)
    language: Mapped[str | None] = mapped_column(sa.String(8))
    avg_logprob: Mapped[float | None]
    no_speech_prob: Mapped[float | None]
    compression_ratio: Mapped[float | None]
    temperature: Mapped[float | None]
    suspect: Mapped[bool] = mapped_column(default=False)  # likely hallucinated: shown greyed
    second_pass: Mapped[bool] = mapped_column(default=False)
    words: Mapped[list[Any]] = mapped_column(default=list)  # [[start, end, word, probability]]


class AudioScene(Base):
    """What is heard (YAMNet): categories, instruments, events, curves."""

    __tablename__ = "audio_scene"

    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(sa.String(64))
    speech_s: Mapped[float | None]
    music_s: Mapped[float | None]
    dominant: Mapped[str | None] = mapped_column(sa.String(24), index=True)
    data: Mapped[dict[str, Any]]  # presence, instruments, top labels, events, curves, shots…
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AudioSegment(Base):
    """A stretch of one sound category, or a specific event (door, applause…), for search."""

    __tablename__ = "audio_segments"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(8))  # segment | event | instrument
    category: Mapped[str] = mapped_column(sa.String(24), index=True)
    label: Mapped[str | None] = mapped_column(sa.String(120))
    start_s: Mapped[float]
    end_s: Mapped[float]
    score: Mapped[float | None]


class OcrText(Base):
    """A line of text read in a keyframe (PP-OCRv6), with its box in display orientation."""

    __tablename__ = "ocr_texts"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    keyframe_id: Mapped[str] = mapped_column(
        sa.ForeignKey("keyframes.id", ondelete="CASCADE"), index=True
    )
    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    t_s: Mapped[float]
    idx: Mapped[int]
    text: Mapped[str] = mapped_column(sa.Text)
    score: Mapped[float]
    box: Mapped[list[Any]]  # 4 points [x, y] in 0..1
    engine: Mapped[str] = mapped_column(sa.String(64))


class Detection(Base):
    """A living being located in a keyframe by one source.

    ``source`` is detector (D-FINE), faces (YuNet) or vlm (the vision model); the sources are
    fused when read (``domain.subjects.fuse``). The box is [x1, y1, x2, y2] in 0..1 of the
    displayed image. A position only: nothing identifies anyone.
    """

    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    keyframe_id: Mapped[str] = mapped_column(
        sa.ForeignKey("keyframes.id", ondelete="CASCADE"), index=True
    )
    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    t_s: Mapped[float]
    source: Mapped[str] = mapped_column(sa.String(16))
    idx: Mapped[int]
    label: Mapped[str] = mapped_column(sa.String(80))
    category: Mapped[str] = mapped_column(sa.String(16))
    box: Mapped[list[Any]]
    score: Mapped[float | None]
    main: Mapped[bool] = mapped_column(default=False)
    points: Mapped[list[Any] | None]  # face landmarks [[x, y] × 5] in 0..1
    model: Mapped[str] = mapped_column(sa.String(200))


class SubjectScan(Base):
    """A keyframe that a subject source looked at, even when it found nobody.

    The fusion needs to know it: detector boxes that the vision model did not confirm are
    dropped only when the vision model did look at that keyframe.
    """

    __tablename__ = "subject_scans"

    keyframe_id: Mapped[str] = mapped_column(
        sa.ForeignKey("keyframes.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(sa.String(16), primary_key=True)
    video_id: Mapped[str] = mapped_column(
        sa.ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(sa.String(200))
    found: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TimelineBin(Base):
    """A DaVinci Resolve timeline shown as a bin of the library.

    A view, never an owner: its items name files by path key and are joined to the videos when
    read (``db.timeline_bins``), so removing it forgets no video and no analysis. An update
    from Resolve replaces its items (the timeline as it is now).
    """

    __tablename__ = "timeline_bins"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    label: Mapped[str] = mapped_column(sa.String(200))
    source_key: Mapped[str] = mapped_column(sa.String(100), unique=True)  # project/timeline ids
    # Database, project and timeline as read (domain.resolve_timeline.resolve_identity), studio.
    resolve: Mapped[dict[str, Any]]
    auto_analyze: Mapped[bool]
    report: Mapped[dict[str, Any]] = mapped_column(default=dict)  # items skipped, read errors
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    synced_at: Mapped[datetime] = mapped_column(default=utcnow)  # when Resolve was read


class TimelineBinItem(Base):
    """One video file of a timeline bin, with every place the timeline uses it."""

    __tablename__ = "timeline_bin_items"
    __table_args__ = (sa.UniqueConstraint("bin_id", "path_key"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    bin_id: Mapped[str] = mapped_column(sa.ForeignKey("timeline_bins.id", ondelete="CASCADE"))
    position: Mapped[int]  # order of first appearance on the timeline
    path: Mapped[str] = mapped_column(sa.Text)  # as Resolve names it (long-path prefix removed)
    path_key: Mapped[str] = mapped_column(sa.Text, index=True)
    # The path key of the library's video when it is the same file under another path (a share
    # and its mapped drive, a copy): checked by content.
    video_key: Mapped[str | None] = mapped_column(sa.Text, index=True)
    uses: Mapped[list[Any]]  # domain.resolve_timeline.use_to_json of each clip
    state: Mapped[str] = mapped_column(sa.String(16))  # domain.enums.TimelineItemState
    note: Mapped[str | None] = mapped_column(sa.Text)


class SubtitleFile(Base):
    """A subtitle file the application wrote next to a video, and what it wrote: the
    file is replaced only while it still holds exactly that. One the user changed, or one the
    application never wrote, is left as it is."""

    __tablename__ = "subtitle_files"

    path_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    path: Mapped[str] = mapped_column(sa.Text)
    # The video it was written for; no link: the record stays while the file does.
    video_id: Mapped[str | None] = mapped_column(_ID)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    written_at: Mapped[datetime] = mapped_column(default=utcnow)


class Translation(Base):
    """A text of the analyses in another language, keyed by the text itself: the
    analysis rows never change, so no stage reading them runs again. ``text`` equal to
    ``source``: the model gave it back unchanged (already in that language, or the same word in
    both), kept so that it is not asked for again."""

    __tablename__ = "translations"

    source_sha: Mapped[str] = mapped_column(sa.String(64), primary_key=True)  # sha256(source)
    target: Mapped[str] = mapped_column(sa.String(8), primary_key=True)  # fr | en
    source: Mapped[str] = mapped_column(sa.Text)
    text: Mapped[str] = mapped_column(sa.Text)
    # The model that translated it; None: read from the analysis files of both languages.
    model: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class BenchRun(Base):
    """A run of the model bench: the frames of the library the vision models were
    asked about, what each model answered and measured (``data``, see ``domain.bench``), and
    the blind ratings the user gave afterwards (``ratings``: keyframe id → model key → 0..3)."""

    __tablename__ = "bench_runs"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    job_id: Mapped[str | None] = mapped_column(_ID, index=True)  # the job that runs it
    status: Mapped[str] = mapped_column(sa.String(16), default="queued")
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ratings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
