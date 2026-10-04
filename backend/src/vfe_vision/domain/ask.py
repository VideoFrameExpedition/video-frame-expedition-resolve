"""Questions on the library, pure functions: which passages the model reads, how
many fit one slot of the loaded instance, the lines it reads them as, and what its citations
point to.

- **Choice**: the search's candidates are taken best first, but each passage already taken from
  a video pushes that video's next ones back ``VIDEO_PENALTY`` places, so that one talkative
  video does not fill the prompt unless the question (or a filter) is about it; a passage that
  mostly repeats one already taken (same video, same kind of data, overlapping times) is left.
- **Budget**: the answer, the instructions and the question are reserved first; the passages
  get what is left of one slot, each cut to ``PASSAGE_MAX_CHARS``.
- **Citations**: ``[n]``, ``[1, 3]``, ``[2-4]``, ``[1][4]``; a number that is not a passage is
  removed from the text, the others are written ``[1][3]``. An answer that starts with
  ``NO_ANSWER`` says that the passages do not answer.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vfe_vision.domain.timecode import format_clock
from vfe_vision.domain.transcript import clean_untrusted

ASK_RULES_VERSION = 1
VIDEO_PENALTY = 4  # places each passage already taken pushes its video's next ones back
MAX_PASSAGES = 16
PASSAGE_MAX_CHARS = 700  # ~230 tokens: about ten passages in a qwen3-vl-4b slot (4,864 tokens)
MIN_OVERLAP = 0.5  # of the shorter range: the second passage repeats the first
ANSWER_MIN_TOKENS = 256
ANSWER_MAX_TOKENS = 800
NO_ANSWER = "NO_ANSWER"
MAX_RANGE = 10  # « [2-40] » is not a citation of 39 passages
MAX_CHECK_FRAMES = 4

# Passages carrying the same kind of data: a keyframe inside a shot already taken repeats it,
# the speech over that shot does not.
_FAMILY = {"video": "video", "chapter": "chapter", "shot": "visual", "keyframe": "visual",
           "transcript": "speech"}  # fmt: skip
_KIND_EN = {"video": "whole video", "chapter": "chapter", "shot": "shot", "keyframe": "frame",
            "transcript": "speech"}  # fmt: skip


class AnswerStatus(StrEnum):
    ANSWERED = "answered"  # cites at least one passage
    NO_ANSWER = "no_answer"  # the passages do not answer (the model says so)
    UNCITED = "uncited"  # text that cites nothing: not shown as an answer from the videos
    NO_PASSAGES = "no_passages"  # nothing found: the model was not asked
    CANCELLED = "cancelled"  # stopped by the user (or the page closed) while it was written
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Candidate:
    """A passage found by the search, in its rank order, as the model would read it."""

    video_id: str
    kind: str  # video, chapter, shot, keyframe, transcript
    t_start: float | None
    t_end: float | None
    line: str  # without its number (``passage_line``)


@dataclass(frozen=True, slots=True)
class AskBudget:
    slot: int  # tokens of one slot of the loaded instance
    answer: int  # max_tokens of the answer
    prompt: int  # instructions and question, estimated
    passages: int  # what is left for the passages


def ask_budget(slot: int, prompt_tokens: int) -> AskBudget:
    """The answer gets a fifth of the slot (256–800 tokens), a twentieth stays as a margin for
    the estimate, the passages get the rest."""
    answer = min(ANSWER_MAX_TOKENS, max(ANSWER_MIN_TOKENS, slot // 5))
    left = slot - answer - prompt_tokens - slot // 20
    return AskBudget(slot=slot, answer=answer, prompt=prompt_tokens, passages=max(0, left))


# ---------------------------------------------------------------- which passages
def _overlap(a: Candidate, b: Candidate) -> float:
    """How much of the shorter range the two share (a point inside the other counts whole)."""
    if a.t_start is None or b.t_start is None:
        return 1.0 if a.t_start is None and b.t_start is None else 0.0
    a_end = a.t_end if a.t_end is not None else a.t_start
    b_end = b.t_end if b.t_end is not None else b.t_start
    shared = min(a_end, b_end) - max(a.t_start, b.t_start)
    shorter = min(a_end - a.t_start, b_end - b.t_start)
    if shorter <= 0:
        return 1.0 if shared >= 0 else 0.0
    return max(0.0, shared) / shorter


def repeats(candidate: Candidate, taken: Candidate) -> bool:
    return (
        candidate.video_id == taken.video_id
        and _FAMILY.get(candidate.kind) == _FAMILY.get(taken.kind)
        and _overlap(candidate, taken) >= MIN_OVERLAP
    )


def choose_passages(
    candidates: Sequence[Candidate],
    budget_tokens: int,
    cost: Callable[[str], int],
    *,
    max_passages: int = MAX_PASSAGES,
    video_penalty: int = VIDEO_PENALTY,
) -> list[int]:
    """The candidates the model reads (their indexes), grouped by video in the order of each
    video's best passage, then in time order: a video told in order is easier to answer from.

    Each round takes the best candidate after the penalty of its video, if it fits what is left
    of ``budget_tokens`` and repeats nothing taken; the others wait for the next rounds.
    """
    taken: list[int] = []
    per_video: dict[str, int] = {}
    left = budget_tokens
    pending = list(range(len(candidates)))

    def place(i: int) -> tuple[int, int]:
        return i + video_penalty * per_video.get(candidates[i].video_id, 0), i

    while pending and len(taken) < max_passages:
        best = min(pending, key=place)
        pending.remove(best)
        candidate = candidates[best]
        if any(repeats(candidate, candidates[i]) for i in taken):
            continue
        price = cost(candidate.line)
        if price > left:
            continue
        taken.append(best)
        left -= price
        per_video[candidate.video_id] = per_video.get(candidate.video_id, 0) + 1
    first: dict[str, int] = {}
    for i in sorted(taken):
        first.setdefault(candidates[i].video_id, i)

    def order(i: int) -> tuple[int, float, int]:
        c = candidates[i]
        return first[c.video_id], -1.0 if c.t_start is None else c.t_start, i

    return sorted(taken, key=order)


# ---------------------------------------------------------------- how the model reads them
def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    space = text.rfind(" ", 0, limit)
    end = space if space >= limit // 2 else limit - 1
    return text[:end].rstrip(" ,;:–—·") + "…"


def _span(t_start: float | None, t_end: float | None) -> str | None:
    if t_start is None:
        return None
    start = format_clock(max(0.0, t_start))
    end = format_clock(max(0.0, t_end)) if t_end is not None else start
    return start if end == start else f"{start}–{end}"


def passage_line(
    *,
    filename: str,
    title: str | None,
    kind: str,
    shot_idx: int | None,
    t_start: float | None,
    t_end: float | None,
    text: str,
    limit: int = PASSAGE_MAX_CHARS,
) -> str:
    """One passage on one cleaned line: its video, its kind and time, then its text (cut).
    Everything in it may come from the footage: no fence tag, control or bidi character."""
    name = clean_untrusted(filename)[:120]
    if title and (shown := clean_untrusted(title)[:120]) and shown != name:
        name += f" ({shown})"
    where = _KIND_EN.get(kind, kind)
    if kind == "shot" and shot_idx is not None:
        where += f" {shot_idx + 1}"
    when = _span(t_start, t_end)
    head = f"{name} — {where}{f', {when}' if when else ''}"
    body = clean_untrusted(" · ".join(line for line in text.splitlines() if line.strip()))
    return f"{head}: {_cut(body, limit)}"


# ---------------------------------------------------------------- citations
_CITATION = re.compile(r"[ \t]*\[\s*(\d{1,3}(?:\s*(?:[,;]|[-–])\s*\d{1,3})*)\s*\]")
_NUMBERS = re.compile(r"(\d{1,3})\s*[-–]\s*(\d{1,3})|(\d{1,3})")
_NO_ANSWER = re.compile(rf"^\s*\**{NO_ANSWER}\**\s*[:：.\-–—]?\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CitedAnswer:
    text: str  # citations to real passages written « [1][3] », the others removed
    cited: tuple[int, ...]  # passage numbers, in the order of their first citation
    dropped: int  # citations of numbers that are not passages
    no_answer: bool  # the model said the passages do not answer


def _numbers(group: str) -> list[int]:
    out: list[int] = []
    for match in _NUMBERS.finditer(group):
        low, high, single = match.groups()
        if single is not None:
            out.append(int(single))
            continue
        a, b = int(low), int(high)
        if a <= b and b - a < MAX_RANGE:
            out += range(a, b + 1)
        else:  # not a range of passages: both ends as cited
            out += [a, b]
    return out


def strip_no_answer(text: str) -> tuple[str, bool]:
    """The answer without its ``NO_ANSWER`` marker, and whether it had one."""
    match = _NO_ANSWER.match(text)
    return (text[match.end() :], True) if match else (text, False)


def read_citations(answer: str, passages: int) -> CitedAnswer:
    """Keep only the citations of passages 1…``passages``, in a single form."""
    text, no_answer = strip_no_answer(answer)
    cited: list[int] = []
    dropped = 0

    def rewrite(match: re.Match[str]) -> str:
        nonlocal dropped
        numbers = _numbers(match.group(1))
        valid = [n for n in dict.fromkeys(numbers) if 1 <= n <= passages]
        dropped += sum(1 for n in numbers if not 1 <= n <= passages)
        cited.extend(n for n in valid if n not in cited)
        if not valid:  # removed with the space before it: « chat [17]. » → « chat. »
            return ""
        space = " " if match.group(0)[:1] in {" ", "\t"} else ""
        return space + "".join(f"[{n}]" for n in valid)

    text = _CITATION.sub(rewrite, text)
    text = "\n".join(line.rstrip() for line in text.strip().splitlines())
    return CitedAnswer(text=text, cited=tuple(cited), dropped=dropped, no_answer=no_answer)


def answer_status(cited: CitedAnswer) -> AnswerStatus:
    if cited.cited:
        return AnswerStatus.ANSWERED
    return AnswerStatus.NO_ANSWER if cited.no_answer else AnswerStatus.UNCITED


# ---------------------------------------------------------------- visual check
class Verdict(StrEnum):
    CONFIRMED = "confirmed"
    PARTLY = "partly_confirmed"
    CONTRADICTED = "contradicted"
    NOT_VISIBLE = "not_visible"


class VisualCheck(BaseModel):
    """What the images of the cited moments say about an answer written from text."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict = Field(
        description="confirmed: the images show what the answer says; partly_confirmed: some of "
        "it; contradicted: the images show something else; not_visible: the images cannot tell."
    )
    note: str = Field(
        description="One or two sentences: what the images show that confirms or corrects the "
        "answer."
    )


def check_frame_count(slot: int, text_tokens: int, image_tokens: int, max_tokens: int) -> int:
    """How many images of the cited moments fit one slot next to the question and the answer
    (at most ``MAX_CHECK_FRAMES``)."""
    room = slot - text_tokens - max_tokens - slot // 20
    return max(0, min(MAX_CHECK_FRAMES, room // max(1, image_tokens)))
