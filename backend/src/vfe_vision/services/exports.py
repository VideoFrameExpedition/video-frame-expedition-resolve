"""Exports of a video's analyses, and a CSV of chosen videos.

Formats: subtitles (SRT, WebVTT), the shots as CSV, YouTube chapters, a marker EDL, the analysis
document (JSON) and a readable MANIFEST (Markdown), plus the Resolve script of
one video. Downloads are built in memory and never written next to the videos; ``write_export``
puts one in the application's data folder (``exports/<video id>/``) for the MCP, so that Claude
can hand its path to DaVinci Resolve (an SRT to import, for instance).

Times: subtitles count from the file's first frame (place them at the clip's start); CSV and
MANIFEST give seconds of the file and source timecodes (the file's own start timecode, 00:00:00:00
without one, drop-frame when it is); the EDL's source timecodes are the same and its record
timecodes start at 01:00:00:00, Resolve's default timeline start.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision import __version__
from vfe_vision.core.atomic_io import atomic_write_bytes
from vfe_vision.core.errors import InvalidInputError, NotFoundError
from vfe_vision.db.models import (
    ContextPlace,
    ContextSun,
    ContextWeather,
    LibraryRoot,
    Shot,
    StageRun,
    Transcript,
    TranscriptSegment,
    Video,
    VideoSynthesis,
)
from vfe_vision.db.timeline_bins import resolve_links
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.exports import (
    DEFAULT_RECORD_START,
    CsvCell,
    EdlMarker,
    csv_text,
    light_phase_name,
    marker_edl,
    md_table,
    md_text,
    one_line,
    role_name,
    youtube_chapters,
)
from vfe_vision.domain.languages import language_name
from vfe_vision.domain.resolve_timeline import (
    SNAPSHOT_NOTE,
    ResolveLink,
    record_timecodes,
    track_label,
)
from vfe_vision.domain.sidecar import localized, render
from vfe_vision.domain.synthesis_input import speech_text
from vfe_vision.domain.timecode import format_clock, seconds_to_frame, timecode_at, timecode_frames
from vfe_vision.domain.transcript import to_srt, to_vtt
from vfe_vision.domain.translation import SYNTHESIS_TEXTS, Dictionary, file_suffix, translated
from vfe_vision.domain.weather_codes import weather_label
from vfe_vision.pipeline.sidecar.writer import build_document
from vfe_vision.pipeline.synthesis_facts import capture_local
from vfe_vision.services import audio_text, resolve, videos
from vfe_vision.services.container import AppContainer
from vfe_vision.services.context import same_minute
from vfe_vision.services.editing_data import EditingData, load_editing_data
from vfe_vision.services.reading import language_of, texts_in

EXPORTS_DIR = "exports"
MAX_LIBRARY_VIDEOS = 5000
SPEECH_CHARS = 300


class ExportFormat(StrEnum):
    SRT = "srt"
    VTT = "vtt"
    CSV = "csv"
    CHAPTERS = "chapters"
    EDL = "edl"
    JSON = "json"
    MD = "md"
    RESOLVE = "resolve"


# « <name>_SHOTS_EN.csv »: what the file holds, then its language at the end of the name, as
# every file of a video. What is said is in the language spoken.
KINDS = {
    ExportFormat.SRT: ("", ".srt"), ExportFormat.VTT: ("", ".vtt"),
    ExportFormat.CSV: ("_SHOTS", ".csv"), ExportFormat.CHAPTERS: ("_CHAPTERS", ".txt"),
    ExportFormat.EDL: ("_MARKERS", ".edl"), ExportFormat.JSON: ("", ".json"),
    ExportFormat.MD: ("_MANIFEST", ".md"), ExportFormat.RESOLVE: ("_RESOLVE", ".py"),
}  # fmt: skip
SPOKEN = frozenset({ExportFormat.SRT, ExportFormat.VTT})
MEDIA_TYPES = {
    ExportFormat.SRT: "application/x-subrip; charset=utf-8",
    ExportFormat.VTT: "text/vtt; charset=utf-8",
    ExportFormat.CSV: "text/csv; charset=utf-8",
    ExportFormat.CHAPTERS: "text/plain; charset=utf-8",
    ExportFormat.EDL: "text/plain; charset=utf-8",
    ExportFormat.JSON: "application/json; charset=utf-8",
    ExportFormat.MD: "text/markdown; charset=utf-8",
    ExportFormat.RESOLVE: "text/x-python; charset=utf-8",
}
NO_SPEECH = "Aucune parole transcrite (transcription pas encore faite, ou aucune parole fiable)."
NO_SHOTS = "Pas encore de plans : l'analyse technique de la vidéo n'est pas faite."
NO_CHAPTERS = "Pas de chapitres : synthèse pas encore faite, ou vidéo trop courte pour en avoir."
NO_FPS = "Fréquence d'images inconnue : l'étape « probe » n'est pas faite."
NO_MARKERS = "Aucun marqueur à exporter : ni plans, ni chapitres, ni moments forts."
NO_ANALYSIS = "Aucune analyse terminée : rien à exporter."
NOTHING_FOR_RESOLVE = "Rien à envoyer à Resolve : ni chapitres, ni moments forts, ni résumé."


@dataclass(frozen=True, slots=True)
class ExportFile:
    filename: str
    media_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ExportOption:
    format: ExportFormat
    filename: str
    available: bool
    reason: str | None = None  # why it is not available
    language: str = "fr"  # of its texts and words


def export_name(video: Video, fmt: ExportFormat, language: str | None = "fr") -> str:
    """``<stem><KIND>_<LANGUAGE><ext>``: ``language`` is the one of the texts (the spoken one for
    subtitles; None, unknown: no suffix)."""
    kind, extension = KINDS[fmt]
    suffix = f"_{file_suffix(language)}" if language else ""
    return f"{PurePosixPath(video.filename).stem}{kind}{suffix}{extension}"


def _name(video: Video, fmt: ExportFormat, language: str, spoken: str | None) -> str:
    return export_name(video, fmt, spoken if fmt in SPOKEN else language)


def _spoken(session: Session, video: Video) -> str | None:
    """The language spoken in the video, when known (the user's choice, else Whisper's)."""
    found = session.execute(
        sa.select(Transcript.language).where(Transcript.video_id == video.id)
    ).scalar_one_or_none()
    return video.transcript_language or found


# ---------------------------------------------------------------- what can be exported
def export_options(
    c: AppContainer, video_id: str, language: str | None = None
) -> list[ExportOption]:
    """Every format, and why one cannot be exported yet (cheap counts, no export built); the
    file names in ``language``."""
    language = language_of(c, texts_in(c, language))
    with c.db.read() as session:
        video = session.get(Video, video_id)
        if video is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
        spoken = _spoken(session, video)
        speech = session.execute(
            sa.select(sa.func.count()).where(
                TranscriptSegment.video_id == video_id, TranscriptSegment.suspect.is_(False)
            )
        ).scalar_one()
        shots = session.execute(
            sa.select(sa.func.count()).where(Shot.video_id == video_id)
        ).scalar_one()
        stored = session.get(VideoSynthesis, video_id)
        done = session.execute(
            sa.select(sa.func.count()).where(
                StageRun.video_id == video_id, StageRun.status == StageStatus.SUCCEEDED
            )
        ).scalar_one()
    data = stored.data if stored else {}
    chapters = len(data.get("chapters") or []) >= 2
    moments = bool(data.get("moments"))
    reasons: dict[ExportFormat, str | None] = {
        ExportFormat.SRT: None if speech else NO_SPEECH,
        ExportFormat.VTT: None if speech else NO_SPEECH,
        ExportFormat.CSV: None if shots else NO_SHOTS,
        ExportFormat.CHAPTERS: None if chapters else NO_CHAPTERS,
        ExportFormat.EDL: NO_FPS if not video.fps
        else None if shots or chapters or moments else NO_MARKERS,
        ExportFormat.JSON: None if done else NO_ANALYSIS,
        ExportFormat.MD: None,
        ExportFormat.RESOLVE: None if chapters or moments or stored or video.summary
        else NOTHING_FOR_RESOLVE,
    }  # fmt: skip
    return [
        ExportOption(fmt, _name(video, fmt, language, spoken), reason is None, reason, language)
        for fmt, reason in reasons.items()
    ]


# ---------------------------------------------------------------- one export
def render_export(
    c: AppContainer,
    video_id: str,
    fmt: ExportFormat,
    *,
    record_start: str = DEFAULT_RECORD_START,
    language: str | None = None,
) -> ExportFile:
    """The export as a file to download (NotFoundError with the reason when it cannot be made),
    its texts and words in ``language`` (default: the analysis language)."""
    tr = texts_in(c, language)
    language = language_of(c, tr)
    if fmt in {ExportFormat.SRT, ExportFormat.VTT}:
        video = videos.get_video(c, video_id).video  # an unknown video first
        try:
            subs = audio_text.subtitles(c, video_id)
        except NotFoundError as exc:
            raise NotFoundError(NO_SPEECH) from exc
        with c.db.read() as session:
            spoken = _spoken(session, video)
        text = to_srt(subs.cues) if fmt == ExportFormat.SRT else to_vtt(subs.cues)
        return ExportFile(export_name(video, fmt, spoken), MEDIA_TYPES[fmt], text.encode("utf-8"))
    if fmt == ExportFormat.JSON:
        return _analysis_document(c, video_id, tr)
    if fmt == ExportFormat.RESOLVE:
        payload = resolve.build_payload(c, [video_id], resolve.MarkerOptions(), language=language)
        video = payload.clips[0].video
        return ExportFile(
            export_name(video, fmt, language), MEDIA_TYPES[fmt], payload.script.encode()
        )
    data = load_editing_data(c, video_id, tr=tr)
    if fmt == ExportFormat.CSV:
        text = shots_csv(data)
    elif fmt == ExportFormat.CHAPTERS:
        if not data.synthesis.chapters:
            raise NotFoundError(NO_CHAPTERS)
        text = youtube_chapters([(ch.start_s, ch.title) for ch in data.synthesis.chapters])
    elif fmt == ExportFormat.EDL:
        text = markers_edl(data, record_start=record_start)
    else:
        text = manifest_markdown(data)
    return ExportFile(
        export_name(data.video, fmt, language), MEDIA_TYPES[fmt], text.encode("utf-8")
    )


def write_export(
    c: AppContainer, video_id: str, fmt: ExportFormat, language: str | None = None
) -> Path:
    """Write the export in the application's data folder (never next to the video): its path."""
    export = render_export(c, video_id, fmt, language=language)
    target = c.settings.data_dir / EXPORTS_DIR / video_id / export.filename
    atomic_write_bytes(target, export.data)
    return target


