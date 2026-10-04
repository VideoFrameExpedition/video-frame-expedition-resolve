"""What happens in a shot, told by the vision model from several of its frames (pure).

The answer schema and its descriptions are the ones that were measured (v2): changing a
word changed the answers, so they stay verbatim. ``camera`` and ``best_image`` are asked because
the validated prompt asks for them; they are stored for diagnostics and never used (the camera
comes from the optical flow, the poster from the sharpest keyframe).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum
from functools import cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from vfe_vision.domain.transcript import clean_untrusted

SHOT_STORY_SCHEMA_VERSION = 1
FRAMES = 4  # images per request
MIN_SHOT_S = 4.0
MAX_BLACK_RATIO = 0.5
MAX_FROZEN_RATIO = 0.8
MAX_PARTS_PER_VIDEO = 60
FILL_GAP_S = 1.0  # a fill-in frame this far at least from a chosen keyframe
EDGE_SLACK_S = 0.5  # a keyframe may sit one frame before the shot's first image
SUMMARY_MAX = 300
_MODEL_DOC = "What happens during one continuous shot, judged from several of its frames in order."


class CameraView(StrEnum):
    STILL = "still"
    MOVING = "moving"
    FOLLOWS_SUBJECT = "follows_subject"


class Continuity(StrEnum):
    SAME_SUBJECT = "same_subject"
    SUBJECT_CHANGES = "subject_changes"
    NO_MAIN_SUBJECT = "no_main_subject"


def _fields(images: int | None) -> dict[str, Any]:
    """The answer's fields, in the measured order; ``images`` bounds the image numbers."""
    beat = create_model(
        "Beat" if images is None else f"Beat{images}",
        __config__=ConfigDict(extra="forbid"),
        image=(int, Field(ge=1, le=images, description="Image number (1 = first).")),
        what=(str, Field(description="What changes at this image, a few words.")),
    )
    return {
        "summary": (
            str,
            Field(
                description="One or two sentences telling what happens during the shot, in order."
            ),
        ),
        "main_action": (
            str,
            Field(
                description="What the main subject does, as a short verb phrase (never a camera "
                "movement); empty string if nothing happens."
            ),
        ),
        "beats": (
            list[beat],
            Field(
                max_length=4,
                description="Only the images where something changes (an action starts or ends, "
                "a subject appears or leaves); empty list if nothing changes.",
            ),
        ),
        "camera": (
            CameraView,
            Field(
                description="still: the camera does not move, only the subject may move (compare "
                "the background of the images); moving: the framing changes (pan, tilt, zoom, "
                "walking, shake); follows_subject: the camera moves to keep a moving subject in "
                "frame."
            ),
        ),
        "continuity": (
            Continuity,
            Field(
                description="same_subject if the same main subject is in all images; "
                "subject_changes if the images show different subjects or places; "
                "no_main_subject for scenery, screens."
            ),
        ),
        "best_image": (
            int,
            Field(
                ge=1,
                le=images,
                description="Number of the image that best shows the main action (sharp, subject "
                "visible).",
            ),
        ),
    }


@cache
def answer_model(images: int | None = None) -> type[BaseModel]:
    """The answer asked for ``images`` frames (image numbers bounded by the grammar), or the
    unbounded model the stored answers are read with."""
    name = "ShotAnalysis" if images is None else f"ShotAnalysis{images}"
    model: type[BaseModel] = create_model(
        name, __config__=ConfigDict(extra="forbid"), __doc__=_MODEL_DOC, **_fields(images)
    )
    return model


# ---------------------------------------------------------------- which shots, which frames
def eligible(
    duration_s: float, metrics: dict[str, Any], motion: str, distinct_keyframes: int
) -> bool:
    """A shot worth a story: long enough, not mostly black or frozen, and something changes on
    screen (a still camera on one distinct keyframe has nothing to tell)."""
    if duration_s < MIN_SHOT_S:
        return False
    if (metrics.get("black_ratio") or 0) > MAX_BLACK_RATIO:
        return False
    if (metrics.get("frozen_ratio") or 0) > MAX_FROZEN_RATIO:
        return False
    return not (motion == "static" and distinct_keyframes <= 1)


def pick_evenly[T](items: Sequence[T], count: int = FRAMES) -> list[T]:
    """Up to ``count`` items spread evenly by index, first and last included."""
    if len(items) <= count:
        return list(items)
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


