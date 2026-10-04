"""Enumerations shared by the domain, persistence and API layers."""

from __future__ import annotations

from enum import StrEnum


class VideoStatus(StrEnum):
    NEW = "new"  # discovered, never analysed
    QUEUED = "queued"
    ANALYZING = "analyzing"
    READY = "ready"  # every enabled stage succeeded
    PARTIAL = "partial"  # some optional stages failed or were skipped
    FAILED = "failed"
    OFFLINE = "offline"  # file no longer found at its path


class JobKind(StrEnum):
    ANALYZE_VIDEO = "analyze_video"
    SCAN_ROOT = "scan_root"
    PROBE_VISION = "probe_vision"  # measure how the loaded vision model writes boxes
    SYNC_TIMELINE = "sync_timeline"  # bring the videos of a Resolve timeline in
    RELINK_VIDEOS = "relink_videos"  # find offline videos' files in a chosen folder
    BENCH_MODELS = "bench_models"  # test vision models one after the other


class RootKind(StrEnum):
    """What a library root covers."""

    FOLDER = "folder"  # its folder and, unless told otherwise, its sub-folders
    FILES = "files"  # only the files it lists, directly in its folder (from a Resolve timeline)


class TimelineItemState(StrEnum):
    """What the last update of a timeline bin made of one of its files."""

    PENDING = "pending"  # read from Resolve, not looked at by the update job yet
    LINKED = "linked"  # a video of the library
    MISSING = "missing"  # no file at that path
    OUTSIDE = "outside"  # in no library folder, and adding folders was not allowed
    ERROR = "error"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }


class AnalysisMode(StrEnum):
    """How much of an existing analysis a new request redoes."""

    # Keep every usable result; only run what is missing, failed, degraded, or whose video data
    # changed (file, capture time, position, folder corrections).
    COMPLETE = "complete"
    # Also redo results made with other settings, another model or an older app version.
    UPDATE = "update"
    FULL = "full"  # redo everything


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    CACHED = "cached"  # up to date: identical cache key, not re-run
    SKIPPED = "skipped"  # not applicable (no audio, offline mode, LM Studio down…)
    FAILED = "failed"
    CANCELLED = "cancelled"


class Orientation(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    SQUARE = "square"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
