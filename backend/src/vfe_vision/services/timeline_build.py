"""« Create a timeline »: the chosen videos, whole and end to end, as
files to import in an editor (OTIO for DaVinci Resolve, FCPXML for Final Cut Pro, one SubRip file
per subtitle track), or as a new timeline in the project open in Resolve. With them, as asked:
markers where their chapters start and over the stretches the application suggests, subtitles of
what is said and of what each shot shows (for Resolve, written next to each video).

Paths are those of the computer Resolve runs on: this one's, or, when Resolve is set on
another computer, the same files through the folder pairs (a video no pair holds is left out; the
subtitle tracks are then written next to the first video, for that computer to read them).
"""

from __future__ import annotations

import io
import zipfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import sqlalchemy as sa

from vfe_vision.core.atomic_io import atomic_write_bytes
from vfe_vision.core.errors import InvalidInputError, NotFoundError
from vfe_vision.db.models import Transcript, Video
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.enums import VideoStatus
from vfe_vision.domain.markers import Marker, chapter_start, highlight_marker, role_marker
from vfe_vision.domain.path_map import FolderPair, to_resolve
from vfe_vision.domain.preferences import folder_pairs
from vfe_vision.domain.timeline_build import (
    DEFAULT_PARTS,
    BuiltTimeline,
    FrameRate,
    TimelineFormat,
    TimelineOrder,
    TimelineParts,
    TimelineRequest,
    TimelineVideo,
    clean_name,
    file_base,
    order_videos,
    placed_chapters,
    placed_suggestions,
    rate_counts,
    rate_named,
    size_counts,
    suggested_format,
    suggested_name,
    timeline_cues,
    timeline_seconds,
    video_markers,
)
from vfe_vision.domain.timeline_files import (
    SubtitleTrack,
    shot_cue,
    subtitle_tracks,
    timeline_bundle,
)
from vfe_vision.domain.transcript import Cue, to_srt
from vfe_vision.domain.translation import Dictionary
from vfe_vision.pipeline.synthesis_facts import capture_local
from vfe_vision.services import audio_text
from vfe_vision.services.container import AppContainer
from vfe_vision.services.editing_data import load_editing_data
from vfe_vision.services.exports import ExportFile
from vfe_vision.services.reading import language_of, texts_in
from vfe_vision.services.subtitle_files import (
    WriteStatus,
    WrittenSubtitles,
    write_file,
    write_subtitles,
)

# The media pool bin of the files and the timeline added to Resolve (« VFE Vision » before the
# application was renamed: a project that has that bin keeps it; its clips are still found and
# reused).
MEDIA_BIN = "Video Frame Expedition"
SUBTITLES_DIR = "timelines"  # in the data folder: the subtitle tracks laid in Resolve
# After the timeline's name, in the files of its tracks written next to its first video for
# Resolve on another computer, apart from the videos' own files (« Été_TIMELINE_FR.srt »).
TRACK_FILE_TAG = "_TIMELINE"
SUGGESTION_KINDS = ("highlight", "establishing", "b_roll", "avoid")
NOTHING_TO_PLACE = (
    "Aucune des vidéos choisies ne peut entrer dans une timeline (fichiers introuvables ou pas "
    "encore examinés)."
)


class SkipReason(StrEnum):
    OFFLINE = "offline"  # the file is no longer where the library knew it
    NOT_EXAMINED = "not_examined"  # its duration and frame rate are not known yet
    NO_FOLDER_PAIR = "no_folder_pair"  # Resolve's computer sees none of its folders


@dataclass(frozen=True, slots=True)
class SkippedVideo:
    video_id: str
    filename: str
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class ResolveBuild:
    """What adding the timeline to Resolve did: the timeline, the subtitle files of the videos."""

    timeline: BuiltTimeline
    subtitle_files: tuple[WrittenSubtitles, ...] = ()