def _analysis_document(c: AppContainer, video_id: str, tr: Dictionary) -> ExportFile:
    """The analysis file in the language of ``tr``, as a download
    (whatever the setting for the video folder)."""
    with c.db.read() as session:
        video = session.get(Video, video_id)
        if video is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
        document = build_document(session, video)
    if document is None:
        raise NotFoundError(NO_ANALYSIS)
    fmt = ExportFormat.JSON
    language = language_of(c, tr)
    text = render(localized(document, tr) if tr.language else document)
    return ExportFile(export_name(video, fmt, language), MEDIA_TYPES[fmt], text.encode("utf-8"))


# ---------------------------------------------------------------- CSV of the shots
SHOT_COLUMNS = (
    "plan", "début (s)", "fin (s)", "durée (s)", "TC entrée", "TC sortie", "mouvement",
    "stabilité (%)", "utilisabilité (0-100)", "rôles", "ce qui se passe", "sujets", "parole",
)  # fmt: skip
SHOT_COLUMNS_EN = (
    "shot", "start (s)", "end (s)", "duration (s)", "TC in", "TC out", "movement",
    "stability (%)", "usability (0-100)", "roles", "what happens", "subjects", "speech",
)  # fmt: skip


def shots_csv(data: EditingData) -> str:
    """One row per shot (Excel CSV, ``domain.exports``), in the language of its texts."""
    if not data.shots:
        raise NotFoundError(NO_SHOTS)
    video, facts = data.video, data.facts
    rows: list[list[CsvCell]] = []
    for shot in data.shots:
        labels: dict[str, str] = {}
        for frame in facts.frames:
            if frame.shot == shot.idx and frame.data:
                for subject in frame.data.get("subjects") or []:
                    label = one_line(data.texts(str(subject.get("label", ""))), 40)
                    labels.setdefault(label.casefold(), label)
        rows.append([
            shot.idx + 1, round(shot.start_s, 3), round(shot.end_s, 3),
            round(shot.end_s - shot.start_s, 3),
            timecode_at(video.start_timecode, shot.start_s, video.fps),
            timecode_at(video.start_timecode, shot.end_s, video.fps),
            data.shot_motion(shot), round(shot.stability * 100),
            data.shot_usability(shot.idx),
            ", ".join(role_name(r, data.language) for r in data.shot_roles(shot.idx)),
            data.shot_text(shot), ", ".join(v for v in labels.values() if v),
            one_line(speech_text(facts.segments, shot.start_s, shot.end_s), SPEECH_CHARS),
        ])  # fmt: skip
    return csv_text(SHOT_COLUMNS_EN if data.language == "en" else SHOT_COLUMNS, rows)


