"""Structured output of the vision model for one keyframe.

Filterable fields are enums (the JSON schema sent to LM Studio constrains them through grammar
sampling); only captions and descriptions are free text.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

FRAME_ANALYSIS_SCHEMA_VERSION = 2

_T = TypeVar("_T")


class ShotType(StrEnum):
    EXTREME_WIDE = "extreme_wide"
    WIDE = "wide"
    MEDIUM = "medium"
    CLOSE_UP = "close_up"
    EXTREME_CLOSE_UP = "extreme_close_up"
    MACRO = "macro"
    UNKNOWN = "unknown"


class CameraAngle(StrEnum):
    EYE_LEVEL = "eye_level"
    HIGH_ANGLE = "high_angle"
    LOW_ANGLE = "low_angle"
    TOP_DOWN = "top_down"
    AERIAL = "aerial"
    POV = "pov"
    UNKNOWN = "unknown"


class Setting(StrEnum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class TimeOfDay(StrEnum):
    NOT_VISIBLE = "not_visible"  # first: the safe default when nothing shows the time
    DAY = "day"
    GOLDEN_HOUR = "golden_hour"
    BLUE_HOUR = "blue_hour"
    NIGHT = "night"


class WeatherVisual(StrEnum):
    SKY_NOT_VISIBLE = "sky_not_visible"  # first: most close-ups do not show the weather
    CLEAR = "clear"
    PARTLY_CLOUDY = "partly_cloudy"
    OVERCAST = "overcast"
    RAIN = "rain"
    SNOW = "snow"
    FOG = "fog"


class Lighting(StrEnum):
    NATURAL_HARD = "natural_hard"
    NATURAL_SOFT = "natural_soft"
    ARTIFICIAL = "artificial"
    MIXED = "mixed"
    BACKLIT = "backlit"
    LOW_LIGHT = "low_light"


class QualityIssue(StrEnum):
    BLUR = "blur"
    MOTION_BLUR = "motion_blur"
    OVEREXPOSED = "overexposed"
    UNDEREXPOSED = "underexposed"
    NOISE = "noise"
    TILTED_HORIZON = "tilted_horizon"
    OBSTRUCTION = "obstruction"
    OUT_OF_FOCUS_SUBJECT = "out_of_focus_subject"


class EditingValue(StrEnum):
    ESTABLISHING = "establishing"
    B_ROLL = "b_roll"
    DETAIL = "detail"
    ACTION = "action"
    INTERVIEW = "interview"
    TRANSITION = "transition"
    HERO = "hero"


class Subject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        description="Short name in the output language (species, object, person role)."
    )
    description: str = Field(description="One short sentence about its appearance or action.")
    is_main: bool = Field(description="True for the main subject of the frame.")


class FrameAnalysis(BaseModel):
    """What the vision model sees in one frame."""

    model_config = ConfigDict(extra="forbid")

    caption: str = Field(description="One short sentence (at most 20 words).")
    description: str = Field(description="Detailed description in 2 to 4 sentences.")
    shot_type: ShotType = Field(
        description="Framing of the shot; macro for extreme close-ups of small subjects."
    )
    camera_angle: CameraAngle
    setting: Setting = Field(
        description="outdoor when plants, sky, sunlight or landscape dominate; indoor for rooms."
    )
    place_type: str = Field(description="Kind of place in 1-3 words, e.g. garden, kitchen, beach.")
    time_of_day: TimeOfDay = Field(
        description="Only from visible cues (sky, sun, light); "
        "not_visible when it cannot be judged."
    )
    weather: WeatherVisual = Field(
        description="sky_not_visible unless the sky or falling precipitation is clearly "
        "visible. Bokeh, blurred highlights or light spots are NOT snow or rain."
    )
    lighting: Lighting
    subjects: list[Subject] = Field(
        max_length=6, description="Main things visible, main subject first."
    )
    people_count: int = Field(ge=0, description="Number of visible people (0 if none).")
    actions: list[str] = Field(max_length=5, description="Visible actions, short phrases.")
    mood: str = Field(description="Mood or atmosphere in a few words.")
    dominant_colors: list[str] = Field(max_length=5)
    visible_text: str = Field(
        description="Text physically written in the image (signs, screens, labels, captions). "
        "Never the file name. Empty string if none."
    )
    quality_issues: list[QualityIssue] = Field(
        max_length=4, description="Only clear technical defects; empty list if none."
    )
    editing_value: list[EditingValue] = Field(
        max_length=3,
        description="Editing uses: establishing = sets the place; b_roll = illustrative footage; "
        "detail = close detail; action = something happening; interview = a person speaking to "
        "the camera; transition = usable between scenes; hero = standout shot. No duplicates.",
    )
    tags: list[str] = Field(
        max_length=10,
        description="Search keywords in the output language: lowercase words, no underscores.",
    )

    @field_validator("actions", "dominant_colors", "tags", "quality_issues", "editing_value")
    @classmethod
    def _unique(cls, values: list[_T]) -> list[_T]:
        seen: list[_T] = []
        for value in values:
            if value not in seen:
                seen.append(value)
        return seen
