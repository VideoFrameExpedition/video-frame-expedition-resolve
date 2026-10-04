"""Bilingual analyses: which stored texts are translated, and how they are read.

The models write the analyses in one language. Every free text they hold is translated into
the other language by the ``translation`` stage and kept apart, in a dictionary keyed by the text
itself: the analysis rows never change, so no stage reading them has to run again. A text is read
in a language through that dictionary, or as it was written when it has no translation there.

Some texts were written in the other language despite the prompt (qwen3-vl-4b sometimes answers
in English): the stage asks for them in their own row's language too, so each language reads
entirely in that language.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

LANGUAGES: tuple[str, ...] = ("fr", "en")
FILE_SUFFIXES = {"fr": "FR", "en": "EN"}  # « _FR » at the end of a file name

Path = tuple[str, ...]  # keys in a JSON value, "*" for every item of a list


class TextKind(StrEnum):
    """What a text is: requests group texts of one kind, told to the model."""

    LABEL = "label"  # a few words: tag, colour, subject name, kind of place, mood
    SENTENCE = "sentence"  # caption, action, note, reason
    PARAGRAPH = "paragraph"  # description, summary
    TITLE = "title"  # title of a video or of a chapter
    PLACE = "place"  # a place name: local names stay, usual exonyms and words change


Spec = tuple[tuple[Path, TextKind], ...]

# ``frame_analyses.data`` (domain.vision.FrameAnalysis). ``visible_text`` is text as seen.
FRAME_TEXTS: Spec = (
    (("caption",), TextKind.SENTENCE),
    (("description",), TextKind.PARAGRAPH),
    (("place_type",), TextKind.LABEL),
    (("mood",), TextKind.LABEL),
    (("subjects", "*", "label"), TextKind.LABEL),
    (("subjects", "*", "description"), TextKind.SENTENCE),
    (("actions", "*"), TextKind.SENTENCE),
    (("dominant_colors", "*"), TextKind.LABEL),
    (("tags", "*"), TextKind.LABEL),
)
# ``shot_stories.story`` (domain.shot_story.StoryText) and ``.answer`` (the model's answer).
STORY_TEXTS: Spec = (
    (("summary",), TextKind.PARAGRAPH),
    (("main_action",), TextKind.SENTENCE),
    (("notes", "*", "what"), TextKind.SENTENCE),
)
ANSWER_TEXTS: Spec = (
    (("summary",), TextKind.PARAGRAPH),
    (("main_action",), TextKind.SENTENCE),
    (("beats", "*", "what"), TextKind.SENTENCE),
)
# ``video_synthesis.data`` (pipeline.stages.synthesis).
SYNTHESIS_TEXTS: Spec = (
    (("title",), TextKind.TITLE),
    (("logline",), TextKind.SENTENCE),
    (("summary",), TextKind.PARAGRAPH),
    (("chapters", "*", "title"), TextKind.TITLE),
    (("chapters", "*", "summary"), TextKind.PARAGRAPH),
    (("moments", "*", "reason"), TextKind.SENTENCE),
    (("tags", "*", "label"), TextKind.LABEL),
)
# ``context_place``: its name columns, and the names in ``data`` (roads and the full address
# keep their local names; codes, postcodes and the attribution are not text).
PLACE_COLUMNS: tuple[str, ...] = ("label", "locality", "region", "country")
PLACE_DATA_TEXTS: Spec = (
    (("sublocality",), TextKind.PLACE),
    (("county",), TextKind.PLACE),
    (("state",), TextKind.PLACE),
    (("feature", "name"), TextKind.PLACE),
)


def text_key(text: str) -> str:
    """The dictionary key of a text: exactly the text as stored."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def other_language(language: str) -> str:
    return "en" if language == "fr" else "fr"


def file_suffix(language: str) -> str:
    """``FR`` or ``EN``: the language at the end of a file name (``clip_FR.txt``)."""
    return FILE_SUFFIXES.get(language, language.upper())


# ---------------------------------------------------------------- walking stored values
def texts_at(value: Any, spec: Spec) -> Iterator[tuple[TextKind, str]]:
    """Every non-blank text of ``value`` named by ``spec``, in order."""
    for path, kind in spec:
        for text in _at(value, path):
            if isinstance(text, str) and text.strip():
                yield kind, text


def _at(value: Any, path: Path) -> Iterator[Any]:
    if not path:
        yield value
        return
    head, rest = path[0], path[1:]
    if head == "*":
        if isinstance(value, list):
            for item in value:
                yield from _at(item, rest)
    elif isinstance(value, dict) and head in value:
        yield from _at(value[head], rest)


def translated(value: Any, spec: Spec, translate: Callable[[str], str]) -> Any:
    """A copy of ``value`` whose texts named by ``spec`` went through ``translate``."""
    for path, _kind in spec:
        value = _replace(value, path, translate)
    return value