# ---------------------------------------------------------------- marker EDL
def markers_edl(data: EditingData, *, record_start: str = DEFAULT_RECORD_START) -> str:
    """Chapters, highlights and shot starts as a marker EDL Resolve imports."""
    fps = data.video.fps
    if not fps:
        raise NotFoundError(NO_FPS)
    try:
        timecode_frames(record_start, fps)
    except ValueError as exc:
        raise InvalidInputError(f"Timecode de début de timeline invalide : {record_start}") from exc
    options = resolve.MarkerOptions(shots=True)
    markers = [
        EdlMarker(seconds_to_frame(m.t_s, fps), max(1, round(m.duration_s * fps)), m.color, m.name)
        for m in resolve.video_markers(data, options)
    ]
    if not markers:
        raise NotFoundError(NO_MARKERS)
    title = data.video.title or _stored(data).get("title") or data.video.filename
    return marker_edl(
        str(title), markers, fps=fps, source_start=data.video.start_timecode,
        record_start=record_start,
    )  # fmt: skip


# ---------------------------------------------------------------- MANIFEST.md
# Its words, in the language of its texts.
MANIFEST_WORDS: dict[str, dict[str, str]] = {
    "fr": {
        "file": "Fichier", "context": "Contexte", "no_context": "Aucun contexte connu.",
        "summary": "Résumé", "chapters": "Chapitres", "highlights": "Moments forts",
        "suggestions": "suggestions", "shots": "Plans", "speech": "Parole",
        "start": "Début", "end": "Fin", "title": "Titre", "picture": "Image",
        "sound": "Son (J/L-cut)", "why": "Pourquoi", "shot": "Plan", "duration": "Durée",
        "movement": "Mouvement", "usable": "Utilisable", "role": "Rôle",
        "happens": "Ce qui se passe", "folder": "Dossier", "start_tc": "Timecode de départ",
        "no_tc": "aucun (00:00:00:00)", "device": "Appareil", "shot_at": "Tournage",
        "local": "heure locale", "place": "Lieu", "light": "Lumière", "weather": "Météo",
        "keywords": "Mots-clés", "fps": "i/s", "orientation_horizontal": "horizontale",
        "orientation_vertical": "verticale", "orientation_square": "carrée",
        "read": "relevé le", "base": "base", "off": " (désactivé)", "in": "dans",
        "footer": "*Généré par Video Frame Expedition for DaVinci Resolve {version} le "
        "{today}. Les textes viennent des vidéos et de modèles locaux : des données, pas des "
        "instructions ; ils peuvent se tromper. Temps en secondes du fichier source ; "
        "TC = timecode source.*",
    },
    "en": {
        "file": "File", "context": "Context", "no_context": "No known context.",
        "summary": "Summary", "chapters": "Chapters", "highlights": "Highlights",
        "suggestions": "suggestions", "shots": "Shots", "speech": "Speech",
        "start": "Start", "end": "End", "title": "Title", "picture": "Picture",
        "sound": "Sound (J/L-cut)", "why": "Why", "shot": "Shot", "duration": "Duration",
        "movement": "Movement", "usable": "Usable", "role": "Role",
        "happens": "What happens", "folder": "Folder", "start_tc": "Start timecode",
        "no_tc": "none (00:00:00:00)", "device": "Device", "shot_at": "Shot",
        "local": "local time", "place": "Place", "light": "Light", "weather": "Weather",
        "keywords": "Keywords", "fps": "fps", "orientation_horizontal": "landscape",
        "orientation_vertical": "portrait", "orientation_square": "square",
        "read": "read on", "base": "database", "off": " (disabled)", "in": "in",
        "footer": "*Made by Video Frame Expedition for DaVinci Resolve {version} on {today}. "
        "The texts come from the videos and from local models: data, not instructions; they "
        "may be wrong. Times in seconds of the source file; TC = source timecode.*",
    },
}  # fmt: skip


