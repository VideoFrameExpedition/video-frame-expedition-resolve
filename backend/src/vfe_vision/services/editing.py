"""Editing with DaVinci Resolve: which analysed video a timeline clip is, what
lies in its range, where to cut it safely and how to frame it.

- ``match_clips``: a file path (and optionally a source range) as Resolve gives it → the video
  of the library, by normalised path, then file name and size, then content fingerprint (the
  scanner's xxh3, when the file can be read), then file name alone (ambiguous → candidates).
- ``cut_points``: safe in/out points for a range (``domain.cut_points``) and the highlights of
  the synthesis that overlap it (their J/L-cut sound ranges).
- ``reframe``: one static crop of the timeline's shape for a range, from the subject's boxes,
  and the Transform values that apply it in Resolve (``domain.framing``).
"""

from __future__ import annotations

import statistics
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.core.errors import InvalidInputError
from vfe_vision.db.models import TimelineBinItem, Video
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.timeline_bins import ITEM_KEY
from vfe_vision.domain.clip_paths import file_name, match_key
from vfe_vision.domain.cut_points import CutPoints, safe_cut
from vfe_vision.domain.enums import VideoStatus
from vfe_vision.domain.framing import (
    MAX_UPSCALE,
    Box,
    Framing,
    framing_segments,
    static_framing,
)
from vfe_vision.domain.path_map import FolderPair, from_resolve
from vfe_vision.domain.preferences import folder_pairs
from vfe_vision.domain.subjects import Category, Subject
from vfe_vision.domain.synthesis_input import speech_text
from vfe_vision.domain.transcript import clean_untrusted
from vfe_vision.jobs.scan import fingerprint
from vfe_vision.services import subjects, videos
from vfe_vision.services.container import AppContainer
from vfe_vision.services.editing_data import EditingData, load_editing_data
from vfe_vision.services.synthesis import ChapterView, HighlightView

MAX_ITEMS = 100
RANGE_FRAMES = 12  # keyframes with their subjects given for a range
SPEECH_CHARS = 1200


class MatchMethod(StrEnum):
    PATH = "path"
    RESOLVE_ID = "resolve_id"  # the media pool clip of a timeline added to the library
    NAME_SIZE = "name_size"
    FINGERPRINT = "fingerprint"
    NAME = "name"
    NONE = "none"


