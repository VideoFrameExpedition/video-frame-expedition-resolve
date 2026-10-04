"""The passages of the search index (pure functions): what a search can find in a
video, cut at the scales an edit needs.

- ``video``: its titles and summaries (the user's and the synthesis'), notes, keywords, when,
  where and with what it was shot, the sounds heard;
- ``chapter``: the title and sentence of each chapter of the synthesis;
- ``shot``: what happens in the shot, what its keyframes show, the beings seen, the text read
  on screen and the sounds heard during it;
- ``keyframe``: the description of one image, at its time;
- ``transcript``: about 30 s of speech, cut between words, overlapping the next window by a
  few seconds so that a sentence on the border is found whole in one of them.

Each passage carries the facets the filters read (framing, beings, weather, light, place,
date, speech, usability), computed from the analyses of its time range. Its text is plain,
labelled in the analysis language; everything in it comes from the footage or from a model:
it is untrusted data wherever it is shown to another model.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from vfe_vision.domain.search_text import dedupe, fold
from vfe_vision.domain.synthesis_input import Frame, Video, Word, speech_seconds, whole_words
from vfe_vision.domain.synthesis_text import sound_in_prompt, sound_label
from vfe_vision.domain.usability import usability_by_shot
from vfe_vision.domain.weather_consensus import weather_consensus

CHUNK_RULES_VERSION = 1
TRANSCRIPT_WINDOW_S = 30.0
TRANSCRIPT_OVERLAP_S = 5.0
TRANSCRIPT_PAUSE_S = 0.6  # a pause this long ends a window early, past half of it
MAX_CHARS = 900  # a passage (~250 tokens): the embedding cost grows with the length
VIDEO_MAX_CHARS = 1500
SPEECH_MIN_S = 1.0  # seconds of reliable speech for a passage to count as « with speech »
MAX_LABELS = 12


class ChunkKind(StrEnum):
    VIDEO = "video"
    CHAPTER = "chapter"
    SHOT = "shot"
    KEYFRAME = "keyframe"
    TRANSCRIPT = "transcript"


@dataclass(frozen=True, slots=True)
class Story:
    """What happens in (a part of) a shot, told by the vision model."""

    shot: int  # 0-based shot index
    start: float
    end: float
    summary: str
    main_action: str = ""


@dataclass(frozen=True, slots=True)
class ScreenText:
    """A line read on a keyframe (OCR)."""

    t: float
    text: str


@dataclass(frozen=True, slots=True)
class Chapter:
    start: float
    end: float
    title: str
    summary: str


@dataclass(frozen=True, slots=True)
class IndexFacts:
    """Everything a video's passages are written from (``pipeline/search_facts.py`` reads it)."""

    video: Video  # the facts of the synthesis: shots, keyframes, speech, sounds, place, weather
    language: str  # of the labels, and of the texts the models wrote
    user_title: str | None = None
    user_summary: str | None = None
    user_notes: str | None = None
    device: str | None = None  # « samsung Galaxy S26 Ultra »
    capture_date: date | None = None  # local date of shooting, when known
    shot_ids: dict[int, str] = field(default_factory=dict)  # shot index → row id
    stories: tuple[Story, ...] = ()
    screen: tuple[ScreenText, ...] = ()
    beings: dict[str, tuple[str, ...]] = field(default_factory=dict)  # keyframe id → labels
    synthesis_title: str | None = None
    logline: str | None = None
    synthesis_summary: str | None = None
    tags: tuple[str, ...] = ()
    chapters: tuple[Chapter, ...] = ()


@dataclass(frozen=True, slots=True)
class Chunk:
    kind: ChunkKind
    text: str
    t_start: float | None = None
    t_end: float | None = None
    shot_id: str | None = None
    keyframe_id: str | None = None  # its keyframe, or the one shown for it
    language: str | None = None
    facets: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- labels
