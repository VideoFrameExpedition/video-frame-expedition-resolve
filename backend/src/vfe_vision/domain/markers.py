"""Markers of a video for DaVinci Resolve and the marker EDL (pure functions).

Times stay in seconds of the source file: the fixed Resolve script converts them to frames with
each clip's own frame rate (Resolve's, for a variable frame rate phone clip), the EDL with the
file's. Each marker says what wrote it (``customData`` = ``vfe:<kind>:<number>``), so that a new
run replaces the application's markers and never the user's.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from vfe_vision.domain.editing import Clip
from vfe_vision.domain.exports import marker_name, one_line
from vfe_vision.domain.synthesis_input import Segment, speech_text
from vfe_vision.domain.timecode import format_clock

PREFIX = "vfe:"
COLORS = {
    "chapter": "Blue",
    "highlight": "Green",
    "shot": "Sand",
    "speech": "Lavender",
    "establishing": "Cyan",
    "b_roll": "Yellow",
    "avoid": "Red",
}
# Editing roles suggested for stretches of a video (services/synthesis.py), as marker names.
ROLE_NAMES = {"establishing": "Plan d'ensemble", "b_roll": "Illustration", "avoid": "À éviter"}
ROLE_NAMES_EN = {"establishing": "Establishing shot", "b_roll": "B-roll", "avoid": "Avoid"}
# The words of the markers, in the language of the timeline.
WORDS = {
    "fr": {"chapter": "Chapitre", "highlight": "Moment fort", "sound": "son", "shot": "Plan",
           "usable": "utilisable", "suggestion": "Suggestion de montage (indicatif)"},
    "en": {"chapter": "Chapter", "highlight": "Highlight", "sound": "sound", "shot": "Shot",
           "usable": "usable", "suggestion": "Editing suggestion (indicative)"},
}  # fmt: skip
# The notes of a clip (domain/editing.py, ``clip_for``) are French; in English:
_NOTES_EN = {
    "entrée déplacée entre deux mots": "in point moved between two words",
    "sortie déplacée entre deux mots": "out point moved between two words",
    "parole continue : coupe dans un mot inévitable": "continuous speech: a cut inside a word",
}
_J_CUT = re.compile(r"^le son commence (.+) avant l'image \(J-cut\)$")
_L_CUT = re.compile(r"^le son continue (.+) après la coupe \(L-cut\)$")
NOTE_CHARS = 300
SPEECH_GAP_S = 2.0  # a longer silence between two segments starts a new speech section


@dataclass(frozen=True, slots=True)
class Marker:
    kind: str  # chapter | highlight | shot | speech
    number: int  # 1-based, per kind
    t_s: float
    duration_s: float  # 0: a single frame
    name: str
    note: str

    @property
    def color(self) -> str:
        return COLORS.get(self.kind, "Blue")

    @property
    def custom_data(self) -> str:
        return f"{PREFIX}{self.kind}:{self.number}"


def _words(language: str) -> dict[str, str]:
    return WORDS.get(language, WORDS["fr"])


def clip_note(note: str, language: str = "fr") -> str:
    """A note of a clip in ``language`` (they are written in French)."""
    if language != "en":
        return note
    if found := _J_CUT.match(note):
        return f"the sound starts {found[1]} before the picture (J-cut)"
    if found := _L_CUT.match(note):
        return f"the sound goes on {found[1]} after the cut (L-cut)"
    return _NOTES_EN.get(note, note)


def chapter_marker(
    index: int, start: float, end: float, title: str, summary: str, *, language: str = "fr"
) -> Marker:
    """The whole chapter as a marker range, its title as the name."""
    fallback = f"{_words(language)['chapter']} {index}"
    return Marker(
        "chapter", index, start, max(0.0, end - start),
        marker_name(f"{index}. {one_line(title) or fallback}"),
        one_line(summary, NOTE_CHARS),
    )  # fmt: skip


def chapter_start(
    index: int, start: float, title: str, summary: str, *, language: str = "fr"
) -> Marker:
    """One frame where a chapter starts, its title as the name: what DaVinci Resolve shows for
    the chapters of a file (« Create a timeline »)."""
    fallback = f"{_words(language)['chapter']} {index}"
    return Marker(
        "chapter", index, start, 0.0,
        marker_name(one_line(title) or fallback), one_line(summary, NOTE_CHARS),
    )  # fmt: skip


def highlight_marker(rank: int, clip: Clip, reason: str, *, language: str = "fr") -> Marker:
    """The picture range of a highlight; the note gives the reason and the sound range (J-cut,
    L-cut) when it differs."""
    words = _words(language)
    sound = ""
    if clip.sound_in is not None or clip.sound_out is not None:
        heard = (clip.sound_in or clip.picture_in, clip.sound_out or clip.picture_out)
        sound = f" — {words['sound']} {_span(*heard)}"
    notes = [clip_note(note, language) for note in clip.notes]
    said = f" ({'; '.join(notes)})" if notes else ""
    return Marker(
        "highlight", rank, clip.picture_in, max(0.0, clip.picture_out - clip.picture_in),
        marker_name(f"{words['highlight']} {rank} — {one_line(reason)}"),
        one_line(f"{reason}{sound}{said}", NOTE_CHARS),
    )  # fmt: skip


def role_marker(
    role: str, number: int, clip: Clip, score: int | None, *, language: str = "fr"
) -> Marker:
    """The picture range an editing role is suggested for (establishing shot, cutaway, to avoid),
    its usability in the note."""
    words = _words(language)
    names = ROLE_NAMES_EN if language == "en" else ROLE_NAMES
    usable = f" · {words['usable']} {score}/100" if score is not None else ""
    return Marker(
        role, number, clip.picture_in, max(0.0, clip.picture_out - clip.picture_in),
        names.get(role, role), f"{words['suggestion']}{usable}",
    )  # fmt: skip


def shot_marker(
    number: int,
    start: float,
    motion: str,
    usability: int | None,
    text: str,
    *,
    language: str = "fr",
) -> Marker:
    """One frame at the start of a shot: its camera movement, usability and what it shows."""
    words = _words(language)
    parts = [motion, f"{words['usable']} {usability}/100" if usability is not None else "", text]
    return Marker(
        "shot", number, start, 0.0, f"{words['shot']} {number}",
        one_line(" · ".join(p for p in parts if p), NOTE_CHARS),
    )  # fmt: skip


def speech_sections(
    segments: Sequence[Segment], gap_s: float = SPEECH_GAP_S
) -> list[tuple[float, float]]:
    """Stretches of speech: reliable segments joined across pauses shorter than ``gap_s``."""
    sections: list[tuple[float, float]] = []
    for segment in sorted(segments, key=lambda s: s.start):
        if sections and segment.start - sections[-1][1] < gap_s:
            sections[-1] = (sections[-1][0], max(sections[-1][1], segment.end))
        else:
            sections.append((segment.start, segment.end))
    return sections


def speech_markers(segments: Sequence[Segment]) -> list[Marker]:
    """One marker range per stretch of speech, its first words as the note."""
    return [
        Marker(
            "speech",
            number,
            start,
            end - start,
            "Parole",
            one_line(speech_text(tuple(segments), start, end + 1e-3), NOTE_CHARS),
        )
        for number, (start, end) in enumerate(speech_sections(segments), 1)
    ]


def _span(start: float, end: float) -> str:
    return f"{format_clock(start, millis=True)} → {format_clock(end, millis=True)}"
