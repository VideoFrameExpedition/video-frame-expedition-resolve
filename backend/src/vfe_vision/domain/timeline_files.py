"""The files of a timeline to import by hand (« Create a timeline »): the timeline as
OpenTimelineIO, which DaVinci Resolve imports with the markers of its clips (it drops them from
FCPXML), the FCPXML for Final Cut Pro, one SubRip file per subtitle track (Resolve's timeline
formats carry no subtitle track) and how to import them.

The OTIO follows what Resolve 21.1 writes itself: times in the timeline's frames at its rate
(a clip's source start is its file's timecode), a video and an audio clip per video, linked, the
markers on both, their notes in ``metadata.Resolve_OTIO.Note``, and the file as a plain path.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from vfe_vision.domain.exports import one_line
from vfe_vision.domain.markers import Marker
from vfe_vision.domain.timecode import timecode_frames
from vfe_vision.domain.timeline_build import (
    DEFAULT_PARTS,
    TIMELINE_START,
    TimelineFormat,
    TimelineParts,
    TimelineVideo,
    clean_name,
    fcpxml,
    first_frame,
    timeline_cues,
    timeline_frames,
    video_markers,
)
from vfe_vision.domain.transcript import Cue, to_srt, wrap_lines
from vfe_vision.domain.translation import file_suffix

SHOT_TEXT_CHARS = 120  # a shot's description, cut between words: three subtitle lines at most
SHOT_LINE = 42
TRACKS = ("transcript", "shots")  # subtitle tracks, in order
# Their names in Resolve, in the language of the timeline.
TRACK_NAMES = {
    "fr": {"transcript": "Transcription", "shots": "Plans"},
    "en": {"transcript": "Transcript", "shots": "Shots"},
}
READ_ME = {"fr": "LISEZ-MOI.txt", "en": "README.txt"}


@dataclass(frozen=True, slots=True)
class SubtitleTrack:
    part: str  # transcript | shots
    label: str  # its name in Resolve: Transcription | Plans (Transcript | Shots in English)
    cues: tuple[Cue, ...]  # in seconds from the start of the timeline
    # The language of its text: spoken (one for the whole track, else None) or the timeline's.
    language: str | None = None

    def file_name(self, base: str) -> str:
        """``<base>_FR.srt`` for what is said, ``<base>_SHOTS_EN.srt`` for what the shots show:
        the language at the end, as every file of a video."""
        kind = "_SHOTS" if self.part == "shots" else ""
        language = f"_{file_suffix(self.language)}" if self.language else ""
        return f"{base}{kind}{language}.srt"


def shot_cue(start: float, end: float, text: str) -> Cue | None:
    """What a shot shows as a subtitle over the whole shot (none without a description)."""
    lines = tuple(wrap_lines(one_line(text, SHOT_TEXT_CHARS), SHOT_LINE))
    return Cue(start, end, lines) if lines and end > start else None


def subtitle_tracks(
    videos: Sequence[TimelineVideo],
    fmt: TimelineFormat,
    parts: TimelineParts,
    language: str = "fr",
) -> list[SubtitleTrack]:
    """The subtitle tracks asked for that hold something, in track order: what is said, in the
    language spoken; what the shots show, in ``language``."""
    picks = {"transcript": lambda v: v.speech, "shots": lambda v: v.shot_texts}
    names = TRACK_NAMES.get(language, TRACK_NAMES["fr"])
    tracks = []
    for part in TRACKS:
        if getattr(parts, part):
            cues = timeline_cues(videos, fmt.rate, picks[part])
            if cues:
                written = language if part == "shots" else spoken_language(videos)
                tracks.append(SubtitleTrack(part, names[part], tuple(cues), written))
    return tracks


def spoken_language(videos: Sequence[TimelineVideo]) -> str | None:
    """The language spoken in the videos that speak, when they all speak the same one."""
    spoken = {video.speech_language for video in videos if video.speech}
    return spoken.pop() if len(spoken) == 1 else None


def _time(value: float, rate: float) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": value}


def _range(start: float, duration: float, rate: float) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "duration": _time(duration, rate),
        "start_time": _time(start, rate),
    }


def _otio_marker(
    marker: Marker, source_start: float, length: int, rate: Fraction
) -> dict[str, Any]:
    """A marker in the clip's source time, on a frame of the timeline."""
    offset = math.floor(marker.t_s * rate + Fraction(1, 10**6))
    duration = max(1, math.floor(marker.duration_s * rate + Fraction(1, 10**6)))
    return {
        "OTIO_SCHEMA": "Marker.2",
        "metadata": {"Resolve_OTIO": {"Keywords": [], "Note": marker.note}},
        "name": marker.name,
        "color": marker.color.upper(),
        "marked_range": _range(
            source_start + offset, min(duration, max(1, length - offset)), float(rate)
        ),
    }


