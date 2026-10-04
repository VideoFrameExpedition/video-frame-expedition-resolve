"""Export formats of a video's analyses: pure text builders.

- **CSV** for a French Excel, opened by a double click: UTF-8 with a BOM (else Excel reads
  ANSI), ``;`` between cells (``,`` is the French decimal mark), decimal comma, CRLF, one line
  per row. A text cell that starts like a formula (``=``, ``+``, ``-``, ``@``, tab) gets a leading
  apostrophe: captions and speech come from the footage (CSV injection).
- **Chapters** as YouTube reads them in a description: one « 00:00 Title » line per chapter, the
  first at 00:00, times rounded down to the second.
- **Marker EDL** (CMX 3600) as DaVinci Resolve exports and imports timeline markers
  (« Import > Timeline Markers from EDL »): one event of one frame per marker, then a comment line
  `` |C:ResolveColorBlue |M:name |D:frames``. Source timecodes are the file's own (its start
  timecode plus the frame); record timecodes start at the timeline's start (Resolve's default
  01:00:00:00), for a timeline that opens on the clip's first frame.
- **Markdown**: text from the footage and the local models is one line, escaped (no link, image,
  table break or HTML comes out of it).
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from vfe_vision.domain.timecode import (
    format_clock,
    frames_timecode,
    is_drop_frame,
    timecode_frames,
)
from vfe_vision.domain.transcript import clean_untrusted

CsvCell = str | int | float | bool | None

RESOLVE_COLORS = frozenset({
    "Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia", "Rose", "Lavender",
    "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream",
})  # fmt: skip
DEFAULT_RECORD_START = "01:00:00:00"  # Resolve's default timeline start
MAX_EDL_EVENTS = 999  # three digits in CMX 3600
MARKER_NAME_CHARS = 60

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>|#~!])")

LIGHT_PHASE_FR = {
    "day": "jour", "golden_hour": "heure dorée", "blue_hour": "heure bleue",
    "nautical_twilight": "crépuscule nautique",
    "astronomical_twilight": "crépuscule astronomique", "night": "nuit",
}  # fmt: skip
ROLE_FR = {"establishing": "plan d'ensemble", "b_roll": "illustration", "avoid": "à éviter"}
LIGHT_PHASE_EN = {
    "day": "day", "golden_hour": "golden hour", "blue_hour": "blue hour",
    "nautical_twilight": "nautical twilight",
    "astronomical_twilight": "astronomical twilight", "night": "night",
}  # fmt: skip
ROLE_EN = {"establishing": "establishing shot", "b_roll": "B-roll", "avoid": "avoid"}


def light_phase_name(phase: str | None, language: str = "fr") -> str | None:
    """A light phase in words, in French or in English."""
    if not phase:
        return phase
    return (LIGHT_PHASE_EN if language == "en" else LIGHT_PHASE_FR).get(phase, phase)


def role_name(role: str, language: str = "fr") -> str:
    """An editing role in words (establishing, b_roll, avoid)."""
    return (ROLE_EN if language == "en" else ROLE_FR).get(role, role)


# ---------------------------------------------------------------- text
def one_line(text: str | None, limit: int | None = None) -> str:
    """Untrusted text as one clean line, cut between words with « … » beyond ``limit``."""
    line = clean_untrusted(text or "")
    if limit is None or len(line) <= limit:
        return line
    space = line.rfind(" ", 0, limit)
    end = space if space >= limit // 2 else limit - 1
    return line[:end].rstrip(" ,;:–—") + "…"


def decimal(value: float, digits: int = 3) -> str:
    """French decimal: ``12.5`` → ``12,5`` (at most ``digits`` decimals, no trailing zero)."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return (text if text not in {"-0", ""} else "0").replace(".", ",")


# ---------------------------------------------------------------- CSV
def csv_text(header: Sequence[str], rows: Iterable[Sequence[CsvCell]]) -> str:
    """CSV for a French Excel (module docstring), BOM included."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_cell(value) for value in row])
    return "﻿" + buffer.getvalue()


def csv_cell(value: CsvCell) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return decimal(value) if math.isfinite(value) else ""
    text = one_line(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


# ---------------------------------------------------------------- chapters
def youtube_chapters(chapters: Sequence[tuple[float, str]]) -> str:
    """« 00:00 Title » lines, the first chapter at 00:00 (YouTube also wants three chapters of
    10 s or more to show them)."""
    lines = []
    for index, (start, title) in enumerate(chapters):
        at = 0 if index == 0 else math.floor(max(0.0, start))
        name = one_line(title, 100) or f"Chapitre {index + 1}"
        lines.append(f"{format_clock(at)} {name}")
    return "\n".join(lines) + "\n" if lines else ""


# ---------------------------------------------------------------- marker EDL
@dataclass(frozen=True, slots=True)
class EdlMarker:
    frame: int  # from the first frame of the file (0-based)
    duration: int  # frames, 1 or more
    color: str  # a Resolve marker colour (RESOLVE_COLORS)
    name: str


def marker_name(text: str) -> str:
    """A marker name that keeps the EDL comment line whole (no ``|``, one line, bounded)."""
    return one_line(text.replace("|", "/"), MARKER_NAME_CHARS) or "Marqueur"


def marker_edl(
    title: str,
    markers: Sequence[EdlMarker],
    *,
    fps: float,
    source_start: str | None,
    record_start: str = DEFAULT_RECORD_START,
) -> str:
    """CMX 3600 marker EDL (module docstring). Drop-frame when the file's start timecode is
    (29.97 or 59.94 i/s with ``;``), and then the record timecodes too."""
    drop = is_drop_frame(source_start, fps)
    if drop and ";" not in record_start:
        record_start = record_start[:8] + ";" + record_start[9:]
    source_zero = timecode_frames(source_start or "00:00:00:00", fps)
    record_zero = timecode_frames(record_start, fps)

    def tc(frames: int) -> str:
        return frames_timecode(frames, fps, drop_frame=drop)

    lines = [f"TITLE: {one_line(title, 70) or 'Video Frame Expedition'}",
             f"FCM: {'DROP FRAME' if drop else 'NON-DROP FRAME'}", ""]  # fmt: skip
    ordered = sorted(markers, key=lambda m: m.frame)[:MAX_EDL_EVENTS]
    for number, marker in enumerate(ordered, 1):
        color = marker.color if marker.color in RESOLVE_COLORS else "Blue"
        source, record = source_zero + marker.frame, record_zero + marker.frame
        lines.append(
            f"{number:03d}  001      V     C        {tc(source)} {tc(source + 1)} "
            f"{tc(record)} {tc(record + 1)}  "
        )
        lines.append(
            f" |C:ResolveColor{color} |M:{marker_name(marker.name)} |D:{max(1, marker.duration)}"
        )
        lines.append("")
    return "\r\n".join(lines)


# ---------------------------------------------------------------- Markdown
def md_text(text: str | None, limit: int | None = None) -> str:
    """Untrusted text for a Markdown document: one line, bounded, Markdown and HTML escaped."""
    return _MD_SPECIAL.sub(r"\\\1", one_line(text, limit))


def md_table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    """A Markdown table (cells already escaped)."""
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(cell or " " for cell in row) + " |" for row in rows]
    return lines