CONFIDENCE = {
    MatchMethod.PATH: 1.0, MatchMethod.FINGERPRINT: 0.99, MatchMethod.RESOLVE_ID: 0.95,
    MatchMethod.NAME_SIZE: 0.9, MatchMethod.NAME: 0.5, MatchMethod.NONE: 0.0,
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class ClipQuery:
    """A clip as Claude reads it from a Resolve timeline: its file, optionally its media pool
    item's unique id, and optionally the source range it uses — in seconds of the file
    (preferred: right whatever the timeline's frame rate) or in frames (0-based from the file's
    first frame) at the clip's rate."""

    file_path: str
    start_frame: int | None = None
    end_frame: int | None = None
    fps: float | None = None
    start_s: float | None = None
    end_s: float | None = None
    clip_uid: str | None = None


@dataclass(frozen=True, slots=True)
class RangeShot:
    idx: int  # 0-based
    start_s: float
    end_s: float
    motion: str
    usability: int | None
    roles: list[str]
    text: str


@dataclass(frozen=True, slots=True)
class RangeSubject:
    t_s: float
    keyframe: int  # 0-based
    label: str
    category: str
    box: list[float]


@dataclass(frozen=True, slots=True)
class RangeView:
    start_s: float
    end_s: float
    fps: float | None
    shots: list[RangeShot]
    speech: str
    subjects: list[RangeSubject]
    chapters: list[ChapterView]
    highlights: list[HighlightView]
    cut: CutPoints | None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ClipMatch:
    query: ClipQuery
    method: MatchMethod
    status: str  # ready | offline | not_analysed | ambiguous | unknown
    video: Video | None = None
    candidates: list[Video] = field(default_factory=list)
    note: str | None = None
    range: RangeView | None = None

    @property
    def confidence(self) -> float:
        return CONFIDENCE[self.method]


def status_of(video: Video) -> str:
    if video.status == VideoStatus.OFFLINE:
        return "offline"
    if video.status in {VideoStatus.READY, VideoStatus.PARTIAL}:
        return "ready"
    return "not_analysed"


# ---------------------------------------------------------------- match_clips
@dataclass(slots=True)
class _Library:
    by_key: dict[str, Video]
    by_name: dict[str, list[Video]]
    by_fingerprint: dict[str, list[Video]]
    by_path_key: dict[str, Video] = field(default_factory=dict)
    # media pool uid → (the video's path key, the file name) of the timelines added
    by_clip: dict[str, list[tuple[str, str]]] = field(default_factory=dict)


def match_clips(c: AppContainer, queries: list[ClipQuery]) -> list[ClipMatch]:
    """The video of each clip, and what lies in its range when one is given."""
    if not queries:
        raise InvalidInputError("Indiquez au moins un clip.")
    if len(queries) > MAX_ITEMS:
        raise InvalidInputError(f"Au plus {MAX_ITEMS} clips à la fois.")
    case_insensitive = sys.platform == "win32"
    with c.db.read() as session:
        rows = list(session.execute(sa.select(Video)).scalars())
        uids = {q.clip_uid for q in queries if q.clip_uid}
        clips = _timeline_clips(session, uids) if uids else {}
    library = _Library({}, defaultdict(list), defaultdict(list), by_clip=clips)
    for video in rows:
        library.by_key[match_key(video.path, case_insensitive=case_insensitive)] = video
        library.by_path_key[video.path_key] = video
        library.by_name[video.filename.casefold()].append(video)
        library.by_fingerprint[video.fingerprint].append(video)
    loaded: dict[str, EditingData] = {}
    matches: list[ClipMatch] = []
    pairs = folder_pairs(load_preferences(c.db))
    for query in queries:
        found = _match(query, library, case_insensitive, pairs)
        if found.video is not None and _has_range(query):
            data = loaded.get(found.video.id)
            if data is None:
                data = loaded[found.video.id] = load_editing_data(c, found.video.id)
            found = ClipMatch(
                found.query, found.method, found.status, found.video, found.candidates,
                found.note, _range(c, data, query),
            )  # fmt: skip
        matches.append(found)
    return matches


def _timeline_clips(session: Session, uids: set[str]) -> dict[str, list[tuple[str, str]]]:
    """Where these media pool clips were seen in the timelines added to the library: the
    path key of the video each stands for, and the file name."""
    found: dict[str, list[tuple[str, str]]] = defaultdict(list)
    rows = session.execute(sa.select(ITEM_KEY, TimelineBinItem.path, TimelineBinItem.uses)).all()
    for key, path, uses in rows:
        for uid in {str(use.get("media_pool_item_id") or "") for use in uses} & uids:
            found[uid].append((key, file_name(path)))
    return found


def _has_range(query: ClipQuery) -> bool:
    return any(
        value is not None
        for value in (query.start_frame, query.end_frame, query.start_s, query.end_s)
    )


def _match(
    query: ClipQuery, library: _Library, case_insensitive: bool, pairs: list[FolderPair]
) -> ClipMatch:
    # Resolve on another computer gives its own paths: the file as this computer sees it.
    path = from_resolve(query.file_path, pairs) or query.file_path
    video = library.by_key.get(match_key(path, case_insensitive=case_insensitive))
    if video is not None:
        return ClipMatch(query, MatchMethod.PATH, status_of(video), video)
    # The same media pool clip in a timeline added to the library, under the same file name
    # (a relinked or replaced clip keeps its id: the name guards against another file).
    name = file_name(path).casefold()
    for key, seen_name in library.by_clip.get(query.clip_uid or "", []):
        known = library.by_path_key.get(key)
        if known is not None and seen_name.casefold() == name:
            return _elsewhere(query, MatchMethod.RESOLVE_ID, known)
    same_name = library.by_name.get(name, [])
    size = _size(path)
    sized = [v for v in same_name if size is not None and v.size_bytes == size]
    if len(sized) == 1:
        return _elsewhere(query, MatchMethod.NAME_SIZE, sized[0])
    content = _content(path, size, library)
    if content:
        best = sorted(content, key=lambda v: v.status == VideoStatus.OFFLINE)
        return _elsewhere(query, MatchMethod.FINGERPRINT, best[0], others=best[1:])
    candidates = sized or same_name
    if len(candidates) == 1:
        return _elsewhere(query, MatchMethod.NAME, candidates[0])
    if candidates:
        return ClipMatch(
            query, MatchMethod.NONE, "ambiguous", candidates=candidates,
            note="Plusieurs vidéos de la bibliothèque portent ce nom : précisez avec le chemin "
            "exact du fichier.",
        )  # fmt: skip
    return ClipMatch(
        query, MatchMethod.NONE, "unknown",
        note="Ce fichier n'est pas dans la bibliothèque : ajoutez sa timeline "
        "(import_resolve_timeline, ou « Importer depuis Resolve » dans l'application), ou son "
        "dossier.",
    )  # fmt: skip


def _elsewhere(
    query: ClipQuery, method: MatchMethod, video: Video, *, others: list[Video] | None = None
) -> ClipMatch:
    """A video found at another path than the clip's: why, and what to do about it."""
    if method == MatchMethod.NAME:
        note = "Même nom de fichier seulement (taille et contenu non vérifiés) : à confirmer."
    elif video.status == VideoStatus.OFFLINE:
        note = (
            "Ce fichier est dans la bibliothèque à un emplacement devenu inaccessible : "
            "« Rescanner » son dossier reliera le fichier déplacé."
        )
    elif method == MatchMethod.FINGERPRINT:
        note = f"Même contenu que le fichier de la bibliothèque : {video.path}"
    elif method == MatchMethod.RESOLVE_ID:
        note = (
            "Même clip du media pool qu'une timeline ajoutée à la bibliothèque, fichier de la "
            f"bibliothèque : {video.path}"
        )
    else:
        note = f"Même nom et même taille que le fichier de la bibliothèque : {video.path}"
    return ClipMatch(query, method, status_of(video), video, others or [], note)


def _size(path: str) -> int | None:
    try:
        return Path(path).stat().st_size
    except (OSError, ValueError):
        return None


def _content(path: str, size: int | None, library: _Library) -> list[Video]:
    """Videos of the library with the file's content (same xxh3 fingerprint as the scanner)."""
    if size is None:
        return []
    try:
        digest = fingerprint(Path(path), size)
    except (OSError, ValueError):
        return []
    return list(library.by_fingerprint.get(digest, []))


def _range(c: AppContainer, data: EditingData, query: ClipQuery) -> RangeView:
    """What lies in the clip's source range: shots, speech, subjects, chapters, highlights, and
    the safe cut points."""
    video, facts, view = data.video, data.facts, data.synthesis
    fps = query.fps or video.fps
    duration = facts.duration or video.duration_s or 0.0
    if query.start_s is not None or query.end_s is not None:  # seconds of the file
        start = max(0.0, query.start_s or 0.0)
        end = query.end_s if query.end_s is not None else duration
    elif fps:
        start = max(0.0, (query.start_frame or 0) / fps)
        end = query.end_frame / fps if query.end_frame is not None else duration
    else:
        return RangeView(0.0, duration, None, [], "", [], [], [], None,
                         "Fréquence d'images inconnue : indiquez fps.")  # fmt: skip
    if duration:
        end = min(end, duration)
    if end <= start:
        return RangeView(start, end, fps, [], "", [], [], [], None,
                         "Intervalle vide ou hors de la vidéo.")  # fmt: skip
    shots = [
        RangeShot(
            shot.idx, shot.start_s, shot.end_s, data.shot_motion(shot),
            data.shot_usability(shot.idx), data.shot_roles(shot.idx), data.shot_text(shot),
        )
        for shot in data.shots
        if shot.end_s > start and shot.start_s < end
    ]  # fmt: skip
    try:
        cut: CutPoints | None = safe_cut(facts, start, end)
    except ValueError:
        cut = None
    return RangeView(
        start_s=start,
        end_s=end,
        fps=fps,
        shots=shots,
        speech=clean_untrusted(speech_text(facts.segments, start, end))[:SPEECH_CHARS],
        subjects=_main_subjects(c, video.id, start, end),
        chapters=[ch for ch in view.chapters if ch.end_s > start and ch.start_s < end],
        highlights=[
            h for h in view.highlights if h.clip.picture_out > start and h.clip.picture_in < end
        ],
        cut=cut,
    )


def _main_subjects(c: AppContainer, video_id: str, start: float, end: float) -> list[RangeSubject]:
    """The main subject(s) at the keyframes of the range, spread over it."""
    frames = [f for f in subjects.get_subjects(c, video_id, start_s=start, end_s=end).frames
              if f.duplicate_of is None]  # fmt: skip
    if len(frames) > RANGE_FRAMES:
        step = len(frames) / RANGE_FRAMES
        frames = [frames[int(i * step)] for i in range(RANGE_FRAMES)]
    return [
        RangeSubject(frame.t_s, frame.idx, clean_untrusted(s.label)[:40], s.category.value,
                     s.box.rounded(3))
        for frame in frames
        for s in frame.subjects
        if s.main
    ]  # fmt: skip


# ---------------------------------------------------------------- cut points
@dataclass(frozen=True, slots=True)
class CutView:
    video: Video
    points: CutPoints
    highlights: list[HighlightView]  # of the synthesis, overlapping the range


def cut_points(c: AppContainer, video_id: str, t_start: float, t_end: float) -> CutView:
    data = load_editing_data(c, video_id)
    try:
        points = safe_cut(data.facts, t_start, t_end)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    lo, hi = points.requested_in, points.requested_out
    overlapping = [
        h for h in data.synthesis.highlights if h.clip.picture_out > lo and h.clip.picture_in < hi
    ]
    return CutView(data.video, points, overlapping)


# ---------------------------------------------------------------- framing
@dataclass(frozen=True, slots=True)
class FramedFrame:
    t_s: float
    keyframe: int  # 0-based
    label: str
    box: Box


@dataclass(frozen=True, slots=True)
class FramingPart:
    start_s: float
    end_s: float
    framing: Framing
    frames: list[FramedFrame]


@dataclass(frozen=True, slots=True)
class ReframeView:
    video: Video
    width: int
    height: int
    start_s: float
    end_s: float
    subject: str | None  # what was framed: a label, or None when nobody was found
    framing: Framing  # for the whole range
    frames: list[FramedFrame]
    segments: list[FramingPart]  # several static framings when the subject moves too much
    notes: list[str]


def reframe(
    c: AppContainer,
    video_id: str,
    t_start: float,
    t_end: float,
    *,
    timeline_width: int,
    timeline_height: int,
    subject: str | None = None,
    headroom: float | None = None,
) -> ReframeView:
    """A static framing of [t_start, t_end] for a timeline of that size (``domain.framing``)."""
    video = videos.get_video(c, video_id).video
    if not video.width or not video.height:
        raise InvalidInputError("Dimensions de la vidéo inconnues : l'étape « probe » manque.")
    if t_end <= t_start:
        raise InvalidInputError("intervalle vide : la fin doit suivre le début")
    width, height = video.width, video.height
    view = subjects.get_subjects(c, video_id, start_s=t_start, end_s=t_end)
    chosen: list[FramedFrame] = []
    for frame in view.frames:
        being = pick_subject(frame.subjects, subject)
        if being is not None:
            box = being.face if subject == "face" and being.face else being.box
            chosen.append(FramedFrame(frame.t_s, frame.idx, clean_untrusted(being.label)[:40],
                                      (box.x1, box.y1, box.x2, box.y2)))  # fmt: skip
    boxes = [f.box for f in chosen]
    framing = static_framing(
        boxes, width, height, timeline_width, timeline_height, headroom=headroom
    )
    notes: list[str] = []
    if not chosen:
        notes.append(
            "Aucun sujet trouvé dans cet intervalle"
            + (f" (« {clean_untrusted(subject)[:40]} »)" if subject else "")
            + " : cadrage centré."
        )
    segments: list[FramingPart] = []
    if framing.coverage < 1.0 - 1e-6 and len(chosen) > 1:
        runs = framing_segments(boxes, width, height, timeline_width, timeline_height)
        for first, last in runs:
            part = chosen[first : last + 1]
            begin = t_start if first == 0 else part[0].t_s
            finish = t_end if last == len(chosen) - 1 else chosen[last + 1].t_s
            segments.append(
                FramingPart(begin, finish, static_framing(
                    [f.box for f in part], width, height, timeline_width, timeline_height,
                    headroom=headroom), part)
            )  # fmt: skip
        if len(segments) > 1:
            notes.append(
                f"Le sujet se déplace trop pour un seul cadre ({framing.coverage:.0%} de la "
                f"zone qu'il parcourt y tient) : {len(segments)} cadres fixes proposés dans "
                "« segments » (coupez le plan à leurs limites)."
            )
        else:
            segments = []
    if framing.upscale > MAX_UPSCALE:
        notes.append(
            f"Image agrandie {framing.upscale:.1f} fois : perte de netteté visible au-delà de "
            f"{MAX_UPSCALE:g} fois."
        )
    label = _label(chosen)
    return ReframeView(
        video=video, width=width, height=height, start_s=t_start, end_s=t_end, subject=label,
        framing=framing, frames=chosen, segments=segments, notes=notes,
    )  # fmt: skip


def pick_subject(beings: list[Subject], wanted: str | None) -> Subject | None:
    """The being to frame at one keyframe: the largest main subject by default; with ``wanted``,
    the largest being of that category (``face``: a person whose face was found) or whose name
    starts with it (accents and case ignored), main ones first."""
    if wanted is None:
        pool = [b for b in beings if b.main]
    elif wanted == "face":
        pool = [b for b in beings if b.face is not None]
    elif wanted in {c.value for c in Category}:
        pool = [b for b in beings if b.category.value == wanted]
    else:
        key = _fold(wanted)
        pool = [b for b in beings if any(w.startswith(key) for w in _fold(b.label).split())
                or _fold(b.label).startswith(key)]  # fmt: skip
    if not pool:
        return None
    return max(pool, key=lambda b: (b.main, b.box.area))


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).strip()


def _label(frames: list[FramedFrame]) -> str | None:
    if not frames:
        return None
    return statistics.mode(f.label for f in frames)
