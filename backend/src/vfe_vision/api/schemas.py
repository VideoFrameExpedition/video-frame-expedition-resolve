"""Public API models (the OpenAPI contract the TypeScript client is generated from)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from vfe_vision.db.models import (
    AudioStats,
    ContextPlace,
    ContextSun,
    ContextWeather,
    GpsPoint,
    Job,
    Keyframe,
    Shot,
    ShotStory,
    StageRun,
    Video,
)
from vfe_vision.db.models import FrameAnalysis as FrameAnalysisRow
from vfe_vision.domain.cut_points import CutPoints
from vfe_vision.domain.editing import Clip
from vfe_vision.domain.enums import (
    AnalysisMode,
    JobKind,
    JobStatus,
    Orientation,
    RootKind,
    StageStatus,
    VideoStatus,
)
from vfe_vision.domain.languages import whisper_code
from vfe_vision.domain.media import playback_issue
from vfe_vision.domain.resolve_timeline import (
    ClipUse,
    ResolveLink,
    SkippedItems,
    TimelineInfo,
    identity_parts,
    record_seconds,
    record_timecodes,
    skipped_to_json,
)
from vfe_vision.domain.search_chunks import ChunkKind
from vfe_vision.domain.shots import shown_motion
from vfe_vision.domain.sidecar import SidecarStatus
from vfe_vision.domain.sound_names import sound_name
from vfe_vision.domain.subjects import Subject
from vfe_vision.domain.sun import LightPhase
from vfe_vision.domain.timecode import format_clock
from vfe_vision.domain.timeline_build import (
    TimelineOrder,
    TimelineParts,
    file_name,
)
from vfe_vision.domain.translation import (
    AS_WRITTEN,
    FRAME_TEXTS,
    PLACE_DATA_TEXTS,
    STORY_TEXTS,
    Dictionary,
    translated,
)
from vfe_vision.domain.vision import FrameAnalysis, ShotType
from vfe_vision.domain.weather_codes import WeatherCategory
from vfe_vision.pipeline.sidecar.writer import SidecarResult
from vfe_vision.pipeline.stage import Resource, Stage, StageFamily
from vfe_vision.pipeline.stages.transcript import DISABLED as TRANSCRIPTION_DISABLED
from vfe_vision.services.ask import AskStarted, Citation, QuestionView
from vfe_vision.services.audio_text import AudioView, OcrView, TranscriptView
from vfe_vision.services.container import AppContainer
from vfe_vision.services.context import ContextView
from vfe_vision.services.editing import MAX_ITEMS as MATCH_MAX_ITEMS
from vfe_vision.services.editing import ClipMatch, MatchMethod, RangeView
from vfe_vision.services.exports import MAX_LIBRARY_VIDEOS, ExportFormat, ExportOption
from vfe_vision.services.jobs import JobsSummary, JobView
from vfe_vision.services.library import Folder, RootFolders, RootStats
from vfe_vision.services.resolve import MAX_VIDEOS as RESOLVE_MAX_VIDEOS
from vfe_vision.services.search import (
    IndexState,
    SearchFacets,
    SearchFilters,
    SearchHit,
    SearchResult,
)
from vfe_vision.services.subjects import SubjectsView
from vfe_vision.services.subtitle_files import WriteStatus, WrittenSubtitles
from vfe_vision.services.synthesis import SynthesisView
from vfe_vision.services.timeline_bins import (
    BinImport,
    BinStats,
    ItemView,
    ProjectView,
    TimelinePreview,
    TimelineView,
)
from vfe_vision.services.timeline_build import (
    ResolveBuild,
    SkippedVideo,
    SkipReason,
    TimelinePlan,
    plan_timeline,
)
from vfe_vision.services.videos import ContextBrief, KeyframeView, SignalsView, VideoDetail

API_PREFIX = "/api/v1"
RESOLVE_ID_PATTERN = r"^[0-9A-Za-z-]+$"  # ids of Resolve's projects and timelines


def media_url(rel_path: str | None) -> str | None:
    return f"{API_PREFIX}/media/{quote(rel_path)}" if rel_path else None


class ApiModel(BaseModel):
    """Base of response models: fields with a default are still always present in responses."""

    model_config = ConfigDict(
        from_attributes=True, json_schema_serialization_defaults_required=True
    )


def _parse_stored[M: BaseModel](model: type[M], data: dict[str, Any] | None) -> M | None:
    """Stored JSON written by an older stage version is hidden rather than breaking the API."""
    if not data:
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------- library
class RootOut(ApiModel):
    id: str
    kind: RootKind = Field(
        description="folder: the folder (and its subfolders); files: only the files added "
        "from a Resolve timeline."
    )
    path: str
    label: str
    recursive: bool
    exclude_globs: list[str]
    analysis_focus: str | None
    auto_analyze: bool
    clock_offset_s: int | None
    default_latitude: float | None
    default_longitude: float | None
    default_timezone: str | None
    created_at: datetime
    last_scan_at: datetime | None
    video_count: int = 0
    ready_count: int = 0
    offline_count: int = 0
    files_count: int | None = Field(
        default=None, description="Chosen files (kind files); null for a folder."
    )

    @classmethod
    def from_stats(cls, stats: RootStats) -> RootOut:
        root = stats.root
        return cls.model_validate(root).model_copy(
            update={
                "video_count": stats.video_count,
                "ready_count": stats.ready_count,
                "offline_count": stats.offline_count,
                "files_count": len(root.files or ()) if root.kind == RootKind.FILES else None,
            }
        )


class RootCreate(BaseModel):
    path: str = Field(min_length=3, description="Absolute path of the video folder.")
    label: str | None = Field(default=None, max_length=200)
    recursive: bool = True
    analysis_focus: str | None = Field(default=None, max_length=2000)
    auto_analyze: bool = True


def _valid_timezone(value: str | None) -> str | None:
    """An IANA zone name (``Europe/Paris``) or nothing; an empty field clears the default."""
    if value is None or not value.strip():
        return None
    name = value.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Fuseau horaire inconnu : {name}") from exc
    return name


class RootUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    recursive: bool | None = None
    exclude_globs: list[str] | None = None
    analysis_focus: str | None = Field(default=None, max_length=2000)
    auto_analyze: bool | None = None
    clock_offset_s: int | None = None
    default_latitude: float | None = Field(default=None, ge=-90, le=90)
    default_longitude: float | None = Field(default=None, ge=-180, le=180)
    default_timezone: str | None = Field(default=None, max_length=64)

    _check_timezone = field_validator("default_timezone")(_valid_timezone)


class RootCreated(BaseModel):
    root: RootOut
    scan_job: JobOut


# ---------------------------------------------------------------- jobs
class JobOut(ApiModel):
    id: str
    kind: JobKind
    status: JobStatus
    video_id: str | None
    root_id: str | None
    payload: dict[str, Any]
    priority: int
    progress: float
    message: str | None
    error: str | None
    attempts: int
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    target: str | None = Field(
        default=None, description="Name of the video, folder or timeline concerned."
    )
    place: str | None = Field(default=None, description="Folder of the video: « CATS › hdr ».")

    @classmethod
    def of(cls, job: Job) -> JobOut:
        return cls.model_validate(job)

    @classmethod
    def of_view(cls, view: JobView) -> JobOut:
        return cls.model_validate(view.job).model_copy(
            update={"target": view.target, "place": view.place}
        )


class JobsSummaryOut(ApiModel):
    """Jobs at a glance: running, queued, tally of the last 24 h."""

    running: int
    queued: int
    finished: dict[str, int] = Field(
        description="Jobs finished in the last 24 h, by status (succeeded, partial, failed, "
        "cancelled)."
    )
    analysis_s: float | None = Field(description="Median duration of the latest analyses.")
    eta_s: float | None = Field(
        description="Estimated time left for the running and queued analyses, at the pace of "
        "the latest ones."
    )
    parallel: int = Field(description="Analyses run at the same time.")

    @classmethod
    def of(cls, found: JobsSummary) -> JobsSummaryOut:
        return cls(
            running=found.running, queued=found.queued, finished=found.finished,
            analysis_s=found.analysis_s, eta_s=found.eta_s, parallel=found.parallel,
        )  # fmt: skip


class JobsCancelRequest(BaseModel):
    """Which active jobs to stop; nothing given: every one (« Stop all »)."""

    model_config = ConfigDict(extra="forbid")

    kinds: list[JobKind] = Field(default_factory=list, max_length=10)
    root_id: str | None = Field(default=None, max_length=32)
    video_ids: list[str] = Field(default_factory=list, max_length=500)


class JobsCancelOut(ApiModel):
    cancelled: int = Field(description="Queued jobs cancelled before they started.")
    stopping: int = Field(description="Running jobs asked to stop.")


# ---------------------------------------------------------------- videos
class VideoOut(ApiModel):
    id: str
    root_id: str
    filename: str
    rel_path: str
    status: VideoStatus
    size_bytes: int
    duration_s: float | None
    width: int | None
    height: int | None
    fps: float | None
    orientation: Orientation | None
    video_codec: str | None
    has_audio: bool | None
    is_hdr: bool | None
    captured_at: datetime | None
    captured_at_confidence: str | None
    captured_at_source: str | None
    capture_timezone: str | None
    capture_utc_offset_min: int | None
    latitude: float | None
    longitude: float | None
    camera_make: str | None
    camera_model: str | None
    title: str | None
    summary: str | None
    rating: int | None
    favorite: bool
    poster_url: str | None = None
    created_at: datetime
    last_analyzed_at: datetime | None
    place: str | None = Field(default=None, description="Locality of the shooting (context).")
    place_approximate: bool = False
    light_phase: str | None = Field(
        default=None, description="Light phase at shooting time (golden hour, blue hour…)."
    )
    weather_category: str | None = None
    temperature_c: float | None = None
    timeline_bins: list[str] = Field(
        default_factory=list, description="Resolve timelines of the library that use it."
    )

    @classmethod
    def of(
        cls,
        video: Video,
        brief: ContextBrief | None = None,
        timeline_bins: Sequence[str] = (),
        tr: Dictionary = AS_WRITTEN,
    ) -> VideoOut:
        update: dict[str, Any] = {
            "poster_url": media_url(video.poster_path),
            "timeline_bins": list(timeline_bins),
        }
        if brief is not None:
            update |= {
                "place": tr.maybe(brief.place),
                "place_approximate": brief.place_approximate,
                "light_phase": brief.light_phase,
                "weather_category": brief.weather_category,
                "temperature_c": brief.temperature_c,
            }
        return cls.model_validate(video).model_copy(update=update)


class StageRunOut(ApiModel):
    stage: str
    status: StageStatus
    error: str | None
    skip_reason: str | None
    summary: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None

    @classmethod
    def of(cls, run: StageRun) -> StageRunOut:
        return cls.model_validate(run)


class VideoDetailOut(VideoOut):
    path: str
    root_label: str
    encoder: str | None
    start_timecode: str | None
    audio_codec: str | None
    color_transfer: str | None
    altitude_m: float | None
    location_source: str | None
    color_profile: str | None
    hdr_format: str | None
    is_vfr: bool | None
    capture_fps: float | None
    camera_os: str | None
    user_notes: str | None
    stream_url: str
    playback_issue: str | None = Field(
        description="Why the browser does not play the original (None: it plays it)."
    )
    proxy_url: str | None = Field(
        description="H.264 viewing copy when the original cannot be played."
    )
    keyframe_count: int
    analysed_count: int
    stages: list[StageRunOut]
    missing_stages: list[str] = Field(
        description="Stages an ordinary analysis would run: never run, failed, to be redone."
    )
    outdated_stages: list[str] = Field(
        description="Stages kept but produced by an earlier version of the application."
    )
    resolve: list[ResolveLinkOut] = Field(
        description="DaVinci Resolve timelines that use it, and where (positions read at their "
        "last update; the ids remain valid)."
    )

    @classmethod
    def from_detail(cls, detail: VideoDetail, tr: Dictionary = AS_WRITTEN) -> VideoDetailOut:
        video = detail.video
        gaps = detail.gaps
        bins = [link.bin_id for link in detail.resolve]
        base = VideoOut.of(video, detail.brief, bins, tr).model_dump()
        return cls(
            **base,
            path=video.path,
            root_label=detail.root.label,
            encoder=video.encoder,
            start_timecode=video.start_timecode,
            audio_codec=video.audio_codec,
            color_transfer=video.color_transfer,
            altitude_m=video.altitude_m,
            location_source=video.location_source,
            color_profile=video.color_profile,
            hdr_format=video.hdr_format,
            is_vfr=video.is_vfr,
            capture_fps=video.capture_fps,
            camera_os=video.camera_os,
            user_notes=video.user_notes,
            stream_url=f"{API_PREFIX}/videos/{video.id}/stream",
            playback_issue=playback_issue(video.video_codec, detail.pix_fmt, video.audio_codec),
            proxy_url=f"{API_PREFIX}/videos/{video.id}/proxy" if detail.has_proxy else None,
            keyframe_count=detail.keyframe_count,
            analysed_count=detail.analysed_count,
            stages=[StageRunOut.of(run) for run in detail.stages],
            missing_stages=gaps.missing,
            outdated_stages=gaps.outdated,
            resolve=[ResolveLinkOut.of(link) for link in detail.resolve],
        )


class VideoPatch(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    rating: int | None = Field(default=None, ge=0, le=5)
    favorite: bool | None = None
    user_notes: str | None = Field(default=None, max_length=20000)
    transcript_mode: Literal["always", "never"] | None = Field(
        default=None, description="Transcription: always, never, or null (automatic)."
    )
    transcript_language: str | None = Field(
        default=None,
        max_length=16,
        description="Spoken language forced on Whisper (fr, fra, fr-FR…), or null (detection).",
    )

    @field_validator("transcript_language")
    @classmethod
    def _whisper_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = whisper_code(value)
        if code is None:
            raise ValueError("langue inconnue de Whisper")
        return code  # stored as the Whisper code


MODE_DESCRIPTION = (
    "complete (default): keeps every existing result and only does what is missing, has failed "
    "or whose video data has changed; update: also redoes what was produced with other "
    "settings, another model or an earlier version; full: redoes everything."
)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[str] | None = Field(
        default=None,
        description="Stages concerned (their dependencies are completed). Default: all.",
    )
    mode: AnalysisMode = Field(default=AnalysisMode.COMPLETE, description=MODE_DESCRIPTION)
    focus: str | None = Field(
        default=None,
        max_length=2000,
        description="Analysis focus for this job: if it changes, the images are described again.",
    )


class RootAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AnalysisMode = Field(default=AnalysisMode.COMPLETE, description=MODE_DESCRIPTION)


class RootAnalyzeOut(ApiModel):
    queued: int = Field(description="Number of videos queued for analysis.")


class FolderOut(ApiModel):
    name: str = Field(description="Folder name (that of the root folder for the root).")
    path: str = Field(description="Path relative to the root, separated by « / »; empty: root.")
    count: int = Field(description="Videos directly in this folder.")
    total: int = Field(description="Videos including those of the subfolders.")
    children: list[FolderOut] = Field(description="Subfolders, in alphabetical order.")


class RootFoldersOut(ApiModel):
    root_id: str
    label: str
    kind: RootKind
    tree: FolderOut

    @classmethod
    def of(cls, item: RootFolders) -> RootFoldersOut:
        def folder(node: Folder) -> FolderOut:
            return FolderOut(
                name=node.name, path=node.path, count=node.count, total=node.total,
                children=[folder(child) for child in node.children],
            )  # fmt: skip

        return cls(
            root_id=item.root.id, label=item.root.label, kind=item.root.kind,
            tree=folder(item.tree),
        )  # fmt: skip


class PickedFolderOut(ApiModel):
    path: str | None = Field(description="Chosen folder, or null if the window was closed.")


# ---------------------------------------------------------------- Resolve timelines
TimelineFileState = Literal[
    "in_library", "adding", "not_processed", "removed", "missing", "outside", "error"
]


class ResolveDatabaseOut(ApiModel):
    type: str = Field(description="Disk, PostgreSQL…")
    name: str


class ResolveProjectRefOut(ApiModel):
    id: str = Field(description="Unique id of the project in Resolve (persistent).")
    name: str


class ResolveTimelineRefOut(ApiModel):
    id: str = Field(description="Unique id of the timeline in Resolve.")
    name: str
    fps: float
    drop_frame: bool
    start_timecode: str
    duration_s: float

    @classmethod
    def of(cls, timeline: TimelineInfo) -> ResolveTimelineRefOut:
        return cls(
            id=timeline.id, name=timeline.name, fps=timeline.fps, drop_frame=timeline.drop_frame,
            start_timecode=timeline.start_timecode, duration_s=round(timeline.duration_s, 3),
        )  # fmt: skip


class ResolveUseOut(ApiModel):
    """One use of the file on a track of the timeline (positions read at the update: the
    timeline may have changed since)."""

    track_type: Literal["video", "audio"]
    track: int
    track_name: str | None
    track_enabled: bool
    enabled: bool
    nested_in: str | None = Field(description="Timeline or compound clip it was read from.")
    record_in_tc: str | None = Field(description="In point in the timeline (timecode).")
    record_out_tc: str | None = Field(description="Out point (excluded), as Resolve shows it.")
    record_in_s: float = Field(description="Seconds from the start of the timeline.")
    record_out_s: float
    source_in_s: float = Field(description="Seconds in the file (its timecode removed).")
    source_out_s: float
    media_pool_item_id: str | None
    timeline_item_id: str | None

    @classmethod
    def of(cls, use: ClipUse, timeline: TimelineInfo) -> ResolveUseOut:
        record_in, record_out = record_seconds(use, timeline)
        tcs = record_timecodes(use, timeline)
        return cls(
            track_type="audio" if use.track_type == "audio" else "video", track=use.track,
            track_name=use.track_name, track_enabled=use.track_enabled, enabled=use.enabled,
            nested_in=use.nested_in, record_in_tc=tcs[0] if tcs else None,
            record_out_tc=tcs[1] if tcs else None, record_in_s=round(record_in, 3),
            record_out_s=round(record_out, 3), source_in_s=round(use.source_start_s, 3),
            source_out_s=round(use.source_end_s, 3), media_pool_item_id=use.media_pool_item_id,
            timeline_item_id=use.timeline_item_id,
        )  # fmt: skip


class ResolveLinkOut(ApiModel):
    """A DaVinci Resolve timeline that uses the video, as read at its last update
    (``synced_at``)."""

    bin_id: str
    bin_label: str
    database: ResolveDatabaseOut
    project: ResolveProjectRefOut
    timeline: ResolveTimelineRefOut
    synced_at: datetime
    uses: list[ResolveUseOut]

    @classmethod
    def of(cls, link: ResolveLink) -> ResolveLinkOut:
        return cls(
            bin_id=link.bin_id,
            bin_label=link.bin_label,
            database=ResolveDatabaseOut(type=link.database.type, name=link.database.name),
            project=ResolveProjectRefOut(id=link.project.id, name=link.project.name),
            timeline=ResolveTimelineRefOut.of(link.timeline),
            synced_at=link.synced_at,
            uses=[ResolveUseOut.of(use, link.timeline) for use in link.uses],
        )


class ResolveTimelineOut(ApiModel):
    id: str
    name: str
    fps: float
    drop_frame: bool
    start_timecode: str
    duration_s: float
    width: int | None
    height: int | None
    video_tracks: int
    audio_tracks: int
    video_clips: int | None = Field(description="Shots of the video tracks (null: not counted).")
    is_current: bool
    bin_id: str | None = Field(description="Its timeline in the library, if it is there.")
    bin_label: str | None
    synced_at: datetime | None
    changed_since_sync: bool | None = Field(
        description="Name, duration or number of shots different since the last update."
    )

    @classmethod
    def of(cls, view: TimelineView) -> ResolveTimelineOut:
        info, found = view.info, view.bin
        return cls(
            id=info.id, name=info.name, fps=info.fps, drop_frame=info.drop_frame,
            start_timecode=info.start_timecode, duration_s=round(info.duration_s, 3),
            width=info.width, height=info.height, video_tracks=info.video_tracks,
            audio_tracks=info.audio_tracks, video_clips=info.video_clips,
            is_current=info.is_current, bin_id=found.id if found else None,
            bin_label=found.label if found else None,
            synced_at=found.synced_at if found else None,
            changed_since_sync=view.changed_since_sync,
        )  # fmt: skip


class ResolveProjectOut(ApiModel):
    product: str
    version: str
    studio: bool
    database: ResolveDatabaseOut
    project: ResolveProjectRefOut
    current_timeline_id: str | None
    timelines: list[ResolveTimelineOut] = Field(description="The current one first, then by name.")

    @classmethod
    def of(cls, view: ProjectView) -> ResolveProjectOut:
        info = view.info
        return cls(
            product=info.product, version=info.version, studio=info.studio,
            database=ResolveDatabaseOut(type=info.database.type, name=info.database.name),
            project=ResolveProjectRefOut(id=info.project.id, name=info.project.name),
            current_timeline_id=info.current_timeline_id,
            timelines=[ResolveTimelineOut.of(timeline) for timeline in view.timelines],
        )  # fmt: skip


class SkippedItemsOut(ApiModel):
    """Timeline items that are not analysable videos (names: 20 at most)."""

    graphics: int = Field(description="Titles, generators, Fusion compositions.")
    graphics_names: list[str]
    containers: int = Field(description="Compound or multicam clips not read inside.")
    container_names: list[str]
    not_video: int = Field(description="Audio files or still images.")
    not_video_names: list[str]
    unsupported: int = Field(description="Unsupported camera formats (.braw…).")
    unsupported_names: list[str]
    elsewhere: int = Field(description="Paths from another system (macOS…).")
    elsewhere_names: list[str]

    @classmethod
    def of(cls, skipped: SkippedItems) -> SkippedItemsOut:
        return cls.model_validate(skipped_to_json(skipped))


class TimelinePreviewOut(ApiModel):
    """What adding the timeline would do (nothing is written)."""

    database: ResolveDatabaseOut
    project: ResolveProjectRefOut
    timeline: ResolveTimelineRefOut
    studio: bool
    files: int = Field(description="Distinct video files of the timeline.")
    in_library: int = Field(description="Already in the library.")
    other_path: int = Field(
        description="A video of the same name and size elsewhere in the library (same file "
        "under another path, checked by its content when added)."
    )
    in_folder: int = Field(description="In a library folder, to be added.")
    new_folder: int = Field(description="In no folder: only these videos will be added.")
    missing: int = Field(description="Files not found.")
    unknown: int = Field(description="On an unreachable network share.")
    disabled_only: int = Field(description="Used only in disabled shots.")
    to_analyze: int = Field(description="Analyses requested if automatic analysis is on.")
    new_folders: list[str] = Field(
        description="Folders of the videos added on their own (20 at most)."
    )
    skipped: SkippedItemsOut
    read_errors: int = Field(description="Items Resolve could not describe.")
    bin_id: str | None = Field(description="Its timeline in the library, if it is already there.")
    bin_label: str | None
    snapshot_id: str | None = Field(
        description="Reading kept for two minutes: send it back when adding so Resolve is not "
        "read again."
    )

    @classmethod
    def of(cls, preview: TimelinePreview) -> TimelinePreviewOut:
        content = preview.content
        return cls(
            database=ResolveDatabaseOut(type=content.database.type, name=content.database.name),
            project=ResolveProjectRefOut(id=content.project.id, name=content.project.name),
            timeline=ResolveTimelineRefOut.of(content.timeline), studio=content.studio,
            files=preview.files, in_library=preview.in_library, other_path=preview.other_path,
            in_folder=preview.in_folder, new_folder=preview.new_folder, missing=preview.missing,
            unknown=preview.unknown, disabled_only=preview.disabled_only,
            to_analyze=preview.to_analyze, new_folders=list(preview.new_folders),
            skipped=SkippedItemsOut.of(preview.skipped), read_errors=content.errors,
            bin_id=preview.bin_id, bin_label=preview.bin_label, snapshot_id=preview.snapshot_id,
        )  # fmt: skip


class TimelineImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(max_length=64, description="The open project, as listed.")
    timeline_id: str = Field(max_length=64, pattern=RESOLVE_ID_PATTERN)
    snapshot_id: str | None = Field(
        default=None,
        max_length=64,
        description="That of the preview, so Resolve is not read again.",
    )
    label: str | None = Field(default=None, max_length=200, description="Default: its name.")
    auto_analyze: bool = Field(default=True, description="Analyse what is missing.")


class TimelineStatesOut(ApiModel):
    """Files of the timeline by state."""

    in_library: int = 0
    adding: int = Field(default=0, description="Being added.")
    not_processed: int = Field(default=0, description="Not processed: run the update again.")
    removed: int = Field(default=0, description="Removed from the library since.")
    missing: int = 0
    outside: int = Field(default=0, description="Outside the library folders.")
    error: int = 0


class TimelineSyncJobOut(ApiModel):
    id: str
    status: JobStatus
    progress: float
    message: str | None


class TimelineReportOut(ApiModel):
    skipped: SkippedItemsOut
    errors: int = Field(description="Items Resolve could not describe.")

    @classmethod
    def of(cls, report: dict[str, Any]) -> TimelineReportOut:
        skipped = _parse_stored(SkippedItemsOut, report.get("skipped"))
        empty = SkippedItemsOut.of(SkippedItems())
        return cls(skipped=skipped or empty, errors=int(report.get("errors") or 0))


class TimelineBinOut(ApiModel):
    """A DaVinci Resolve timeline in the library: a view on its videos (removing it forgets
    no video or analysis)."""

    id: str
    label: str
    database: ResolveDatabaseOut
    project: ResolveProjectRefOut
    timeline: ResolveTimelineRefOut
    auto_analyze: bool
    items: int = Field(description="Video files of the timeline.")
    videos: int = Field(description="Library videos it uses.")
    states: TimelineStatesOut
    report: TimelineReportOut
    sync_job: TimelineSyncJobOut | None = Field(description="The last update.")
    created_at: datetime
    synced_at: datetime = Field(description="Reading of Resolve (positions read then).")

    @classmethod
    def of(cls, stats: BinStats) -> TimelineBinOut:
        found = stats.bin
        database, project, timeline = identity_parts(found.resolve)
        job = stats.sync_job
        return cls(
            id=found.id, label=found.label,
            database=ResolveDatabaseOut(type=database.type, name=database.name),
            project=ResolveProjectRefOut(id=project.id, name=project.name),
            timeline=ResolveTimelineRefOut.of(timeline), auto_analyze=found.auto_analyze,
            items=stats.items, videos=stats.videos,
            states=TimelineStatesOut.model_validate(stats.states),
            report=TimelineReportOut.of(found.report or {}),
            sync_job=TimelineSyncJobOut.model_validate(job) if job else None,
            created_at=found.created_at, synced_at=found.synced_at,
        )  # fmt: skip


class TimelineImportOut(ApiModel):
    bin: TimelineBinOut
    job: JobOut = Field(description="The update that examines the files and adds them.")
    created: bool = Field(description="New in the library (otherwise updated).")
    preview: TimelinePreviewOut

    @classmethod
    def of(cls, result: BinImport) -> TimelineImportOut:
        return cls(
            bin=TimelineBinOut.of(result.stats),
            job=JobOut.of(result.job),
            created=result.created,
            preview=TimelinePreviewOut.of(result.preview),
        )


# ---------------------------------------------------------------- « Create a timeline »
PLAN_FILES = 50  # file names the preview of a timeline lists
TimelineRateName = Literal[
    "23.976", "24", "25", "29.97", "30", "47.952", "48", "50", "59.94", "60", "100", "119.88",
    "120",
]  # fmt: skip


class TimelineBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_LIBRARY_VIDEOS,
        description="Chosen videos (in the wanted order for « selection »).",
    )
    order: TimelineOrder = Field(
        default=TimelineOrder.CAPTURE,
        description="capture: shooting date; name: file name; selection: this order.",
    )
    name: str | None = Field(
        default=None, max_length=200, description="Default: the shooting days."
    )
    frame_rate: TimelineRateName | None = Field(
        default=None, description="Default: the most frequent among the videos."
    )
    width: int | None = Field(default=None, ge=16, le=16384, description="With ``height``.")
    height: int | None = Field(default=None, ge=16, le=16384)
    transcript: bool = Field(default=True, description="The transcript as subtitles (first track).")
    suggestions: bool = Field(
        default=True,
        description=(
            "The suggestions as duration markers: highlights (green), establishing shots "
            "(cyan), cutaways (yellow), to avoid (red)."
        ),
    )
    chapters: bool = Field(
        default=True,
        description="A marker at the start of each chapter (synthesis), its title as the name.",
    )
    shots: bool = Field(
        default=False, description="The description of each shot as subtitles (its own track)."
    )

    @model_validator(mode="after")
    def _both_sides(self) -> TimelineBuildRequest:
        if (self.width is None) != (self.height is None):
            raise ValueError("width et height vont ensemble")
        return self

    def plan(self, c: AppContainer, language: str | None = None) -> TimelinePlan:
        """The timeline asked for, its texts in ``language`` (the interface's)."""
        size = (self.width, self.height) if self.width and self.height else None
        parts = TimelineParts(transcript=self.transcript, suggestions=self.suggestions,
                              chapters=self.chapters, shots=self.shots)  # fmt: skip
        return plan_timeline(c, self.video_ids, order=self.order, name=self.name,
                             rate=self.frame_rate, size=size, parts=parts,
                             language=language)  # fmt: skip


class TimelineRateOut(ApiModel):
    frame_rate: str = Field(description="As Resolve writes it: « 29.97 ».")
    fps: float
    videos: int


class TimelineSizeOut(ApiModel):
    width: int
    height: int
    videos: int


class TimelineSkippedOut(ApiModel):
    video_id: str
    filename: str
    reason: SkipReason = Field(
        description="offline: file not found; not_examined: duration and frame rate not known "
        "yet; no_folder_pair: folder the Resolve computer does not see."
    )

    @classmethod
    def of(cls, skipped: SkippedVideo) -> TimelineSkippedOut:
        return cls(video_id=skipped.video_id, filename=skipped.filename, reason=skipped.reason)


class SubtitleFileOut(ApiModel):
    """A subtitle file of a video, written next to it, or, when DaVinci Resolve runs on another
    computer, of a timeline track, written next to its first video (``<name>_TIMELINE_EN.srt``)."""

    video_id: str
    part: str = Field(
        description="transcript: what is said (<name>.srt); shots: what each shot shows "
        "(<name>_SHOTS.srt)."
    )
    file: str = Field(description="File name.")
    folder: str = Field(description="Its folder (the video's), on this computer.")
    status: WriteStatus = Field(
        description="written: written (or already up to date); conflict: a file of that name, "
        "which the application did not write or which has changed since, left as it is; "
        "failed: could not be written (detail)."
    )
    detail: str | None = None

    @classmethod
    def of(cls, written: WrittenSubtitles) -> SubtitleFileOut:
        return cls(
            video_id=written.video_id, part=written.part, file=written.path.name,
            folder=str(written.path.parent), status=written.status, detail=written.detail,
        )  # fmt: skip


class TimelineSuggestionsOut(ApiModel):
    highlights: int = Field(description="Highlights (green).")
    establishing: int = Field(description="Establishing shots (cyan).")
    b_roll: int = Field(description="Cutaways (yellow).")
    avoid: int = Field(description="Shots to avoid (red).")

    @classmethod
    def of(cls, counts: Mapping[str, int]) -> TimelineSuggestionsOut:
        return cls(
            highlights=counts.get("highlight", 0),
            establishing=counts.get("establishing", 0),
            b_roll=counts.get("b_roll", 0),
            avoid=counts.get("avoid", 0),
        )


class TimelinePlanOut(ApiModel):
    """The timeline the chosen videos would give (nothing is written)."""

    name: str
    suggested_name: str
    file_name: str = Field(description="Name of the file to download (ZIP).")
    frame_rate: str
    fps: float
    width: int
    height: int
    suggested_frame_rate: str = Field(description="The most frequent among the videos.")
    suggested_width: int
    suggested_height: int
    videos: int = Field(description="Videos placed, whole, end to end.")
    files: list[str] = Field(description=f"Their names in order ({PLAN_FILES} at most).")
    duration_s: float
    rates: list[TimelineRateOut] = Field(
        description="Frame rates of the videos, the most frequent first."
    )
    sizes: list[TimelineSizeOut] = Field(description="Frame sizes, the most frequent first.")
    skipped: list[TimelineSkippedOut]
    resolve_host: str | None = Field(
        description="Resolve on this other computer: paths as it sees them."
    )
    chapters: int = Field(description="Chapter starts the timeline can mark.")
    chaptered: int = Field(description="Videos that have chapters (synthesis).")
    suggestions: TimelineSuggestionsOut = Field(
        description="Suggestions the timeline can mark, by kind."
    )
    speech_subtitles: int = Field(description="Subtitles of the transcript.")
    shot_subtitles: int = Field(description="Subtitles of the shot descriptions.")

    @classmethod
    def of(cls, plan: TimelinePlan) -> TimelinePlanOut:
        fmt, suggested = plan.format, plan.suggested
        return cls(
            name=plan.name, suggested_name=plan.suggested_name, file_name=file_name(plan.name),
            frame_rate=fmt.rate.name, fps=fmt.rate.fps, width=fmt.width, height=fmt.height,
            suggested_frame_rate=suggested.rate.name, suggested_width=suggested.width,
            suggested_height=suggested.height, videos=len(plan.videos),
            files=[video.filename for video in plan.videos[:PLAN_FILES]],
            duration_s=plan.duration_s,
            rates=[TimelineRateOut(frame_rate=rate.name, fps=rate.fps, videos=count)
                   for rate, count in plan.rates],
            sizes=[TimelineSizeOut(width=width, height=height, videos=count)
                   for (width, height), count in plan.sizes],
            skipped=[TimelineSkippedOut.of(skipped) for skipped in plan.skipped],
            resolve_host=plan.resolve_host,
            chapters=plan.chapter_count,
            chaptered=plan.chaptered,
            suggestions=TimelineSuggestionsOut.of(plan.suggestion_counts),
            speech_subtitles=plan.speech_cues,
            shot_subtitles=plan.shot_cues,
        )  # fmt: skip


class ResolveTimelineBuiltOut(ApiModel):
    """The timeline created in the project open in DaVinci Resolve, now the current one."""

    project: ResolveProjectRefOut
    timeline_id: str
    timeline_name: str = Field(description="« (2) » added when the name was taken.")
    fps: float | None = Field(description="That of the project when Resolve refused another one.")
    width: int | None
    height: int | None
    clips: int = Field(description="Clips placed on track V1.")
    imported: int = Field(description="Files imported into the ``folder`` bin.")
    reused: int = Field(description="Files already in the media pool, used as they are.")
    missing: list[str] = Field(description="Files Resolve could not open.")
    folder: str
    markers: int = Field(description="Markers placed on the clips (chapters, suggestions).")
    markers_missed: int = Field(
        description="Markers refused (beyond the end of the clip, no free frame next to it)."
    )
    subtitles: list[str] = Field(
        description=(
            "Subtitle clips imported into the ``folder`` bin: those of the laid tracks or, "
            "when Resolve runs on another computer and no folder of the videos could take the "
            "tracks, the files of the videos (``subtitle_files``), each to be dragged to the "
            "start of its video."
        )
    )
    subtitles_laid: list[str] = Field(
        description="Subtitle tracks laid on the timeline (« Transcript », « Shots »)."
    )
    subtitle_files: list[SubtitleFileOut] = Field(
        description="Subtitle files of the videos, written next to them, and, when Resolve runs "
        "on another computer, those of the timeline's tracks."
    )
    skipped: list[TimelineSkippedOut] = Field(description="Videos left out before Resolve.")

    @classmethod
    def of(cls, result: ResolveBuild, plan: TimelinePlan) -> ResolveTimelineBuiltOut:
        built = result.timeline
        return cls(
            project=ResolveProjectRefOut(id=built.project.id, name=built.project.name),
            timeline_id=built.timeline_id, timeline_name=built.timeline_name, fps=built.fps,
            width=built.width, height=built.height, clips=built.clips, imported=built.imported,
            reused=built.reused, missing=list(built.missing), folder=built.folder,
            markers=built.markers, markers_missed=built.markers_missed,
            subtitles=list(built.subtitles), subtitles_laid=list(built.subtitles_laid),
            subtitle_files=[SubtitleFileOut.of(written) for written in result.subtitle_files],
            skipped=[TimelineSkippedOut.of(skipped) for skipped in plan.skipped],
        )  # fmt: skip


class TimelineItemOut(ApiModel):
    position: int = Field(description="Order of first appearance in the timeline.")
    path: str
    state: TimelineFileState
    note: str | None
    video_id: str | None

    @classmethod
    def of(cls, item: ItemView) -> TimelineItemOut:
        return cls.model_validate(item)


class TimelineBinPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=200)
    auto_analyze: bool | None = None


class BatchAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_ids: list[str] = Field(
        min_length=1, max_length=1000, description="Videos to analyse (ids)."
    )
    stages: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Stages to run (their dependencies are completed). Default: all.",
    )
    mode: AnalysisMode = Field(default=AnalysisMode.COMPLETE, description=MODE_DESCRIPTION)


class BatchAnalyzeOut(ApiModel):
    queued: int = Field(description="Videos queued for analysis.")
    up_to_date: int = Field(
        description="Videos left out: nothing to complete for these stages (complete mode)."
    )
    offline: int = Field(description="Offline videos (file inaccessible), not analysed.")
    unknown: list[str] = Field(
        description="Ids of videos not found (removed from the library since)."
    )


class ExportSidecarsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_ids: list[str] = Field(
        min_length=1,
        max_length=1000,
        description="Videos whose analysis file to write now (ids).",
    )


class SidecarResultOut(ApiModel):
    """What became of a video's analysis file."""

    video_id: str
    status: SidecarStatus = Field(
        description="written: written; conflict: a file of the same name, which the application "
        "did not write, is left as it is; offline: video inaccessible; not_analyzed: "
        "nothing analysed to write; failed: could not be written (read-only folder, access "
        "denied, disk full…); unknown: video removed from the library."
    )
    file: str | None = Field(
        description="Name of the file, next to the video: the one left as it is, or the first "
        "one written."
    )
    path: str | None = Field(description="Full path of this file.")
    files: list[str] = Field(
        default_factory=list,
        description="The files written, one per language (<name>_FR.txt, <name>_EN.txt).",
    )
    detail: str | None = Field(description="Why it was not written.")

    @classmethod
    def of(cls, result: SidecarResult) -> SidecarResultOut:
        return cls(
            video_id=result.video_id,
            status=result.status,
            file=result.path.name if result.path is not None else None,
            path=str(result.path) if result.path is not None else None,
            files=[path.name for path in result.paths],
            detail=result.detail,
        )