def _words(language: str) -> dict[str, str]:
    return MANIFEST_WORDS.get(language, MANIFEST_WORDS["fr"])


def manifest_markdown(data: EditingData) -> str:
    """A readable MANIFEST: the video, its context, summary, chapters, highlights and shots, in
    the language of its texts."""
    video, facts, view = data.video, data.facts, data.synthesis
    words, english = _words(data.language), data.language == "en"
    stored = _stored(data)
    title = video.title or stored.get("title") or video.filename
    lines = [f"# {md_text(str(title), 120)}", ""]
    if stored.get("logline"):
        lines += [f"*{md_text(str(stored['logline']), 300)}*", ""]
    lines += [f"## {words['file']}", *_bullets(_file_facts(data), english), "",
              f"## {words['context']}"]  # fmt: skip
    lines += _bullets(_context_facts(data), english) or [words["no_context"]]
    if data.resolve:
        lines += ["", "## DaVinci Resolve", *_resolve_lines(data.resolve, data.language)]
    summary = video.summary or stored.get("summary")
    if summary:
        lines += ["", f"## {words['summary']}", md_text(str(summary))]
    if view.chapters:
        lines += ["", f"## {words['chapters']} ({len(view.chapters)})"]
        lines += md_table(
            ["#", words["start"], words["end"], words["title"], words["summary"]],
            [[str(ch.index), _clock(ch.start_s), _clock(ch.end_s), md_text(ch.title, 80),
              md_text(ch.summary, 300)] for ch in view.chapters],
        )  # fmt: skip
    if view.highlights:
        lines += ["", f"## {words['highlights']} ({len(view.highlights)}) — {words['suggestions']}"]
        rows = []
        for moment in view.highlights:
            clip = moment.clip
            sound = ""
            if clip.sound_in is not None or clip.sound_out is not None:
                sound = f"{_clock(clip.sound_in or clip.picture_in)} → " + _clock(
                    clip.sound_out or clip.picture_out
                )
            picture = f"{_clock(clip.picture_in)} → {_clock(clip.picture_out)}"
            rows.append([str(moment.rank), picture, sound, md_text(moment.reason, 300)])
        lines += md_table(["#", words["picture"], words["sound"], words["why"]], rows)
    if data.shots:
        lines += ["", f"## {words['shots']} ({len(data.shots)})"]
        lines += md_table(
            [words["shot"], words["start"], words["end"], words["duration"], "TC",
             words["movement"], words["usable"], words["role"], words["happens"]],
            [[str(shot.idx + 1), _clock(shot.start_s), _clock(shot.end_s),
              _seconds(shot.end_s - shot.start_s, english),
              timecode_at(video.start_timecode, shot.start_s, video.fps) or "",
              data.shot_motion(shot),
              str(u) if (u := data.shot_usability(shot.idx)) is not None else "",
              ", ".join(role_name(r, data.language) for r in data.shot_roles(shot.idx)),
              md_text(data.shot_text(shot), 200)] for shot in data.shots],
        )  # fmt: skip
    if facts.segments:
        said = speech_text(facts.segments, 0.0, facts.duration + 1.0)
        lines += ["", f"## {words['speech']}", md_text(said, 2000)]
    now = datetime.now(UTC).astimezone()
    today = f"{now:%Y-%m-%d %H:%M}" if english else f"{now:%d/%m/%Y %H:%M}"
    lines += ["", "---", words["footer"].format(version=__version__, today=today), ""]
    return "\n".join(lines)