def _replace(value: Any, path: Path, translate: Callable[[str], str]) -> Any:
    if not path:
        return translate(value) if isinstance(value, str) and value.strip() else value
    head, rest = path[0], path[1:]
    if head == "*":
        if isinstance(value, list):
            return [_replace(item, rest, translate) for item in value]
        return value
    if isinstance(value, dict) and head in value:
        return {**value, head: _replace(value[head], rest, translate)}
    return value


# ---------------------------------------------------------------- reading
@dataclass(frozen=True, slots=True)
class Dictionary:
    """The texts of the analyses in one language: ``language`` None reads them as written."""

    language: str | None
    entries: Mapping[str, str]  # text_key(source) -> text in ``language``

    def __call__(self, text: str) -> str:
        if self.language is None or not text:
            return text
        return self.entries.get(text_key(text), text)

    def maybe(self, text: str | None) -> str | None:
        return None if text is None else self(text)

    def has(self, text: str) -> bool:
        return text_key(text) in self.entries


AS_WRITTEN = Dictionary(None, {})


# ---------------------------------------------------------------- what the stage asks
@dataclass(frozen=True, slots=True)
class SourceText:
    """A text of a video's analyses and the language its row says it is written in (None:
    unknown, e.g. the subject labels of the vision model)."""

    text: str
    kind: TextKind
    language: str | None


@dataclass(frozen=True, slots=True)
class Wanted:
    """One text to ask for in one language."""

    text: str
    kind: TextKind
    target: str


def wanted(
    sources: Sequence[SourceText], known: Mapping[tuple[str, str], str]
) -> tuple[list[Wanted], list[Wanted]]:
    """What is still to ask for: first pass, then second pass.

    First pass: each text in the other language of its row (both languages when unknown).
    Second pass: a text the first pass gave back unchanged is already in that other language,
    or the same word in both (« beige »): it is asked for in its row's language too, so that a
    text the model wrote in the wrong language reads in the right one. ``known``: the entries
    already in the dictionary, ``(text_key, target) -> text``. Texts come once, in order.
    """
    first: list[Wanted] = []
    second: list[Wanted] = []
    seen: set[tuple[str, str]] = set()

    def ask(into: list[Wanted], source: SourceText, target: str) -> None:
        key = (text_key(source.text), target)
        if key not in known and key not in seen:
            seen.add(key)
            into.append(Wanted(source.text, source.kind, target))

    for source in sources:
        if source.language not in LANGUAGES:
            for target in LANGUAGES:
                ask(first, source, target)
            continue
        other = other_language(source.language)
        found = known.get((text_key(source.text), other))
        if found is None:
            ask(first, source, other)
        elif found == source.text:
            ask(second, source, source.language)
    return first, second


def unchanged(source: str, answer: str) -> bool:
    """The model gave the text back as it was (spaces aside)."""
    return source.strip() == answer.strip()


# Scripts neither French nor English is written in: Cyrillic, Hebrew, Arabic, Devanagari, Thai,
# Japanese kana, Chinese characters, Korean. Qwen sometimes slips a Chinese word into an English
# sentence (« a 茄子 and chicken stew »).
_OTHER_SCRIPTS = re.compile("[Ѐ-ӿ֐-׿؀-ۿऀ-ॿ฀-๿぀-ヿ㐀-䶿一-鿿가-힯]")


def plausible(source: str, answer: str) -> bool:
    """A translation: not empty, not a commentary about the text, and no word slipped in from a
    script the text does not use."""
    if not answer.strip() or len(answer) > 3 * len(source) + 40:
        return False
    return not (_OTHER_SCRIPTS.search(answer) and not _OTHER_SCRIPTS.search(source))


# ---------------------------------------------------------------- requests
MAX_ITEMS = {TextKind.LABEL: 60, TextKind.PARAGRAPH: 12}  # texts per request; others: 30
DEFAULT_MAX_ITEMS = 30


@dataclass(frozen=True, slots=True)
class Batch:
    """Texts of one kind asked for in one language, in one request."""

    target: str
    kind: TextKind
    items: tuple[Wanted, ...]


def batches(items: Sequence[Wanted], *, max_chars: int) -> list[Batch]:
    """``items`` grouped by language and kind (in the order they come), cut so that a request
    holds at most ``max_chars`` characters of text and a few dozen texts."""
    groups: dict[tuple[str, TextKind], list[Wanted]] = {}
    for item in items:
        groups.setdefault((item.target, item.kind), []).append(item)
    found: list[Batch] = []
    for (target, kind), group in groups.items():
        limit = MAX_ITEMS.get(kind, DEFAULT_MAX_ITEMS)
        current: list[Wanted] = []
        size = 0
        for item in group:
            if current and (len(current) >= limit or size + len(item.text) > max_chars):
                found.append(Batch(target, kind, tuple(current)))
                current, size = [], 0
            current.append(item)
            size += len(item.text)
        if current:
            found.append(Batch(target, kind, tuple(current)))
    return found