class ExportSidecarsOut(ApiModel):
    results: list[SidecarResultOut] = Field(description="One row per requested video.")


class StageOut(ApiModel):
    name: str = Field(description="Stage id.")
    family: StageFamily = Field(description="What the stage looks at (grouping on screen).")
    requires: list[str] = Field(description="Stages it needs: completed first if they are missing.")
    after: list[str] = Field(description="Stages it follows when both are requested.")
    resource: Resource = Field(description="What it occupies: CPU, LM Studio, network…")
    optional: bool = Field(description="A failure of this stage does not fail the analysis.")

    @classmethod
    def of(cls, stage: Stage) -> StageOut:
        return cls(
            name=stage.name,
            family=stage.family,
            requires=list(stage.requires),
            after=list(stage.after),
            resource=stage.resource,
            optional=stage.optional,
        )


class FrameAnalysisOut(ApiModel):
    model: str
    prompt_version: str
    language: str
    focus: str | None
    created_at: datetime
    data: FrameAnalysis

    @classmethod
    def of(cls, row: FrameAnalysisRow, tr: Dictionary = AS_WRITTEN) -> FrameAnalysisOut:
        """The texts in the language of ``tr``."""
        out = cls.model_validate(row)
        if tr.language is None:
            return out
        data = translated(out.data.model_dump(mode="json"), FRAME_TEXTS, tr)
        try:
            return out.model_copy(update={"data": FrameAnalysis.model_validate(data)})
        except ValidationError:  # a translation the schema refuses: the texts as written
            return out


