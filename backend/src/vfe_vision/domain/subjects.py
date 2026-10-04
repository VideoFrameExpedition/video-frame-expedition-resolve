"""Where the living subjects are in a keyframe: boxes from three sources, fused.

- ``detector``: D-FINE (COCO) on the CPU. Fast and tight boxes for people and common animals,
  complete in crowds, but blind to insects and most small creatures.
- ``faces``: YuNet on the CPU. Face boxes (never who the person is), used to refine a person.
- ``vlm``: the vision model already loaded in LM Studio, asked for every living being with a
  box (Qwen3-VL ``bbox_2d`` in 0–1000). Sees insects and names things precisely, and says which
  one is the main subject; slower, and it stops listing after about twelve people.

Boxes are ``[x1, y1, x2, y2]`` normalised to 0–1 in the *displayed* image (rotation applied),
like the OCR boxes: multiplied by the displayed width and height they give pixels, whatever the
size of the keyframe file. Positions only: nothing here identifies anyone.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

SUBJECTS_FUSION_VERSION = 3

MATCH_IOU = 0.5  # a detector box and a VLM box are the same being (they agree at median 0.95)
CROSS_MATCH_IOU = 0.7  # a VLM "mammal" and a detector "person" must agree more closely
DUPLICATE_IOU = 0.8  # the VLM listed the same being twice
MIN_SIDE = 0.004  # boxes thinner than this (share of the image side) are noise
DETECTOR_ALONE = 0.5  # a detector box that no other source confirms (stored from 0.4)
DETECTOR_TRUSTED = 0.8  # on a matched animal, the detector's class wins over the VLM's name
PEOPLE_LISTED = 2  # the VLM saw people: a detector "person" it missed is one more of them
MAX_FACE_SHARE = 0.5  # a "face" wider than half the image is a hand or a background
MAIN_SHARE = 0.4  # in crowds the VLM calls everyone "main": keep the large ones only
MAIN_CROWD = 3  # from this many "main" beings on one frame, it is a crowd (a hand holding a
# butterfly, a man and a caterpillar: both stay main)


class Category(StrEnum):
    PERSON = "person"
    BODY_PART = "body_part"  # a hand or feet in the frame, not a whole person
    MAMMAL = "mammal"
    BIRD = "bird"
    INSECT = "insect"  # insects, spiders, caterpillars and other small creatures
    OTHER_ANIMAL = "other_animal"  # reptiles, fish, amphibians, molluscs…
    FACE = "face"  # a face with no person box around it


class Source(StrEnum):
    DETECTOR = "detector"
    FACES = "faces"
    VLM = "vlm"


ANIMALS = frozenset({Category.MAMMAL, Category.BIRD, Category.INSECT, Category.OTHER_ANIMAL})

# COCO classes that are living beings, with their category.
COCO_LIVING: dict[str, Category] = {
    "person": Category.PERSON,
    "bird": Category.BIRD,
    **dict.fromkeys(
        ("cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"),
        Category.MAMMAL,
    ),
}

# Detector labels are stored in English (COCO); shown in the interface language.
_LABELS_FR = {
    "person": "personne", "bird": "oiseau", "cat": "chat", "dog": "chien", "horse": "cheval",
    "sheep": "mouton", "cow": "vache", "elephant": "éléphant", "bear": "ours", "zebra": "zèbre",
    "giraffe": "girafe", "face": "visage",
}  # fmt: skip


def localized(label: str, language: str) -> str:
    """Display name of a detector label (VLM labels are already in the output language)."""
    if language == "fr":
        return _LABELS_FR.get(label, label)
    return label


@dataclass(frozen=True, slots=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    @classmethod
    def of(cls, values: Sequence[float]) -> Box | None:
        """A box clipped to the image, or None when it is empty or degenerate."""
        if len(values) != 4:
            return None
        x1, y1, x2, y2 = (min(1.0, max(0.0, float(v))) for v in values)
        if x2 - x1 < MIN_SIDE or y2 - y1 < MIN_SIDE:
            return None
        return cls(x1, y1, x2, y2)

    @property
    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    def iou(self, other: Box) -> float:
        w = min(self.x2, other.x2) - max(self.x1, other.x1)
        h = min(self.y2, other.y2) - max(self.y1, other.y1)
        if w <= 0 or h <= 0:
            return 0.0
        inter = w * h
        return inter / (self.area + other.area - inter)

    def contains(self, point: tuple[float, float]) -> bool:
        x, y = point
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    def rounded(self, digits: int = 4) -> list[float]:
        return [round(v, digits) for v in (self.x1, self.y1, self.x2, self.y2)]


@dataclass(frozen=True, slots=True)
class Detection:
    """One box from one source, as stored."""

    source: Source
    label: str
    category: Category
    box: Box
    score: float | None = None  # detector and face confidence; the VLM gives none
    main: bool = False  # the VLM's "main subject of the shot"
    points: tuple[tuple[float, float], ...] = ()  # face landmarks: eyes, nose, mouth corners


@dataclass(slots=True)
class Subject:
    """A living being in a keyframe after fusion."""

    label: str
    category: Category
    box: Box
    score: float | None
    main: bool
    sources: list[Source]
    face: Box | None = None
    face_points: tuple[tuple[float, float], ...] = ()


def _compatible(a: Category, b: Category) -> bool:
    """Same kind of being: an animal named differently by two sources is still one animal, and
    the detector calls a hand in the frame a "person"."""
    people = {Category.PERSON, Category.BODY_PART}
    return a == b or (a in ANIMALS and b in ANIMALS) or (a in people and b in people)


def _mammal_on_person(v: Detection, d: Detection) -> bool:
    """A person the VLM filed under mammals (its category is noisy for people): only when the
    detector is sure of a person on nearly the same box, so a dog in someone's arms stays a dog."""
    return (
        v.category == Category.MAMMAL
        and d.category == Category.PERSON
        and (d.score or 0.0) >= DETECTOR_TRUSTED
        and v.box.iou(d.box) >= CROSS_MATCH_IOU
    )