def _seconds(value: float, english: bool) -> str:
    text = f"{value:.1f} s"
    return text if english else text.replace(".", ",")


def _resolve_lines(links: list[ResolveLink], language: str = "fr") -> list[str]:
    """Where the video is used in DaVinci Resolve, as read when its timeline was added or
    updated."""
    words = _words(language)
    lines: list[str] = []
    for link in links:
        read = link.synced_at.astimezone()
        when = f"{read:%Y-%m-%d %H:%M}" if language == "en" else f"{read:%d/%m/%Y %H:%M}"
        lines.append(
            f"- **{md_text(link.project.name, 80)}** › timeline "
            f"**{md_text(link.timeline.name, 80)}** ({words['base']} "
            f"{md_text(link.database.name, 60)}, timeline `{link.timeline.id}`, "
            f"{words['read']} {when})"
        )
        for use in link.uses:
            tcs = record_timecodes(use, link.timeline)
            record = f"{tcs[0]} → {tcs[1]}" if tcs else ""
            off = "" if use.enabled and use.track_enabled else words["off"]
            nested = f", {words['in']} « {md_text(use.nested_in, 60)} »" if use.nested_in else ""
            lines.append(
                f"  - {track_label(use)} {record} · source {_clock(use.source_start_s)} → "
                f"{_clock(use.source_end_s)}{nested}{off}"
            )
    lines.append(f"- *{SNAPSHOT_NOTE}.*")
    return lines


