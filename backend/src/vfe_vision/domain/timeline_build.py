"""A timeline made of chosen videos: the videos whole, end to end on one track, in
the chosen order, at one frame rate and one picture size.

It is written as FCPXML 1.10, which DaVinci Resolve (File › Import › Timeline) and Final Cut Pro
import with their media, or built by the application in the project open in Resolve. Every time
in the file is a whole number of frames: the timeline's for positions and lengths, each file's
own for where it starts and for its markers.

What goes with the videos is chosen: markers where chapters start and over the
stretches the application suggests, subtitles of what is said and of what each shot shows.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from fractions import Fraction
from pathlib import PurePosixPath, PureWindowsPath

from vfe_vision.domain.markers import Marker
from vfe_vision.domain.resolve_timeline import ResolveProjectRef
from vfe_vision.domain.timecode import timecode_frames
from vfe_vision.domain.transcript import Cue

FCPXML_VERSION = "1.10"
EVENT_NAME = "Video Frame Expedition"
TIMELINE_START = "01:00:00:00"  # DaVinci Resolve's default first frame
MAX_NAME_CHARS = 120


@dataclass(frozen=True, slots=True)
class FrameRate:
    """A frame rate editors offer, ``num / den`` frames per second, named as Resolve writes it."""

    name: str  # "29.97"
    num: int
    den: int

    @property
    def fps(self) -> float:
        return self.num / self.den

    @property
    def nominal(self) -> int:
        """Frames counted per second of timecode (30 at 29.97)."""
        return round(self.fps)

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.num, self.den)

    def seconds(self, frames: int) -> Fraction:
        return Fraction(frames * self.den, self.num)


RATES = (
    FrameRate("23.976", 24000, 1001),
    FrameRate("24", 24, 1),
    FrameRate("25", 25, 1),
    FrameRate("29.97", 30000, 1001),
    FrameRate("30", 30, 1),
    FrameRate("47.952", 48000, 1001),
    FrameRate("48", 48, 1),
    FrameRate("50", 50, 1),
    FrameRate("59.94", 60000, 1001),
    FrameRate("60", 60, 1),
    FrameRate("100", 100, 1),
    FrameRate("119.88", 120000, 1001),
    FrameRate("120", 120, 1),
)
RATE_NAMES = tuple(rate.name for rate in RATES)
DEFAULT_RATE = RATES[2]  # 25
DEFAULT_SIZE = (1920, 1080)
RATE_TOLERANCE = 0.005  # a file within 0.5 % of a standard rate plays at it
_NTSC = {30: "29.97", 60: "59.94"}


def rate_named(name: str | None) -> FrameRate | None:
    return next((rate for rate in RATES if rate.name == name), None)


def standard_rate(fps: float | None, *, variable: bool = False) -> FrameRate | None:
    """The standard rate an editor plays a file at: the closest one within 0.5 % (None for an
    odd rate such as 26 i/s). A phone file at a variable 30 or 60 i/s reads as 29.97 or 59.94
    in DaVinci Resolve (measured on a Samsung file)."""
    if fps is None or not math.isfinite(fps) or fps <= 0:
        return None
    best = min(RATES, key=lambda rate: abs(rate.fps - fps))
    if abs(best.fps / fps - 1) > RATE_TOLERANCE:
        return None
    if variable and best.den == 1 and best.num in _NTSC:
        return rate_named(_NTSC[best.num])
    return best


@dataclass(frozen=True, slots=True)
class TimelineParts:
    """What goes with the videos: subtitles of what is said and of what each shot
    shows (one track each), markers over the suggested stretches and where chapters start."""

    transcript: bool = True
    suggestions: bool = True
    chapters: bool = True
    shots: bool = False


DEFAULT_PARTS = TimelineParts()


class TimelineOrder(StrEnum):
    CAPTURE = "capture"  # shooting time, then name; videos without one at the end
    NAME = "name"  # file name, numbers counted as numbers
    SELECTION = "selection"  # the order the videos were given in


@dataclass(frozen=True, slots=True)
class TimelineVideo:
    video_id: str
    filename: str
    path: str  # as the editor that opens the timeline sees it
    duration_s: float
    fps: float  # the file's own rate (its average for a variable rate)
    variable_rate: bool = False
    width: int | None = None  # as shown, rotation applied
    height: int | None = None
    start_timecode: str | None = None
    has_audio: bool = True
    captured_at: datetime | None = None  # UTC, for the order
    chapters: tuple[Marker, ...] = ()  # where its chapters start, in seconds of the file
    suggestions: tuple[Marker, ...] = ()  # highlights and editing roles: picture ranges
    speech: tuple[Cue, ...] = ()  # subtitles of what is said, in seconds of the file
    shot_texts: tuple[Cue, ...] = ()  # what each shot shows, in seconds of the file
    speech_language: str | None = None  # what is said is in it (Whisper code), when known

    @property
    def rate(self) -> FrameRate | None:
        return standard_rate(self.fps, variable=self.variable_rate)

    @property
    def rate_fraction(self) -> Fraction:
        """The file's rate as editors count its frames: its standard rate, else its own."""
        rate = self.rate
        return Fraction(rate.num, rate.den) if rate else Fraction(self.fps).limit_denominator(1001)

    @property
    def frames(self) -> int:
        """Frames in the file (rounded down: never past its last one)."""
        return max(0, math.floor(self.duration_s * self.fps + 1e-6))