class ColorShareOut(ApiModel):
    hex: str = Field(pattern=r"^#[0-9a-f]{6}$")
    share: float = Field(ge=0, le=1)


class KeyframeMetricsOut(ApiModel):
    """Measured on the extracted keyframe by the ``technical`` stage (luma and ratios in 0–1)."""

    luma_mean: float
    luma_median: float
    contrast: float
    clipped_shadows: float
    clipped_highlights: float
    saturation: float
    sharpness: float
    cct_k: float | None = None
    cct_label: str | None = None
    colors: list[ColorShareOut] = Field(default_factory=list)


class KeyframeOut(ApiModel):
    id: str
    idx: int
    t_s: float
    width: int
    height: int
    selection_reason: str
    sharpness: float | None
    shot_id: str | None
    metrics: KeyframeMetricsOut | None
    image_url: str
    thumb_url: str
    analysis: FrameAnalysisOut | None

    @classmethod
    def of(cls, view: KeyframeView, tr: Dictionary = AS_WRITTEN) -> KeyframeOut:
        kf: Keyframe = view.keyframe
        return cls(
            id=kf.id,
            idx=kf.idx,
            t_s=kf.t_s,
            width=kf.width,
            height=kf.height,
            selection_reason=kf.selection_reason,
            sharpness=kf.sharpness,
            shot_id=kf.shot_id,
            metrics=_parse_stored(KeyframeMetricsOut, kf.metrics),
            image_url=media_url(kf.image_path) or "",
            thumb_url=media_url(kf.thumb_path) or "",
            analysis=FrameAnalysisOut.of(view.analysis, tr) if view.analysis else None,
        )


class ShotMetricsOut(ApiModel):
    """Averages over the shot from the low-resolution analysis pass (0–1 except sharpness)."""

    luma: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    sharpness: float | None = None
    black_ratio: float = 0.0
    frozen_ratio: float = 0.0
    cct_k: float | None = None


class StoryFrameOut(ApiModel):
    t_s: float = Field(description="Time of the image in the video (s).")
    keyframe_id: str | None = Field(description="Null: image extracted for the story.")
    thumb_url: str
    note: str | None = Field(description="What the model sees changing in this image.")


class ShotStoryOut(ApiModel):
    """What happens in a shot (or a part of about 20 s of a long shot), told by the vision
    model from its images in order. Generated text: may be wrong."""

    part: int
    parts: int
    start_s: float
    end_s: float
    summary: str
    main_action: str = Field(description="Empty if nothing happens.")
    possible_cut: bool = Field(
        description="The images seem to show different subjects: a possible cut."
    )
    frames: list[StoryFrameOut]
    model: str

    @classmethod
    def of(cls, row: ShotStory, tr: Dictionary = AS_WRITTEN) -> ShotStoryOut:
        story = translated(row.story, STORY_TEXTS, tr)
        notes = {round(float(n["t_s"]), 3): str(n["what"]) for n in story.get("notes", [])}
        return cls(
            part=row.part,
            parts=row.parts,
            start_s=row.start_s,
            end_s=row.end_s,
            summary=str(story.get("summary", "")),
            main_action=str(story.get("main_action", "")),
            possible_cut=bool(story.get("possible_cut")),
            frames=[
                StoryFrameOut(
                    t_s=t,
                    keyframe_id=kf,
                    thumb_url=media_url(path) or "",
                    note=notes.get(round(float(t), 3)),
                )
                for t, kf, path in zip(
                    row.frame_times, row.keyframe_ids, row.frame_paths, strict=True
                )
            ],
            model=row.model,
        )


class ShotOut(ApiModel):
    id: str
    idx: int
    start_s: float
    end_s: float
    boundary: str = Field(description="start, cut or fade.")
    motion: str = Field(
        description="static, pan_left, pan_right, tilt_up, tilt_down, zoom_in, zoom_out, "
        "handheld or moving (a weak zoom, movement or shake counts as static)."
    )
    motion_score: float
    stability: float = Field(ge=0, le=1)
    metrics: ShotMetricsOut
    stories: list[ShotStoryOut] = Field(
        default_factory=list, description="What happens (vision_shots stage)."
    )

    @classmethod
    def of(
        cls, shot: Shot, stories: Sequence[ShotStory] = (), tr: Dictionary = AS_WRITTEN
    ) -> ShotOut:
        return cls(
            id=shot.id,
            idx=shot.idx,
            start_s=shot.start_s,
            end_s=shot.end_s,
            boundary=shot.boundary,
            motion=shown_motion(shot.motion, shot.motion_score),
            motion_score=shot.motion_score,
            stability=shot.stability,
            metrics=_parse_stored(ShotMetricsOut, shot.metrics) or ShotMetricsOut(),
            stories=[ShotStoryOut.of(story, tr) for story in stories],
        )


class ClipOut(ApiModel):
    """Where to cut: the picture stays within its shot; the sound may start before or end after
    (J-cut, L-cut) so as not to cut a sentence. Seconds of the source file."""

    picture_in_s: float
    picture_out_s: float
    sound_in_s: float | None = Field(description="Null: the sound follows the picture.")
    sound_out_s: float | None
    notes: list[str]

    @classmethod
    def of(cls, clip: Clip) -> ClipOut:
        return cls(
            picture_in_s=round(clip.picture_in, 3),
            picture_out_s=round(clip.picture_out, 3),
            sound_in_s=None if clip.sound_in is None else round(clip.sound_in, 3),
            sound_out_s=None if clip.sound_out is None else round(clip.sound_out, 3),
            notes=list(clip.notes),
        )


class SynthesisChapterOut(ApiModel):
    index: int
    start_s: float
    end_s: float
    title: str
    summary: str


class SynthesisHighlightOut(ApiModel):
    rank: int
    chapter: int
    clip: ClipOut
    keyframe_id: str | None
    thumb_url: str | None
    reason: str = Field(
        description="Written by the model for this moment chosen by the application."
    )
    criteria: list[str] = Field(description="Why the application chose it (indicative).")


class SynthesisSuggestionOut(ApiModel):
    role: Literal["establishing", "b_roll", "avoid"]
    shots: list[int] = Field(description="Indices of the shots (0 = first).")
    clip: ClipOut
    usability: int
    reasons: list[str]


class ShotUsabilityOut(ApiModel):
    shot_idx: int
    score: int = Field(ge=0, le=100, description="Technical usability, indicative.")
    reasons: list[str]


class WeatherConsensusOut(ApiModel):
    category: str | None
    agreement: Literal["agree", "close", "disagree", "api_only", "visual_only", "unknown"]
    confidence: Literal["high", "medium", "low"]
    line: str