def dedupe(detections: Iterable[Detection], threshold: float = DUPLICATE_IOU) -> list[Detection]:
    """Drop boxes that repeat an earlier one of a compatible category (first one wins, and
    keeps the main flag of its repeats)."""
    kept: list[Detection] = []
    for d in detections:
        for i, k in enumerate(kept):
            if _compatible(d.category, k.category) and d.box.iou(k.box) >= threshold:
                if d.main and not k.main:
                    kept[i] = replace(k, main=True)
                break
        else:
            kept.append(d)
    return kept


def _pairs(vlm: Sequence[Detection], detector: Sequence[Detection]) -> dict[int, int]:
    """VLM index → detector index, each box used once: pairs of the same kind first, then a
    VLM "mammal" on a detector "person"; best overlaps first within each pass."""
    matched: dict[int, int] = {}
    used: set[int] = set()
    passes = (
        lambda v, d: _compatible(v.category, d.category),
        _mammal_on_person,
    )
    for accept in passes:
        candidates = sorted(
            (
                (v.box.iou(d.box), vi, di)
                for vi, v in enumerate(vlm)
                for di, d in enumerate(detector)
                if vi not in matched and di not in used and accept(v, d)
            ),
            reverse=True,
        )
        for value, vi, di in candidates:
            if value < MATCH_IOU:
                break
            if vi not in matched and di not in used:
                matched[vi] = di
                used.add(di)
    return matched


def _merged(v: Detection, d: Detection, language: str) -> Subject:
    """The VLM's name and main flag on the detector's tighter box — unless the detector is sure
    of another animal (the VLM once called a black cat a bird)."""
    label, category = v.label, v.category
    if category == Category.MAMMAL and d.category == Category.PERSON:
        category = Category.PERSON  # a person the VLM filed under mammals
    elif (
        category != d.category
        and category in ANIMALS
        and d.category in ANIMALS
        and (d.score or 0.0) >= DETECTOR_TRUSTED
    ):
        label, category = localized(d.label, language), d.category
    return Subject(label, category, d.box, d.score, v.main, [Source.VLM, Source.DETECTOR])