@dataclass(frozen=True, slots=True)
class TimelineFormat:
    rate: FrameRate
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class TimelineRequest:
    """A timeline to build in the project open in Resolve: the clips in order, as
    ``(video id, path as Resolve sees it)``; ``folder``: the media pool bin of what is added;
    the markers of each video; the kinds of marker it writes (those an earlier build left are
    replaced); the subtitle tracks to lay, as ``(name, path)`` of a SubRip file in the timeline's
    time; subtitle files only to import into ``folder`` (paths)."""

    name: str
    format: TimelineFormat
    clips: tuple[tuple[str, str], ...]
    folder: str
    markers: dict[str, tuple[Marker, ...]] = field(default_factory=dict)  # by video id
    marker_kinds: tuple[str, ...] = ()
    subtitle_tracks: tuple[tuple[str, str], ...] = ()
    subtitle_files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BuiltTimeline:
    """What Resolve did: the timeline (its name may carry « (2) »), the rate and size it has (the
    project's rate when Resolve refused another), the clips placed, the files imported into
    ``folder`` or found in the media pool, those Resolve could not open, the markers written
    (and those it refused: past the end of its clip, or no free frame near), the subtitle files
    imported into ``folder`` (their clips' names) and the subtitle tracks laid (their names)."""

    project: ResolveProjectRef
    timeline_id: str
    timeline_name: str
    fps: float | None
    width: int | None
    height: int | None
    clips: int
    imported: int
    reused: int
    missing: tuple[str, ...]
    folder: str
    markers: int = 0
    markers_missed: int = 0
    subtitles: tuple[str, ...] = ()
    subtitles_laid: tuple[str, ...] = ()


def order_videos(videos: Sequence[TimelineVideo], order: TimelineOrder) -> list[TimelineVideo]:
    if order == TimelineOrder.SELECTION:
        return list(videos)
    if order == TimelineOrder.NAME:
        return sorted(videos, key=lambda v: name_key(v.filename))

    def by_capture(video: TimelineVideo) -> tuple[bool, datetime, list[tuple[int, int | str]]]:
        taken = video.captured_at
        if taken is not None:
            taken = taken.replace(tzinfo=UTC) if taken.tzinfo is None else taken.astimezone(UTC)
        return (taken is None, taken or _EPOCH, name_key(video.filename))

    return sorted(videos, key=by_capture)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def name_key(name: str) -> list[tuple[int, int | str]]:
    """Sorts « clip2 » before « clip10 », letter case aside."""
    return [(0, int(part)) if part.isdigit() else (1, part) for part in
            re.split(r"(\d+)", name.casefold()) if part]  # fmt: skip


def rate_counts(videos: Sequence[TimelineVideo]) -> list[tuple[FrameRate, int]]:
    """The standard rates of the videos, the most frequent first (ties: the most footage)."""
    return _most(videos, lambda video: video.rate)


def size_counts(videos: Sequence[TimelineVideo]) -> list[tuple[tuple[int, int], int]]:
    """The picture sizes of the videos, the most frequent first (ties: the most footage)."""
    return _most(videos, lambda v: (v.width, v.height) if v.width and v.height else None)