def _stored(data: EditingData) -> dict[str, object]:
    """The stored synthesis, its texts in the language asked for."""
    return dict(data.synthesis.data) if data.synthesis.row else {}


def _clock(seconds: float) -> str:
    return format_clock(max(0.0, seconds), millis=True)


def _bullets(items: list[tuple[str, str | None]], english: bool = False) -> list[str]:
    colon = ":" if english else " :"
    return [f"- **{label}**{colon} {value}" for label, value in items if value]


def _file_facts(data: EditingData) -> list[tuple[str, str | None]]:
    video, detail = data.video, data.detail
    words = _words(data.language)
    folder = str(PurePosixPath(video.rel_path).parent)
    orientation = video.orientation.value if video.orientation else ""
    picture = " · ".join(
        part
        for part in (
            f"{video.width}×{video.height}" if video.width and video.height else "",
            f"{video.fps:g} {words['fps']}{' (VFR)' if video.is_vfr else ''}" if video.fps else "",
            (video.video_codec or "").upper(),
            words.get(f"orientation_{orientation}", "") if orientation else "",
        )
        if part
    )
    device = " ".join(p for p in (video.camera_make, video.camera_model) if p)
    return [
        (words["file"], md_text(video.filename, 200)),
        (words["folder"],
         md_text(detail.root.label + ("" if folder == "." else f" / {folder}"), 300)),
        (words["duration"], format_clock(video.duration_s) if video.duration_s else None),
        (words["picture"], md_text(picture) or None),
        (words["start_tc"], video.start_timecode or words["no_tc"]),
        (words["device"], md_text(device, 100) or None),
    ]  # fmt: skip


