"""What DaVinci Resolve receives: the markers and the metadata of
analysed videos, as data for the fixed script (``adapters/resolve``), never as code.

- **Markers**, in seconds of the source file (the script turns them into frames with each clip's
  own rate): chapters (blue ranges), highlights (green ranges, the note gives the sound range of
  a J-cut or an L-cut), and on request shot starts (sand) and stretches of speech (lavender).
- **Metadata**: Keywords (the synthesis' tags and the beings seen), Description (the user's
  summary, else the synthesis'), Comments (place, local date and time of shooting, weather and
  light).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from vfe_vision import __version__
from vfe_vision.adapters.resolve.script import SCRIPT_FORMAT, SCRIPT_VERSION, render_script
from vfe_vision.core.errors import InvalidInputError, NotFoundError
from vfe_vision.core.paths import CASE_INSENSITIVE_PATHS
from vfe_vision.db.models import Video
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.clip_paths import file_name
from vfe_vision.domain.exports import light_phase_name, one_line
from vfe_vision.domain.markers import (
    PREFIX,
    Marker,
    chapter_marker,
    highlight_marker,
    shot_marker,
    speech_markers,
)
from vfe_vision.domain.path_map import FolderPair, to_resolve
from vfe_vision.domain.preferences import folder_pairs
from vfe_vision.services.container import AppContainer
from vfe_vision.services.editing_data import EditingData, load_editing_data
from vfe_vision.services.reading import texts_in

MAX_VIDEOS = 50
MAX_KEYWORDS = 20
DESCRIPTION_CHARS = 1000
COMMENTS_CHARS = 500


@dataclass(frozen=True, slots=True)
class MarkerOptions:
    chapters: bool = True
    highlights: bool = True
    shots: bool = False
    speech: bool = False


@dataclass(frozen=True, slots=True)
class ClipPayload:
    video: Video
    markers: list[Marker]
    metadata: dict[str, str | list[str]]


@dataclass(frozen=True, slots=True)
class ResolvePayload:
    clips: list[ClipPayload]
    data: dict[str, Any]  # what the script reads
    script: str  # the fixed script with ``data`` as a JSON string literal
    unknown: list[str] = field(default_factory=list)  # video ids not in the library


def video_markers(data: EditingData, options: MarkerOptions) -> list[Marker]:
    """The markers of a video, in time order, in the language of its texts."""
    view, language = data.synthesis, data.language
    markers: list[Marker] = []
    if options.chapters:
        markers += [
            chapter_marker(ch.index, ch.start_s, ch.end_s, ch.title, ch.summary,
                           language=language)
            for ch in view.chapters
        ]  # fmt: skip
    if options.highlights:
        markers += [
            highlight_marker(h.rank, h.clip, h.reason, language=language) for h in view.highlights
        ]
    if options.shots:
        markers += [
            shot_marker(shot.idx + 1, shot.start_s, data.shot_motion(shot),
                        data.shot_usability(shot.idx), data.shot_text(shot), language=language)
            for shot in data.shots
        ]  # fmt: skip
    if options.speech:
        markers += speech_markers(data.facts.segments)
    return sorted(markers, key=lambda m: (m.t_s, m.kind))


def video_metadata(data: EditingData) -> dict[str, str | list[str]]:
    """Keywords, Description and Comments of a video (only those it has)."""
    stored = data.synthesis.data
    keywords: dict[str, str] = {}
    for label in [str(t.get("label", "")) for t in data.synthesis.tags] + data.subject_labels:
        word = one_line(label.replace(",", " "), 40)
        if word and word.casefold() not in keywords:
            keywords[word.casefold()] = word
    metadata: dict[str, str | list[str]] = {}
    if keywords:
        metadata["Keywords"] = list(keywords.values())[:MAX_KEYWORDS]
    description = data.video.summary or stored.get("summary") or stored.get("logline")
    if description:
        metadata["Description"] = one_line(str(description), DESCRIPTION_CHARS)
    if comments := context_line(data):
        metadata["Comments"] = one_line(comments, COMMENTS_CHARS)
    return metadata


def context_line(data: EditingData) -> str:
    """« Lieu : … · Tournage : … · Météo : … · Lumière : … », what is known of it, in the
    language of its texts (« Place: … · Shot: … » in English)."""
    facts, parts, english = data.facts, [], data.language == "en"
    if facts.place_label:
        parts.append(f"{'Place: ' if english else 'Lieu : '}{data.texts(facts.place_label)}")
    if facts.capture_local is not None:
        if english:
            parts.append(f"Shot: {facts.capture_local:%Y-%m-%d %H:%M} (local time)")
        else:
            parts.append(f"Tournage : {facts.capture_local:%d/%m/%Y %H:%M} (heure locale)")
    weather = data.synthesis.weather
    if weather is not None and weather.category:
        parts.append(f"{'Weather: ' if english else 'Météo : '}{weather.line}")
    if facts.light_phase:
        light = light_phase_name(facts.light_phase, data.language)
        parts.append(f"{'Light: ' if english else 'Lumière : '}{light}")
    return " · ".join(parts)


def build_payload(
    c: AppContainer,
    video_ids: list[str],
    options: MarkerOptions,
    *,
    metadata: bool = True,
    language: str | None = None,
) -> ResolvePayload:
    """Markers and metadata of the videos, and the ready-to-run script; their texts in
    ``language`` (default: the analysis language)."""
    tr = texts_in(c, language)
    wanted = list(dict.fromkeys(video_ids))
    if not wanted:
        raise InvalidInputError("Indiquez au moins une vidéo.")
    if len(wanted) > MAX_VIDEOS:
        raise InvalidInputError(f"Au plus {MAX_VIDEOS} vidéos à la fois.")
    clips: list[ClipPayload] = []
    unknown: list[str] = []
    for video_id in wanted:
        try:
            data = load_editing_data(c, video_id, tr=tr)
        except NotFoundError:
            unknown.append(video_id)
            continue
        clips.append(
            ClipPayload(
                data.video, video_markers(data, options), video_metadata(data) if metadata else {}
            )
        )
    if not clips:
        raise NotFoundError("Aucune de ces vidéos n'est dans la bibliothèque.")
    document = payload_document(clips, pairs=folder_pairs(load_preferences(c.db)))
    return ResolvePayload(clips, document, render_script(document), unknown)


def payload_document(clips: list[ClipPayload], pairs: Sequence[FolderPair] = ()) -> dict[str, Any]:
    """The versioned data the script reads; the paths as Resolve sees them when it
    runs on another computer."""
    return {
        "format": SCRIPT_FORMAT,
        "version": SCRIPT_VERSION,
        "app_version": __version__,
        "prefix": PREFIX,
        "case_insensitive": CASE_INSENSITIVE_PATHS,
        "clips": [
            {
                "video_id": clip.video.id,
                "file_name": file_name(clip.video.path),
                "path": to_resolve(clip.video.path, pairs) or clip.video.path,
                "fps": clip.video.fps,
                "duration_s": clip.video.duration_s,
                "markers": [
                    {
                        "kind": m.kind, "t_s": round(m.t_s, 3),
                        "duration_s": round(m.duration_s, 3), "color": m.color, "name": m.name,
                        "note": m.note, "custom_data": m.custom_data,
                    }
                    for m in clip.markers
                ],
                "metadata": clip.metadata,
            }
            for clip in clips
        ],
    }  # fmt: skip