def _most[T](
    videos: Sequence[TimelineVideo], key: Callable[[TimelineVideo], T | None]
) -> list[tuple[T, int]]:
    counts: Counter[T] = Counter()
    footage: dict[T, float] = {}
    for video in videos:
        if (value := key(video)) is not None:
            counts[value] += 1
            footage[value] = footage.get(value, 0.0) + video.duration_s
    return sorted(counts.items(), key=lambda item: (-item[1], -footage[item[0]]))


def suggested_format(videos: Sequence[TimelineVideo]) -> TimelineFormat:
    """The rate and the picture size most videos have."""
    rates, sizes = rate_counts(videos), size_counts(videos)
    width, height = sizes[0][0] if sizes else DEFAULT_SIZE
    return TimelineFormat(rate=rates[0][0] if rates else DEFAULT_RATE, width=width, height=height)


def timeline_frames(video: TimelineVideo, rate: FrameRate) -> int:
    """Frames of a timeline at ``rate`` that the whole file fills: its frames at its own rate,
    played at their real speed (as Resolve plays mixed rates), rounded down."""
    own = video.rate_fraction
    if own == rate.fraction:
        return video.frames
    return math.floor(video.frames / own * rate.fraction + Fraction(1, 10**6))


def shown_seconds(video: TimelineVideo, rate: FrameRate) -> float:
    """What the timeline shows of the video, in seconds."""
    return float(rate.seconds(timeline_frames(video, rate)))


def placed_chapters(video: TimelineVideo, rate: FrameRate) -> list[Marker]:
    """The chapter starts that fall within what the timeline shows of the video."""
    shown_s = shown_seconds(video, rate)
    return [marker for marker in video.chapters if 0 <= marker.t_s < shown_s]


def placed_suggestions(video: TimelineVideo, rate: FrameRate) -> list[Marker]:
    """The suggested stretches that start within what the timeline shows, cut at its end."""
    shown_s = shown_seconds(video, rate)
    return [
        replace(marker, duration_s=min(marker.duration_s, shown_s - marker.t_s))
        for marker in video.suggestions
        if 0 <= marker.t_s < shown_s
    ]


def video_markers(video: TimelineVideo, rate: FrameRate, parts: TimelineParts) -> list[Marker]:
    """The markers of a video in the timeline, in time order: chapters, suggestions."""
    markers = placed_chapters(video, rate) if parts.chapters else []
    markers += placed_suggestions(video, rate) if parts.suggestions else []
    return sorted(markers, key=lambda marker: (marker.t_s, marker.kind))


def timeline_cues(
    videos: Sequence[TimelineVideo], rate: FrameRate, pick: Callable[[TimelineVideo], Sequence[Cue]]
) -> list[Cue]:
    """Subtitles of the whole timeline, in seconds from its start: each video's (``pick``)
    moved to where it is placed, cut at the end of what the timeline shows of it."""
    cues: list[Cue] = []
    offset = Fraction(0)
    for video in videos:
        frames = timeline_frames(video, rate)
        shown_s = float(rate.seconds(frames))
        for cue in pick(video):
            if cue.start < shown_s and cue.end > cue.start:
                start = float(offset) + max(0.0, cue.start)
                cues.append(Cue(start, float(offset) + min(cue.end, shown_s), cue.lines))
        offset += rate.seconds(frames)
    return cues


def timeline_seconds(videos: Sequence[TimelineVideo], rate: FrameRate) -> float:
    return float(rate.seconds(sum(timeline_frames(video, rate) for video in videos)))


def suggested_name(days: Sequence[date]) -> str:
    """« VFE 2026-09-15 », or the first and last days of shooting."""
    if not days:
        return "Video Frame Expedition"
    first, last = min(days), max(days)
    return f"VFE {first.isoformat()}" if first == last else f"VFE {first} – {last}"


def clean_name(name: str | None) -> str:
    """A timeline name on one line, bounded (Resolve accepts any other character)."""
    text = " ".join((name or "").split())
    return text[:MAX_NAME_CHARS].strip()


def file_base(name: str) -> str:
    """The timeline's name as a file name: without what Windows refuses in one."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean_name(name)).strip(" .")
    return safe or "timeline"


def file_name(name: str) -> str:
    """The name of the files to download (ZIP: the timeline, its subtitles, how to import)."""
    return f"{file_base(name)}.zip"


def file_uri(path: str) -> str:
    """``D:\\cats 2026\\été.mp4`` → ``file:///D:/cats%202026/%C3%A9t%C3%A9.mp4`` (a network path
    ``\\\\nas\\rushs\\a.mov`` → ``file://nas/rushs/a.mov``; a Mac path as it is)."""
    if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", path):
        return PureWindowsPath(path).as_uri()
    return PurePosixPath(path).as_uri()


