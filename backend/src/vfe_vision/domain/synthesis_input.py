"""What the synthesis of a video is built from: the facts already analysed, read once from the
database (pure data; ``pipeline/stages/synthesis.py`` fills it).

Every time here is in seconds of the source file. The synthesis never lets the language model
write a time: chapters, highlights and in/out points are computed from these facts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class Segment:
    """A reliable transcript segment (suspect ones are left out), with its words when known."""

    start: float
    end: float
    text: str
    words: tuple[Word, ...] = ()


@dataclass(frozen=True, slots=True)
class Frame:
    """A distinct keyframe (duplicates left out) and its description by the vision model."""

    idx: int  # 0-based keyframe index, as stored
    keyframe_id: str
    t: float
    shot: int | None  # 0-based index of its shot (by ``keyframes.shot_id``)
    sharpness: float | None
    metrics: dict[str, Any]  # technical metrics of the keyframe (clipped_highlights…)
    data: dict[str, Any] | None  # the stored FrameAnalysis (caption, shot_type, quality_issues…)


Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in 0–1 of the displayed picture


@dataclass(frozen=True, slots=True)
class Picture:
    """What the picture does second by second (analysis pass, ``hz`` samples a second) and where
    the living beings are at the keyframes (people and animals the detectors boxed): where to
    place a clip inside its block. Read-time only: never part of what the language model gets,
    nor of the synthesis key.
    """

    hz: float = 2.0
    t: tuple[float, ...] = ()
    motion: tuple[float | None, ...] = ()  # camera motion magnitude
    luma: tuple[float | None, ...] = ()  # 0–1
    beings: Mapping[str, tuple[Box, ...]] = field(default_factory=dict)  # keyframe id → boxes


@dataclass(frozen=True, slots=True)
class Shot:
    idx: int  # 0-based
    start: float
    end: float
    motion: str  # the label shown (domain.shots.shown_motion), not the raw one
    stability: float  # 0 shaky … 1 locked off
    boundary: str  # start | cut | fade
    metrics: dict[str, Any]  # luma, black_ratio, frozen_ratio…
    heard: tuple[str, ...] = ()  # AudioSet labels heard during the shot

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class HeardSound:
    """A sound heard in the video: which taggers heard it, how long, how strongly."""

    label: str  # AudioSet label (English), e.g. "Clip-clop"
    category: str  # family: speech, music, nature, wind, water, vehicles, crowd, tools, silence…
    seconds: float
    score: float
    sources: tuple[str, ...]  # "yamnet", "ced"


@dataclass(frozen=True, slots=True)
class WeatherFacts:
    """The model weather at the capture hour (Open-Meteo), as stored by the weather stage."""

    category: str | None  # clear, partly_cloudy, overcast, rain, snow, fog…
    cloud_cover_pct: float | None
    sun_fraction: float | None  # share of the hour with sunshine
    is_day: bool | None
    temperature_c: float | None
    precipitation_mm: float | None


@dataclass(frozen=True, slots=True)
class Video:
    id: str
    filename: str
    duration: float
    orientation: str | None  # horizontal, vertical, square
    capture_local: datetime | None  # local time of capture, only when its confidence is high/medium
    place_label: str | None  # e.g. "Giverny, Eure, France"
    place_feature: str | None  # a nearby named feature ("Lac de …"), when known
    light_phase: str | None  # day, golden_hour, blue_hour, night… (sun stage)
    day_part: str | None  # morning, midday, afternoon, evening, night
    weather: WeatherFacts | None
    presence: dict[str, float]  # share of time per sound family (speech, music, nature…)
    heard: tuple[HeardSound, ...]
    instruments: tuple[str, ...]  # AudioSet labels of the instruments heard
    transcript_language: str | None  # None: no reliable speech
    segments: tuple[Segment, ...]
    silences: tuple[tuple[float, float], ...]  # measured silences (audio levels stage)
    shots: tuple[Shot, ...]
    frames: tuple[Frame, ...]
    fps: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # reserved: future inputs (OCR…)

    @property
    def words(self) -> list[Word]:
        """All the words, or one pseudo-word per segment when no word times exist."""
        words = [w for s in self.segments for w in s.words]
        return words or [Word(s.start, s.end, s.text) for s in self.segments]


@dataclass(frozen=True, slots=True)
class Block:
    """A piece of the video the synthesis talks about: a shot, or ~20 s of a long shot (a few
    short shots merged on very long videos). Numbered B1, B2… for the language model."""

    no: int  # 1-based
    start: float
    end: float
    shots: tuple[int, ...]  # 0-based shot indices
    frames: tuple[Frame, ...]  # its keyframes; without any, the keyframe in force at its start
    speech_s: float  # seconds of reliable speech inside it
    heard: tuple[str, ...]  # sounds heard during its shots (AudioSet labels)

    @property
    def duration(self) -> float:
        return self.end - self.start


def speech_seconds(segments: tuple[Segment, ...], start: float, end: float) -> float:
    """Seconds of reliable speech inside [start, end) (word level when words exist)."""
    total = 0.0
    for segment in segments:
        if segment.end <= start or segment.start >= end:
            continue
        spans = segment.words or (Word(segment.start, segment.end, segment.text),)
        total += sum(max(0.0, min(end, w.end) - max(start, w.start)) for w in spans)
    return total


def whole_words(segment: Segment) -> list[Word]:
    """The segment's words, a Whisper piece without a leading space joined to the word before
    it (« J » + « 'ajoute »: a cut between them would split a word); the whole segment as one
    word when no word times exist."""
    if not segment.words:
        return [Word(segment.start, segment.end, segment.text)]
    words: list[Word] = []
    for word in segment.words:
        if words and word.text and not word.text[0].isspace():
            before = words[-1]
            words[-1] = Word(before.start, max(before.end, word.end), before.text + word.text)
        else:
            words.append(word)
    return words


def speech_text(segments: tuple[Segment, ...], start: float, end: float) -> str:
    """What is said in [start, end): whole words (see ``whole_words``), each in the block
    holding its midpoint, so a word is never split across two blocks; segments without word
    times by their midpoint."""
    parts: list[str] = []
    for segment in segments:
        if segment.end <= start or segment.start >= end:
            continue
        if segment.words:
            words = [w.text for w in whole_words(segment) if start <= (w.start + w.end) / 2 < end]
            text = " ".join("".join(words).split())
        else:
            middle = (segment.start + segment.end) / 2
            text = segment.text if start <= middle < end else ""
        if text.strip():
            parts.append(text.strip())
    return " ".join(parts)
