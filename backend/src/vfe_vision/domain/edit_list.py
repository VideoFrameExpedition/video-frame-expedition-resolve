"""An edit list for DaVinci Resolve: the clips of a new timeline, in order, with their
ranges in seconds of the file and their Transform values, and what Resolve made of it.

The timeline's name ends with « - vfe vN »: the assistant's timelines are told apart from the
user's at a glance, and a new version never replaces an older one.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from vfe_vision.domain.resolve_timeline import ResolveProjectRef

TRANSFORM_KEYS = ("ZoomX", "ZoomY", "Pan", "Tilt", "RotationAngle")
DEFAULT_FOLDER = "Video Frame Expedition"  # the bin of « Create a timeline »
_VERSIONED = re.compile(r"^(?P<base>.*?) - vfe v(?P<n>\d+)(?: \(\d+\))?$")


@dataclass(frozen=True, slots=True)
class EditItem:
    in_s: float
    out_s: float
    path: str | None = None  # as Resolve sees the file
    media_pool_item_id: str | None = None
    video_only: bool = False
    props: Mapping[str, float] | None = None  # ZoomX, ZoomY, Pan, Tilt, RotationAngle
    video_id: str | None = None


@dataclass(frozen=True, slots=True)
class EditRequest:
    name: str
    items: list[EditItem]
    width: int | None = None
    height: int | None = None
    folder: str = DEFAULT_FOLDER


@dataclass(frozen=True, slots=True)
class PlacedItem:
    n: int  # 1-based, in the edit list
    timeline_item_id: str
    record_start: int
    record_end: int
    duration_ok: bool
    props_ok: bool | None  # None: no Transform asked


@dataclass(frozen=True, slots=True)
class SkippedItem:
    n: int
    why: str  # not_in_pool, no_fps, empty_range


@dataclass(frozen=True, slots=True)
class EditResult:
    project: ResolveProjectRef
    timeline_id: str
    timeline_name: str
    fps: float | None
    width: int | None
    height: int | None
    start_frame: int
    end_frame: int
    items: list[PlacedItem]
    skipped: list[SkippedItem] = field(default_factory=list)
    not_placed: int = 0
    imported: int = 0
    audio_items: int = 0
    scaling: str = ""

    @property
    def duration_s(self) -> float | None:
        if not self.fps:
            return None
        return (self.end_frame - self.start_frame) / self.fps


def base_name(name: str) -> str:
    """The name without its « - vfe vN » ending."""
    found = _VERSIONED.match(name.strip())
    return (found.group("base") if found else name).strip()


def versioned_name(wanted: str, existing: Iterable[str]) -> str:
    """« wanted - vfe vN »: N one more than the highest version of that name in the project."""
    base = base_name(wanted) or "Montage"
    versions = [
        int(found.group("n"))
        for name in existing
        if (found := _VERSIONED.match(name.strip())) and found.group("base").strip() == base
    ]
    return f"{base} - vfe v{max(versions, default=0) + 1}"


def clean_props(props: Mapping[str, float] | None) -> dict[str, float] | None:
    """The Transform values kept (known keys, finite numbers), ZoomY following ZoomX."""
    if not props:
        return None
    kept = {
        k: float(v) for k, v in props.items() if k in TRANSFORM_KEYS and math.isfinite(float(v))
    }
    if "ZoomX" in kept and "ZoomY" not in kept:
        kept["ZoomY"] = kept["ZoomX"]
    return kept or None
