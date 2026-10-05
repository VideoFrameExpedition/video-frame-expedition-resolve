"""DaVinci Resolve timelines as the application sees them.

The application only reads Resolve: which project is open, its timelines, and for one timeline
the files its clips use and where. A timeline becomes a bin of the library; the link from a
video to the timelines using it travels with the video's data (API, MCP, analysis file).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vfe_vision.domain.timecode import frames_timecode, timecode_frames

RESOLVE_SCHEMA_VERSION = 1
NAMES_KEPT = 20  # names of skipped items kept to tell the user


@dataclass(frozen=True, slots=True)
class ResolveDatabase:
    type: str  # "Disk", "PostgreSQL"…
    name: str


@dataclass(frozen=True, slots=True)
class ResolveProjectRef:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class TimelineInfo:
    id: str
    name: str
    fps: float
    drop_frame: bool
    start_frame: int  # absolute timeline frame of the first frame (e.g. 108000 at 01:00:00:00)
    end_frame: int  # exclusive
    start_timecode: str
    width: int | None = None
    height: int | None = None
    video_tracks: int = 0
    audio_tracks: int = 0
    video_clips: int | None = None  # items on the video tracks (None: not counted, big project)
    is_current: bool = False

    @property
    def duration_s(self) -> float:
        return max(0, self.end_frame - self.start_frame) / self.fps if self.fps > 0 else 0.0


@dataclass(frozen=True, slots=True)
class ResolveProjectInfo:
    """The project open in Resolve and its timelines (light: no clip is read)."""

    product: str
    version: str
    database: ResolveDatabase
    project: ResolveProjectRef
    current_timeline_id: str | None
    timelines: tuple[TimelineInfo, ...]
    studio: bool = True  # the free version refuses external scripts


@dataclass(frozen=True, slots=True)
class ClipUse:
    """Where a file is used on a timeline: one clip of one track."""

    track_type: str  # "video" | "audio"
    track: int
    track_name: str | None
    timeline_item_id: str | None
    media_pool_item_id: str | None
    name: str
    enabled: bool
    record_start_frame: int  # absolute timeline frames, end exclusive
    record_end_frame: int
    source_start_s: float  # seconds in the file (its start timecode removed), end exclusive
    source_end_s: float
    source_start_frame: int | None = None  # GetLeftOffset() turned into the clip's frames
    clip_fps: float | None = None
    track_enabled: bool = True
    nested_in: str | None = None  # the timeline or compound clip it was read from, if nested


class ClipKind:
    """What a timeline item is, as the reader tells it."""

    FILE = "file"  # a media file (video, audio, still)
    GRAPHICS = "graphics"  # title, generator, Fusion composition: no file
    CONTAINER = "container"  # compound or multicam clip, nested timeline not read inside


@dataclass(frozen=True, slots=True)
class TimelineClip:
    """One item read from a timeline track: its file (None for titles, generators, compound
    clips…), what it is, the media pool type Resolve gives it, and where it sits."""

    file_path: str | None
    clip_type: str | None
    use: ClipUse
    kind: str = ClipKind.FILE
    vfe_video_id: str | None = None  # written into Resolve by our markers script


@dataclass(frozen=True, slots=True)
class TimelineContent:
    product: str
    version: str
    database: ResolveDatabase
    project: ResolveProjectRef
    timeline: TimelineInfo
    clips: tuple[TimelineClip, ...]
    studio: bool = True
    errors: int = 0  # items that could not be read


@dataclass(frozen=True, slots=True)
class TimelineFile:
    """A file used by a timeline, with every place it is used."""

    path: str  # as the library writes it (see ``canonical`` in ``timeline_files``)
    position: int  # order of first appearance on the timeline, from 0
    uses: tuple[ClipUse, ...]
    vfe_video_id: str | None = None

    @property
    def enabled(self) -> bool:
        """Used at least once on an enabled clip of an enabled track."""
        return any(u.enabled and u.track_enabled for u in self.uses)


@dataclass(frozen=True, slots=True)
class SkippedItems:
    """Timeline items that are not a video file the library can analyse."""

    graphics: int = 0  # titles, generators, Fusion compositions
    graphics_names: tuple[str, ...] = ()
    containers: int = 0  # compound or multicam clips, nested timelines not read inside
    container_names: tuple[str, ...] = ()
    not_video: int = 0  # audio files, stills
    not_video_names: tuple[str, ...] = ()
    unsupported: int = 0  # camera formats the library cannot read (.braw, .r3d…)
    unsupported_names: tuple[str, ...] = ()
    elsewhere: int = 0  # paths of another system (a macOS path in a shared project…)
    elsewhere_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolveLink:
    """A video used in a Resolve timeline, as read at the last update of its bin."""

    bin_id: str
    bin_label: str
    database: ResolveDatabase
    project: ResolveProjectRef
    timeline: TimelineInfo
    synced_at: datetime
    uses: tuple[ClipUse, ...]


# ---------------------------------------------------------------- files of a timeline
def timeline_files(
    clips: Iterable[TimelineClip],
    *,
    is_video: Callable[[str], bool],
    canonical: Callable[[str], str | None],
    key: Callable[[str], str],
) -> tuple[list[TimelineFile], SkippedItems]:
    """The distinct video files of a timeline, in order of first appearance.

    A file's uses are its clips on video tracks; the clips on audio tracks count only for a file
    used for its sound alone (the audio linked to a video clip would repeat it). Paths are
    rewritten with ``canonical`` (None: not a path of this computer) and told apart with ``key``
    (the library's path key: case, slashes). A file ``is_video`` refuses while Resolve reads it
    as video is a camera format the library does not support.
    """
    ordered = sorted(clips, key=_timeline_order)
    video: dict[str, list[ClipUse]] = {}
    audio: dict[str, list[ClipUse]] = {}
    paths: dict[str, str] = {}
    first: dict[str, int] = {}
    ids: dict[str, str] = {}
    # Per bucket: what was seen (an item for graphics and containers, a file for the others)
    # and the names to show.
    seen: dict[str, set[str]] = {name: set() for name in _BUCKETS}
    shown: dict[str, dict[str, str]] = {name: {} for name in _BUCKETS}

    def skip(bucket: str, what: str, name: str) -> None:
        seen[bucket].add(what)
        shown[bucket].setdefault(name.casefold(), name)

    for index, clip in enumerate(ordered):
        raw = (clip.file_path or "").strip()
        name = clip.use.name or "?"
        if clip.kind == ClipKind.GRAPHICS or (not raw and clip.kind == ClipKind.FILE):
            skip("graphics", str(index), name)
            continue
        if clip.kind == ClipKind.CONTAINER:
            skip("containers", str(index), name)
            continue
        path = canonical(raw)
        if path is None:
            skip("elsewhere", raw, raw)
            continue
        k = key(path)
        if not is_video(path):
            skip("unsupported" if _read_as_video(clip.clip_type) else "not_video", k, name)
            continue
        paths.setdefault(k, path)
        first.setdefault(k, len(first))
        if clip.vfe_video_id:
            ids.setdefault(k, clip.vfe_video_id)
        (video if clip.use.track_type == "video" else audio).setdefault(k, []).append(clip.use)
    files = [
        TimelineFile(
            path=paths[k],
            position=position,
            uses=tuple(video.get(k) or audio.get(k, [])),
            vfe_video_id=ids.get(k),
        )
        for k, position in sorted(first.items(), key=lambda item: item[1])
    ]

    def names(bucket: str) -> tuple[str, ...]:
        return tuple(shown[bucket].values())[:NAMES_KEPT]

    report = SkippedItems(
        graphics=len(seen["graphics"]),
        graphics_names=names("graphics"),
        containers=len(seen["containers"]),
        container_names=names("containers"),
        not_video=len(seen["not_video"]),
        not_video_names=names("not_video"),
        unsupported=len(seen["unsupported"]),
        unsupported_names=names("unsupported"),
        elsewhere=len(seen["elsewhere"]),
        elsewhere_names=names("elsewhere"),
    )
    return files, report


_BUCKETS = ("graphics", "containers", "not_video", "unsupported", "elsewhere")


def _read_as_video(clip_type: str | None) -> bool:
    """Resolve's media pool type says the file holds moving pictures ("Video", "Video + Audio"),
    as opposed to "Audio" or "Still"."""
    return "video" in (clip_type or "").casefold()


def skipped_to_json(skipped: SkippedItems) -> dict[str, Any]:
    return {
        "graphics": skipped.graphics,
        "graphics_names": list(skipped.graphics_names),
        "containers": skipped.containers,
        "container_names": list(skipped.container_names),
        "not_video": skipped.not_video,
        "not_video_names": list(skipped.not_video_names),
        "unsupported": skipped.unsupported,
        "unsupported_names": list(skipped.unsupported_names),
        "elsewhere": skipped.elsewhere,
        "elsewhere_names": list(skipped.elsewhere_names),
    }


def _timeline_order(clip: TimelineClip) -> tuple[int, int, int]:
    use = clip.use
    return (use.record_start_frame, 0 if use.track_type == "video" else 1, use.track)


# ---------------------------------------------------------------- record positions
def record_seconds(use: ClipUse, timeline: TimelineInfo) -> tuple[float, float]:
    """The use's in and out points in seconds from the start of the timeline."""
    if timeline.fps <= 0:
        return 0.0, 0.0
    return (
        max(0, use.record_start_frame - timeline.start_frame) / timeline.fps,
        max(0, use.record_end_frame - timeline.start_frame) / timeline.fps,
    )


def record_timecodes(use: ClipUse, timeline: TimelineInfo) -> tuple[str, str] | None:
    """The use's record in and out timecodes, as Resolve shows them (out exclusive, like
    Resolve's out point). None when the timeline's start timecode cannot be read."""
    if timeline.fps <= 0 or not math.isfinite(timeline.fps):
        return None
    start = timeline.start_timecode
    if timeline.drop_frame and ";" not in start:  # read as drop-frame whatever its separator
        head, _sep, frames = start.rpartition(":")
        start = f"{head};{frames}"
    try:
        first = timecode_frames(start, timeline.fps)
    except ValueError:
        return None

    def tc(frame: int) -> str:
        offset = max(0, frame - timeline.start_frame)
        return frames_timecode(first + offset, timeline.fps, drop_frame=timeline.drop_frame)

    return tc(use.record_start_frame), tc(use.record_end_frame)


def track_label(use: ClipUse) -> str:
    """``V1`` or ``A2``, as Resolve names tracks in its timeline header."""
    return f"{'V' if use.track_type == 'video' else 'A'}{use.track}"


# ---------------------------------------------------------------- stored form
def use_to_json(use: ClipUse) -> dict[str, Any]:
    return {
        "track_type": use.track_type,
        "track": use.track,
        "track_name": use.track_name,
        "timeline_item_id": use.timeline_item_id,
        "media_pool_item_id": use.media_pool_item_id,
        "name": use.name,
        "enabled": use.enabled,
        "record_start_frame": use.record_start_frame,
        "record_end_frame": use.record_end_frame,
        "source_start_s": round(use.source_start_s, 4),
        "source_end_s": round(use.source_end_s, 4),
        "source_start_frame": use.source_start_frame,
        "clip_fps": use.clip_fps,
        "track_enabled": use.track_enabled,
        "nested_in": use.nested_in,
    }


def use_from_json(data: Mapping[str, Any]) -> ClipUse:
    """A stored use; tolerant of missing keys (older or hand-edited documents)."""
    return ClipUse(
        track_type=str(data.get("track_type") or "video"),
        track=_int(data.get("track"), 1),
        track_name=_str_or_none(data.get("track_name")),
        timeline_item_id=_str_or_none(data.get("timeline_item_id")),
        media_pool_item_id=_str_or_none(data.get("media_pool_item_id")),
        name=str(data.get("name") or ""),
        enabled=bool(data.get("enabled", True)),
        record_start_frame=_int(data.get("record_start_frame"), 0),
        record_end_frame=_int(data.get("record_end_frame"), 0),
        source_start_s=_float(data.get("source_start_s"), 0.0),
        source_end_s=_float(data.get("source_end_s"), 0.0),
        source_start_frame=_int_or_none(data.get("source_start_frame")),
        clip_fps=_float_or_none(data.get("clip_fps")),
        track_enabled=bool(data.get("track_enabled", True)),
        nested_in=_str_or_none(data.get("nested_in")),
    )


def timeline_to_json(timeline: TimelineInfo) -> dict[str, Any]:
    return {
        "id": timeline.id,
        "name": timeline.name,
        "fps": timeline.fps,
        "drop_frame": timeline.drop_frame,
        "start_frame": timeline.start_frame,
        "end_frame": timeline.end_frame,
        "start_timecode": timeline.start_timecode,
        "width": timeline.width,
        "height": timeline.height,
        "video_tracks": timeline.video_tracks,
        "audio_tracks": timeline.audio_tracks,
        "video_clips": timeline.video_clips,
    }


def timeline_from_json(data: Mapping[str, Any]) -> TimelineInfo:
    return TimelineInfo(
        id=str(data.get("id") or ""),
        name=str(data.get("name") or ""),
        fps=_float(data.get("fps"), 0.0),
        drop_frame=bool(data.get("drop_frame", False)),
        start_frame=_int(data.get("start_frame"), 0),
        end_frame=_int(data.get("end_frame"), 0),
        start_timecode=str(data.get("start_timecode") or "00:00:00:00"),
        width=_int_or_none(data.get("width")),
        height=_int_or_none(data.get("height")),
        video_tracks=_int(data.get("video_tracks"), 0),
        audio_tracks=_int(data.get("audio_tracks"), 0),
        video_clips=_int_or_none(data.get("video_clips")),
    )


def resolve_identity(
    product: str,
    version: str,
    database: ResolveDatabase,
    project: ResolveProjectRef,
    timeline: TimelineInfo,
) -> dict[str, Any]:
    """What a bin stores of the Resolve timeline it mirrors."""
    return {
        "schema_version": RESOLVE_SCHEMA_VERSION,
        "product": product,
        "version": version,
        "database": {"type": database.type, "name": database.name},
        "project": {"id": project.id, "name": project.name},
        "timeline": timeline_to_json(timeline),
    }


def identity_parts(
    data: Mapping[str, Any],
) -> tuple[ResolveDatabase, ResolveProjectRef, TimelineInfo]:
    """The database, project and timeline of a stored identity (see ``resolve_identity``)."""
    database = data.get("database") or {}
    project = data.get("project") or {}
    return (
        ResolveDatabase(type=str(database.get("type") or ""), name=str(database.get("name") or "")),
        ResolveProjectRef(id=str(project.get("id") or ""), name=str(project.get("name") or "")),
        timeline_from_json(data.get("timeline") or {}),
    )


SNAPSHOT_NOTE = "positions relevées à cette date ; la timeline a pu changer depuis"


def link_to_json(link: ResolveLink) -> dict[str, Any]:
    """A video's link as the analysis file and the JSON export keep it: no file path, no id of
    the application's own."""
    timeline = link.timeline
    return {
        "database": {"type": link.database.type, "name": link.database.name},
        "project": {"id": link.project.id, "name": link.project.name},
        "timeline": {
            "id": timeline.id,
            "name": timeline.name,
            "fps": timeline.fps,
            "drop_frame": timeline.drop_frame,
            "start_timecode": timeline.start_timecode,
        },
        "label": link.bin_label,
        "synced_at": link.synced_at.isoformat(),
        "note": SNAPSHOT_NOTE,
        "uses": [use_to_json(use) for use in link.uses],
    }


def source_key(project_id: str, timeline_id: str) -> str:
    """What makes a timeline bin unique: the timeline, in its project."""
    return f"{project_id}/{timeline_id}"


def _int(value: Any, default: int) -> int:
    result = _int_or_none(value)
    return default if result is None else result


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None or isinstance(value, bool) else int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float) -> float:
    result = _float_or_none(value)
    return default if result is None else result


def _float_or_none(value: Any) -> float | None:
    try:
        result = None if value is None or isinstance(value, bool) else float(value)
    except (TypeError, ValueError):
        return None
    return result if result is not None and math.isfinite(result) else None


def _str_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