@dataclass(frozen=True, slots=True)
class TimelinePlan:
    name: str
    suggested_name: str
    format: TimelineFormat
    suggested: TimelineFormat
    videos: tuple[TimelineVideo, ...]  # in the timeline's order
    skipped: tuple[SkippedVideo, ...]
    rates: tuple[tuple[FrameRate, int], ...]  # of the videos, the most frequent first
    sizes: tuple[tuple[tuple[int, int], int], ...]
    resolve_host: str | None  # paths are as this computer sees them
    parts: TimelineParts = DEFAULT_PARTS  # what goes with the videos
    language: str = "fr"  # of its texts: markers, shots, track names, read-me

    @property
    def duration_s(self) -> float:
        return timeline_seconds(self.videos, self.format.rate)

    def markers_of(self, video: TimelineVideo) -> list[Marker]:
        """The markers of a video in this timeline, as asked."""
        return video_markers(video, self.format.rate, self.parts)

    @property
    def marker_kinds(self) -> tuple[str, ...]:
        """The kinds of marker this timeline writes."""
        return ("chapter",) * self.parts.chapters + SUGGESTION_KINDS * self.parts.suggestions

    @property
    def subtitle_tracks(self) -> list[SubtitleTrack]:
        return subtitle_tracks(self.videos, self.format, self.parts, self.language)

    # What each choice would bring, whether asked or not (the dialog shows it next to each).
    @property
    def chapter_count(self) -> int:
        return sum(len(placed_chapters(video, self.format.rate)) for video in self.videos)

    @property
    def chaptered(self) -> int:
        """Videos with chapters to show."""
        return sum(1 for video in self.videos if placed_chapters(video, self.format.rate))

    @property
    def suggestion_counts(self) -> Counter[str]:
        """Suggested stretches by kind (highlight, establishing, b_roll, avoid)."""
        return Counter(
            marker.kind
            for video in self.videos
            for marker in placed_suggestions(video, self.format.rate)
        )

    @property
    def speech_cues(self) -> int:
        return len(timeline_cues(self.videos, self.format.rate, lambda v: v.speech))

    @property
    def shot_cues(self) -> int:
        return len(timeline_cues(self.videos, self.format.rate, lambda v: v.shot_texts))


def plan_timeline(
    c: AppContainer,
    video_ids: Sequence[str],
    *,
    order: TimelineOrder = TimelineOrder.CAPTURE,
    name: str | None = None,
    rate: str | None = None,
    size: tuple[int, int] | None = None,
    parts: TimelineParts = DEFAULT_PARTS,
    language: str | None = None,
) -> TimelinePlan:
    """What the timeline of these videos is: which ones, in what order, at what rate and size
    (the most frequent among them unless chosen), those left out with the reason, and what goes
    with them (``parts``: the markers and subtitles asked for), its texts in ``language`` (the
    interface's; default: the analysis language)."""
    prefs = load_preferences(c.db)
    tr = texts_in(c, language)
    host = (prefs.resolve_host or "").strip() or None
    pairs = folder_pairs(prefs) if host else []
    wanted = list(dict.fromkeys(video_ids))
    with c.db.read() as session:
        rows = {v.id: v for v in session.scalars(sa.select(Video).where(Video.id.in_(wanted)))}
    videos: list[TimelineVideo] = []
    skipped: list[SkippedVideo] = []
    days = []
    for video in (rows[video_id] for video_id in wanted if video_id in rows):
        reason = _left_out(video)
        path = video.path
        if reason is None and host:
            mapped = to_resolve(video.path, pairs)
            reason = SkipReason.NO_FOLDER_PAIR if mapped is None else None
            path = mapped or path
        if reason is not None:
            skipped.append(SkippedVideo(video.id, video.filename, reason))
            continue
        videos.append(_timeline_video(c, video, path, tr))
        if (local := capture_local(video)) is not None:
            days.append(local.date())
    ordered = tuple(order_videos(videos, order))
    suggested = suggested_format(ordered)
    width, height = size or (suggested.width, suggested.height)
    suggestion = suggested_name(days)
    return TimelinePlan(
        name=clean_name(name) or suggestion,
        suggested_name=suggestion,
        format=TimelineFormat(rate=rate_named(rate) or suggested.rate, width=width, height=height),
        suggested=suggested,
        videos=ordered,
        skipped=tuple(skipped),
        rates=tuple(rate_counts(ordered)),
        sizes=tuple(size_counts(ordered)),
        resolve_host=host,
        parts=parts,
        language=language_of(c, tr),
    )