def fill_times(
    start: float, end: float, chosen: Sequence[float], count: int = FRAMES
) -> list[float]:
    """Times of the extra frames extracted when a part has fewer than ``count`` keyframes: the
    empty slots at 6/35/65/94 % of the part, each at least 1 s from a chosen keyframe."""
    duration = end - start
    low = start + max(0.15, 0.06 * duration)
    high = end - max(0.2, 0.06 * duration)
    taken = list(chosen)
    extra: list[float] = []
    for index in range(count):
        if len(taken) >= count:
            break
        t = round(low + (high - low) * index / (count - 1), 3)
        if all(abs(t - other) >= FILL_GAP_S for other in taken):
            taken.append(t)
            extra.append(t)
    return extra


def caption_line(analysis: dict[str, Any] | None) -> str | None:
    """A keyframe's description as the model sees it: caption, then its actions."""
    if not analysis or not analysis.get("caption"):
        return None
    actions = "; ".join(str(a) for a in analysis.get("actions") or [])
    text = str(analysis["caption"]) + (f" (actions: {actions})" if actions else "")
    return clean_untrusted(text)


# ---------------------------------------------------------------- answer → stored story
# What ``tidy`` keeps from an answer. A setting of the stage: an update re-tidies the stored
# answers from the LLM cache, without asking the model again. 2: only camera movements dropped;
# 3: only when the camera is the action's subject or the action is a named camera move.
TIDY_VERSION = 3
# An action that only tells how the camera moves (the optical flow says it better): its subject is
# the camera, the frame or the camera operator (« La caméra s'élève… », « Le cadre se déplace… »),
# it starts with a named camera move (« Zoom progressif sur… », « pans across the valley »), or it
# says « mouvement de caméra ». A subject doing something while the camera moves is an action
# (« Une femme danse pendant que la caméra… »), and so is a mere mention of the camera (« regarde
# la caméra ») or of the words (« a car zooms out of the car park »). A language without its own
# list keeps every action: another language's words are ordinary words there (« pan » is bread).
_CAMERA_MOVES = {
    "fr": re.compile(
        r"^(?:(?:le|la|les|un|une)\s+)?(?:cam[eé]ra(?:man)?s?|cadreur)\b"
        r"|^(?:le\s+)?cadre\s+(?:se\s|s['’])"
        r"|^(?:(?:lent|lente|léger|légère|rapide|brusque|progressif|progressive)\s+)?"
        r"(?:(?:dé)?zoom\w*|panoramiques?|travellings?)\b"
        r"|^(?:on|il|elle)\s+(?:déplace|bouge|pivote|incline|tourne|oriente)\b.*\bcam[eé]ra\b"
        r"|\bmouvements?\s+de\s+(?:la\s+)?cam[eé]ra\b",
        re.IGNORECASE,
    ),
    "en": re.compile(
        r"^(?:(?:the|a)\s+)?camera(?:man|men|person|\s+operator)?\b"
        r"|^(?:(?:slow|quick|fast|gentle|slight)\s+)?(?:zoom(?:s|ing)?|pan(?:s|ning)?"
        r"|tilt(?:s|ing)?|dolly|dollies|tracking\s+shot)"
        r"\s+(?:in|out|left|right|up|down|across|over|to|on|toward|towards)\b"
        r"|\bcamera\s+(?:movement|motion)\b",
        re.IGNORECASE,
    ),
}
_NOTHING = frozenset({"nothing", "rien", "none", "aucune", "aucun", "n/a"})


class FrameNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t_s: float  # time of the frame in the video
    what: str


class StoryText(BaseModel):
    """What is shown and used from an answer."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    main_action: str
    notes: list[FrameNote]
    possible_cut: bool  # the images seem to show different subjects: a hint, not a cut


def tidy(answer: BaseModel, frame_times: Sequence[float], language: str) -> StoryText:
    """Clean one answer: one line of text, no camera-movement action, beats on real frame times
    (deduplicated), and the continuity flag as a hedged hint."""
    data = answer.model_dump(mode="json")
    summary = clean_untrusted(str(data["summary"]))[:SUMMARY_MAX].rstrip()
    action = clean_untrusted(str(data["main_action"])).strip(" .")
    moves = _CAMERA_MOVES.get(language)
    camera_move = moves is not None and moves.search(action.strip()) is not None
    if action.lower() in _NOTHING or "_" in action or camera_move:
        action = ""
    notes: list[FrameNote] = []
    seen: set[int] = set()
    for beat in data["beats"]:
        index = min(max(int(beat["image"]), 1), len(frame_times)) - 1
        what = clean_untrusted(str(beat["what"]))
        if index in seen or not what:
            continue
        seen.add(index)
        notes.append(FrameNote(t_s=frame_times[index], what=what))
    notes.sort(key=lambda note: note.t_s)
    return StoryText(
        summary=summary,
        main_action=action,
        notes=notes,
        possible_cut=data["continuity"] == Continuity.SUBJECT_CHANGES,
    )
