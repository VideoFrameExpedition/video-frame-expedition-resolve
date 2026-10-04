"""The analysis file kept next to each video: its name, its format, pure helpers.

``<stem>_FR.txt`` and ``<stem>_EN.txt`` beside ``<stem>.<ext>`` are JSON texts holding every
analysis of the video, one per language (``<stem>.txt`` before), without images,
absolute paths or internal ids other than the local references an import needs to link the rows
again. Besides them, the application writes there only the video's subtitle files, when a
timeline is added to DaVinci Resolve with subtitles: named by the same rule.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from vfe_vision.core.paths import is_video_file
from vfe_vision.domain.translation import (
    ANSWER_TEXTS,
    FRAME_TEXTS,
    PLACE_COLUMNS,
    PLACE_DATA_TEXTS,
    STORY_TEXTS,
    SYNTHESIS_TEXTS,
    Dictionary,
    file_suffix,
    texts_at,
    translated,
)

FORMAT = "vfe-vision-analysis"
FORMAT_VERSION = 1
SUFFIX = ".txt"  # the file's extension; ``<stem>.txt`` alone: the one file of earlier versions
MAX_BYTES = 64 * 1024 * 1024  # a bigger ".txt" is not one of ours: never read whole


class SidecarStatus(StrEnum):
    """What became of an analysis file asked for (export button, end of an analysis)."""

    WRITTEN = "written"
    CONFLICT = "conflict"  # a file of that name the application did not write: left as it is
    OFFLINE = "offline"  # the video file is out of reach
    NOT_ANALYZED = "not_analyzed"  # nothing analysed yet: nothing to write
    FAILED = "failed"  # read-only folder, access denied, disk full…
    UNKNOWN = "unknown"  # no such video in the library


# ---------------------------------------------------------------- name
def sidecar_name(video_name: str, siblings: Iterable[str], suffix: str = SUFFIX) -> str:
    """``<stem>.txt``, or ``<stem>.<ext>.txt`` when another video of the folder has the same stem
    (``clip.mp4`` and ``clip.mov`` get ``clip.mp4.txt`` and ``clip.mov.txt``): one rule for both,
    whatever order they are analysed in. ``siblings``: names of the folder's files. ``suffix``:
    another file of the video (``_SHOTS.srt``)."""
    stem = Path(video_name).stem
    own = video_name.casefold()
    for name in siblings:
        other = Path(name)
        if (
            name.casefold() != own
            and other.stem.casefold() == stem.casefold()
            and is_video_file(other)
        ):
            return video_name + suffix
    return stem + suffix


def other_name(video_name: str, chosen: str, suffix: str = SUFFIX) -> str:
    """The other spelling of the rule, looked for too when importing (the folder's videos may
    have changed since the file was written)."""
    short = Path(video_name).stem + suffix
    return video_name + suffix if chosen.casefold() == short.casefold() else short


def language_suffix(language: str) -> str:
    """``_FR.txt``: the end of the name of the file in that language."""
    return f"_{file_suffix(language)}{SUFFIX}"


# ---------------------------------------------------------------- reading
def is_ours(document: Any) -> bool:
    """Written by the application (a JSON object carrying our ``format``)."""
    return isinstance(document, dict) and document.get("format") == FORMAT


def parse(data: bytes) -> Any:
    """The JSON value of a file (a BOM added by an editor is fine), None when it is not JSON."""
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None


def import_problem(document: Any, *, fingerprint: str, size_bytes: int) -> str | None:
    """Why this document cannot be imported for this video (None: it can)."""
    if not is_ours(document):
        return "ce fichier n'a pas été écrit par l'application"
    version = document.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return "version du format illisible"
    if version > FORMAT_VERSION:
        return f"format {version} écrit par une version plus récente de l'application"
    video = document.get("video")
    if not isinstance(video, dict):
        return "description de la vidéo absente"
    if video.get("fingerprint") != fingerprint or video.get("size_bytes") != size_bytes:
        return "il décrit un autre contenu (empreinte ou taille différente de la vidéo)"
    return None


# ---------------------------------------------------------------- values
def iso_utc(value: datetime) -> str:
    """ISO 8601 in UTC with a ``Z`` (naive values are UTC, as in the database)."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_utc(text: str) -> datetime:
    parsed = datetime.fromisoformat(text)
    return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def without_folder(value: Any, folder: str) -> Any:
    """``value`` with the video's folder taken out of every text (ffprobe and ExifTool name the
    file by its absolute path): a file keeps its name, the folder itself becomes ``.``."""
    variants = {
        v.rstrip("\\/")
        for v in (folder, folder.replace("\\", "/"), folder.replace("/", "\\"))
        if v.rstrip("\\/")
    }
    if not variants:
        return value
    alternatives = "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))
    pattern = re.compile(rf"(?:{alternatives})(?:[\\/]|$)", re.IGNORECASE)

    def clean(item: Any) -> Any:
        if isinstance(item, str):
            return (pattern.sub("", item) or ".") if pattern.search(item) else item
        if isinstance(item, dict):
            return {key: clean(inner) for key, inner in item.items()}
        if isinstance(item, list):
            return [clean(inner) for inner in item]
        return item

    return clean(value)


# ---------------------------------------------------------------- ids inside JSON payloads
def story_keyframes(ids: Iterable[Any], mapping: Mapping[str, str]) -> list[str | None]:
    """The keyframe ids of a shot story in this library; None: a frame extracted for the story
    (or a keyframe the file does not have, extracted again like one)."""
    return [mapping.get(item) if isinstance(item, str) else None for item in ids]