def _context_facts(data: EditingData) -> list[tuple[str, str | None]]:
    facts, view = data.facts, data.synthesis
    words, english = _words(data.language), data.language == "en"
    shot_at = None
    if facts.capture_local:
        when = facts.capture_local
        shot_at = (f"{when:%Y-%m-%d %H:%M}" if english else f"{when:%d/%m/%Y %H:%M}") + (
            f" ({words['local']})"
        )
    phase = light_phase_name(facts.light_phase, data.language)
    weather = view.weather.line if view.weather is not None and view.weather.category else None
    speech = None
    if facts.transcript_language:
        speech = (
            language_name(facts.transcript_language, "en" if english else "fr")
            or facts.transcript_language
        )
    tags = [str(t.get("label", "")) for t in view.tags] + data.subject_labels
    place = data.texts(facts.place_label) if facts.place_label else None
    return [
        (words["shot_at"], shot_at),
        (words["place"], md_text(place, 200) or None),
        (words["light"], phase),
        (words["weather"], md_text(weather, 300) or None),
        (words["speech"], speech),
        (words["keywords"], md_text(", ".join(dict.fromkeys(t for t in tags if t)), 400) or None),
    ]


# ---------------------------------------------------------------- CSV of chosen videos
LIBRARY_COLUMNS = (
    "fichier", "dossier", "chemin", "statut", "durée (s)", "résolution", "i/s", "orientation",
    "codec", "tournage (heure locale)", "lieu", "lumière", "météo", "appareil", "titre",
    "résumé", "mots-clés", "plans", "parole", "note", "favori", "timelines Resolve",
)  # fmt: skip
LIBRARY_COLUMNS_EN = (
    "file", "folder", "path", "status", "duration (s)", "resolution", "fps", "orientation",
    "codec", "shot (local time)", "place", "light", "weather", "device", "title",
    "summary", "keywords", "shots", "speech", "rating", "favourite", "Resolve timelines",
)  # fmt: skip


def library_csv(c: AppContainer, video_ids: list[str], language: str | None = None) -> ExportFile:
    """One row per chosen video (the order asked; ids no longer in the library are left out),
    its texts and headers in ``language`` (default: the analysis language)."""
    wanted = list(dict.fromkeys(video_ids))
    if not wanted:
        raise InvalidInputError("Sélectionnez au moins une vidéo.")
    if len(wanted) > MAX_LIBRARY_VIDEOS:
        raise InvalidInputError(f"Au plus {MAX_LIBRARY_VIDEOS} vidéos à la fois.")
    tr = texts_in(c, language)
    with c.db.read() as session:
        found = _by_video(session, Video, wanted, key=Video.id)
        rows = _LibraryRows(
            roots={r.id: r.label for r in session.execute(sa.select(LibraryRoot)).scalars()},
            places=_by_video(session, ContextPlace, wanted),
            suns=_by_video(session, ContextSun, wanted),
            weathers=_by_video(session, ContextWeather, wanted),
            syntheses=_by_video(session, VideoSynthesis, wanted),
            transcripts=_by_video(session, Transcript, wanted),
            shots={
                row[0]: int(row[1])
                for row in session.execute(
                    sa.select(Shot.video_id, sa.func.count())
                    .where(Shot.video_id.in_(wanted))
                    .group_by(Shot.video_id)
                )
            },
            resolve=resolve_links(session, [video.path_key for video in found.values()]),
            texts=tr,
            language=language_of(c, tr),
        )
    table = [rows.row(found[video_id]) for video_id in wanted if video_id in found]
    if not table:
        raise NotFoundError("Aucune de ces vidéos n'est dans la bibliothèque.")
    stamp = datetime.now(UTC).astimezone()
    name = f"vfe-videos-{stamp:%Y%m%d-%H%M}_{file_suffix(rows.language)}.csv"
    header = LIBRARY_COLUMNS_EN if rows.language == "en" else LIBRARY_COLUMNS
    text = csv_text(header, table)
    return ExportFile(name, MEDIA_TYPES[ExportFormat.CSV], text.encode("utf-8"))