class SynthesisTagOut(ApiModel):
    label: str
    source: Literal["llm", "frames", "sounds"]


class SynthesisOut(ApiModel):
    """Title, summary, chapters and highlights of a video. The texts are written by the loaded
    model (they may contain errors); chapters, moments, in/out points and usability are
    computed by the application."""

    status: Literal["ready", "not_run", "skipped", "failed", "running"]
    note: str | None
    stale: bool = Field(description="Written before newer analyses: to be regenerated.")
    title: str | None
    logline: str | None
    summary: str | None
    chapters: list[SynthesisChapterOut]
    highlights: list[SynthesisHighlightOut]
    suggestions: list[SynthesisSuggestionOut]
    usability: list[ShotUsabilityOut]
    weather: WeatherConsensusOut | None
    tags: list[SynthesisTagOut]
    model: str | None
    created_at: datetime | None
    strategy: str | None
    proofread: bool

    @classmethod
    def of(cls, view: SynthesisView) -> SynthesisOut:
        row = view.row
        data = view.data  # its texts in the language asked for
        status = (
            "ready" if row is not None and view.state.status == "not_run" else view.state.status
        )
        return cls(
            status=status,  # type: ignore[arg-type]
            note=view.state.note,
            stale=view.stale,
            title=data.get("title") or None,
            logline=data.get("logline") or None,
            summary=data.get("summary") or None,
            chapters=[
                SynthesisChapterOut(
                    index=c.index, start_s=round(c.start_s, 3), end_s=round(c.end_s, 3),
                    title=c.title, summary=c.summary,
                )
                for c in view.chapters
            ],
            highlights=[
                SynthesisHighlightOut(
                    rank=h.rank, chapter=h.chapter, clip=ClipOut.of(h.clip),
                    keyframe_id=h.keyframe_id,
                    thumb_url=media_url(view.thumbs.get(h.keyframe_id or "")),
                    reason=h.reason, criteria=list(h.criteria),
                )
                for h in view.highlights
            ],
            suggestions=[
                SynthesisSuggestionOut(
                    role=s.role,  # type: ignore[arg-type]
                    shots=list(s.shots), clip=ClipOut.of(s.clip),
                    usability=s.usability.score, reasons=list(s.usability.reasons),
                )
                for s in view.suggestions
            ],
            usability=[
                ShotUsabilityOut(shot_idx=idx, score=u.score, reasons=list(u.reasons))
                for idx, u in sorted(view.usability.items())
            ],
            weather=WeatherConsensusOut(
                category=view.weather.category, agreement=view.weather.agreement,
                confidence=view.weather.confidence, line=view.weather.line,
            )
            if view.weather
            else None,
            tags=[
                SynthesisTagOut(label=str(t["label"]), source=t["source"])
                for t in view.tags
                if t.get("source") in {"llm", "frames", "sounds"}
            ],
            model=row.model if row else None,
            created_at=row.updated_at if row else None,
            strategy=row.strategy if row else None,
            proofread=bool(row.proofread) if row else False,
        )  # fmt: skip


# ---------------------------------------------------------------- exports, Resolve
class ExportOptionOut(ApiModel):
    """An export format of the video, and why it is not possible yet."""

    format: ExportFormat
    filename: str = Field(description="Name of the downloaded file.")
    available: bool
    reason: str | None = Field(description="Why this format is not available.")
    url: str = Field(description="Download (GET).")

    @classmethod
    def of(cls, video_id: str, option: ExportOption) -> ExportOptionOut:
        return cls(
            format=option.format,
            filename=option.filename,
            available=option.available,
            reason=option.reason,
            url=f"{API_PREFIX}/videos/{quote(video_id)}/exports/{option.format.value}"
            f"?lang={option.language}",
        )


class ExportsOut(ApiModel):
    video_id: str
    formats: list[ExportOptionOut]


class ExportCsvRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_LIBRARY_VIDEOS,
        description="Videos to list (one row each, in this order).",
    )


class ResolveScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_ids: list[str] = Field(min_length=1, max_length=RESOLVE_MAX_VIDEOS)
    shots: bool = Field(default=False, description="A marker at the start of each shot.")
    speech: bool = Field(default=False, description="A marker per speech passage.")
    metadata: bool = Field(
        default=True, description="Keywords, description and comments of the clips."
    )


class ClipQueryIn(BaseModel):
    """A clip as Resolve gives it: its file, and the range of the source used."""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(min_length=1, max_length=4096)
    clip_uid: str | None = Field(
        default=None,
        max_length=64,
        pattern=RESOLVE_ID_PATTERN,
        description="Id of the Media Pool clip (GetMediaPoolItem().GetUniqueId()): finds the "
        "video even if its path has changed, through the library's timelines.",
    )
    source_start_s: float | None = Field(
        default=None, ge=0, description="GetLeftOffset() ÷ FPS of the clip (s)."
    )
    source_end_s: float | None = Field(
        default=None, ge=0, description="source_start_s + GetDuration() ÷ fps of the timeline (s)."
    )
    source_start_frame: int | None = Field(
        default=None, ge=0, description="First frame used (0 = first of the file)."
    )
    source_end_frame: int | None = Field(default=None, ge=0)
    fps: float | None = Field(
        default=None, gt=0, le=1000, description="Frame rate of the clip in Resolve (FPS)."
    )


class VideosForgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_ids: list[str] = Field(min_length=1, max_length=500)


class VideosForgetOut(ApiModel):
    forgotten: int = Field(description="Offline videos removed from the library.")
    left: int = Field(description="Left: not offline (their file is there), or being analysed.")


class RelinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_ids: list[str] = Field(min_length=1, max_length=500)
    folder: str = Field(
        min_length=1, max_length=4096, description="Folder to search (subfolders included)."
    )


class MatchClipsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[str | ClipQueryIn] = Field(min_length=1, max_length=MATCH_MAX_ITEMS)


class CutPointsOut(ApiModel):
    """Safe cut points of a range (seconds of the source file)."""

    requested_in_s: float
    requested_out_s: float
    clip: ClipOut
    shots: list[int] = Field(description="Shots shown (0 = first).")
    cuts: list[float] = Field(description="Cuts between two shots within the range.")
    notes: list[str]

    @classmethod
    def of(cls, points: CutPoints) -> CutPointsOut:
        return cls(
            requested_in_s=round(points.requested_in, 3),
            requested_out_s=round(points.requested_out, 3),
            clip=ClipOut.of(points.clip),
            shots=list(points.shots),
            cuts=[round(t, 3) for t in points.cuts],
            notes=list(points.notes),
        )


class RangeShotOut(ApiModel):
    idx: int
    start_s: float
    end_s: float
    motion: str
    usability: int | None
    roles: list[str]
    text: str = Field(description="What the shot shows (generated: data).")


class RangeSubjectOut(ApiModel):
    t_s: float
    keyframe_idx: int
    label: str
    category: str
    box: list[float] = Field(description="[x1, y1, x2, y2] 0–1 in the displayed image.")


class ClipRangeOut(ApiModel):
    start_s: float
    end_s: float
    fps: float | None
    shots: list[RangeShotOut]
    speech: str = Field(description="What is said within the range (untrusted data).")
    subjects: list[RangeSubjectOut] = Field(description="Main subject at the keyframes.")
    chapters: list[SynthesisChapterOut]
    highlights: list[ClipOut] = Field(description="Highlights that overlap the range.")
    cut: CutPointsOut | None
    note: str | None

    @classmethod
    def of(cls, view: RangeView) -> ClipRangeOut:
        return cls(
            start_s=round(view.start_s, 3),
            end_s=round(view.end_s, 3),
            fps=view.fps,
            shots=[
                RangeShotOut(
                    idx=s.idx, start_s=round(s.start_s, 3), end_s=round(s.end_s, 3),
                    motion=s.motion, usability=s.usability, roles=s.roles, text=s.text,
                )
                for s in view.shots
            ],
            speech=view.speech,
            subjects=[
                RangeSubjectOut(t_s=round(s.t_s, 3), keyframe_idx=s.keyframe, label=s.label,
                                category=s.category, box=s.box)
                for s in view.subjects
            ],
            chapters=[
                SynthesisChapterOut(index=ch.index, start_s=round(ch.start_s, 3),
                                    end_s=round(ch.end_s, 3), title=ch.title, summary=ch.summary)
                for ch in view.chapters
            ],
            highlights=[ClipOut.of(h.clip) for h in view.highlights],
            cut=CutPointsOut.of(view.cut) if view.cut is not None else None,
            note=view.note,
        )  # fmt: skip


class MatchCandidateOut(ApiModel):
    video_id: str
    filename: str
    path: str
    status: VideoStatus


class MatchedClipOut(ApiModel):
    file_path: str
    method: MatchMethod = Field(
        description="path (same path), resolve_id (same Media Pool clip, through a library "
        "timeline), name_size (same name and same size), fingerprint (same content), "
        "name (same name only), none."
    )
    confidence: float
    status: Literal["ready", "offline", "not_analysed", "ambiguous", "unknown"]
    video_id: str | None
    filename: str | None
    path: str | None = Field(description="Path of the file in the library.")
    candidates: list[MatchCandidateOut]
    note: str | None
    range: ClipRangeOut | None

    @classmethod
    def of(cls, match: ClipMatch) -> MatchedClipOut:
        video = match.video
        return cls(
            file_path=match.query.file_path, method=match.method,
            confidence=match.confidence, status=match.status,  # type: ignore[arg-type]
            video_id=video.id if video else None, filename=video.filename if video else None,
            path=video.path if video else None,
            candidates=[
                MatchCandidateOut(video_id=v.id, filename=v.filename, path=v.path,
                                  status=v.status)
                for v in match.candidates
            ],
            note=match.note,
            range=ClipRangeOut.of(match.range) if match.range is not None else None,
        )  # fmt: skip


class MatchClipsOut(ApiModel):
    items: list[MatchedClipOut] = Field(description="One row per requested clip, in order.")


class AudioStatsOut(ApiModel):
    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbfs: float | None
    silence_ratio: float | None
    silences: list[list[float]]


class CctSeriesOut(ApiModel):
    t: list[float]
    k: list[float]


class VisualSignalsOut(ApiModel):
    """Per-bucket means at ``hz`` samples per second; ``t`` is the bucket start in seconds."""

    hz: float
    t: list[float]
    luma: list[float]
    contrast: list[float]
    saturation: list[float]
    sharpness: list[float]
    motion: list[float] = Field(description="Median optical flow, pixels per analysed frame.")
    cct: CctSeriesOut


class AudioCurveOut(ApiModel):
    """Momentary loudness (EBU R128, 400 ms window) averaged in energy per bucket."""

    hz: float
    t: list[float]
    lufs: list[float]


class SignalsOut(ApiModel):
    """Time series for the timeline and charts (see ``analysis_pass`` / ``audio_levels``)."""

    visual: VisualSignalsOut | None
    audio: AudioCurveOut | None
    audio_stats: AudioStatsOut | None

    @classmethod
    def of(cls, view: SignalsView) -> SignalsOut:
        stats: AudioStats | None = view.audio_stats
        return cls(
            visual=_parse_stored(VisualSignalsOut, view.visual),
            audio=_parse_stored(AudioCurveOut, view.audio),
            audio_stats=AudioStatsOut.model_validate(stats) if stats else None,
        )


class GpsPointOut(ApiModel):
    t_s: float | None
    utc: datetime | None
    latitude: float
    longitude: float
    altitude_m: float | None
    speed_mps: float | None

    @classmethod
    def of(cls, point: GpsPoint) -> GpsPointOut:
        return cls.model_validate(point)


class CaptureDateOut(ApiModel):
    """One date found in the file, before arbitration (``kind`` ranks its reliability)."""

    value: str
    kind: str = Field(description="GPS_UTC, WITH_OFFSET, CONTAINER_UTC, CAMERA_LOCAL, FILE_SYSTEM")
    source: str


