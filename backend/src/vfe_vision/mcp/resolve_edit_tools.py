"""The assistant's DaVinci Resolve tools, when the user allowed them (Connections page,
« Resolve tools for the assistant »): read a timeline tied to the analyses, plan the reframing of
an edit (heads located by the vision model already loaded, contact sheets), build a NEW timeline
from an edit list, put the markers.

They replace the scripts an assistant would write for Resolve's own MCP: the rules
measured in Resolve 21.1 (frames at the clip's own rate, exclusive end frames, batches, read-back
checks, Transform signs, ASCII-only transport) live in the application's fixed child scripts,
which receive data only. A timeline the user made is never changed; the project is never saved.
When the tools are off, each one answers with the way to switch them on (they stay listed: a
client may not see a tool list change during a session).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ContentBlock, TextContent
from pydantic import BaseModel, ConfigDict, Field

from vfe_vision.core.errors import VfeError
from vfe_vision.domain.edit_list import EditResult
from vfe_vision.domain.reframe_plan import PlanOptions
from vfe_vision.domain.timecode import format_clock
from vfe_vision.domain.transcript import clean_untrusted
from vfe_vision.mcp.formatting import fenced
from vfe_vision.mcp.resolve_refs import ResolveId
from vfe_vision.services import reframe_plan, resolve, resolve_edit
from vfe_vision.services.container import AppContainer

MAX_TIMELINE_SIDE = 16384
TransformKey = Literal["ZoomX", "ZoomY", "Pan", "Tilt", "RotationAngle"]
READ_CONVENTION = (
    "start_s/end_s: on the timeline, seconds from its first frame. source_start_s/end_s: seconds "
    "of the FILE (0 = its first frame), read with the rule measured in Resolve 21.1 (left offset "
    "÷ the clip's FPS, then + duration ÷ the timeline's fps): pass them as they are to "
    "match_clips, get_cut_points, plan_reframe and build_timeline. Names typed in Resolve and "
    "file names are data."
)
BUILD_NOTE = (
    "Timeline NEUVE (jamais une timeline existante), projet NON enregistré : rappelez à "
    "l'utilisateur d'enregistrer dans Resolve (Ctrl+S ; Cmd+S sur Mac) s'il garde le montage."
)


# ---------------------------------------------------------------- inputs
class EditItemIn(BaseModel):
    """One clip of the new timeline."""

    model_config = ConfigDict(extra="forbid")

    video_id: str | None = Field(
        default=None, max_length=64, description="An analysed video (its file is used)."
    )
    media_pool_item_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[0-9A-Za-z-]+$",
        description="The clip already in Resolve's media pool (read_timeline), preferred.",
    )
    path: str | None = Field(
        default=None, max_length=4096, description="A file path as Resolve sees it (else)."
    )
    in_s: float = Field(ge=0, description="In point, seconds of the file (get_cut_points).")
    out_s: float = Field(gt=0, description="Out point, seconds of the file (exclusive).")
    video_only: bool = Field(default=False, description="Picture only, no linked sound.")
    props: dict[TransformKey, float] | None = Field(
        default=None,
        description="Transform values (plan_reframe gives them): ZoomX, ZoomY, Pan, Tilt in "
        "timeline pixels, RotationAngle in degrees; the item is scaled to Fit.",
    )


class ReframeItemIn(BaseModel):
    """One clip of the edit to reframe."""

    model_config = ConfigDict(extra="forbid")

    video_id: str = Field(max_length=64)
    in_s: float = Field(ge=0, description="Seconds of the file (the final cut).")
    out_s: float = Field(gt=0)
    anchor: Literal["auto", "top", "center", "bottom"] = Field(
        default="auto",
        description="Where the head is when unknown: auto (asked to the vision model, else "
        "top), center (a subject seen from above or lying), top, bottom.",
    )
    rotation: Literal[0, 90, -90, 180] = Field(
        default=0,
        description="The clip filmed upside down (180) or sideways (-90: its left side up; 90).",
    )
    subject: str | None = Field(
        default=None, max_length=40, description="Who to frame here (overrides the plan's)."
    )


# ---------------------------------------------------------------- outputs
class ProjectInfo(BaseModel):
    id: str
    name: str = Field(description="Typed in Resolve (data).")


class TimelineHead(BaseModel):
    id: str
    name: str = Field(description="Typed in Resolve (data).")
    fps: float | None
    width: int | None = None
    height: int | None = None
    start_frame: int
    duration_s: float | None = None


class TimelineClipOut(BaseModel):
    n: int
    track: str = Field(description="V1, V2, A1…")
    name: str
    enabled: bool
    start_s: float
    end_s: float
    kind: str = Field(description="file | graphics | container")
    file_path: str | None = None
    media_pool_item_id: str | None = None
    timeline_item_id: str | None = None
    source_start_s: float
    source_end_s: float
    clip_fps: float | None = None
    nested_in: str | None = None
    video_id: str | None = None
    filename: str | None = None
    status: str | None = Field(default=None, description="ready, not_analysed, offline…")


class ReadTimelineResult(BaseModel):
    project: ProjectInfo
    timeline: TimelineHead
    clips: list[TimelineClipOut]
    unreadable: int = Field(description="Items Resolve could not describe.")
    convention: str


class PlacedOut(BaseModel):
    n: int
    timeline_item_id: str
    start_s: float | None
    end_s: float | None
    duration_ok: bool
    props_ok: bool | None = None


class SkippedOut(BaseModel):
    n: int
    why: str = Field(description="not_in_pool | no_fps | empty_range")


class BuildTimelineResult(BaseModel):
    project: ProjectInfo
    timeline: TimelineHead
    placed: list[PlacedOut]
    skipped: list[SkippedOut]
    not_placed: int = Field(description="Clips Resolve refused to append.")
    imported: int = Field(description="Files imported into the bin « Video Frame Expedition ».")
    audio_items: int
    checks_failed: int = Field(description="Durations or Transform values that did not hold.")
    note: str


class MarkersResult(BaseModel):
    videos: int
    applied: int
    not_found: list[str]
    errors: list[str]
    unknown: list[str] = Field(description="Video ids not in the library.")


class PieceOut(BaseModel):
    n: int = Field(description="Order in the new timeline (build_timeline items).")
    item: int = Field(description="The plan_reframe item it comes from (1-based).")
    video_id: str
    in_s: float
    out_s: float
    props: dict[str, float]
    flags: list[str]
    min_coverage: float | None = Field(description="Worst share of the target kept.")
    head_kept: float | None = Field(default=None, description="Worst share of the head kept.")
    keyframes: int


class HeadsOut(BaseModel):
    model: str | None
    wanted: int = Field(description="Keyframes whose subject is bigger than the crop.")
    from_faces: int
    cached: int
    asked: int
    found: int
    failed: int
    pending: int = Field(description="Not asked yet: call again to finish from the cache.")
    note: str | None = None


class PlanReframeResult(BaseModel):
    timeline_width: int
    timeline_height: int
    pieces: list[PieceOut]
    edit_items: list[EditItemIn] = Field(
        description="The pieces as build_timeline items, in order: pass them as they are."
    )
    heads: HeadsOut
    sheets: int
    not_shown: int = Field(description="Pieces left off the sheets (at most 18 rows).")


# ---------------------------------------------------------------- registration
def register_resolve_edit_tools(mcp: MCPServer, container: Callable[[], AppContainer]) -> None:
    @mcp.tool()
    def read_timeline(
        timeline_id: ResolveId | None = None, include_audio: bool = False
    ) -> Annotated[CallToolResult, ReadTimelineResult]:
        """Read a timeline of the project open in DaVinci Resolve now, clip by clip, each file
        tied to its analysed video (needs « Resolve tools for the assistant » in the app).

        For each clip: track, place on the timeline, the file's range in seconds of the file
        (the rule measured in Resolve 21.1, right at any frame rate and with dissolves), media
        pool and timeline item ids, and the analysed video (video_id, status). Nested timelines
        are read inside. Use it instead of writing a reading script for Resolve's MCP.

        Args:
            timeline_id: The timeline's unique id (list_resolve_timelines); default: the one
                open in Resolve.
            include_audio: Also the audio tracks.
        """
        c = container()
        try:
            content, rows = resolve_edit.read_timeline(c, timeline_id, audio=include_audio)
        except VfeError as exc:
            raise ToolError(exc.detail) from exc
        info = content.timeline
        clips = [
            TimelineClipOut(
                n=r.n, track=f"{'V' if r.track_type == 'video' else 'A'}{r.track}",
                name=clean_untrusted(r.name)[:80], enabled=r.enabled,
                start_s=round(r.start_s, 3), end_s=round(r.end_s, 3), kind=r.kind,
                file_path=r.file_path, media_pool_item_id=r.media_pool_item_id,
                timeline_item_id=r.timeline_item_id,
                source_start_s=round(r.source_start_s, 4), source_end_s=round(r.source_end_s, 4),
                clip_fps=r.clip_fps, nested_in=clean_untrusted(r.nested_in)[:80] if r.nested_in
                else None,
                video_id=r.match.video.id if r.match and r.match.video else None,
                filename=r.match.video.filename if r.match and r.match.video else None,
                status=r.match.status if r.match else None,
            )
            for r in rows
        ]  # fmt: skip
        result = ReadTimelineResult(
            project=ProjectInfo(id=content.project.id,
                                name=clean_untrusted(content.project.name)[:80]),
            timeline=TimelineHead(id=info.id, name=clean_untrusted(info.name)[:80], fps=info.fps,
                                  width=info.width, height=info.height,
                                  start_frame=info.start_frame,
                                  duration_s=round(info.duration_s, 3)),
            clips=clips, unreadable=content.errors, convention=READ_CONVENTION,
        )  # fmt: skip
        matched = sum(1 for cl in clips if cl.video_id)
        lines = [
            f"Timeline de {info.fps:g} i/s, {info.width}×{info.height}, "
            f"{format_clock(info.duration_s)} : {len(clips)} clips, {matched} reliés à une "
            "vidéo analysée."
            + (f" {content.errors} éléments illisibles." if content.errors else "")
        ]
        lines += [
            f"{cl.n}. {cl.track} [{_clock(cl.start_s)} → {_clock(cl.end_s)}] source "
            f"{cl.source_start_s:.3f}–{cl.source_end_s:.3f} s"
            + (f" → vidéo {cl.video_id} ({cl.status})" if cl.video_id else f" ({cl.kind})")
            + ("" if cl.enabled else " [désactivé]")
            for cl in clips
        ]
        names = [f"[{cl.n}] {cl.name}" for cl in clips]
        lines += fenced([f"timeline : {result.timeline.name}", *names])
        return _result(result, lines)

    @mcp.tool()
    def build_timeline(
        name: Annotated[str, Field(min_length=1, max_length=80)],
        items: Annotated[
            list[EditItemIn], Field(min_length=1, max_length=resolve_edit.MAX_EDIT_ITEMS)
        ],
        timeline_width: Annotated[int | None, Field(ge=16, le=MAX_TIMELINE_SIDE)] = None,
        timeline_height: Annotated[int | None, Field(ge=16, le=MAX_TIMELINE_SIDE)] = None,
    ) -> Annotated[CallToolResult, BuildTimelineResult]:
        """Build a NEW timeline « name - vfe vN » in the project open in DaVinci Resolve from an
        edit list, in order (needs « Resolve tools for the assistant » in the app).

        Each item: the clip (media_pool_item_id, else video_id, else path; files not in the
        media pool are imported into the bin « Video Frame Expedition »), its in/out in seconds
        of the file (from get_cut_points or plan_reframe) and optional Transform values
        (plan_reframe's edit_items carry them). The application turns seconds into frames with
        each clip's own FPS read in Resolve, appends picture and sound in batches, reads every
        duration and Transform value back, and reports what did not hold. Never changes an
        existing timeline; never saves the project. Markers already on the media pool clips
        (apply_markers) are copied onto the new timeline's clips.

        Args:
            name: The edit's name (« - vfe vN » is added: N follows the versions already there).
            items: The clips, in order.
            timeline_width: The new timeline's width (with its height); default: the project's.
            timeline_height: Its height.
        """
        c = container()
        inputs = [
            resolve_edit.EditInput(
                in_s=i.in_s, out_s=i.out_s, video_id=i.video_id,
                media_pool_item_id=i.media_pool_item_id, path=i.path, video_only=i.video_only,
                props={str(k): v for k, v in i.props.items()} if i.props else None,
            )
            for i in items
        ]  # fmt: skip
        try:
            built = resolve_edit.build_timeline(
                c, name, inputs, width=timeline_width, height=timeline_height
            )
        except VfeError as exc:
            raise ToolError(exc.detail) from exc
        return _built(built)

    @mcp.tool()
    def apply_markers(
        video_ids: Annotated[list[str], Field(min_length=1, max_length=resolve.MAX_VIDEOS)],
        include_shots: bool = False,
        include_speech: bool = False,
        include_metadata: bool = True,
        language: Literal["fr", "en"] | None = None,
    ) -> Annotated[CallToolResult, MarkersResult]:
        """Put the analyses' markers and metadata on the videos' clips in DaVinci Resolve's
        media pool, run by the application itself (needs « Resolve tools for the assistant »).

        The same fixed script as get_resolve_payload (chapters blue, highlights green, shot
        starts sand, speech lavender; Keywords, Description, Comments), without passing it
        through Resolve's MCP. Running it again replaces its own markers and never touches the
        user's. Timeline clips made AFTER it carry the markers (build_timeline).

        Args:
            video_ids: Analysed videos (read_timeline, find_clips, list_watched).
            include_shots: Also a marker at the start of each shot.
            include_speech: Also a marker range per stretch of speech.
            include_metadata: Write Keywords, Description and Comments.
            language: "fr" or "en" for names, notes and metadata (default: the app's).
        """
        options = resolve.MarkerOptions(shots=include_shots, speech=include_speech)
        try:
            payload, report = resolve_edit.apply_markers(
                container(), video_ids, options, metadata=include_metadata, language=language
            )
        except VfeError as exc:
            raise ToolError(exc.detail) from exc
        not_found = [
            clean_untrusted(str(entry.get("file") or entry.get("path") or ""))[:200]
            for entry in report.get("not_found") or []
            if isinstance(entry, dict)
        ]
        errors = [clean_untrusted(str(e))[:300] for e in report.get("errors") or []]
        result = MarkersResult(
            videos=len(payload.clips), applied=len(report.get("applied") or []),
            not_found=not_found, errors=errors, unknown=payload.unknown,
        )  # fmt: skip
        lines = [
            (
                f"Marqueurs posés par l'application sur {result.applied} clips du media pool "
                f"({result.videos} vidéos)."
            )
        ]
        if not_found:
            lines.append(f"Pas dans le media pool : {len(not_found)} (fichiers à importer).")
        lines += [f"Erreur : {e}" for e in errors[:10]]
        if payload.unknown:
            lines.append(f"Hors bibliothèque : {', '.join(payload.unknown)}")
        return _result(result, lines)

    @mcp.tool()
    async def plan_reframe(
        items: Annotated[
            list[ReframeItemIn], Field(min_length=1, max_length=reframe_plan.MAX_ITEMS)
        ],
        timeline_width: Annotated[int, Field(ge=16, le=MAX_TIMELINE_SIDE)],
        timeline_height: Annotated[int, Field(ge=16, le=MAX_TIMELINE_SIDE)],
        *,
        subject: str | None = None,
        heads: bool = True,
        sheets: Literal["flagged", "all", "none"] = "flagged",
        min_piece_s: Annotated[float, Field(ge=1.0, le=10.0)] = 2.5,
    ) -> CallToolResult:
        """Plan the reframing of an edit for a timeline of another shape (e.g. vertical rushes
        in a 16:9 edit): static crops per piece of each clip, aimed at the subject's HEAD,
        without 2-second jumps, every keyframe scored, contact sheets to look at (needs « Resolve
        tools for the assistant » in the app).

        For keyframes where the subject is bigger than the crop, the vision model already loaded
        in LM Studio is asked where its head is (cached: asked once per keyframe, reused by every
        later plan; people use their detected face). One crop is kept while every keyframe keeps
        75 % of the target; pieces shorter than min_piece_s are merged. Flags: low_coverage,
        head_cut, head_pending (call again: the rest comes from the cache), no_subject,
        rotated_*, upscale_*. The images are contact sheets: yellow frame = what the timeline
        shows, red below 60 %, cyan = the head. LOOK at them; fix per item with anchor (a
        subject seen from above: center), rotation (footage filmed sideways or upside down:
        nothing else detects it) or subject, and call again. Then pass edit_items to
        build_timeline.

        Args:
            items: The clips of the edit, in order, with their final cuts (get_cut_points).
            timeline_width: The timeline's width in pixels (e.g. 1920).
            timeline_height: Its height (e.g. 1080).
            subject: Who to frame: a category (person, mammal, bird, insect…) or the start of a
                name ("chat"); default: the main subject of each keyframe.
            heads: Ask the vision model for heads (off: the top of the subject).
            sheets: Contact sheets of the flagged pieces, of all, or none.
            min_piece_s: Shortest piece in seconds.
        """
        c = container()
        inputs = [
            reframe_plan.ReframeInput(video_id=i.video_id, in_s=i.in_s, out_s=i.out_s,
                                      anchor=i.anchor, rotation=i.rotation, subject=i.subject)
            for i in items
        ]  # fmt: skip
        try:
            plan = await reframe_plan.plan_reframe(
                c, inputs, timeline_width=timeline_width, timeline_height=timeline_height,
                subject=subject, heads=heads, options=PlanOptions(min_piece_s=min_piece_s),
            )  # fmt: skip
            images: list[bytes] = []
            not_shown = 0
            if sheets != "none":
                images, not_shown = await anyio.to_thread.run_sync(
                    lambda: reframe_plan.render_sheets(
                        c, plan, flagged_only=sheets == "flagged", flag=PlanOptions().flag
                    )
                )
        except VfeError as exc:
            raise ToolError(exc.detail) from exc
        return _planned(plan, images, not_shown)


# ---------------------------------------------------------------- results
def _result(model: BaseModel, lines: list[str]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structured_content=model.model_dump(mode="json", exclude_none=True),
    )


def _clock(seconds: float) -> str:
    return format_clock(max(0.0, seconds), millis=True)


def _built(built: EditResult) -> CallToolResult:
    fps = built.fps

    def seconds(frame: int) -> float | None:
        return round((frame - built.start_frame) / fps, 3) if fps else None

    placed = [
        PlacedOut(n=p.n, timeline_item_id=p.timeline_item_id, start_s=seconds(p.record_start),
                  end_s=seconds(p.record_end), duration_ok=p.duration_ok, props_ok=p.props_ok)
        for p in built.items
    ]  # fmt: skip
    failed = sum(1 for p in placed if not p.duration_ok or p.props_ok is False)
    result = BuildTimelineResult(
        project=ProjectInfo(id=built.project.id, name=clean_untrusted(built.project.name)[:80]),
        timeline=TimelineHead(id=built.timeline_id, name=clean_untrusted(built.timeline_name)[:80],
                              fps=fps, width=built.width, height=built.height,
                              start_frame=built.start_frame,
                              duration_s=round(built.duration_s, 3) if built.duration_s else None),
        placed=placed, skipped=[SkippedOut(n=s.n, why=s.why) for s in built.skipped],
        not_placed=built.not_placed, imported=built.imported, audio_items=built.audio_items,
        checks_failed=failed, note=BUILD_NOTE,
    )  # fmt: skip
    duration = format_clock(built.duration_s or 0.0)
    lines = [
        f"Timeline créée : « {result.timeline.name} » ({built.width}×{built.height}, {fps:g} i/s)"
        f" — {len(placed)} plans, {duration}, {built.audio_items} clips son."
        if fps
        else f"Timeline créée : « {result.timeline.name} » — {len(placed)} plans.",
    ]
    if failed:
        lines.append(f"Contrôles non tenus : {failed} (voir placed : duration_ok, props_ok).")
    else:
        lines.append("Durées et transformations relues : toutes conformes.")
    if built.skipped:
        lines.append(
            "Non placés : " + ", ".join(f"plan {s.n} ({s.why})" for s in built.skipped[:20])
        )
    if built.not_placed:
        lines.append(f"Refusés par Resolve à l'ajout : {built.not_placed}.")
    if built.imported:
        lines.append(
            f"Fichiers importés dans le chutier « Video Frame Expedition » : {built.imported}."
        )
    lines.append(BUILD_NOTE)
    return _result(result, lines)


def _planned(plan: reframe_plan.ReframePlan, images: list[bytes], not_shown: int) -> CallToolResult:
    pieces: list[PieceOut] = []
    edit_items: list[EditItemIn] = []
    for n, (item, piece) in enumerate(plan.pieces(), 1):
        heads = [s.head for s in piece.frames if s.head is not None]
        pieces.append(
            PieceOut(n=n, item=item.n, video_id=item.video_id, in_s=piece.in_s, out_s=piece.out_s,
                     props=piece.props, flags=piece.flags,
                     min_coverage=min((s.cov for s in piece.frames), default=None),
                     head_kept=min(heads) if heads else None, keyframes=len(piece.frames))
        )  # fmt: skip
        edit_items.append(
            EditItemIn(video_id=item.video_id, in_s=piece.in_s, out_s=piece.out_s,
                       props=piece.props)  # type: ignore[arg-type]
        )  # fmt: skip
    h = plan.heads
    result = PlanReframeResult(
        timeline_width=plan.timeline_width, timeline_height=plan.timeline_height, pieces=pieces,
        edit_items=edit_items,
        heads=HeadsOut(model=h.model, wanted=h.wanted, from_faces=h.from_faces, cached=h.cached,
                       asked=h.asked, found=h.found, failed=h.failed, pending=h.pending,
                       note=h.note),
        sheets=len(images), not_shown=not_shown,
    )  # fmt: skip
    flagged = [p for p in pieces if p.flags]
    reframed = sum(1 for p in pieces if p.props)
    lines = [
        (
            f"{len(plan.items)} plans → {len(pieces)} morceaux ({reframed} recadrés) pour une "
            f"timeline {plan.timeline_width}×{plan.timeline_height} ; {len(flagged)} à regarder."
        ),
        (
            f"Têtes : {h.wanted} images clés à viser (sujet plus grand que le cadre) — "
            f"{h.from_faces} par les visages, {h.cached} déjà connues, {h.asked} demandées au "
            f"modèle ({h.model or 'aucun'}), {h.found} trouvées, {h.pending} en attente."
        )
        + (f" {h.note}" if h.note else ""),
    ]
    lines += [
        f"  {p.n}. [{_clock(p.in_s)} → {_clock(p.out_s)}] {', '.join(p.flags)}"
        + (f" (cible {p.min_coverage:.0%})" if p.min_coverage is not None else "")
        for p in flagged[:40]
    ]
    if images:
        lines.append(
            f"{len(images)} planche(s) ci-dessous : cadre jaune = ce que montre la timeline, "
            "rouge = sujet mal gardé, cyan = la tête."
            + (f" {not_shown} morceaux hors planches." if not_shown else "")
        )
    if h.pending:
        lines.append("Relancez plan_reframe tel quel : les têtes en attente viendront du cache.")
    lines.append("Ensuite : build_timeline(name, edit_items).")
    labels = sorted({item.subject for item in plan.items if item.subject})
    if labels:
        lines += fenced([f"sujets cadrés : {', '.join(labels)}"])
    content: list[ContentBlock] = [TextContent(type="text", text="\n".join(lines))]
    content += [Image(data=data, format="jpeg").to_image_content() for data in images]
    return CallToolResult(
        content=content, structured_content=result.model_dump(mode="json", exclude_none=True)
    )