def _by_video(session: Session, model: Any, ids: list[str], *, key: Any = None) -> dict[str, Any]:
    column = key if key is not None else model.video_id
    rows: list[Any] = list(session.execute(sa.select(model).where(column.in_(ids))).scalars())
    return {row.id if key is not None else row.video_id: row for row in rows}


@dataclass(frozen=True, slots=True)
class _LibraryRows:
    roots: dict[str, str]
    places: dict[str, ContextPlace]
    suns: dict[str, ContextSun]
    weathers: dict[str, ContextWeather]
    syntheses: dict[str, VideoSynthesis]
    transcripts: dict[str, Transcript]
    shots: dict[str, int]
    resolve: dict[str, list[ResolveLink]]  # by path key
    texts: Dictionary
    language: str

    def row(self, video: Video) -> list[CsvCell]:
        english = self.language == "en"
        place = self.places.get(video.id)
        stored = self.syntheses.get(video.id)
        data = translated(stored.data, SYNTHESIS_TEXTS, self.texts) if stored else {}
        sun = self.suns.get(video.id)
        light = sun.light_phase if sun and same_minute(sun.at_utc, video.captured_at) else None
        weather = self.weathers.get(video.id)
        sky = None
        if weather and same_minute(weather.at_utc, video.captured_at):
            sky = weather_label(weather.weather_code, self.language)
            if sky and weather.temperature_c is not None:
                sky += f", {weather.temperature_c:.0f} °C"
        transcript = self.transcripts.get(video.id)
        speech = None
        if transcript is not None:
            if transcript.status == "no_speech":
                speech = "none" if english else "aucune"
            else:
                speech = language_name(transcript.language, "en" if english else "fr")
        local = capture_local(video)
        when = None
        if local:
            when = f"{local:%Y-%m-%d %H:%M}" if english else f"{local:%d/%m/%Y %H:%M}"
        folder = str(PurePosixPath(video.rel_path).parent)
        return [
            video.filename,
            self.roots.get(video.root_id, "") + ("" if folder == "." else f" / {folder}"),
            video.path, video.status.value,
            round(video.duration_s, 3) if video.duration_s is not None else None,
            f"{video.width}×{video.height}" if video.width and video.height else None,
            video.fps, video.orientation.value if video.orientation else None, video.video_codec,
            when, self.texts(place.label) if place and place.label else None,
            light_phase_name(light, self.language), sky,
            " ".join(p for p in (video.camera_make, video.camera_model) if p) or None,
            video.title or data.get("title"), video.summary or data.get("summary"),
            ", ".join(str(t.get("label", "")) for t in data.get("tags") or []),
            self.shots.get(video.id, 0), speech, video.rating, video.favorite,
            "; ".join(f"{link.project.name} › {link.timeline.name}"
                      for link in self.resolve.get(video.path_key, [])) or None,
        ]  # fmt: skip
