"""MCP tools for the DaVinci Resolve timelines of the library: which timelines the
open project has and which are in the library, and adding (or updating) one.

The application reads Resolve itself (never writes to it). Adding the files of a timeline that
no folder of the library holds obeys the same rule as ``analyze_folder``: only when the user
allowed Claude to add folders (System page), since footage can carry instructions.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, Field

from vfe_vision.core.errors import VfeError
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.resolve_timeline import SkippedItems, identity_parts
from vfe_vision.domain.transcript import clean_untrusted
from vfe_vision.mcp.resolve_refs import ResolveId
from vfe_vision.services import timeline_bins
from vfe_vision.services.container import AppContainer

PROBLEMS_SHOWN = 20
PROBLEM_STATES = ("missing", "outside", "error", "removed", "not_processed")
OUTSIDE_HINT = (
    "Des vidéos de cette timeline ne sont dans aucun dossier de la bibliothèque et Claude n'est "
    "pas autorisé à en ajouter : l'utilisateur peut importer la timeline depuis l'application "
    "(bouton « Importer depuis Resolve ») ou autoriser Claude à ajouter des dossiers (page "
    "Système)."
)


def _name(value: str, limit: int = 80) -> str:
    """A name typed in Resolve: data, one line, bounded."""
    return clean_untrusted(value)[:limit]


# ---------------------------------------------------------------- outputs
class ProjectRef(BaseModel):
    id: str
    name: str = Field(description="Typed in Resolve (data).")


class TimelineProblem(BaseModel):
    path: str
    state: str = Field(description="missing | outside | error | removed | not_processed")
    note: str | None = None


class TimelineEntry(BaseModel):
    timeline_id: str
    name: str = Field(description="Typed in Resolve (data).")
    is_current: bool
    fps: float
    start_timecode: str
    duration_s: float
    video_clips: int | None = Field(default=None, description="Items on the video tracks.")
    in_library: bool = Field(description="Added to the library (a vfe-vision timeline bin).")
    bin_id: str | None = None
    label: str | None = None
    read_at: str | None = Field(default=None, description="When the library last read it.")
    changed_since_sync: bool | None = Field(
        default=None, description="It looks different from that read (name, length, clips)."
    )
    videos: int | None = Field(default=None, description="Videos of the library it uses.")
    states: dict[str, int] = Field(
        default_factory=dict,
        description="Its files: in_library, adding, not_processed, removed, missing, outside, "
        "error.",
    )
    problems: list[TimelineProblem] = Field(default_factory=list)


class ResolveProjectResult(BaseModel):
    product: str
    version: str
    studio: bool
    database: str
    project: ProjectRef
    current_timeline_id: str | None
    timelines: list[TimelineEntry]


class SkippedInfo(BaseModel):
    graphics: int = Field(description="Titles, generators, Fusion compositions.")
    containers: int = Field(description="Compound or multicam clips (not read inside).")
    not_video: int = Field(description="Audio files and stills.")
    unsupported: int = Field(description="Camera formats the library cannot read.")
    elsewhere: int = Field(description="Paths of another computer.")


class ImportResult(BaseModel):
    bin_id: str
    label: str
    project: ProjectRef
    timeline_id: str
    timeline: str
    created: bool = Field(description="False: the timeline was already in the library, updated.")
    job_id: str = Field(description="Registers the files and asks for analyses (get_job).")
    files: int
    in_library: int
    other_path: int = Field(description="Same file known under another path (linked).")
    in_folder: int = Field(description="In a folder of the library, added now.")
    new_folder: int = Field(description="In no folder of the library.")
    new_folders_allowed: bool = Field(
        description="Whether those files are added (alone, never the rest of their folder)."
    )
    missing: int
    unknown: int = Field(description="On a network share out of reach during the read.")
    to_analyze: int
    analyze: bool
    skipped: SkippedInfo
    note: str | None = None


def _skipped(skipped: SkippedItems) -> SkippedInfo:
    return SkippedInfo(
        graphics=skipped.graphics, containers=skipped.containers, not_video=skipped.not_video,
        unsupported=skipped.unsupported, elsewhere=skipped.elsewhere,
    )  # fmt: skip


def _result(model: BaseModel, lines: list[str]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structured_content=model.model_dump(mode="json", exclude_none=True),
    )


# ---------------------------------------------------------------- registration
def register_resolve_tools(mcp: MCPServer, container: Callable[[], AppContainer]) -> None:
    """Add the tools about the Resolve timelines of the library."""

    @mcp.tool()
    def list_resolve_timelines() -> Annotated[CallToolResult, ResolveProjectResult]:
        """The project open in DaVinci Resolve and its timelines, read by the application itself
        (reading only; Resolve Studio with the preference « External scripting using: Local »).

        For each timeline: whether it is in the library (a vfe-vision timeline bin, not a Resolve
        media pool bin), when the library read it, whether it looks changed since
        (changed_since_sync), how many videos of the library it uses and the state of its files
        (missing, outside the library…). Use its timeline_id with list_watched, find_clips,
        search_memory and ask_library; bring it in or refresh it with import_resolve_timeline.
        An error with reason scripting_off is a Resolve preference only the user can change.
        """
        c = container()
        try:
            view = timeline_bins.project_view(c)
            in_library = [t.bin.id for t in view.timelines if t.bin is not None]
            stats = {s.bin.id: s for s in timeline_bins.list_bins(c, in_library)}
            problems = {bin_id: _problems(c, bin_id) for bin_id in in_library}
        except VfeError as exc:
            raise ToolError(exc.detail) from exc
        info = view.info
        entries = []
        for item in view.timelines:
            timeline, found = item.info, item.bin
            bin_stats = stats.get(found.id) if found is not None else None
            entries.append(
                TimelineEntry(
                    timeline_id=timeline.id, name=_name(timeline.name),
                    is_current=timeline.is_current, fps=timeline.fps,
                    start_timecode=timeline.start_timecode,
                    duration_s=round(timeline.duration_s, 3), video_clips=timeline.video_clips,
                    in_library=found is not None, bin_id=found.id if found else None,
                    label=_name(found.label) if found else None,
                    read_at=found.synced_at.isoformat() if found else None,
                    changed_since_sync=item.changed_since_sync,
                    videos=bin_stats.videos if bin_stats else None,
                    states=dict(bin_stats.states) if bin_stats else {},
                    problems=problems.get(found.id, []) if found else [],
                )
            )  # fmt: skip
        result = ResolveProjectResult(
            product=info.product, version=info.version, studio=info.studio,
            database=_name(info.database.name, 60),
            project=ProjectRef(id=info.project.id, name=_name(info.project.name)),
            current_timeline_id=info.current_timeline_id, timelines=entries,
        )  # fmt: skip
        lines = [
            (
                f"Projet « {result.project.name} » ({info.product} {info.version}, base "
                f"{result.database}) : {len(entries)} timelines."
            )
        ]
        for entry in entries:
            current = " (actuelle)" if entry.is_current else ""
            where = "hors bibliothèque"
            if entry.in_library:
                changed = ", modifiée depuis" if entry.changed_since_sync else ""
                where = (
                    f"dans la bibliothèque ({entry.videos} vidéos, lue le {entry.read_at}{changed})"
                )
            clips = entry.video_clips
            count = "? plans" if clips is None else f"{clips} plan{'s' if clips > 1 else ''}"
            lines.append(
                f"- « {entry.name} »{current} [timeline_id {entry.timeline_id}] — {count}, "
                f"{entry.duration_s:.0f} s, {where}"
            )
        return _result(result, lines)

    @mcp.tool()
    def import_resolve_timeline(
        timeline_id: ResolveId | None = None,
        analyze: bool | None = None,
        label: Annotated[str | None, Field(max_length=200)] = None,
    ) -> Annotated[CallToolResult, ImportResult]:
        """Add a timeline of the project open in DaVinci Resolve to the library, or update it.

        ASK THE USER FIRST: it writes the application's library and may queue analyses. Never
        bring in your own « … - vfe vN » copies unless asked. The timeline's videos are linked
        (a video known under another path too); the ones in a folder of the library are added;
        the ones in no folder are added alone (never the rest of their folder) only if the user
        allowed Claude to add folders (System page), else reported outside. It returns once
        Resolve is read: a job registers the files and asks for what is missing of the analyses
        (follow it with get_job). Each video then carries its link to the project and timeline
        (get_video, list_watched).

        Args:
            timeline_id: The timeline's unique id (GetUniqueId(), list_resolve_timelines);
                None = the timeline open in Resolve.
            analyze: Ask for the missing analyses; None keeps the timeline's own setting (on
                for a timeline new to the library).
            label: Name in the library (default: the timeline's name).
        """
        c = container()
        allowed = load_preferences(c.db).mcp_add_folders
        try:
            seen = timeline_bins.preview(c, project_id=None, timeline_id=timeline_id)
            wanted = analyze
            if wanted is None:
                existing = timeline_bins.list_bins(c, [seen.bin_id]) if seen.bin_id else []
                wanted = existing[0].bin.auto_analyze if existing else True
            done = timeline_bins.import_timeline(
                c, project_id=seen.content.project.id, timeline_id=seen.content.timeline.id,
                snapshot_id=seen.snapshot_id, label=label, auto_analyze=wanted,
                allow_new_folders=allowed,
            )  # fmt: skip
        except VfeError as exc:
            raise ToolError(exc.detail) from exc
        shown, bin_row = done.preview, done.stats.bin
        _database, project, timeline = identity_parts(bin_row.resolve)
        note = OUTSIDE_HINT if shown.new_folder and not allowed else None
        result = ImportResult(
            bin_id=bin_row.id, label=_name(bin_row.label),
            project=ProjectRef(id=project.id, name=_name(project.name)),
            timeline_id=timeline.id, timeline=_name(timeline.name), created=done.created,
            job_id=done.job.id, files=shown.files, in_library=shown.in_library,
            other_path=shown.other_path, in_folder=shown.in_folder, new_folder=shown.new_folder,
            new_folders_allowed=allowed, missing=shown.missing, unknown=shown.unknown,
            to_analyze=shown.to_analyze if wanted else 0, analyze=wanted,
            skipped=_skipped(shown.skipped), note=note,
        )  # fmt: skip
        verb = "ajoutée à" if done.created else "mise à jour dans"
        alone = "ajoutées seules" if allowed else "hors bibliothèque"
        lines = [
            (
                f"Timeline « {result.timeline} » (projet « {result.project.name} ») {verb} la "
                f"bibliothèque [timeline_id {result.timeline_id}, bin {result.bin_id}] : "
                f"{result.files} vidéos, {result.in_library} déjà dans la bibliothèque, "
                f"{result.in_folder + result.other_path} reliées ou ajoutées depuis un dossier, "
                f"{result.new_folder} dans aucun dossier ({alone}), {result.missing} introuvables."
            ),
            (
                f"Job {result.job_id} (suivre avec get_job) ; analyses demandées : "
                f"{result.to_analyze if wanted else 'aucune'}."
            ),
        ]
        if note:
            lines.append(note)
        return _result(result, lines)


def _problems(c: AppContainer, bin_id: str) -> list[TimelineProblem]:
    """The files of a timeline that are not (or no longer) videos of the library."""
    found = [
        TimelineProblem(
            path=clean_untrusted(item.path)[:300], state=item.state,
            note=clean_untrusted(item.note)[:200] if item.note else None,
        )
        for item in timeline_bins.bin_items(c, bin_id)
        if item.state in PROBLEM_STATES
    ]  # fmt: skip
    return found[:PROBLEMS_SHOWN]