_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "sounds": "Sons", "screen": "Texte à l'écran", "beings": "Sujets", "tags": "Mots-clés",
        "notes": "Notes", "device": "Appareil", "weather": "Météo", "shot_on": "Tourné le",
        "at": "à", "light": "lumière",
    },
    "en": {
        "sounds": "Sounds", "screen": "On-screen text", "beings": "Subjects", "tags": "Keywords",
        "notes": "Notes", "device": "Device", "weather": "Weather", "shot_on": "Shot on",
        "at": "in", "light": "light",
    },
}  # fmt: skip
_MONTHS = {
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
           "octobre", "novembre", "décembre"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"),
}  # fmt: skip
_LIGHT = {
    "fr": {"golden_hour": "heure dorée", "blue_hour": "heure bleue", "night": "nuit",
           "nautical_twilight": "crépuscule", "astronomical_twilight": "crépuscule",
           "day": "jour"},
    "en": {"golden_hour": "golden hour", "blue_hour": "blue hour", "night": "night",
           "nautical_twilight": "twilight", "astronomical_twilight": "twilight", "day": "daylight"},
}  # fmt: skip
_DAY_PART = {
    "fr": {"morning": "le matin", "midday": "vers midi", "afternoon": "l'après-midi",
           "evening": "le soir", "night": "la nuit"},
    "en": {"morning": "in the morning", "midday": "around midday", "afternoon": "in the afternoon",
           "evening": "in the evening", "night": "at night"},
}  # fmt: skip
WEATHER_NAMES = {
    "fr": {"clear": "ciel dégagé", "partly_cloudy": "partiellement nuageux", "overcast": "couvert",
           "fog": "brouillard", "drizzle": "bruine", "rain": "pluie", "snow": "neige",
           "thunderstorm": "orage"},
    "en": {"clear": "clear sky", "partly_cloudy": "partly cloudy", "overcast": "overcast",
           "fog": "fog", "drizzle": "drizzle", "rain": "rain", "snow": "snow",
           "thunderstorm": "thunderstorm"},
}  # fmt: skip
_NO_WEATHER = frozenset({"sky_not_visible", "unknown"})


def _labels(language: str) -> dict[str, str]:
    return _LABELS.get(language, _LABELS["en"])


def spoken_date(day: date, language: str) -> str:
    months = _MONTHS.get(language, _MONTHS["en"])
    if language == "fr":
        return f"{'1er' if day.day == 1 else day.day} {months[day.month - 1]} {day.year}"
    return f"{day.day} {months[day.month - 1]} {day.year}"


