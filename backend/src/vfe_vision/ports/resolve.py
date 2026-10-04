"""Ports: reading the project open in DaVinci Resolve, and building a timeline in it
when the user asks.

The implementations run short-lived child processes with Resolve's scripting library
(adapters/resolve/reader.py, adapters/resolve/builder.py); tests inject fakes. The reader never
changes anything in Resolve; the builder only adds (files to the media pool, a new timeline).
"""

from __future__ import annotations

from typing import Any, Protocol

from vfe_vision.domain.edit_list import EditRequest, EditResult
from vfe_vision.domain.resolve_timeline import ResolveProjectInfo, TimelineContent
from vfe_vision.domain.timeline_build import BuiltTimeline, TimelineRequest


class ResolveSource(Protocol):
    def project(self) -> ResolveProjectInfo:
        """The open project and its timelines (no clip is read)."""
        ...

    def timeline(self, timeline_id: str | None) -> TimelineContent:
        """One timeline of the open project and every clip of its tracks (None: the current
        timeline)."""
        ...


class ResolveTimelineMaker(Protocol):
    def build_timeline(self, request: TimelineRequest) -> BuiltTimeline:
        """A new timeline of the request's clips in the open project, made the current one."""
        ...


class ResolveTimelineEditor(Protocol):
    """The assistant's Resolve tools: new timelines and markers, fixed scripts only."""

    def build_edit(self, request: EditRequest) -> EditResult:
        """A NEW timeline of the edit list (ranges, Transform values), made the current one."""
        ...

    def apply_markers(self, script: str) -> dict[str, Any]:
        """Run the application's fixed markers script; its report."""
        ...