def _left_out(video: Video) -> SkipReason | None:
    if video.status == VideoStatus.OFFLINE:
        return SkipReason.OFFLINE
    if not video.duration_s or video.duration_s <= 0 or not video.fps or video.fps <= 0:
        return SkipReason.NOT_EXAMINED
    return None


def _timeline_video(c: AppContainer, video: Video, path: str, tr: Dictionary) -> TimelineVideo:
    """A video of the timeline, with what may go with it: where its chapters start, the
    stretches suggested (highlights, then editing roles), its subtitles, what its shots show;
    its texts in the language of ``tr``."""
    language = language_of(c, tr)
    data = load_editing_data(c, video.id, tr=tr)
    view = data.synthesis
    per_role: Counter[str] = Counter()
    roles = []
    for suggestion in view.suggestions:
        per_role[suggestion.role] += 1
        roles.append(role_marker(suggestion.role, per_role[suggestion.role], suggestion.clip,
                                 suggestion.usability.score, language=language))  # fmt: skip
    try:
        speech: tuple[Cue, ...] = tuple(audio_text.subtitles(c, video.id).cues)
    except NotFoundError:  # nothing said, or not transcribed yet
        speech = ()
    with c.db.read() as session:
        spoken = session.execute(
            sa.select(Transcript.language).where(Transcript.video_id == video.id)
        ).scalar_one_or_none()
    return TimelineVideo(
        video_id=video.id,
        filename=video.filename,
        path=path,
        duration_s=video.duration_s or 0.0,
        fps=video.fps or 0.0,
        variable_rate=bool(video.is_vfr),
        width=video.width,
        height=video.height,
        start_timecode=video.start_timecode,
        has_audio=video.has_audio is not False,
        captured_at=video.captured_at,
        chapters=tuple(
            chapter_start(ch.index, ch.start_s, ch.title, ch.summary, language=language)
            for ch in view.chapters
        ),
        suggestions=tuple(
            [highlight_marker(h.rank, h.clip, h.reason, language=language) for h in view.highlights]
            + roles
        ),
        speech=speech,
        speech_language=(video.transcript_language or spoken) if speech else None,
        shot_texts=tuple(
            cue
            for shot in data.shots
            if (cue := shot_cue(shot.start_s, shot.end_s, data.shot_text(shot))) is not None
        ),
    )


