"""The assistant's DaVinci Resolve tools: read a timeline tied to the analyses, build
a NEW timeline from an edit list, put the markers — when the user allowed them (Connections page,
« Enhanced DaVinci Resolve tools for the assistant », off by default).

Everything runs through the application's own fixed child scripts (``adapters/resolve``): the
assistant sends data (ids, seconds, Transform values), never code. A timeline the user made is
never changed: every build is a new « … - vfe vN ». The project is never saved by these tools.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from vfe_vision.core.errors import InvalidInputError, NotAllowedError
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.edit_list import (
    DEFAULT_FOLDER,
    EditItem,
    EditRequest,
    EditResult,
    clean_props,
    versioned_name,
)
from vfe_vision.domain.path_map import to_resolve
from vfe_vision.domain.preferences import folder_pairs
from vfe_vision.domain.resolve_timeline import ClipKind, TimelineContent
from vfe_vision.services import editing, resolve, videos
from vfe_vision.services.container import AppContainer

MAX_EDIT_ITEMS = 500
TOOLS_OFF = (
    "Les outils DaVinci Resolve améliorés sont désactivés. L'utilisateur peut les activer dans "
    "l'application, page Connexions, carte « Outils Resolve pour l'assistant »."
)


def ensure_allowed(c: AppContainer) -> None:
    """Refuse unless the user switched the tools on (read at every call, like mcp_add_folders)."""
    if not load_preferences(c.db).mcp_resolve_tools:
        raise NotAllowedError(TOOLS_OFF)


# ---------------------------------------------------------------- read
@dataclass(frozen=True, slots=True)
class TimelineRow:
    n: int
    track_type: str
    track: int
    name: str
    enabled: bool
    record_start: int
    record_end: int
    start_s: float  # on the timeline, from its first frame
    end_s: float
    kind: str
    file_path: str | None
    media_pool_item_id: str | None
    timeline_item_id: str | None
    source_start_s: float
    source_end_s: float
    clip_fps: float | None
    nested_in: str | None
    match: editing.ClipMatch | None


def read_timeline(
    c: AppContainer, timeline_id: str | None, *, audio: bool = False, match: bool = True
) -> tuple[TimelineContent, list[TimelineRow]]:
    """The timeline read now in Resolve, clip by clip (video tracks, and audio on request), each
    file tied to its analysed video. Ranges follow the rule measured in Resolve 21.1: left offset
    ÷ the clip's FPS, then + duration ÷ the timeline's fps (``timeline_reader.source_range``)."""
    ensure_allowed(c)
    content = c.resolve.timeline(timeline_id)
    info = content.timeline
    fps = info.fps or 0.0
    kept = [clip for clip in content.clips if clip.use.track_type == "video" or audio]
    kept.sort(
        key=lambda cl: (cl.use.track_type != "video", cl.use.track, cl.use.record_start_frame)
    )
    matches: dict[int, editing.ClipMatch] = {}
    if match:
        files = [(i, cl) for i, cl in enumerate(kept) if cl.kind == ClipKind.FILE and cl.file_path]
        for start in range(0, len(files), editing.MAX_ITEMS):
            chunk = files[start : start + editing.MAX_ITEMS]
            queries = [
                editing.ClipQuery(cl.file_path or "", clip_uid=cl.use.media_pool_item_id)
                for _, cl in chunk
            ]
            for (i, _), found in zip(chunk, editing.match_clips(c, queries), strict=True):
                matches[i] = found
    rows = []
    for i, clip in enumerate(kept):
        use = clip.use
        rows.append(
            TimelineRow(
                n=i + 1, track_type=use.track_type, track=use.track, name=use.name,
                enabled=use.enabled and use.track_enabled,
                record_start=use.record_start_frame, record_end=use.record_end_frame,
                start_s=(use.record_start_frame - info.start_frame) / fps if fps else 0.0,
                end_s=(use.record_end_frame - info.start_frame) / fps if fps else 0.0,
                kind=clip.kind, file_path=clip.file_path,
                media_pool_item_id=use.media_pool_item_id,
                timeline_item_id=use.timeline_item_id, source_start_s=use.source_start_s,
                source_end_s=use.source_end_s, clip_fps=use.clip_fps, nested_in=use.nested_in,
                match=matches.get(i),
            )
        )  # fmt: skip
    return content, rows


# ---------------------------------------------------------------- build
@dataclass(frozen=True, slots=True)
class EditInput:
    """One clip of the edit list as the assistant gives it."""

    in_s: float
    out_s: float
    video_id: str | None = None
    media_pool_item_id: str | None = None
    path: str | None = None
    video_only: bool = False
    props: dict[str, float] | None = None


def build_timeline(
    c: AppContainer,
    name: str,
    items: Sequence[EditInput],
    *,
    width: int | None = None,
    height: int | None = None,
) -> EditResult:
    """A NEW timeline « name - vfe vN » of the items, in order, in the project open in Resolve."""
    ensure_allowed(c)
    if not items:
        raise InvalidInputError("La liste de montage est vide.")
    if len(items) > MAX_EDIT_ITEMS:
        raise InvalidInputError(f"Au plus {MAX_EDIT_ITEMS} plans par timeline.")
    if (width is None) != (height is None):
        raise InvalidInputError("Donnez la largeur et la hauteur de la timeline, ou aucune.")
    prefs = load_preferences(c.db)
    pairs = folder_pairs(prefs) if (prefs.resolve_host or "").strip() else []
    edit_items = [_edit_item(c, n, item, pairs) for n, item in enumerate(items, 1)]
    existing = [t.name for t in c.resolve.project().timelines]
    request = EditRequest(
        name=versioned_name(name, existing), items=edit_items, width=width, height=height,
        folder=DEFAULT_FOLDER,
    )  # fmt: skip
    return c.resolve_editor.build_edit(request)


def _edit_item(c: AppContainer, n: int, item: EditInput, pairs: list[Any]) -> EditItem:
    if item.out_s <= item.in_s or item.in_s < 0:
        raise InvalidInputError(f"Plan {n} : la sortie doit suivre l'entrée (secondes du fichier).")
    path = item.path
    if item.video_id:
        video = videos.get_video(c, item.video_id).video
        path = path or (to_resolve(video.path, pairs) if pairs else video.path) or video.path
    if not path and not item.media_pool_item_id:
        raise InvalidInputError(
            f"Plan {n} : donnez video_id (une vidéo analysée), media_pool_item_id ou path."
        )
    return EditItem(
        in_s=float(item.in_s), out_s=float(item.out_s), path=path,
        media_pool_item_id=item.media_pool_item_id, video_only=item.video_only,
        props=clean_props(item.props), video_id=item.video_id,
    )  # fmt: skip


# ---------------------------------------------------------------- markers
def apply_markers(
    c: AppContainer,
    video_ids: list[str],
    options: resolve.MarkerOptions,
    *,
    metadata: bool = True,
    language: str | None = None,
) -> tuple[resolve.ResolvePayload, dict[str, Any]]:
    """The fixed markers script (``get_resolve_payload``), run by the application itself."""
    ensure_allowed(c)
    payload = resolve.build_payload(c, video_ids, options, metadata=metadata, language=language)
    return payload, c.resolve_editor.apply_markers(payload.script)