class ExifPointOut(ApiModel):
    lat: float
    lon: float
    alt: float | None = None
    source: str | None = None


class TrackSummaryOut(ApiModel):
    points: int
    distance_m: float
    bbox: list[float] = Field(description="[lat_min, lon_min, lat_max, lon_max]")
    altitude_range_m: list[float | None]


class CaptureOut(ApiModel):
    utc: datetime | None = None
    local: datetime | None = Field(default=None, description="Local time with its offset.")
    utc_offset_min: int | None = None
    source: str | None = None
    confidence: str
    recording_s: float | None = Field(
        default=None, description="Real duration of the recording (slow motion corrected)."
    )


class ExifSummaryOut(ApiModel):
    """What the ``metadata`` stage extracted and how it arbitrated the capture time."""

    make: str | None = None
    model: str | None = None
    software: str | None = None
    lens: str | None = None
    iso: float | None = None
    f_number: float | None = None
    exposure_time: float | None = None
    focal_length_mm: float | None = None
    point: ExifPointOut | None = None
    track_points: int = 0
    dates: list[CaptureDateOut] = Field(default_factory=list)
    color_profile: str | None = None
    reencoded_by: str | None = None
    model_name: str | None = None
    os_version: str | None = None
    capture_fps: float | None = None
    utc_offset_min: int | None = None
    duration_s: float | None = None
    container_dates_at_stop: bool = False
    warnings: list[str] = Field(default_factory=list)
    sidecars: list[str] = Field(default_factory=list)
    sidecar_settings: dict[str, Any] = Field(default_factory=dict)
    exported_by_editor: bool = False
    timezone: str | None = None
    track: TrackSummaryOut | None = None
    capture: CaptureOut | None = None


class MetadataOut(ApiModel):
    exif: ExifSummaryOut | None
    probe: dict[str, Any] | None = Field(description="Normalised ffprobe summary.")
    raw_tags: dict[str, Any] | None = Field(
        default=None, description="Raw ExifTool tags (only with include_raw=true)."
    )

    @classmethod
    def of(cls, normalized: dict[str, Any] | None, raw: dict[str, Any] | None) -> MetadataOut:
        normalized = normalized or {}
        return cls(
            exif=_parse_stored(ExifSummaryOut, normalized.get("exif")),
            probe=normalized.get("probe"),
            raw_tags=raw,
        )


# ---------------------------------------------------------------- capture context
class PlaceFeatureOut(ApiModel):
    name: str
    category: str
    type: str = Field(description="beach, peak, island…")
    distance_m: float


class PlaceOut(ApiModel):
    source: str = Field(description="nominatim (OpenStreetMap) or offline (GeoNames, approximate).")
    label: str | None
    locality: str | None
    sublocality: str | None = None
    road: str | None = None
    postcode: str | None = None
    county: str | None = None
    state: str | None = None
    country: str | None
    country_code: str | None
    iso3166_2: str | None = None
    display_name: str | None = None
    feature: PlaceFeatureOut | None = None
    at_sea: bool = False
    approximate: bool = False
    locality_distance_km: float | None = None
    grid_precision_m: int | None = Field(
        default=None, description="Maximum offset of the point sent to the service (rounded)."
    )
    attribution: str

    @classmethod
    def of(cls, row: ContextPlace, tr: Dictionary = AS_WRITTEN) -> PlaceOut | None:
        """Place names in the language of ``tr``: usual names only."""
        named = translated(row.data, PLACE_DATA_TEXTS, tr)
        data = {k: v for k, v in named.items() if k in cls.model_fields}
        try:
            return cls.model_validate(
                data
                | {
                    "source": row.source,
                    "label": tr.maybe(row.label),
                    "locality": tr.maybe(row.locality),
                    "country": tr.maybe(row.country),
                    "country_code": row.country_code,
                }
            )
        except ValidationError:
            return None


class WeatherValuesOut(ApiModel):
    """Model values interpolated to the capture minute (°C, %, hPa, m, km/h, mm, cm, W/m², s)."""

    temperature_c: float | None
    dew_point_c: float | None
    relative_humidity_pct: float | None
    apparent_temperature_c: float | None
    pressure_hpa: float | None
    cloud_cover_pct: float | None
    cloud_low_pct: float | None
    cloud_mid_pct: float | None
    cloud_high_pct: float | None
    visibility_m: float | None
    wind_speed_kmh: float | None
    wind_direction_deg: float | None = Field(
        description="Direction the wind comes from (0 = north)."
    )
    wind_gusts_kmh: float | None
    precipitation_mm: float | None = Field(description="Hour that covers the moment of shooting.")
    rain_mm: float | None
    showers_mm: float | None
    snowfall_cm: float | None
    precipitation_last_3h_mm: float | None
    sunshine_s: float | None = Field(description="Sunshine duration of the covering hour (s).")
    shortwave_wm2: float | None
    direct_wm2: float | None
    diffuse_wm2: float | None
    weather_code: int | None
    is_day: bool | None


class WeatherHourOut(ApiModel):
    t: datetime
    temperature_2m: float | None
    cloud_cover: float | None
    precipitation: float | None
    weather_code: float | None


class WeatherGridOut(ApiModel):
    latitude: float
    longitude: float
    elevation_m: float | None
    distance_km: float


class WeatherOut(ApiModel):
    source: str = Field(description="historical_forecast or archive (Open-Meteo).")
    at_utc: datetime
    provisional: bool = Field(description="Data less than 48 h old, still liable to change.")
    time_confidence: str | None
    weather_code: int | None
    category: str | None
    values: WeatherValuesOut
    sun_fraction: float | None
    diffuse_fraction: float | None
    hours: list[WeatherHourOut]
    grid: WeatherGridOut | None
    attribution: str
    attribution_url: str
    notice: str | None
    fetched_at: datetime

    @classmethod
    def of(cls, row: ContextWeather) -> WeatherOut | None:
        data = {k: v for k, v in row.data.items() if k in cls.model_fields}
        try:
            return cls.model_validate(
                data
                | {
                    "source": row.source,
                    "at_utc": row.at_utc,
                    "provisional": row.provisional,
                    "weather_code": row.weather_code,
                    "category": row.category,
                    "fetched_at": row.fetched_at,
                }
            )
        except ValidationError:
            return None


class SunLightOut(ApiModel):
    """Theoretical natural light (kelvin ranges), never a measurement."""

    regime: str = Field(description="sunlit, overcast, mixed, clear_assumed or twilight.")
    direct_k: list[int] | None = Field(description="Direct sunlight [min, max].")
    ambient_k: list[int] = Field(description="Overall lighting of the scene [min, max].")
    off_locus: bool
    nominal_k: int
    comparable: bool = Field(
        default=True, description="False if the take spans several light phases."
    )


class SunWindowOut(ApiModel):
    start: datetime
    end: datetime
    elevation_min: float
    elevation_max: float
    phases: list[str]


class SunTakeOut(ApiModel):
    end: datetime
    elevation_end: float
    phases: list[str]


class SunEventsOut(ApiModel):
    local_date: str
    rising: dict[str, datetime | None] = Field(
        description="astronomical, nautical, civil, blue_golden, sun (sunrise), golden_day."
    )
    setting: dict[str, datetime | None]
    solar_noon: datetime | None
    polar: str | None = Field(description="polar_day or polar_night.")


class MoonOut(ApiModel):
    illuminated: float
    waxing: bool


class SunOut(ApiModel):
    at_utc: datetime
    elevation_deg: float = Field(description="Geometric elevation of the centre of the sun.")
    apparent_elevation_deg: float
    azimuth_deg: float
    light_phase: str | None = Field(description="None if the uncertain time spans two phases.")
    twilight_phase: str | None
    day_part: str | None
    direction: str | None
    near_culmination: bool
    phase_at_instant: str
    time_confidence: str | None
    window: SunWindowOut | None
    take: SunTakeOut | None
    events: SunEventsOut
    light: SunLightOut | None
    theoretical_cct_k: int | None
    moon: MoonOut
    assumptions: list[str]

    @classmethod
    def of(cls, row: ContextSun) -> SunOut | None:
        data = {k: v for k, v in row.data.items() if k in cls.model_fields}
        try:
            return cls.model_validate(
                data
                | {
                    "at_utc": row.at_utc,
                    "elevation_deg": row.elevation_deg,
                    "azimuth_deg": row.azimuth_deg,
                    "light_phase": row.light_phase,
                    "twilight_phase": row.twilight_phase,
                    "day_part": row.day_part,
                    "theoretical_cct_k": row.theoretical_cct_k,
                }
            )
        except ValidationError:
            return None


class SunCurveOut(ApiModel):
    start_utc: datetime = Field(description="Local midnight of the shooting day, in UTC.")
    step_min: int
    elevations_deg: list[float] = Field(
        description="Apparent elevation of the sun (refraction included) every step_min "
        "minutes, over 24 h from start_utc."
    )


class ContextOut(ApiModel):
    """Where and in what conditions the video was shot (sources and assumptions included)."""

    latitude: float | None
    longitude: float | None
    altitude_m: float | None
    location_source: str | None
    online_services: bool
    place: PlaceOut | None
    weather: WeatherOut | None
    sun: SunOut | None
    measured_cct_k: int | None = Field(
        description="Median of the Kelvin values measured on the images."
    )
    light_comparison: str | None = Field(
        description="consistent, warmer or cooler: an assumption, not a measurement."
    )
    notes: dict[str, str] = Field(description="Stage → reason for the missing result.")
    sun_curve: SunCurveOut | None = None

    @classmethod
    def of(cls, view: ContextView) -> ContextOut:
        video = view.video
        return cls(
            latitude=video.latitude,
            longitude=video.longitude,
            altitude_m=video.altitude_m,
            location_source=video.location_source,
            online_services=view.online_services,
            place=PlaceOut.of(view.place, view.texts) if view.place else None,
            weather=WeatherOut.of(view.weather) if view.weather else None,
            sun=SunOut.of(view.sun) if view.sun else None,
            measured_cct_k=view.measured_cct_k,
            light_comparison=view.light_comparison,
            notes=view.notes,
            sun_curve=SunCurveOut.model_validate(view.sun_curve, from_attributes=True)
            if view.sun_curve
            else None,
        )


class HealthOut(ApiModel):
    status: str
    version: str
    worker_pid: int | None


RootCreated.model_rebuild()


# Audio and text: what is heard, said and written.
class PartStatus(ApiModel):
    status: str = Field(
        description="ready when a result is available (no_speech: transcription without "
        "speech), otherwise the state of the last run: not_run, skipped, failed or running."
    )
    run_status: str = Field(
        description="State of the last run of the stage (ready, not_run, skipped, failed, "
        "running): a kept result can coexist with a more recent attempt."
    )
    note: str | None = Field(description="Reason given by the last run (skipped, failed).")


class AudioShareOut(ApiModel):
    category: str
    share: float = Field(
        description="Share of the duration where the category is present (0 to 1)."
    )


_SOUND_LABEL = "AudioSet class (original English name, stable)."
_SOUND_NAME = "Displayed name, in the language of the analyses."


class AudioLabelOut(ApiModel):
    label: str = Field(description=_SOUND_LABEL)
    name: str = Field(description=_SOUND_NAME)
    score: float


class SoundNameOut(ApiModel):
    label: str = Field(description=_SOUND_LABEL)
    name: str = Field(description=_SOUND_NAME)


class InstrumentOut(ApiModel):
    label: str = Field(description=_SOUND_LABEL)
    name: str = Field(description=_SOUND_NAME)
    seconds: float = Field(description="Duration during which it is heard.")
    max_score: float


