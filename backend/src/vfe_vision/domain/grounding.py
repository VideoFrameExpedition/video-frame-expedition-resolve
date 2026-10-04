"""Living beings located by the vision model (grounding), and how its boxes are read.

Qwen3-VL answers ``bbox_2d = [x1, y1, x2, y2]`` in 0–1000 of the image it was sent, whatever its
size (measured on synthetic frames: under 1 % of error). Other families use other
conventions (Gemma: ``box_2d = [y1, x1, y2, x2]``): a probe measures them, and a
model whose convention is unknown or unreliable is not asked.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vfe_vision.domain.subjects import Box, Category, Detection, Source
from vfe_vision.domain.transcript import clean_untrusted
from vfe_vision.domain.vision_profile import BoxConvention, BoxField, prior_for, project

GROUNDING_SCHEMA_VERSION = 1
MAX_BEINGS = 16
LABEL_MAX_CHARS = 40

XYXY_1000 = BoxConvention.XYXY_1000
YXYX_1000 = BoxConvention.YXYX_1000


class BeingCategory(StrEnum):
    PERSON = "person"
    BODY_PART = "body_part"
    MAMMAL = "mammal"
    BIRD = "bird"
    INSECT = "insect"
    OTHER_ANIMAL = "other_animal"


class Being(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        description="One or two words naming it, in the output language; the species when "
        "you are sure."
    )
    category: BeingCategory = Field(
        description="person for any human, whole or partly visible (never mammal); body_part "
        "only for a hand, arm or feet when the rest of that person is not in the frame; mammal "
        "for animals only; insect also for spiders, caterpillars and other small creatures; "
        "other_animal for reptiles, amphibians, fish."
    )
    main: bool = Field(description="True if it is a main subject of the shot.")
    bbox_2d: list[int] = Field(
        min_length=4,
        max_length=4,
        description="Tight box [x1, y1, x2, y2] in 0-1000 relative to the image width and height.",
    )


class Grounding(BaseModel):
    """Every living being visible in one frame."""

    model_config = ConfigDict(extra="forbid")

    beings: list[Being] = Field(max_length=MAX_BEINGS)


class BeingYFirst(BaseModel):
    """``Being`` for models whose boxes are y-first (Gemma's ``box_2d``)."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description=Being.model_fields["label"].description)
    category: BeingCategory = Field(description=Being.model_fields["category"].description)
    main: bool = Field(description=Being.model_fields["main"].description)
    box_2d: list[int] = Field(
        min_length=4,
        max_length=4,
        description="Tight box [y1, x1, y2, x2] in 0-1000 relative to the image height and width.",
    )


class GroundingYFirst(BaseModel):
    """Every living being visible in one frame (y-first boxes)."""

    model_config = ConfigDict(extra="forbid")

    beings: list[BeingYFirst] = Field(max_length=MAX_BEINGS)


def grounding_schema(box_field: BoxField) -> type[Grounding] | type[GroundingYFirst]:
    return GroundingYFirst if box_field == BoxField.BOX_2D else Grounding


# Examples in the output language: English examples made the model answer "person" in French.
LABEL_EXAMPLES = {
    "fr": "personne, homme, femme, enfant, main, chat, chien, cheval, oiseau, mouette, "
    "papillon, abeille, chenille, lézard",
    "en": "person, man, woman, child, hand, cat, dog, horse, bird, seagull, butterfly, bee, "
    "caterpillar, lizard",
    "es": "persona, hombre, mujer, niño, mano, gato, perro, caballo, pájaro, gaviota, "
    "mariposa, abeja, oruga, lagarto",
    "de": "Person, Mann, Frau, Kind, Hand, Katze, Hund, Pferd, Vogel, Möwe, Schmetterling, "
    "Biene, Raupe, Eidechse",
    "it": "persona, uomo, donna, bambino, mano, gatto, cane, cavallo, uccello, gabbiano, "
    "farfalla, ape, bruco, lucertola",
}


def label_examples(language: str) -> str:
    return LABEL_EXAMPLES.get(language, LABEL_EXAMPLES["en"])


def convention_for(model_key: str, architecture: str | None = None) -> BoxConvention | None:
    """The convention known without a probe (Qwen3-VL), or None: Qwen2.5-VL answers in pixels
    of its resized input, Gemma in [y1, x1, y2, x2], and they wait for a calibration."""
    prior = prior_for(model_key, architecture)
    return prior[0] if prior else None


# ---------------------------------------------------------------- the head of one being
HEAD_SCHEMA_VERSION = 1
HEAD_CONVENTIONS = (XYXY_1000, YXYX_1000)  # the box of the being is written in the prompt


class Head(BaseModel):
    """The head of the being named in the prompt."""

    model_config = ConfigDict(extra="forbid")

    found: bool = Field(description="False when the head of that being is not visible.")
    bbox_2d: list[int] = Field(
        min_length=4,
        max_length=4,
        description="Tight box of the head [x1, y1, x2, y2] in 0-1000 relative to the image "
        "width and height; zeros when not found.",
    )


class HeadYFirst(BaseModel):
    """``Head`` for models whose boxes are y-first."""

    model_config = ConfigDict(extra="forbid")

    found: bool = Field(description=Head.model_fields["found"].description)
    box_2d: list[int] = Field(
        min_length=4,
        max_length=4,
        description="Tight box of the head [y1, x1, y2, x2] in 0-1000 relative to the image "
        "height and width; zeros when not found.",
    )


def head_schema(box_field: BoxField) -> type[Head] | type[HeadYFirst]:
    return HeadYFirst if box_field == BoxField.BOX_2D else Head


def box_for_prompt(box: Box, convention: BoxConvention) -> list[int]:
    """A 0–1 box written as the model writes its own (0–1000, x-first or y-first)."""
    x1, y1, x2, y2 = (round(v * 1000) for v in (box.x1, box.y1, box.x2, box.y2))
    return [y1, x1, y2, x2] if convention == YXYX_1000 else [x1, y1, x2, y2]


def head_box(answer: Head | HeadYFirst, convention: BoxConvention) -> Box | None:
    """The head in 0–1 of the image, or None (not found, or a degenerate box)."""
    if not answer.found:
        return None
    raw = answer.bbox_2d if isinstance(answer, Head) else answer.box_2d
    projected = project(convention, [float(v) for v in raw], 1000, 1000)
    if projected is None:
        return None
    x1, y1, x2, y2 = projected
    return Box.of((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))


def to_detections(
    answer: Grounding | GroundingYFirst,
    convention: BoxConvention,
    width: int = 1000,
    height: int = 1000,
) -> list[Detection]:
    """Boxes in 0–1 of the image (``width`` × ``height``: the image the model was sent, for
    pixel conventions); degenerate boxes and empty labels are dropped."""
    out: list[Detection] = []
    for being in answer.beings:
        raw = being.bbox_2d if isinstance(being, Being) else being.box_2d
        projected = project(convention, [float(v) for v in raw], width, height)
        if projected is None:
            continue
        x1, y1, x2, y2 = projected
        box = Box.of((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
        label = clean_untrusted(being.label)[:LABEL_MAX_CHARS].strip()
        if box is None or not label:
            continue
        out.append(
            Detection(Source.VLM, label, Category(being.category.value), box, None, being.main)
        )
    return out
