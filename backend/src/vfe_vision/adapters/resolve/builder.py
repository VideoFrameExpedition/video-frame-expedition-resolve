"""Build a timeline in the project open in DaVinci Resolve: the one change the
application makes in Resolve, when the user asks for it in « Create a timeline ».

The work runs in the child ``timeline_builder.py``, as the reader's: Resolve's
scripting library lives only there, one conversation with Resolve at a time, bounded in time.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vfe_vision.adapters.resolve.reader import run_child
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import ExternalToolError
from vfe_vision.core.logging import get_logger
from vfe_vision.domain.markers import Marker
from vfe_vision.domain.resolve_timeline import ResolveProjectRef
from vfe_vision.domain.timeline_build import BuiltTimeline, TimelineRequest

log = get_logger(__name__)
BUILDER_SCRIPT = Path(__file__).with_name("timeline_builder.py")
BUILD_TIMEOUT_S = 300.0  # importing many files from a network share takes a while
BUILDING = "La création de la timeline dans DaVinci Resolve"
NO_MEDIA = (
    "DaVinci Resolve n'a pu ouvrir aucune des vidéos : vérifiez que leurs fichiers sont toujours "
    "là, puis réessayez."
)
NO_MEDIA_REMOTE = (
    "DaVinci Resolve sur « {host} » n'a pu ouvrir aucune des vidéos : vérifiez que leurs dossiers "
    "y sont accessibles (partage réseau monté) et les correspondances de dossiers de la page "
    "Connexions."
)
TIMELINE_REFUSED = "DaVinci Resolve a refusé de créer la timeline : essayez un autre nom."
NOT_OPENED = (
    "DaVinci Resolve n'a pas ouvert la nouvelle timeline : elle a été retirée et rien n'a été "
    "ajouté. Fermez les fenêtres ouvertes dans Resolve, puis réessayez."
)
NOTHING_PLACED = (
    "DaVinci Resolve n'a placé aucune vidéo dans la nouvelle timeline : elle a été retirée. Les "
    "vidéos importées restent dans le chutier « Video Frame Expedition »."
)


class ResolveTimelineBuilder:
    """The :class:`~vfe_vision.ports.resolve.ResolveTimelineMaker` of the running Resolve."""

    def __init__(self, settings: Settings, host: Callable[[], str | None] | None = None) -> None:
        self._settings = settings
        self._host = host  # the computer Resolve runs on; None or "": this one

    def build_timeline(self, request: TimelineRequest) -> BuiltTimeline:
        remote = bool(((self._host() if self._host else None) or "").strip())
        payload = {
            "name": request.name,
            "rate": request.format.rate.name,
            "width": request.format.width,
            "height": request.format.height,
            "folder": request.folder,
            "clips": [
                {
                    "video_id": video_id,
                    "path": path,
                    "markers": [_marker(m) for m in request.markers.get(video_id, ())],
                }
                for video_id, path in request.clips
            ],
            "marker_kinds": list(request.marker_kinds),
            "subtitle_tracks": [{"name": n, "path": p} for n, p in request.subtitle_tracks],
            "subtitle_files": list(request.subtitle_files),
        }
        data = run_child(
            self._settings,
            self._host,
            BUILDER_SCRIPT,
            ["build"],
            timeout_s=BUILD_TIMEOUT_S,
            input_bytes=json.dumps(payload, ensure_ascii=True).encode("ascii"),
            doing=BUILDING,
            errors={
                "no_media": NO_MEDIA_REMOTE if remote else NO_MEDIA,
                "timeline_refused": TIMELINE_REFUSED,
                "timeline_not_current": NOT_OPENED,
                "nothing_placed": NOTHING_PLACED,
            },
        )
        built = built_from_json(data)
        log.info(
            "timeline built in Resolve",
            timeline=built.timeline_name,
            clips=built.clips,
            imported=built.imported,
            reused=built.reused,
            missing=len(built.missing),
            markers=built.markers,
            markers_missed=built.markers_missed,
            subtitles=list(built.subtitles),
            subtitles_laid=list(built.subtitles_laid),
            tried=[str(step) for step in data.get("tried") or []],  # the call forms that worked
        )
        return built


def _marker(marker: Marker) -> dict[str, Any]:
    return {
        "t_s": marker.t_s,
        "duration_s": marker.duration_s,
        "name": marker.name,
        "note": marker.note,
        "color": marker.color,
        "custom_data": marker.custom_data,
    }


def built_from_json(data: dict[str, Any]) -> BuiltTimeline:
    project = data.get("project") or {}
    timeline = data.get("timeline") or {}
    if not isinstance(project, dict) or not isinstance(timeline, dict) or not timeline.get("name"):
        raise ExternalToolError(
            "Réponse incomplète de DaVinci Resolve après la création de la timeline.",
            tool="DaVinci Resolve",
        )
    return BuiltTimeline(
        project=ResolveProjectRef(
            id=str(project.get("id") or ""), name=str(project.get("name") or "")
        ),
        timeline_id=str(timeline.get("id") or ""),
        timeline_name=str(timeline["name"]),
        fps=_number(timeline.get("fps")),
        width=_whole(timeline.get("width")),
        height=_whole(timeline.get("height")),
        clips=_whole(data.get("clips")) or 0,
        imported=_whole(data.get("imported")) or 0,
        reused=_whole(data.get("reused")) or 0,
        missing=tuple(str(path) for path in data.get("missing") or [] if path),
        folder=str(data.get("folder") or ""),
        markers=_whole(data.get("markers")) or 0,
        markers_missed=_whole(data.get("markers_missed")) or 0,
        subtitles=tuple(str(name) for name in data.get("subtitles") or [] if name),
        subtitles_laid=tuple(str(name) for name in data.get("subtitles_laid") or [] if name),
    )


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _whole(value: Any) -> int | None:
    number = _number(value)
    return round(number) if number is not None else None
