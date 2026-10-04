"""Edit in the project open in DaVinci Resolve for the assistant: a new timeline from
an edit list, and the fixed markers script, run by the child ``timeline_editor.py`` as the
reader's and the builder's are: Resolve's scripting library lives only
there, one conversation with Resolve at a time, bounded in time.
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
from vfe_vision.domain.edit_list import EditRequest, EditResult, PlacedItem, SkippedItem
from vfe_vision.domain.resolve_timeline import ResolveProjectRef

log = get_logger(__name__)
EDITOR_SCRIPT = Path(__file__).with_name("timeline_editor.py")
EDIT_TIMEOUT_S = 300.0  # importing files from a network share takes a while
MARKERS_TIMEOUT_S = 120.0
EDITING = "La construction de la timeline dans DaVinci Resolve"
MARKING = "La pose des marqueurs dans DaVinci Resolve"
NO_MEDIA = (
    "Aucun des plans n'est dans DaVinci Resolve et aucun fichier n'a pu y être importé : "
    "vérifiez les chemins (et, pour un Resolve sur un autre ordinateur, les dossiers de la page "
    "Connexions)."
)
TIMELINE_REFUSED = "DaVinci Resolve a refusé de créer la timeline : essayez un autre nom."
NOT_OPENED = (
    "DaVinci Resolve n'a pas ouvert la nouvelle timeline : elle a été retirée et rien n'a été "
    "ajouté. Fermez les fenêtres ouvertes dans Resolve, puis réessayez."
)
NOTHING_PLACED = "DaVinci Resolve n'a placé aucun plan : la timeline vide a été retirée."
NOT_THE_SCRIPT = "Le script des marqueurs attendu n'a pas été reconnu."


class ResolveEditor:
    """The :class:`~vfe_vision.ports.resolve.ResolveTimelineEditor` of the running Resolve."""

    def __init__(self, settings: Settings, host: Callable[[], str | None] | None = None) -> None:
        self._settings = settings
        self._host = host  # the computer Resolve runs on; None or "": this one

    def build_edit(self, request: EditRequest) -> EditResult:
        payload = {
            "name": request.name,
            "folder": request.folder,
            "width": request.width,
            "height": request.height,
            "items": [
                {
                    "uid": item.media_pool_item_id,
                    "path": item.path,
                    "in_s": item.in_s,
                    "out_s": item.out_s,
                    "video_only": item.video_only,
                    "props": dict(item.props) if item.props else None,
                }
                for item in request.items
            ],
        }
        data = run_child(
            self._settings,
            self._host,
            EDITOR_SCRIPT,
            ["edit"],
            timeout_s=EDIT_TIMEOUT_S,
            input_bytes=json.dumps(payload, ensure_ascii=True).encode("ascii"),
            doing=EDITING,
            errors={
                "no_media": NO_MEDIA,
                "timeline_refused": TIMELINE_REFUSED,
                "timeline_not_current": NOT_OPENED,
                "nothing_placed": NOTHING_PLACED,
            },
        )
        result = edit_result_from_json(data)
        log.info(
            "edit built in Resolve",
            timeline=result.timeline_name,
            placed=len(result.items),
            skipped=len(result.skipped),
            not_placed=result.not_placed,
            imported=result.imported,
            tried=[str(step) for step in data.get("tried") or []],
        )
        return result

    def apply_markers(self, script: str) -> dict[str, Any]:
        data = run_child(
            self._settings,
            self._host,
            EDITOR_SCRIPT,
            ["markers"],
            timeout_s=MARKERS_TIMEOUT_S,
            input_bytes=json.dumps({"script": script}, ensure_ascii=True).encode("ascii"),
            doing=MARKING,
            errors={"usage": NOT_THE_SCRIPT},
        )
        report = data.get("result")
        return report if isinstance(report, dict) else {}


def edit_result_from_json(data: dict[str, Any]) -> EditResult:
    project = data.get("project") or {}
    timeline = data.get("timeline") or {}
    if not isinstance(project, dict) or not isinstance(timeline, dict) or not timeline.get("name"):
        raise ExternalToolError(
            "Réponse incomplète de DaVinci Resolve après la construction de la timeline.",
            tool="DaVinci Resolve",
        )
    items = [
        PlacedItem(
            n=int(row.get("n") or 0),
            timeline_item_id=str(row.get("id") or ""),
            record_start=int(row.get("record_start") or 0),
            record_end=int(row.get("record_end") or 0),
            duration_ok=bool(row.get("duration_ok")),
            props_ok=None if row.get("props_ok") is None else bool(row["props_ok"]),
        )
        for row in data.get("items") or []
        if isinstance(row, dict)
    ]
    skipped = [
        SkippedItem(n=int(row.get("n") or 0), why=str(row.get("why") or ""))
        for row in data.get("missing") or []
        if isinstance(row, dict)
    ]
    return EditResult(
        project=ResolveProjectRef(
            id=str(project.get("id") or ""), name=str(project.get("name") or "")
        ),
        timeline_id=str(timeline.get("id") or ""),
        timeline_name=str(timeline["name"]),
        fps=_positive(timeline.get("fps")),
        width=_whole(timeline.get("width")),
        height=_whole(timeline.get("height")),
        start_frame=int(timeline.get("start_frame") or 0),
        end_frame=int(timeline.get("end_frame") or 0),
        items=items,
        skipped=skipped,
        not_placed=int(data.get("not_placed") or 0),
        imported=int(data.get("imported") or 0),
        audio_items=int(data.get("audio_items") or 0),
        scaling=str(data.get("scaling") or ""),
    )


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _whole(value: Any) -> int | None:
    number = _positive(value)
    return round(number) if number is not None else None
