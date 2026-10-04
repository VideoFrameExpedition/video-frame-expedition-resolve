"""DaVinci Resolve: the open project, its timelines, and bringing one into the library;
and « Create a timeline », the one change made in Resolve, when the user asks.

Reading Resolve changes nothing in it. Reading and the timeline are allowed to the devices given
access with the token, like the rest of the API: a read shows only the open project's
names and paths.
A read takes up to a few seconds (one at a time: 503 ``busy`` meanwhile); every error says in
French what to do (``reason``: not_installed, not_running, scripting_off, starting,
not_responding, no_project, no_timeline, busy).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from vfe_vision.api.deps import Container, DisplayLanguage
from vfe_vision.api.schemas import (
    RESOLVE_ID_PATTERN,
    ResolveProjectOut,
    ResolveTimelineBuiltOut,
    TimelineBuildRequest,
    TimelineImportOut,
    TimelineImportRequest,
    TimelinePreviewOut,
)
from vfe_vision.services import timeline_bins, timeline_build

router = APIRouter(prefix="/resolve", tags=["resolve"])


@router.get("/project")
def get_project(c: Container) -> ResolveProjectOut:
    """The project open in DaVinci Resolve and its timelines (the current one first), each with
    its timeline in the library when it is already there."""
    return ResolveProjectOut.of(timeline_bins.project_view(c))


@router.get("/timelines/{timeline_id}/preview")
def preview_timeline(
    c: Container,
    timeline_id: Annotated[str, Path(max_length=64, pattern=RESOLVE_ID_PATTERN)],
    project_id: Annotated[str | None, Query(max_length=64)] = None,
) -> TimelinePreviewOut:
    """Read a timeline and say what adding it would do, without writing anything: videos
    already in the library, to add, not found, items left out. 409 if the open project is no
    longer ``project_id``."""
    return TimelinePreviewOut.of(
        timeline_bins.preview(c, project_id=project_id, timeline_id=timeline_id)
    )


@router.post("/timelines/import", status_code=status.HTTP_201_CREATED)
def import_timeline(c: Container, body: TimelineImportRequest) -> TimelineImportOut:
    """Add the timeline to the library (or update it if it is already there): its videos come
    in, alone when their folder is not there. Answers as soon as the read is done; the update
    (``job``) examines the files and asks for the missing analyses."""
    result = timeline_bins.import_timeline(
        c, project_id=body.project_id, timeline_id=body.timeline_id,
        snapshot_id=body.snapshot_id, label=body.label, auto_analyze=body.auto_analyze,
        allow_new_folders=True,
    )  # fmt: skip
    return TimelineImportOut.of(result)


@router.post("/timelines", status_code=status.HTTP_201_CREATED)
def create_timeline(
    c: Container, language: DisplayLanguage, body: TimelineBuildRequest
) -> ResolveTimelineBuiltOut:
    """Create the timeline of the chosen videos in the project open in DaVinci Resolve and open
    it there: the whole videos, end to end, picture and sound; the files the media pool does
    not have yet are imported into the « Video Frame Expedition » bin, with the timeline.
    Resolve keeps the project's frame rate if it refuses another one (``fps`` gives the
    timeline's).
    Takes up to a few minutes for many files; 503 if Resolve cannot be reached (``reason`` as
    for the read)."""
    plan = body.plan(c, language)
    return ResolveTimelineBuiltOut.of(timeline_build.build_in_resolve(c, plan), plan)