# ---------------------------------------------------------------- FCPXML
def _at(frames: int, rate: Fraction) -> str:
    """``frames`` frames at ``rate`` per second as FCPXML writes a time, as Final Cut Pro does:
    whole seconds (``3600s``), else over the rate's timebase (``108108000/30000s`` at 29.97)."""
    numerator, timebase = frames * rate.denominator, rate.numerator
    if numerator % timebase == 0:
        return f"{numerator // timebase}s"
    return f"{numerator}/{timebase}s"


def first_frame(video: TimelineVideo) -> int:
    """The file's first frame counted from 00:00:00:00: its start timecode (0 without one)."""
    if not video.start_timecode:
        return 0
    try:
        return timecode_frames(video.start_timecode, float(video.rate_fraction))
    except ValueError:
        return 0


def _marker(clip: ET.Element, video: TimelineVideo, marker: Marker) -> None:
    """A marker from the file's frame where ``marker`` starts: one frame, or its range."""
    own = video.rate_fraction
    frame = math.floor(marker.t_s * own + Fraction(1, 10**6))
    length = max(1, math.floor(marker.duration_s * own + Fraction(1, 10**6)))
    attrib = {
        "start": _at(first_frame(video) + frame, own),
        "duration": _at(length, own),
        "value": marker.name,
    }
    if marker.note:
        attrib["note"] = marker.note
    ET.SubElement(clip, "marker", attrib)


def fcpxml(
    name: str,
    videos: Sequence[TimelineVideo],
    fmt: TimelineFormat,
    parts: TimelineParts = DEFAULT_PARTS,
) -> str:
    """The timeline as an FCPXML 1.10 document (UTF-8 text): one asset per file (its path as a
    ``file://`` URL, its times in its own frames), one clip per video on the primary storyline,
    starting at 01:00:00:00 (positions and lengths in the timeline's frames), and the markers
    ``parts`` asks for on each clip (in the clip's own time, which starts at the file's
    timecode). Final Cut Pro reads the markers; DaVinci Resolve 21.1 does not."""
    rate = fmt.rate.fraction
    root = ET.Element("fcpxml", version=FCPXML_VERSION)
    resources = ET.SubElement(root, "resources")
    ET.SubElement(resources, "format", id="r1", frameDuration=_at(1, rate),
                  width=str(fmt.width), height=str(fmt.height))  # fmt: skip
    assets: dict[str, str] = {}
    for video in videos:
        if video.path in assets:
            continue
        assets[video.path] = f"r{len(assets) + 2}"
        attrib = {
            "id": assets[video.path],
            "name": video.filename,
            "start": _at(first_frame(video), video.rate_fraction),
            "duration": _at(video.frames, video.rate_fraction),
            "hasVideo": "1",
        }
        if video.has_audio:
            attrib |= {"hasAudio": "1", "audioSources": "1"}
        asset = ET.SubElement(resources, "asset", attrib)
        ET.SubElement(asset, "media-rep", kind="original-media", src=file_uri(video.path))

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name=EVENT_NAME)
    project = ET.SubElement(event, "project", name=clean_name(name) or "Video Frame Expedition")
    start = timecode_frames(TIMELINE_START, fmt.rate.fps)
    lengths = [timeline_frames(video, fmt.rate) for video in videos]
    sequence = ET.SubElement(
        project, "sequence", format="r1", duration=_at(sum(lengths), rate),
        tcStart=_at(start, rate), tcFormat="NDF", audioLayout="stereo", audioRate="48k",
    )  # fmt: skip
    spine = ET.SubElement(sequence, "spine")
    position = start
    for video, length in zip(videos, lengths, strict=True):
        if length <= 0:
            continue
        clip = ET.SubElement(
            spine, "asset-clip", ref=assets[video.path], name=video.filename,
            offset=_at(position, rate), start=_at(first_frame(video), video.rate_fraction),
            duration=_at(length, rate), tcFormat="NDF",
        )  # fmt: skip
        for marker in video_markers(video, fmt.rate, parts):
            _marker(clip, video, marker)
        position += length
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n\n{body}\n'