class HeardSoundOut(ApiModel):
    """A specific sound heard in the file (« Sounds heard »)."""

    label: str = Field(description=_SOUND_LABEL)
    name: str = Field(description=_SOUND_NAME)
    category: str = Field(description="Family: nature, water, vehicles, crowd, tools…")
    seconds: float = Field(description="Duration during which it is heard.")
    score: float = Field(description="Confidence of the better of the two models (0 to 1).")
    spans: list[tuple[float, float]] = Field(
        description="Moments when it is heard (start, end in seconds), merged, 12 at most."
    )
    moments: int = Field(description="Total number of moments (beyond the 12 given).")
    sources: list[str] = Field(description="Models that heard it: yamnet, ced.")


class AudioSegmentOut(ApiModel):
    kind: str = Field(
        description="segment (family), event (short, clear sound) or heard (moment of a sound "
        "heard)."
    )
    category: str
    label: str | None = Field(description=_SOUND_LABEL)
    name: str | None = Field(description=_SOUND_NAME)
    start_s: float
    end_s: float
    score: float | None


class AudioCurvesOut(ApiModel):
    hz: float
    series: dict[str, list[int]] = Field(description="Category → presence 0–100 per step.")


class ShotAudioOut(ApiModel):
    shot_idx: int
    start_s: float
    end_s: float
    labels: list[AudioLabelOut]
    heard: list[SoundNameOut] = Field(description="Sounds heard during the shot.")


class AudioOut(PartStatus):
    """What is heard (YAMNet, CED-small): families, instruments, sounds heard, per-shot labels."""

    model: str | None
    taggers: list[str] = Field(description="Models used: yamnet, and ced-small if installed.")
    ced_available: bool = Field(
        description="CED-small is installed: « Update » adds its opinion if it is missing."
    )
    speech_s: float | None
    music_s: float | None
    dominant: str | None
    presence: list[AudioShareOut]
    heard: list[HeardSoundOut] | None = Field(
        description="Sounds heard; null for an analysis older than this list."
    )
    instruments: list[InstrumentOut]
    top_labels: list[AudioLabelOut]
    environment: str | None
    issues: list[str]
    segments: list[AudioSegmentOut]
    curves: AudioCurvesOut | None
    shots: list[ShotAudioOut]

    @classmethod
    def of(cls, view: AudioView) -> AudioOut:
        scene, data = view.scene, (view.scene.data if view.scene else {}) or {}
        presence = data.get("presence") or {}

        def named(item: dict[str, Any]) -> dict[str, Any]:
            return {**item, "name": sound_name(str(item.get("label", "")), view.language)}

        return cls(
            status="ready" if scene is not None else view.state.status,
            run_status=view.state.status,
            note=view.state.note,
            model=scene.model if scene else None,
            taggers=list(data.get("taggers") or (["yamnet"] if scene else [])),
            ced_available=view.ced_available,
            speech_s=scene.speech_s if scene else None,
            music_s=scene.music_s if scene else None,
            dominant=scene.dominant if scene else None,
            presence=[
                AudioShareOut(category=k, share=float(v))
                for k, v in sorted(presence.items(), key=lambda kv: -float(kv[1]))
            ],
            heard=(
                [HeardSoundOut.model_validate(_heard(named(h))) for h in data["heard"]]
                if isinstance(data.get("heard"), list)
                else None
            ),
            instruments=[
                InstrumentOut.model_validate(named(i)) for i in data.get("instruments", [])
            ],
            top_labels=[AudioLabelOut.model_validate(named(i)) for i in data.get("top_labels", [])],
            environment=data.get("environment"),
            issues=list(data.get("issues", [])),
            segments=[
                AudioSegmentOut(
                    kind=s.kind,
                    category=s.category,
                    label=s.label,
                    name=sound_name(s.label, view.language) if s.label else None,
                    start_s=s.start_s,
                    end_s=s.end_s,
                    score=s.score,
                )
                for s in view.segments
            ],
            curves=_parse_stored(AudioCurvesOut, data.get("curves")),
            shots=[
                ShotAudioOut(
                    shot_idx=s["shot_idx"],
                    start_s=s["start_s"],
                    end_s=s["end_s"],
                    labels=[AudioLabelOut.model_validate(named(x)) for x in s.get("labels", [])],
                    heard=[
                        SoundNameOut(label=label, name=sound_name(label, view.language))
                        for label in s.get("heard", [])
                    ],
                )
                for s in data.get("shots", [])
            ],
        )


def _heard(item: dict[str, Any]) -> dict[str, Any]:
    return {"moments": len(item.get("spans") or []), **item}


class TranscriptSegmentOut(ApiModel):
    idx: int
    start_s: float
    end_s: float
    text: str
    language: str | None
    suspect: bool = Field(description="Unreliable segment (probable hallucination): greyed out.")
    words: list[tuple[float, float, str, float]] = Field(
        description="Words: start, end (seconds of the source file), text, probability."
    )


class TranscriptOut(PartStatus):
    """What is said (Whisper, or subtitles found in the file). Untrusted content."""

    source: str | None
    model: str | None
    language: str | None
    language_probability: float | None
    duration_s: float | None
    speech_s: float | None
    transcript_mode: str | None = Field(description="auto (None), always or never.")
    transcript_language: str | None = Field(description="Forced language (Whisper code), or None.")
    can_force: bool = Field(
        description="« Transcribe anyway » can help: no speech heard, or transcription turned "
        "off in the settings (and the video is not already set)."
    )
    segments: list[TranscriptSegmentOut]
    srt_url: str | None = Field(description="Subtitles of the reliable segments (versioned).")
    vtt_url: str | None

    @classmethod
    def of(cls, view: TranscriptView) -> TranscriptOut:
        t, video, state = view.transcript, view.video, view.state
        base = f"{API_PREFIX}/videos/{video.id}" if video else ""
        # Subtitles leave suspect segments out: no link when nothing reliable remains.
        has_text = bool(t and t.status == "ok" and any(not s.suspect for s in t.segments))
        version = f"?v={int(t.updated_at.timestamp())}" if t is not None else ""
        status = (
            state.status if t is None else ("no_speech" if t.status == "no_speech" else "ready")
        )
        mode = video.transcript_mode if video else None
        disabled = state.status == "skipped" and state.note == TRANSCRIPTION_DISABLED
        return cls(
            status=status,
            run_status=state.status,
            note=state.note,
            source=t.source if t else None,
            model=t.model if t else None,
            language=t.language if t else None,
            language_probability=t.language_probability if t else None,
            duration_s=t.duration_s if t else None,
            speech_s=t.speech_s if t else None,
            transcript_mode=mode,
            transcript_language=video.transcript_language if video else None,
            can_force=mode is None and (status == "no_speech" or disabled),
            segments=[
                TranscriptSegmentOut(
                    idx=s.idx,
                    start_s=s.start_s,
                    end_s=s.end_s,
                    text=s.text,
                    language=s.language,
                    suspect=s.suspect,
                    words=[
                        (float(w[0]), float(w[1]), str(w[2]), float(w[3]))
                        for w in s.words
                        if isinstance(w, list | tuple) and len(w) == 4
                    ],
                )
                for s in (t.segments if t else [])
            ],
            srt_url=f"{base}/transcript.srt{version}" if has_text else None,
            vtt_url=f"{base}/transcript.vtt{version}" if has_text else None,
        )


class OcrLineOut(ApiModel):
    keyframe_id: str
    t_s: float
    idx: int
    text: str = Field(description="Text read in the image (untrusted content).")
    score: float
    box: list[list[float]] = Field(description="4 corners [x, y] normalised 0–1 (displayed image).")


class OcrOut(PartStatus):
    lines: list[OcrLineOut]

    @classmethod
    def of(cls, view: OcrView) -> OcrOut:
        ready = bool(view.lines) or view.state.status == "ready"
        return cls(
            status="ready" if ready else view.state.status,
            run_status=view.state.status,
            note=view.state.note,
            lines=[OcrLineOut.model_validate(line, from_attributes=True) for line in view.lines],
        )


# Subjects: where the living beings are in the keyframes.
class StageStateOut(ApiModel):
    status: str = Field(description="ready, not_run, skipped, failed or running.")
    note: str | None


class SubjectOut(ApiModel):
    label: str = Field(description="Short name (interface language), given by the model.")
    category: str = Field(
        description="person, body_part, mammal, bird, insect, other_animal or face."
    )
    box: list[float] = Field(
        description="[x1, y1, x2, y2] normalised 0–1 in the displayed image (rotation applied)."
    )
    score: float | None = Field(description="Confidence of the detector (absent for the model).")
    main: bool = Field(
        description="Main subject according to the vision model (without it: the most certain, "
        "large and central)."
    )
    sources: list[str] = Field(description="detector, faces and/or vlm.")
    face_box: list[float] | None = Field(description="Face of this person (position only).")
    face_points: list[list[float]] | None = Field(
        description="Eyes, nose and corners of the mouth [x, y] 0–1: the eye line is used for "
        "reframing."
    )

    @classmethod
    def of(cls, subject: Subject) -> SubjectOut:
        return cls(
            label=subject.label,
            category=subject.category.value,
            box=subject.box.rounded(),
            score=round(subject.score, 3) if subject.score is not None else None,
            main=subject.main,
            sources=[s.value for s in subject.sources],
            face_box=subject.face.rounded() if subject.face else None,
            face_points=[[round(x, 4), round(y, 4)] for x, y in subject.face_points] or None,
        )


class FrameSubjectsOut(ApiModel):
    keyframe_id: str
    t_s: float
    until_s: float | None = Field(
        description="Next keyframe (or end of the video): the interval these positions represent."
    )
    idx: int
    duplicate_of: str | None
    subjects: list[SubjectOut]


class SubjectsOut(PartStatus):
    detector: StageStateOut = Field(description="CPU detector (people, animals, faces).")
    vision: StageStateOut = Field(description="Spotting by the vision model (LM Studio).")
    width: int | None = Field(description="Displayed width of the video, in pixels.")
    height: int | None
    frames: list[FrameSubjectsOut]

    @classmethod
    def of(cls, view: SubjectsView) -> SubjectsOut:
        return cls(
            status=view.status,
            run_status=view.run_status,
            note=view.note,
            detector=StageStateOut(status=view.detector.status, note=view.detector.note),
            vision=StageStateOut(status=view.vision.status, note=view.vision.note),
            width=view.width,
            height=view.height,
            frames=[
                FrameSubjectsOut(
                    keyframe_id=f.keyframe_id,
                    t_s=f.t_s,
                    until_s=f.until_s,
                    idx=f.idx,
                    duplicate_of=f.duplicate_of,
                    subjects=[SubjectOut.of(s) for s in f.subjects],
                )
                for f in view.frames
            ],
        )


# ---------------------------------------------------------------- search
ChunkKindName = Literal["video", "chapter", "shot", "keyframe", "transcript"]


class SearchIndexOut(ApiModel):
    """The state of the library's search index."""

    videos: int = Field(description="Videos of the library.")
    indexed: int = Field(description="Videos that have passages in the index.")
    passages: int
    vectors: int = Field(description="Passages that have a vector from the installed model.")
    semantic: bool = Field(description="Search-by-meaning model installed.")
    model: str | None

    @classmethod
    def of(cls, state: IndexState) -> SearchIndexOut:
        return cls(
            videos=state.videos, indexed=state.indexed, passages=state.passages,
            vectors=state.vectors, semantic=state.semantic, model=state.model,
        )  # fmt: skip