def _centrality(box: Box) -> float:
    x, y = box.center
    return max(0.0, 1.0 - math.hypot(x - 0.5, y - 0.5) / math.hypot(0.5, 0.5))


def _choose_main(subjects: list[Subject]) -> None:
    """The VLM's flags, minus the small ones of a crowd. When nobody is flagged (no VLM, or it
    flagged no one): the most confident, large and central being."""
    flagged = [s for s in subjects if s.main]
    if flagged:
        if len(flagged) >= MAIN_CROWD:
            largest = max(s.box.area for s in flagged)
            for s in flagged:
                s.main = s.box.area >= MAIN_SHARE * largest
        return
    candidates = [s for s in subjects if s.category != Category.FACE] or subjects
    if candidates:
        best = max(candidates, key=lambda s: (s.score or 0.5) * s.box.area * _centrality(s.box))
        best.main = True


def fuse(
    detections: Sequence[Detection], language: str = "fr", *, vlm_scanned: bool | None = None
) -> list[Subject]:
    """Merge the sources of one keyframe into subjects, main subject first then by size.

    ``vlm_scanned`` says the vision model looked at this keyframe (even if it saw nobody);
    by default, when it gave at least one box. Rules measured on 61 real keyframes:

    - a VLM box matched by a detector box (compatible kind, IoU ≥ 0.5) keeps the VLM's name and
      main flag, with the detector's box and score;
    - the VLM's other boxes are kept: insects, small or blurred animals the detector misses;
    - the detector's other boxes (score ≥ 0.5) are kept when the VLM did not look; when it did,
      only a "person" among at least two people it listed (crowds): the others were a statue,
      lavender, a dark background;
    - a face goes to the smallest person box around its centre that is larger than the face; a
      face alone is kept only when the VLM did not look (the false faces were a statue's).
    """
    vlm = dedupe(d for d in detections if d.source == Source.VLM)
    scanned = bool(vlm) if vlm_scanned is None else vlm_scanned
    detector = [d for d in detections if d.source == Source.DETECTOR]
    faces = sorted(
        (d for d in detections if d.source == Source.FACES), key=lambda d: -(d.score or 0.0)
    )
    matched = _pairs(vlm, detector)
    used = set(matched.values())

    subjects: list[Subject] = [
        _merged(v, detector[matched[vi]], language)
        if vi in matched
        else Subject(v.label, v.category, v.box, None, v.main, [Source.VLM])
        for vi, v in enumerate(vlm)
    ]
    people = sum(1 for v in vlm if v.category == Category.PERSON)
    for di, d in enumerate(detector):
        if di in used or (d.score or 0.0) < DETECTOR_ALONE:
            continue
        if scanned and not (d.category == Category.PERSON and people >= PEOPLE_LISTED):
            continue
        subjects.append(
            Subject(
                localized(d.label, language), d.category, d.box, d.score,
                main=False, sources=[Source.DETECTOR],
            )
        )  # fmt: skip

    for face in faces:
        if face.box.x2 - face.box.x1 > MAX_FACE_SHARE:
            continue
        holders = [
            s for s in subjects
            if s.category == Category.PERSON and s.face is None
            and s.box.contains(face.box.center) and s.box.area > face.box.area
        ]  # fmt: skip
        if holders:
            holder = min(holders, key=lambda s: s.box.area)
            holder.face, holder.face_points = face.box, face.points
            holder.sources.append(Source.FACES)
        elif not scanned:
            subjects.append(
                Subject(
                    localized("face", language), Category.FACE, face.box, face.score,
                    main=False, sources=[Source.FACES], face=face.box, face_points=face.points,
                )
            )  # fmt: skip

    _choose_main(subjects)
    subjects.sort(key=lambda s: (not s.main, -s.box.area))
    return subjects