def _clip(
    video: TimelineVideo, fmt: TimelineFormat, parts: TimelineParts, link: int
) -> dict[str, Any]:
    rate = fmt.rate.fraction
    own = video.rate_fraction
    length = timeline_frames(video, fmt.rate)
    source_start = float(Fraction(first_frame(video)) / own * rate)  # the file's timecode
    return {
        "OTIO_SCHEMA": "Clip.2",
        "metadata": {"Resolve_OTIO": {"Link Group ID": link}},  # picture and sound linked
        "name": video.filename,
        "source_range": _range(source_start, length, float(rate)),
        "effects": [],
        "markers": [
            _otio_marker(marker, source_start, length, rate)
            for marker in video_markers(video, fmt.rate, parts)
            if math.floor(marker.t_s * rate + Fraction(1, 10**6)) < length
        ],
        "enabled": True,
        "media_references": {
            "DEFAULT_MEDIA": {
                "OTIO_SCHEMA": "ExternalReference.1",
                "metadata": {},
                "name": video.filename,
                "available_range": _range(first_frame(video), video.frames, float(own)),
                "available_image_bounds": None,
                "target_url": video.path,
            }
        },
        "active_media_reference_key": "DEFAULT_MEDIA",
    }


def _gap(frames: int, rate: float) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Gap.1",
        "metadata": {},
        "name": "",
        "source_range": _range(0, frames, rate),
        "effects": [],
        "markers": [],
        "enabled": True,
    }


def _track(name: str, kind: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Track.1",
        "metadata": {"Resolve_OTIO": {"Locked": False}},
        "name": name,
        "source_range": None,
        "effects": [],
        "markers": [],
        "enabled": True,
        "children": children,
        "kind": kind,
    }


def otio(
    name: str,
    videos: Sequence[TimelineVideo],
    fmt: TimelineFormat,
    parts: TimelineParts = DEFAULT_PARTS,
) -> str:
    """The timeline as an OpenTimelineIO document (JSON text): the videos whole, end to end, on
    Video 1 and Audio 1 (a video without sound leaves a gap there), from 01:00:00:00, with the
    markers ``parts`` asks for on their clips."""
    rate = float(fmt.rate.fraction)
    pictures: list[dict[str, Any]] = []
    sounds: list[dict[str, Any]] = []
    for link, video in enumerate(videos, 1):
        length = timeline_frames(video, fmt.rate)
        if length <= 0:
            continue
        pictures.append(_clip(video, fmt, parts, link))
        sounds.append(_clip(video, fmt, parts, link) if video.has_audio else _gap(length, rate))
    document = {
        "OTIO_SCHEMA": "Timeline.1",
        "metadata": {"Resolve_OTIO": {"Resolve OTIO Meta Version": "1.0"}},
        "name": clean_name(name) or "Video Frame Expedition",
        "global_start_time": _time(timecode_frames(TIMELINE_START, fmt.rate.fps), rate),
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "metadata": {},
            "name": "",
            "source_range": None,
            "effects": [],
            "markers": [],
            "enabled": True,
            "children": [_track("Video 1", "Video", pictures), _track("Audio 1", "Audio", sounds)],
        },
    }
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def read_me(base: str, tracks: Sequence[SubtitleTrack], language: str = "fr") -> str:
    """How to import the files, in the timeline's language (Windows line ends: it is opened in
    Notepad)."""
    if language == "en":
        lines = [
            f"Timeline “{base}” — Video Frame Expedition for DaVinci Resolve",
            "",
            "In DaVinci Resolve:",
            f"1. File › Import › Timeline…: choose “{base}.otio”. The videos are imported with",
            "   it; the markers (chapters, suggestions) are on their clips.",
        ]
        if tracks:
            lines += [
                "2. Subtitles: File › Import › Subtitle… (or drag the .srt files into the media",
                "   pool), then drag each file to the very start of the timeline (01:00:00:00),",
                "   each on its own subtitle track:",
            ]
            lines += [f"   - “{track.file_name(base)}” ({track.label})" for track in tracks]
        lines += ["", f"Final Cut Pro: “{base}.fcpxml” (File › Import › XML)."]
        return "\r\n".join(lines) + "\r\n"
    lines = [
        f"Timeline « {base} » — Video Frame Expedition for DaVinci Resolve",
        "",
        "Dans DaVinci Resolve :",
        f"1. Fichier › Importer › Timeline… : choisissez « {base}.otio ». Les vidéos sont",
        "   importées avec elle ; les marqueurs (chapitres, suggestions) sont sur leurs clips.",
    ]
    if tracks:
        lines += [
            "2. Sous-titres : Fichier › Importer › Sous-titres… (ou glissez les fichiers .srt dans",
            "   le media pool), puis faites glisser chaque fichier au tout début de la timeline",
            "   (01:00:00:00), chacun sur sa propre piste de sous-titres :",
        ]
        lines += [f"   - « {track.file_name(base)} » ({track.label})" for track in tracks]
    lines += ["", f"Final Cut Pro : « {base}.fcpxml » (Fichier › Importer › XML)."]
    return "\r\n".join(lines) + "\r\n"


def timeline_bundle(
    base: str,
    videos: Sequence[TimelineVideo],
    fmt: TimelineFormat,
    parts: TimelineParts,
    language: str = "fr",
) -> list[tuple[str, bytes]]:
    """The files to import by hand, as ``(name, content)``: ``base`` names them; their texts
    and names in ``language``."""
    tracks = subtitle_tracks(videos, fmt, parts, language)
    files = [
        (f"{base}.otio", otio(base, videos, fmt, parts).encode("utf-8")),
        (f"{base}.fcpxml", fcpxml(base, videos, fmt, parts).encode("utf-8")),
    ]
    files += [(t.file_name(base), to_srt(t.cues).encode("utf-8")) for t in tracks]
    name = READ_ME.get(language, READ_ME["fr"])
    files.append((name, read_me(base, tracks, language).encode("utf-8")))
    return files
