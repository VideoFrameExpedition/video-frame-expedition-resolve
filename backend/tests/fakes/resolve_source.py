"""A fake DaVinci Resolve for the timeline bins: the open project and its timelines,
as ``ports.resolve.ResolveSource`` gives them (no child process, no Resolve)."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from vfe_vision.core.errors import NotFoundError, VfeError
from vfe_vision.domain.resolve_timeline import (
    ClipKind,
    ClipUse,
    ResolveDatabase,
    ResolveProjectInfo,
    ResolveProjectRef,
    TimelineClip,
    TimelineContent,
    TimelineInfo,
)

DATABASE = ResolveDatabase(type="Disk", name="Local Database")
PROJECT = ResolveProjectRef(id="prj-cats-2026", name="cats 2026")
FPS = 25.0
START = 90000  # 01:00:00:00 at 25 fps


def timeline(
    timeline_id: str, name: str, *, end_frame: int = START + 2500, current: bool = False
) -> TimelineInfo:
    return TimelineInfo(
        id=timeline_id,
        name=name,
        fps=FPS,
        drop_frame=False,
        start_frame=START,
        end_frame=end_frame,
        start_timecode="01:00:00:00",
        width=1920,
        height=1080,
        video_tracks=1,
        audio_tracks=1,
        is_current=current,
    )


def clip(
    path: str | None,
    start_s: float,
    end_s: float,
    *,
    track: int = 1,
    track_type: str = "video",
    kind: str = ClipKind.FILE,
    clip_type: str | None = None,
    enabled: bool = True,
    vfe_video_id: str | None = None,
    source: tuple[float, float] = (0.0, 5.0),
) -> TimelineClip:
    """A clip ``start_s``–``end_s`` seconds after the start of the timeline."""
    name = Path(path).name if path else "Texte"
    start, end = START + round(start_s * FPS), START + round(end_s * FPS)
    return TimelineClip(
        file_path=path,
        clip_type=clip_type or ("Video + Audio" if path else None),
        use=ClipUse(
            track_type=track_type,
            track=track,
            track_name=None,
            timeline_item_id=f"item-{track_type}-{start}",
            media_pool_item_id=f"pool-{re.sub('[^0-9A-Za-z]+', '-', name)}" if path else None,
            name=name,
            enabled=enabled,
            record_start_frame=start,
            record_end_frame=end,
            source_start_s=source[0],
            source_end_s=source[1],
            source_start_frame=round(source[0] * FPS),
            clip_fps=FPS,
        ),
        kind=kind,
        vfe_video_id=vfe_video_id,
    )


class FakeResolve:
    """The project open in Resolve: timelines are added, changed or removed by the tests;
    ``error`` is raised by the next read (Resolve not running…)."""

    def __init__(self, project: ResolveProjectRef = PROJECT) -> None:
        self.project_ref = project
        self.database = DATABASE
        self.timelines: dict[str, tuple[TimelineInfo, list[TimelineClip]]] = {}
        self.current: str | None = None
        self.timeline_reads: list[str | None] = []
        self.project_reads = 0
        self.error: VfeError | None = None

    def put(self, info: TimelineInfo, clips: list[TimelineClip]) -> TimelineInfo:
        self.timelines[info.id] = (info, clips)
        if info.is_current or self.current is None:
            self.current = info.id
        return info

    def open_project(self, project: ResolveProjectRef) -> None:
        """Another project opened in Resolve: its timelines replace the others."""
        self.project_ref = project
        self.timelines = {}
        self.current = None

    def project(self) -> ResolveProjectInfo:
        self.project_reads += 1
        self._fail()
        return ResolveProjectInfo(
            product="DaVinci Resolve Studio",
            version="21.1.0.17",
            database=self.database,
            project=self.project_ref,
            current_timeline_id=self.current,
            timelines=tuple(
                replace(info, is_current=info.id == self.current, video_clips=len(clips))
                for info, clips in self.timelines.values()
            ),
        )

    def timeline(self, timeline_id: str | None) -> TimelineContent:
        self.timeline_reads.append(timeline_id)
        self._fail()
        found = self.timelines.get(timeline_id or self.current or "")
        if found is None:
            raise NotFoundError(
                "Cette timeline n'est plus dans le projet ouvert dans DaVinci Resolve."
            )
        info, clips = found
        return TimelineContent(
            product="DaVinci Resolve Studio",
            version="21.1.0.17",
            database=self.database,
            project=self.project_ref,
            timeline=replace(info, is_current=info.id == self.current),
            clips=tuple(clips),
        )

    def _fail(self) -> None:
        if self.error is not None:
            raise self.error