def _cut(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut between words and marked with « … »."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    space = text.rfind(" ", 0, limit - 1)
    end = space if space >= limit // 2 else limit - 1
    return text[:end].rstrip(" ,;:–—") + "…"


def _compose(parts: Iterable[str], limit: int) -> str:
    """Lines in priority order while they fit; the first one that does not is cut to fit."""
    lines: list[str] = []
    for raw in parts:
        part = " ".join(raw.split())
        if not part:
            continue
        room = limit - sum(len(line) + 1 for line in lines)
        if room < 40:
            break
        if len(part) > room:
            lines.append(_cut(part, room))
            break
        lines.append(part)
    return "\n".join(lines)


def _listed(label: str, items: Sequence[str]) -> str:
    return f"{label} : {', '.join(items)}" if items else ""


# ---------------------------------------------------------------- facets
@dataclass(frozen=True, slots=True)
class _VideoFacets:
    """The facets every passage of the video shares."""

    base: dict[str, Any]  # date, light, place, the consensus weather
    weather: str | None
    usable: dict[int, int]  # shot index → usability


def _video_facets(facts: IndexFacts) -> _VideoFacets:
    video = facts.video
    base: dict[str, Any] = {}
    if facts.capture_date is not None:
        base["date"] = facts.capture_date.isoformat()
    if video.light_phase:
        base["light_phase"] = video.light_phase
    place = " ".join(p for p in (video.place_label, video.place_feature) if p)
    if place:
        base["place"] = fold(place)
    consensus = weather_consensus(video).category
    usable = {idx: u.score for idx, u in usability_by_shot(video).items()}
    return _VideoFacets(base, consensus, usable)


def _facets(
    facts: IndexFacts,
    shared: _VideoFacets,
    start: float | None,
    end: float | None,
    frames: Sequence[Frame],
) -> dict[str, Any]:
    """The facets of a passage: the video's, and those of the frames and shots of its range."""
    video = facts.video
    lo = 0.0 if start is None else start
    hi = video.duration if end is None else end
    described = [f.data for f in frames if f.data]
    out = dict(shared.base)
    framing = dedupe(str(d.get("shot_type")) for d in described if d.get("shot_type"))
    framing = [t for t in framing if t != "unknown"]
    if framing:
        out["shot_types"] = framing
    beings = _beings(facts, frames)
    if beings:
        out["subjects"] = beings
        out["subject_keys"] = [fold(b) for b in beings]
    weather = [shared.weather] if shared.weather else []
    weather += [str(d.get("weather")) for d in described if d.get("weather")]
    weather = [w for w in dict.fromkeys(weather) if w not in _NO_WEATHER]
    if weather:
        out["weather"] = weather
    out["speech"] = speech_seconds(video.segments, lo, max(hi, lo + 0.001)) >= SPEECH_MIN_S
    if start is not None:
        scores = [
            shared.usable[s.idx]
            for s in video.shots
            if s.idx in shared.usable and s.start < max(hi, lo + 0.001) and s.end > lo
        ]
        if scores:
            out["usability"] = max(scores)
    return out


def _beings(facts: IndexFacts, frames: Sequence[Frame]) -> list[str]:
    """Beings and things seen: the vision model's subjects, then the fused boxes' labels."""
    named: list[str] = []
    for frame in frames:
        subjects = (frame.data or {}).get("subjects") or []
        named += [
            str(s["label"]).lower() for s in subjects if isinstance(s, dict) and s.get("label")
        ]
        named += [label.lower() for label in facts.beings.get(frame.keyframe_id, ())]
    return dedupe(named)[:MAX_LABELS]


# ---------------------------------------------------------------- passages
def build_chunks(facts: IndexFacts, *, speech: bool = True) -> list[Chunk]:
    """Every passage of a video: the video, its chapters, shots, keyframes and speech (not
    with ``speech`` False: what is said has one language, written once)."""
    shared = _video_facets(facts)
    chunks = [_video_chunk(facts, shared)]
    chunks += _chapter_chunks(facts, shared)
    chunks += _shot_chunks(facts, shared)
    chunks += _keyframe_chunks(facts, shared)
    if speech:
        chunks += _transcript_chunks(facts, shared)
    return chunks


def video_chunk(facts: IndexFacts) -> Chunk:
    """The passage about the whole video alone (refreshed when the user edits its fields)."""
    return _video_chunk(facts, _video_facets(facts))


def _video_chunk(facts: IndexFacts, shared: _VideoFacets) -> Chunk:
    video, words = facts.video, _labels(facts.language)
    heading = " — ".join(t for t in (facts.synthesis_title, facts.logline) if t)
    parts = [
        facts.user_title or "",
        heading,
        video.filename,
        facts.user_summary or "",
        facts.synthesis_summary or "",
        f"{words['notes']} : {facts.user_notes}" if facts.user_notes else "",
        _listed(words["tags"], dedupe(facts.tags)),
        _context_line(facts, shared),
        _listed(words["sounds"], _sounds(video, [h.label for h in video.heard], facts.language)),
    ]
    frames = list(video.frames)
    return Chunk(
        ChunkKind.VIDEO,
        _compose(parts, VIDEO_MAX_CHARS),
        language=facts.language,
        facets=_facets(facts, shared, None, None, frames),
    )


def _context_line(facts: IndexFacts, shared: _VideoFacets) -> str:
    """« Tourné le 14 juillet 2026 le matin, heure dorée, à Hyères, Var, France · Météo : … »."""
    video, language, words = facts.video, facts.language, _labels(facts.language)
    when: list[str] = []
    if facts.capture_date is not None:
        when.append(f"{words['shot_on']} {spoken_date(facts.capture_date, language)}")
        part = _DAY_PART.get(language, _DAY_PART["en"]).get(video.day_part or "")
        if part:
            when[-1] += f" {part}"
    light = _LIGHT.get(language, _LIGHT["en"]).get(video.light_phase or "")
    if light and video.light_phase != "day":
        when.append(light)
    place = ", ".join(p for p in (video.place_label, video.place_feature) if p)
    if place:
        when.append(f"{words['at']} {place}")
    pieces = [", ".join(when)] if when else []
    if shared.weather:
        name = WEATHER_NAMES.get(language, WEATHER_NAMES["en"]).get(shared.weather)
        pieces.append(f"{words['weather']} : {name or shared.weather}")
    if facts.device:
        pieces.append(f"{words['device']} : {facts.device}")
    line = " · ".join(pieces)
    return line[0].upper() + line[1:] if line else ""


def _sounds(video: Video, labels: Iterable[str], language: str) -> list[str]:
    """Names of the sounds worth writing (the synthesis gate: heard by both taggers, loud and
    long, or confirmed by a picture), in the order given."""
    passed = {h.label for h in video.heard if sound_in_prompt(video, h)}
    return dedupe(sound_label(label, language) for label in labels if label in passed)


def _chapter_chunks(facts: IndexFacts, shared: _VideoFacets) -> list[Chunk]:
    if len(facts.chapters) < 2:  # one chapter is the whole video: the video passage says it
        return []
    chunks = []
    for chapter in facts.chapters:
        frames = _frames_in(facts.video, chapter.start, chapter.end)
        shown = frames[0] if frames else _frame_at(facts.video, chapter.start)
        chunks.append(
            Chunk(
                ChunkKind.CHAPTER,
                _compose((chapter.title, chapter.summary), MAX_CHARS),
                t_start=chapter.start, t_end=chapter.end,
                keyframe_id=shown.keyframe_id if shown else None,
                language=facts.language,
                facets=_facets(facts, shared, chapter.start, chapter.end, frames),
            )
        )  # fmt: skip
    return [c for c in chunks if c.text]


def _shot_chunks(facts: IndexFacts, shared: _VideoFacets) -> list[Chunk]:
    video, words = facts.video, _labels(facts.language)
    chunks = []
    for shot in video.shots:
        frames = [f for f in video.frames if f.shot == shot.idx]
        described = [f.data for f in frames if f.data]
        told = [s for s in facts.stories if s.shot == shot.idx]
        story = " ".join(
            s.summary + (f" {s.main_action}." if s.main_action and s.main_action not in s.summary
                         else "")
            for s in told
        )  # fmt: skip
        captions = dedupe(str(d.get("caption", "")) for d in described)
        descriptions = dedupe(str(d.get("description", "")) for d in described)
        screen = dedupe(
            [t.text for t in facts.screen if shot.start <= t.t < shot.end]
            + [str(d.get("visible_text", "")) for d in described]
        )
        parts = [
            story,
            " ".join(captions),
            _listed(words["beings"], _beings(facts, frames)),
            _listed(words["screen"], screen),
            _listed(words["sounds"], _sounds(video, shot.heard, facts.language)),
            " ".join(descriptions),
        ]
        text = _compose(parts, MAX_CHARS)
        if not text:
            continue
        shown = _best(frames) or _frame_at(video, shot.start)
        chunks.append(
            Chunk(
                ChunkKind.SHOT, text, t_start=shot.start, t_end=shot.end,
                shot_id=facts.shot_ids.get(shot.idx),
                keyframe_id=shown.keyframe_id if shown else None, language=facts.language,
                facets=_facets(facts, shared, shot.start, shot.end, frames),
            )
        )  # fmt: skip
    return chunks


def _keyframe_chunks(facts: IndexFacts, shared: _VideoFacets) -> list[Chunk]:
    video, words = facts.video, _labels(facts.language)
    frames = sorted(video.frames, key=lambda f: f.t)
    shots = {s.idx: s for s in video.shots}
    chunks = []
    for position, frame in enumerate(frames):
        data = frame.data or {}
        if not data:
            continue
        shot = shots.get(frame.shot) if frame.shot is not None else None
        until = frames[position + 1].t if position + 1 < len(frames) else video.duration
        if shot is not None:
            until = min(until, shot.end)
        tags = dedupe(str(t) for t in data.get("tags") or [])
        text = _compose(
            (str(data.get("caption", "")), str(data.get("description", "")),
             _listed(words["tags"], tags)),
            MAX_CHARS,
        )  # fmt: skip
        if not text:
            continue
        chunks.append(
            Chunk(
                ChunkKind.KEYFRAME, text, t_start=frame.t, t_end=max(frame.t, until),
                shot_id=facts.shot_ids.get(shot.idx) if shot is not None else None,
                keyframe_id=frame.keyframe_id, language=facts.language,
                facets=_facets(facts, shared, frame.t, max(frame.t, until), [frame]),
            )
        )  # fmt: skip
    return chunks


def transcript_windows(words: Sequence[Word]) -> list[tuple[int, int]]:
    """Word ranges ``[first, last)`` of about TRANSCRIPT_WINDOW_S seconds each.

    A window ends at a sentence end or a pause past half of its length when there is one, else
    before the word that would overflow it; the next one starts TRANSCRIPT_OVERLAP_S earlier,
    at a word start. A single word longer than a window is a window of its own.
    """
    windows: list[tuple[int, int]] = []
    first = 0
    while first < len(words):
        begin = words[first].start
        last = first + 1
        while last < len(words) and words[last].end - begin <= TRANSCRIPT_WINDOW_S:
            last += 1
        if last < len(words):
            last = _break_before(words, first, last, begin)
        windows.append((first, last))
        if last >= len(words):
            break
        resume = words[last - 1].end - TRANSCRIPT_OVERLAP_S
        nxt = next((i for i in range(first + 1, last) if words[i].start >= resume), last)
        first = max(nxt, first + 1)
    return windows


def _break_before(words: Sequence[Word], first: int, last: int, begin: float) -> int:
    """Where a full window ends best: after the last sentence end or long pause in its second
    half, else where it is."""
    half = begin + TRANSCRIPT_WINDOW_S / 2
    for i in range(last - 1, first, -1):
        if words[i].end < half:
            break
        ends_sentence = words[i].text.rstrip().endswith((".", "!", "?", "…"))
        paused = words[i + 1].start - words[i].end >= TRANSCRIPT_PAUSE_S
        if ends_sentence or paused:
            return i + 1
    return last


def _transcript_chunks(facts: IndexFacts, shared: _VideoFacets) -> list[Chunk]:
    video = facts.video
    spoken = [w for segment in video.segments for w in whole_words(segment) if w.text.strip()]
    chunks = []
    for first, last in transcript_windows(spoken):
        part = spoken[first:last]
        start, end = part[0].start, max(w.end for w in part)
        text = _cut(" ".join(w.text.strip() for w in part), MAX_CHARS)
        middle = (start + end) / 2
        shot = next((s for s in video.shots if s.start <= middle < s.end), None)
        shown = _frame_near(video, middle, shot.idx if shot is not None else None)
        chunks.append(
            Chunk(
                ChunkKind.TRANSCRIPT, text, t_start=start, t_end=end,
                shot_id=facts.shot_ids.get(shot.idx) if shot is not None else None,
                keyframe_id=shown.keyframe_id if shown else None,
                language=video.transcript_language or facts.language,
                facets=_facets(facts, shared, start, end, _frames_in(video, start, end)),
            )
        )  # fmt: skip
    return chunks


# ---------------------------------------------------------------- frames of a range
def _frames_in(video: Video, start: float, end: float) -> list[Frame]:
    return [f for f in video.frames if start <= f.t < end]


def _frame_near(video: Video, t: float, shot: int | None) -> Frame | None:
    """The keyframe of that shot nearest to ``t``, else the one in force at ``t``."""
    own = [f for f in video.frames if shot is not None and f.shot == shot]
    if own:
        return min(own, key=lambda f: abs(f.t - t))
    return _frame_at(video, t)


def _frame_at(video: Video, t: float) -> Frame | None:
    """The keyframe in force at ``t`` (the last one before it), else the first one."""
    frames = sorted(video.frames, key=lambda f: f.t)
    before = [f for f in frames if f.t <= t]
    return before[-1] if before else (frames[0] if frames else None)


def _best(frames: Sequence[Frame]) -> Frame | None:
    """The frame a shot is shown by: the sharpest described one, else the sharpest."""
    described = [f for f in frames if f.data] or list(frames)
    return max(described, key=lambda f: f.sharpness or 0.0, default=None)


def digest(chunks: Sequence[Chunk]) -> str:
    """What the index of a video is built from: a change of any passage re-indexes it."""
    payload = json.dumps(
        [
            [c.kind.value, c.t_start, c.t_end, c.shot_id, c.keyframe_id, c.language, c.text,
             c.facets]
            for c in chunks
        ],
        ensure_ascii=False,
        sort_keys=True,
    )  # fmt: skip
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