def synthesis_data(data: Mapping[str, Any], mapping: Mapping[str, str]) -> dict[str, Any]:
    """The stored synthesis with the keyframe ids of its blocks in this library."""
    blocks = []
    for block in data.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        ids = [mapping[i] for i in block.get("keyframe_ids") or [] if i in mapping]
        blocks.append({**block, "keyframe_ids": ids})
    return {**data, "blocks": blocks}


# ---------------------------------------------------------------- languages
def written_language(document: Mapping[str, Any], default: str) -> str:
    """The language the analyses of a document were written in: the one most of its rows say
    (descriptions, stories, synthesis), ``default`` when none says."""
    found: list[str] = []
    for key in ("frame_analyses", "shot_stories"):
        found += [str(row["language"]) for row in _table(document, key) if row.get("language")]
    synthesis = document.get("video_synthesis")
    if isinstance(synthesis, dict) and synthesis.get("language"):
        found.append(str(synthesis["language"]))
    return max(set(found), key=found.count) if found else default


def _table(document: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key)
    rows = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def document_texts(document: Mapping[str, Any]) -> list[str]:
    """Every text of a document that reads differently in French and in English, in order: the
    same slots in the two files of a video, written together."""
    found: list[str] = []
    for row in _table(document, "frame_analyses"):
        found += [text for _k, text in texts_at(row.get("data"), FRAME_TEXTS)]
    for row in _table(document, "shot_stories"):
        found += [text for _k, text in texts_at(row.get("story"), STORY_TEXTS)]
        found += [text for _k, text in texts_at(row.get("answer"), ANSWER_TEXTS)]
    for row in _table(document, "video_synthesis"):
        found += [text for _k, text in texts_at(row.get("data"), SYNTHESIS_TEXTS)]
    for row in _table(document, "detections"):
        label = row.get("label")
        if row.get("source") == "vlm" and isinstance(label, str) and label.strip():
            found.append(label)
    for row in _table(document, "context_place"):
        found += [row[c] for c in PLACE_COLUMNS if isinstance(row.get(c), str) and row[c].strip()]
        found += [text for _k, text in texts_at(row.get("data"), PLACE_DATA_TEXTS)]
    return found


def localized(document: Mapping[str, Any], tr: Dictionary) -> dict[str, Any]:
    """The document with its texts in the language of ``tr``, and its rows saying so."""
    language = tr.language
    out = dict(document)

    def rows(key: str, change: Any) -> None:
        value = document.get(key)
        if isinstance(value, list):
            out[key] = [change(dict(row)) if isinstance(row, dict) else row for row in value]
        elif isinstance(value, dict):
            out[key] = change(dict(value))

    def frame(row: dict[str, Any]) -> dict[str, Any]:
        row["data"] = translated(row.get("data"), FRAME_TEXTS, tr)
        row["language"] = language
        return row

    def story(row: dict[str, Any]) -> dict[str, Any]:
        row["story"] = translated(row.get("story"), STORY_TEXTS, tr)
        row["answer"] = translated(row.get("answer"), ANSWER_TEXTS, tr)
        row["language"] = language
        return row

    def synthesis(row: dict[str, Any]) -> dict[str, Any]:
        row["data"] = translated(row.get("data"), SYNTHESIS_TEXTS, tr)
        row["language"] = language
        return row

    def detection(row: dict[str, Any]) -> dict[str, Any]:
        if row.get("source") == "vlm" and isinstance(row.get("label"), str):
            row["label"] = tr(row["label"])
        return row

    def place(row: dict[str, Any]) -> dict[str, Any]:
        for column in PLACE_COLUMNS:
            if isinstance(row.get(column), str):
                row[column] = tr(row[column])
        data = translated(row.get("data"), PLACE_DATA_TEXTS, tr)
        if isinstance(data, dict) and "language" in data:  # the names' language (Nominatim)
            data = {**data, "language": language}
        row["data"] = data
        return row

    rows("frame_analyses", frame)
    rows("shot_stories", story)
    rows("video_synthesis", synthesis)
    rows("detections", detection)
    rows("context_place", place)
    out["language"] = language
    return out


def paired(base: Mapping[str, Any], other: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(text in base, the same text in other)`` for the two files of a video written together
    (same export, same slots); none when they were not."""
    if base.get("exported_at") != other.get("exported_at"):
        return []
    mine, theirs = document_texts(base), document_texts(other)
    if len(mine) != len(theirs):
        return []
    return list(zip(mine, theirs, strict=True))


# ---------------------------------------------------------------- text
def render(document: Mapping[str, Any]) -> str:
    """The file's text: JSON with one line per key of the small blocks and one line per row of
    the tables, readable and short enough to open in any editor."""
    items = list(document.items())
    lines = ["{"]
    for position, (key, value) in enumerate(items):
        comma = "," if position < len(items) - 1 else ""
        lines.append(f" {_compact(key)}: {_block(value)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _block(value: Any) -> str:
    if isinstance(value, list) and value and all(isinstance(row, dict) for row in value):
        return "[\n" + ",\n".join(f"  {_compact(row)}" for row in value) + "\n ]"
    if isinstance(value, dict) and value:
        pairs = [f"  {_compact(k)}: {_compact(v)}" for k, v in value.items()]
        return "{\n" + ",\n".join(pairs) + "\n }"
    return _compact(value)


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