def timeline_file(plan: TimelinePlan) -> ExportFile:
    """The files to import by hand, in one ZIP: the timeline as OTIO (DaVinci Resolve: File ›
    Import › Timeline) and as FCPXML (Final Cut Pro), a SubRip file per subtitle track, and how
    to import them."""
    if not plan.videos:
        raise InvalidInputError(NOTHING_TO_PLACE)
    base = file_base(plan.name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in timeline_bundle(
            base, plan.videos, plan.format, plan.parts, plan.language
        ):
            bundle.writestr(name, data)
    return ExportFile(filename=f"{base}.zip", media_type="application/zip", data=buffer.getvalue())


def build_in_resolve(c: AppContainer, plan: TimelinePlan) -> ResolveBuild:
    """The timeline built in the project open in Resolve, and made its current one. The
    subtitles asked for are written next to each video and laid on the timeline, one
    track per kind (« Transcription », then « Plans »; « Transcript », « Shots » in English),
    from files in the timeline's time in the data folder. Resolve on another
    computer does not see that folder: those files go next to the timeline's first video, which
    it opens through the folder pairs, as the videos; when no folder of the videos takes them, it
    gets the videos' own files in the bin, to lay by hand."""
    if not plan.videos:
        raise InvalidInputError(NOTHING_TO_PLACE)
    results = _write_subtitles(c, plan)
    tracks: tuple[tuple[str, str], ...] = ()
    loose: tuple[str, ...] = ()
    if plan.resolve_host is None:
        tracks = _track_files(c, plan)
    else:
        pairs = folder_pairs(load_preferences(c.db))
        written, tracks = _tracks_beside_videos(c, plan, pairs)
        results += written
        if not tracks:
            loose = tuple(
                path
                for done in results
                if done.status == WriteStatus.WRITTEN
                and (path := to_resolve(str(done.path), pairs))
            )
    request = TimelineRequest(
        name=plan.name,
        format=plan.format,
        clips=tuple((video.video_id, video.path) for video in plan.videos),
        folder=MEDIA_BIN,
        markers={
            video.video_id: tuple(markers)
            for video in plan.videos
            if (markers := plan.markers_of(video))
        },
        marker_kinds=plan.marker_kinds,
        subtitle_tracks=tracks,
        subtitle_files=loose,
    )
    return ResolveBuild(c.resolve_builder.build_timeline(request), tuple(results))


def _track_files(c: AppContainer, plan: TimelinePlan) -> tuple[tuple[str, str], ...]:
    """Each subtitle track as a SubRip file in the timeline's time, in the data folder (one
    folder per timeline name, written again at each build): ``(track name, path)``."""
    tracks = plan.subtitle_tracks
    if not tracks:
        return ()
    base = file_base(plan.name)
    folder = c.settings.data_dir / SUBTITLES_DIR / base
    files = []
    for track in tracks:
        path = folder / track.file_name(base)
        atomic_write_bytes(path, to_srt(track.cues).encode("utf-8"))
        files.append((track.label, str(path)))
    return tuple(files)


def _tracks_beside_videos(
    c: AppContainer, plan: TimelinePlan, pairs: Sequence[FolderPair]
) -> tuple[list[WrittenSubtitles], tuple[tuple[str, str], ...]]:
    """Each subtitle track as a SubRip file in the timeline's time, next to the timeline's first
    video (``<name>_TIMELINE_FR.srt``…; the next folder of its videos when one cannot take it),
    for Resolve on another computer: what was written, or why not, and ``(track name, path as
    Resolve sees it)`` of each track written."""
    tracks = plan.subtitle_tracks
    if not tracks:
        return [], ()
    local = _local_paths(c, plan)
    folders: dict[Path, str] = {}  # the videos' folders, in the timeline's order
    for video in plan.videos:
        if video.video_id in local:
            folders.setdefault(Path(local[video.video_id]).parent, video.video_id)
    base = file_base(plan.name) + TRACK_FILE_TAG
    results: list[WrittenSubtitles] = []
    laid: list[tuple[str, str]] = []
    for track in tracks:
        data = to_srt(track.cues).encode("utf-8")
        done: WrittenSubtitles | None = None
        for folder, video_id in folders.items():
            done = write_file(c.db, video_id, track.part, folder / track.file_name(base), data)
            there = to_resolve(str(done.path), pairs)
            if done.status == WriteStatus.WRITTEN and there:
                laid.append((track.label, there))
                break
        if done is not None:  # the file written, else why the last folder did not take it
            results.append(done)
    return results, tuple(laid)


def _write_subtitles(c: AppContainer, plan: TimelinePlan) -> list[WrittenSubtitles]:
    """The subtitle files asked for, next to each video (its own time), in the timeline's order."""
    if not (plan.parts.transcript or plan.parts.shots):
        return []
    local = _local_paths(c, plan)
    return [
        write_subtitles(c.db, video.video_id, Path(local[video.video_id]), track)
        for video in plan.videos
        if video.video_id in local
        for track in subtitle_tracks([video], plan.format, plan.parts, plan.language)
    ]


def _local_paths(c: AppContainer, plan: TimelinePlan) -> dict[str, str]:
    """The paths of the timeline's videos on this computer."""
    ids = [video.video_id for video in plan.videos]
    with c.db.read() as session:
        rows = session.execute(sa.select(Video.id, Video.path).where(Video.id.in_(ids)))
        return {row.id: row.path for row in rows}