class SearchHitOut(ApiModel):
    chunk_id: int
    video_id: str
    filename: str
    title: str | None = Field(description="Entered title, otherwise that of the synthesis.")
    kind: ChunkKindName = Field(
        description="video (the whole video), chapter, shot, keyframe (an image) or transcript "
        "(~30 s of speech)."
    )
    t_start: float | None = Field(description="Seconds of the source file; null for the video.")
    t_end: float | None
    shot_idx: int | None = Field(description="Index of the shot (0 = first).")
    thumb_url: str | None = Field(description="Thumbnail of the passage's keyframe.")
    snippet: str = Field(description="Snippet in plain text (never HTML).")
    highlights: list[tuple[int, int]] = Field(
        description="Words found: [start, end) in Unicode code points of the snippet."
    )
    score: float
    retrievers: list[Literal["words", "meaning"]] = Field(
        description="Found by words, by meaning, or both."
    )
    captured_at: datetime | None
    duration_s: float | None

    @classmethod
    def of(cls, hit: SearchHit) -> SearchHitOut:
        return cls(
            chunk_id=hit.chunk_id, video_id=hit.video_id, filename=hit.filename, title=hit.title,
            kind=hit.kind.value, t_start=hit.t_start, t_end=hit.t_end, shot_idx=hit.shot_idx,
            thumb_url=media_url(hit.thumb_path), snippet=hit.snippet,
            highlights=list(hit.highlights), score=hit.score,
            retrievers=[r for r in hit.retrievers if r in {"words", "meaning"}],  # type: ignore[misc]
            captured_at=hit.captured_at, duration_s=hit.duration_s,
        )  # fmt: skip


class SearchOut(ApiModel):
    """Passages found, best first (words and meaning merged by reciprocal rank)."""

    query: str
    hits: list[SearchHitOut]
    total: int = Field(description="Passages found before pagination.")
    limit: int
    offset: int
    retrievers: list[Literal["words", "meaning"]] = Field(
        description="What searched: words (FTS5), meaning (vectors), or both."
    )
    note: str | None = Field(description="Why the search by meaning did not answer.")
    index: SearchIndexOut

    @classmethod
    def of(cls, result: SearchResult, *, limit: int, offset: int) -> SearchOut:
        return cls(
            query=result.query, hits=[SearchHitOut.of(hit) for hit in result.hits],
            total=result.total, limit=limit, offset=offset,
            retrievers=[r for r in result.retrievers if r in {"words", "meaning"}],  # type: ignore[misc]
            note=result.note, index=SearchIndexOut.of(result.state),
        )  # fmt: skip


class FacetValueOut(ApiModel):
    value: str
    count: int


def _values(items: Sequence[tuple[str, int]]) -> list[FacetValueOut]:
    return [FacetValueOut(value=value, count=count) for value, count in items]


class SearchFacetsOut(ApiModel):
    """The values present in the index, for the search filters (an empty list: nothing to
    filter in the library)."""

    index: SearchIndexOut
    kinds: list[FacetValueOut]
    devices: list[FacetValueOut] = Field(description="Devices (number of videos).")
    places: list[FacetValueOut] = Field(description="Localities and countries (number of videos).")
    subjects: list[FacetValueOut] = Field(description="Subjects seen (number of shots).")
    shot_types: list[FacetValueOut] = Field(description="Framings (number of shots).")
    weather: list[FacetValueOut] = Field(description="Weather (number of videos).")
    light_phases: list[FacetValueOut]
    orientations: list[FacetValueOut]
    date_min: date | None
    date_max: date | None
    speech: bool
    rated: bool
    favorites: bool
    usability: bool

    @classmethod
    def of(cls, facets: SearchFacets) -> SearchFacetsOut:
        return cls(
            index=SearchIndexOut.of(facets.state), kinds=_values(facets.kinds),
            devices=_values(facets.devices), places=_values(facets.places),
            subjects=_values(facets.subjects), shot_types=_values(facets.shot_types),
            weather=_values(facets.weather), light_phases=_values(facets.light_phases),
            orientations=_values(facets.orientations), date_min=facets.date_min,
            date_max=facets.date_max, speech=facets.speech, rated=facets.rated,
            favorites=facets.favorites, usability=facets.usability,
        )  # fmt: skip


# ---------------------------------------------------------------- questions
AskStatus = Literal["answered", "no_answer", "uncited", "no_passages", "cancelled", "failed"]
_CHUNK_KINDS = frozenset({"video", "chapter", "shot", "keyframe", "transcript"})


class AskFilters(BaseModel):
    """The search filters, applied to the passages the model reads (empty: the whole
    library)."""

    model_config = ConfigDict(extra="forbid")

    kinds: list[ChunkKindName] = Field(default_factory=list)
    video_id: str | None = Field(default=None, max_length=32)
    date_from: date | None = None
    date_to: date | None = None
    place: str | None = Field(
        default=None, max_length=200, description="Part of the name of the place or country."
    )
    weather: list[WeatherCategory] = Field(default_factory=list)
    light_phase: list[LightPhase] = Field(default_factory=list)
    device: str | None = Field(default=None, max_length=200)
    orientation: Orientation | None = None
    has_speech: bool | None = None
    subjects: list[Annotated[str, Field(max_length=80)]] = Field(
        default_factory=list, max_length=10, description="Subjects seen, all required."
    )
    shot_types: list[ShotType] = Field(default_factory=list)
    min_rating: int | None = Field(default=None, ge=1, le=5)
    favorite: bool | None = None
    root_id: str | None = Field(default=None, max_length=32)
    folder: str | None = Field(
        default=None, max_length=1000, description="Folder of root_id and its subfolders."
    )
    min_usability: int | None = Field(default=None, ge=0, le=100)

    def to_filters(self) -> SearchFilters:
        return SearchFilters(
            kinds=tuple(ChunkKind(k) for k in self.kinds), video_id=self.video_id,
            date_from=self.date_from, date_to=self.date_to, place=self.place,
            weather=tuple(w.value for w in self.weather),
            light_phase=tuple(p.value for p in self.light_phase), device=self.device,
            orientation=self.orientation, has_speech=self.has_speech,
            subjects=tuple(s for s in self.subjects if s.strip()),
            shot_types=tuple(t.value for t in self.shot_types), min_rating=self.min_rating,
            favorite=self.favorite, root_id=self.root_id,
            folder=self.folder if self.root_id else None, min_usability=self.min_usability,
        )  # fmt: skip

    @classmethod
    def stored(cls, data: dict[str, Any]) -> AskFilters:
        """The filters kept with a question (written by an older version: shown as none)."""
        return _parse_stored(cls, data) or cls()


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1, max_length=1000, description="The question, in plain words."
    )
    filters: AskFilters = Field(default_factory=AskFilters)
    visual_check: bool = Field(
        default=False,
        description="Show the model 1 to 4 images of the cited moments, to confirm or correct "
        "the answer in a separate note (slower; vision model).",
    )


class AskCitationOut(ApiModel):
    n: int = Field(description="The number the answer cites: [n].")
    video_id: str
    filename: str
    title: str | None = Field(description="Entered title, otherwise that of the synthesis.")
    kind: ChunkKindName
    t_start: float | None = Field(description="Seconds of the source file; null: the video.")
    t_end: float | None
    shot_idx: int | None = Field(description="Index of the shot (0 = first).")
    timecode: str | None = Field(description="The moment, « mm:ss » (« h:mm:ss » after 1 h).")
    thumb_url: str | None
    excerpt: str = Field(description="The cited passage, in plain text (never HTML).")
    available: bool = Field(description="The video is still in the library.")

    @classmethod
    def of(cls, citation: Citation) -> AskCitationOut:
        start = citation.t_start
        return cls(
            n=citation.n, video_id=citation.video_id, filename=citation.filename,
            title=citation.title,
            kind=citation.kind if citation.kind in _CHUNK_KINDS else "video",  # type: ignore[arg-type]
            t_start=start, t_end=citation.t_end, shot_idx=citation.shot_idx,
            timecode=format_clock(max(0.0, start)) if start is not None else None,
            thumb_url=media_url(citation.thumb_path), excerpt=citation.excerpt,
            available=citation.available,
        )  # fmt: skip


class AskCheckFrameOut(ApiModel):
    n: int = Field(description="The citation this image shows.")
    t_s: float
    thumb_url: str | None


class AskCheckOut(ApiModel):
    """The visual check: what the images of the cited moments say about the answer."""

    status: Literal["done", "skipped", "failed"]
    verdict: Literal["confirmed", "partly_confirmed", "contradicted", "not_visible"] | None
    note: str = Field(description="Written by the model (done), otherwise the reason.")
    frames: list[AskCheckFrameOut]

    @classmethod
    def stored(cls, data: dict[str, Any] | None) -> AskCheckOut | None:
        if not data:
            return None
        frames = [
            {"n": f.get("n", 0), "t_s": f.get("t_s", 0.0),
             "thumb_url": media_url(f.get("thumb_path"))}
            for f in data.get("frames") or []
        ]  # fmt: skip
        return _parse_stored(
            cls,
            {"status": data.get("status"), "verdict": data.get("verdict"),
             "note": str(data.get("note") or ""), "frames": frames},
        )  # fmt: skip


class AskOut(ApiModel):
    """A question asked about the library and its answer (the « done » event of the stream)."""

    id: str
    question: str
    filters: AskFilters
    language: str
    model: str | None
    status: AskStatus = Field(
        description="answered (cites passages), no_answer (the passages do not answer), "
        "uncited (text without citation: not an answer drawn from the videos), no_passages "
        "(nothing found: the model was not asked), cancelled, failed."
    )
    answer: str = Field(
        description="Plain text from the local model, citations written [n] (never HTML)."
    )
    citations: list[AskCitationOut]
    passages: int = Field(description="Passages read by the model.")
    visual_check: AskCheckOut | None
    truncated: bool = Field(description="Answer cut at its maximum length.")
    dropped_citations: int = Field(description="Citations of non-existent passages, removed.")
    error: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    timings: dict[str, int] = Field(
        description="Milliseconds: retrieval_ms, first_token_ms, answer_ms, check_ms, total_ms."
    )
    created_at: datetime

    @classmethod
    def of(cls, view: QuestionView) -> AskOut:
        return cls(
            id=view.id, question=view.question, filters=AskFilters.stored(view.filters),
            language=view.language, model=view.model, status=view.status.value,
            answer=view.answer, citations=[AskCitationOut.of(c) for c in view.citations],
            passages=view.passages, visual_check=AskCheckOut.stored(view.visual_check),
            truncated=view.truncated, dropped_citations=view.dropped_citations,
            error=view.error, prompt_tokens=view.prompt_tokens,
            completion_tokens=view.completion_tokens, timings=view.timings,
            created_at=view.created_at,
        )  # fmt: skip


class AskSummaryOut(ApiModel):
    id: str
    question: str
    status: AskStatus
    citations: int
    model: str | None
    created_at: datetime

    @classmethod
    def of(cls, view: QuestionView) -> AskSummaryOut:
        return cls(
            id=view.id, question=view.question, status=view.status.value,
            citations=len(view.citations), model=view.model, created_at=view.created_at,
        )  # fmt: skip


class AskStartOut(ApiModel):
    """Event « start »: the loaded model reads the chosen passages."""

    id: str = Field(description="The id under which the question will be kept.")
    model: str
    vision: bool = Field(description="The model reads images (visual check).")
    passages: int
    slot_tokens: int = Field(
        description="Share of a slot of the loaded model: context ÷ parallel requests."
    )
    answer_tokens: int = Field(description="Maximum length of the answer.")
    context_length: int | None
    parallel: int | None

    @classmethod
    def of(cls, started: AskStarted) -> AskStartOut:
        return cls(
            id=started.id, model=started.model, vision=started.vision,
            passages=started.passages, slot_tokens=started.budget.slot,
            answer_tokens=started.budget.answer, context_length=started.context_length,
            parallel=started.parallel,
        )  # fmt: skip


class AskTokenOut(ApiModel):
    """Event « token »: the next part of the answer, as written (plain text)."""

    text: str


class AskCheckingOut(ApiModel):
    """Event « checking »: the model looks at the images of the cited moments."""

    frames: int


class AskErrorOut(ApiModel):
    """Event « error », as an RFC 9457 problem."""

    code: str
    title: str
    detail: str
    status: int


AskStreamEvent = AskStartOut | AskTokenOut | AskCheckingOut | AskOut | AskErrorOut
