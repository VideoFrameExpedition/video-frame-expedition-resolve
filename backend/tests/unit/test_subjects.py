"""Subject positions: boxes, fusion of the three sources, VLM answers, D-FINE output."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from vfe_vision.adapters.detection.dfine import postprocess
from vfe_vision.adapters.lmstudio.schema import strict_json_schema
from vfe_vision.domain.grounding import (
    XYXY_1000,
    YXYX_1000,
    Being,
    BeingCategory,
    Grounding,
    convention_for,
    to_detections,
)
from vfe_vision.domain.subjects import (
    COCO_LIVING,
    Box,
    Category,
    Detection,
    Source,
    dedupe,
    fuse,
    localized,
)


def _box(x1: float, y1: float, x2: float, y2: float) -> Box:
    box = Box.of((x1, y1, x2, y2))
    assert box is not None
    return box


def _vlm(label: str, category: Category, box: Box, *, main: bool = False) -> Detection:
    return Detection(Source.VLM, label, category, box, None, main)


def _det(label: str, box: Box, score: float = 0.9) -> Detection:
    return Detection(Source.DETECTOR, label, COCO_LIVING[label], box, score)


def _face(box: Box, score: float = 0.9) -> Detection:
    return Detection(Source.FACES, "face", Category.FACE, box, score)


# ---------------------------------------------------------------- boxes
def test_box_is_clipped_and_degenerate_boxes_are_dropped() -> None:
    assert Box.of((-0.2, 0.1, 1.3, 0.5)) == Box(0.0, 0.1, 1.0, 0.5)
    assert Box.of((0.5, 0.5, 0.5, 0.9)) is None  # no width
    assert Box.of((0.2, 0.2, 0.201, 0.9)) is None  # thinner than the noise floor
    assert Box.of((0.1, 0.2, 0.3)) is None


def test_iou() -> None:
    a = _box(0.0, 0.0, 0.5, 0.5)
    assert a.iou(a) == pytest.approx(1.0)
    assert a.iou(_box(0.5, 0.5, 1.0, 1.0)) == 0.0
    assert a.iou(_box(0.25, 0.0, 0.75, 0.5)) == pytest.approx(1 / 3)


@given(st.lists(st.floats(0, 1, allow_nan=False), min_size=4, max_size=4))
def test_valid_boxes_stay_inside_the_image(values: list[float]) -> None:
    box = Box.of(values)
    if box is not None:
        assert 0.0 <= box.x1 < box.x2 <= 1.0
        assert 0.0 <= box.y1 < box.y2 <= 1.0
        assert box.iou(box) == pytest.approx(1.0)


# ---------------------------------------------------------------- fusion
def test_matched_boxes_keep_the_vlm_name_and_the_detector_box() -> None:
    subjects = fuse(
        [
            _vlm("chat tigré", Category.MAMMAL, _box(0.1, 0.1, 0.5, 0.6), main=True),
            _det("cat", _box(0.12, 0.1, 0.5, 0.62), 0.93),
        ]
    )
    assert len(subjects) == 1
    cat = subjects[0]
    assert (cat.label, cat.category, cat.main, cat.score) == (
        "chat tigré",
        Category.MAMMAL,
        True,
        0.93,
    )
    assert cat.box == _box(0.12, 0.1, 0.5, 0.62)
    assert cat.sources == [Source.VLM, Source.DETECTOR]


def test_animal_names_may_differ_between_sources() -> None:
    """COCO has no fox: D-FINE says "dog", the vision model "renard" — one animal."""
    subjects = fuse(
        [
            _vlm("renard", Category.MAMMAL, _box(0.2, 0.2, 0.6, 0.7)),
            _det("dog", _box(0.21, 0.2, 0.6, 0.7)),
        ]
    )
    assert [s.label for s in subjects] == ["renard"]


def test_a_person_is_not_merged_with_an_animal() -> None:
    """A dog in someone's arms: the boxes overlap, but not enough to be the same being."""
    subjects = fuse(
        [
            _vlm("chien", Category.MAMMAL, _box(0.2, 0.3, 0.55, 0.7)),
            _det("person", _box(0.15, 0.1, 0.6, 0.9)),
        ]
    )
    assert [(s.label, s.category, s.sources) for s in subjects] == [
        ("chien", Category.MAMMAL, [Source.VLM])
    ]


def test_a_hand_seen_as_a_person_is_one_being() -> None:
    """D-FINE calls a hand in the frame a "person": the VLM's "main" wins, one box."""
    subjects = fuse(
        [
            _vlm("main", Category.BODY_PART, _box(0.3, 0.5, 0.6, 1.0)),
            _det("person", _box(0.31, 0.5, 0.6, 1.0), 0.7),
        ]
    )
    assert [(s.label, s.category, len(s.sources)) for s in subjects] == [
        ("main", Category.BODY_PART, 2)
    ]


def test_a_confident_detector_corrects_the_animal_name() -> None:
    """The VLM once called a black cat a bird; D-FINE at 0.9 knows better."""
    [cat] = fuse(
        [
            _vlm("oiseau", Category.BIRD, _box(0.2, 0.2, 0.6, 0.7), main=True),
            _det("cat", _box(0.2, 0.21, 0.6, 0.7), 0.9),
        ]
    )
    assert (cat.label, cat.category, cat.main) == ("chat", Category.MAMMAL, True)
    [bee] = fuse(
        [
            _vlm("abeille", Category.INSECT, _box(0.2, 0.2, 0.3, 0.3)),
            _det("bird", _box(0.2, 0.2, 0.3, 0.3), 0.6),  # D-FINE's "bird" on a flying bee
        ]
    )
    assert (bee.label, bee.category) == ("abeille", Category.INSECT)


def test_detector_boxes_the_vlm_did_not_confirm_are_dropped() -> None:
    """A goddess statue at 0.81 "person": when the VLM looked and saw no one there, it goes."""
    subjects = fuse(
        [
            _vlm("papillon", Category.INSECT, _box(0.4, 0.4, 0.5, 0.5), main=True),
            _det("person", _box(0.0, 0.0, 0.3, 0.9), 0.81),
            _face(_box(0.1, 0.1, 0.2, 0.2)),  # the statue's face
        ]
    )
    assert [s.label for s in subjects] == ["papillon"]
    assert fuse([_det("person", _box(0.0, 0.0, 0.3, 0.9))], vlm_scanned=True) == []


def test_the_detector_completes_a_crowd() -> None:
    """The VLM stops listing people early: once it saw two, a "person" it missed is kept."""
    listed = [
        _vlm("personne", Category.PERSON, _box(0.1 * i, 0.1, 0.1 * i + 0.08, 0.5), main=True)
        for i in range(2)
    ]
    extra = _det("person", _box(0.3, 0.6, 0.4, 0.95), 0.55)
    weak = _det("person", _box(0.5, 0.6, 0.6, 0.95), 0.45)  # stored at 0.4, too weak alone
    dog = _det("dog", _box(0.7, 0.6, 0.9, 0.95), 0.9)  # not confirmed by the VLM: dropped
    subjects = fuse([*listed, extra, weak, dog])
    assert len(subjects) == 3
    assert subjects[-1].sources == [Source.DETECTOR]
    # One person listed is not a crowd: the lone detector box goes (a statue, a background).
    assert len(fuse([listed[0], extra])) == 1


def test_without_the_vlm_the_confident_detector_boxes_stay() -> None:
    subjects = fuse(
        [
            _det("person", _box(0.0, 0.0, 0.3, 0.9), 0.9),
            _det("dog", _box(0.4, 0.5, 0.6, 0.9), 0.6),
            _det("cat", _box(0.7, 0.7, 0.8, 0.8), 0.45),  # stored at 0.4, too weak alone
        ]
    )
    assert [s.label for s in subjects] == ["personne", "chien"]


def test_a_person_filed_under_mammals_is_one_person() -> None:
    """The VLM's category is noisy for people: its "mammal" on a detector person is a person."""
    [person] = fuse(
        [
            _vlm("homme", Category.MAMMAL, _box(0.2, 0.1, 0.5, 0.9), main=True),
            _det("person", _box(0.21, 0.1, 0.5, 0.9), 0.85),
        ]
    )
    assert (person.label, person.category, len(person.sources)) == ("homme", Category.PERSON, 2)
    # A same-kind pair wins over the cross-category one.
    subjects = fuse(
        [
            _vlm("chien", Category.MAMMAL, _box(0.2, 0.5, 0.45, 0.9)),
            _vlm("femme", Category.PERSON, _box(0.2, 0.1, 0.5, 0.9)),
            _det("person", _box(0.2, 0.1, 0.5, 0.9), 0.9),
        ]
    )
    assert {(s.label, s.category) for s in subjects} == {
        ("femme", Category.PERSON),
        ("chien", Category.MAMMAL),
    }


def test_faces_larger_than_their_person_or_half_the_image_are_dropped() -> None:
    hand = _vlm("main", Category.PERSON, _box(0.3, 0.0, 0.9, 0.35))
    huge = _face(_box(0.1, 0.0, 0.86, 0.4), 0.75)  # a hand over a pan read as a face
    small = _face(_box(0.5, 0.1, 0.6, 0.2))
    person = _vlm("femme", Category.PERSON, _box(0.45, 0.05, 0.65, 0.3))
    subjects = fuse([hand, person, huge, small])
    assert [s.face for s in subjects if s.face] == [_box(0.5, 0.1, 0.6, 0.2)]
    assert fuse([_face(_box(0.0, 0.0, 0.7, 0.6))], language="en") == []


def test_main_subject_in_a_crowd_and_without_the_vlm() -> None:
    """In crowds the VLM calls everyone main: only the large ones stay main."""
    big = _vlm("guide", Category.PERSON, _box(0.3, 0.1, 0.7, 0.95), main=True)
    small = _vlm("passant", Category.PERSON, _box(0.0, 0.5, 0.05, 0.6), main=True)
    other = _vlm("passante", Category.PERSON, _box(0.9, 0.5, 0.95, 0.6), main=True)
    subjects = fuse([big, small, other])
    assert [(s.label, s.main) for s in subjects] == [
        ("guide", True), ("passant", False), ("passante", False)
    ]  # fmt: skip
    # Two main beings are not a crowd: the caterpillar on a man's hand stays main.
    man = _vlm("homme", Category.PERSON, _box(0.2, 0.0, 0.8, 1.0), main=True)
    caterpillar = _vlm("chenille", Category.INSECT, _box(0.45, 0.45, 0.55, 0.52), main=True)
    assert all(s.main for s in fuse([man, caterpillar]))
    # No VLM: the most confident, large and central being.
    alone = fuse(
        [
            _det("person", _box(0.0, 0.0, 0.2, 0.5), 0.95),  # in a corner
            _det("horse", _box(0.3, 0.3, 0.7, 0.8), 0.8),
        ]
    )
    assert [(s.label, s.main) for s in alone] == [("cheval", True), ("personne", False)]
    # The VLM looked but flagged no one (two swimmers): the same fallback applies.
    swimmers = fuse(
        [
            _vlm("nageur", Category.PERSON, _box(0.1, 0.5, 0.2, 0.6)),
            _vlm("nageuse", Category.PERSON, _box(0.45, 0.4, 0.6, 0.6)),
        ]
    )
    assert [(s.label, s.main) for s in swimmers] == [("nageuse", True), ("nageur", False)]


def test_each_detector_box_matches_one_vlm_box_at_most() -> None:
    person = _box(0.1, 0.1, 0.5, 0.9)
    subjects = fuse(
        [
            _vlm("femme", Category.PERSON, person),
            _vlm("femme", Category.PERSON, _box(0.12, 0.1, 0.48, 0.7)),  # IoU < 0.8: not a repeat
            _det("person", person),
        ]
    )
    assert sorted(len(s.sources) for s in subjects) == [1, 2]


def test_vlm_repeats_are_dropped() -> None:
    box = _box(0.1, 0.1, 0.5, 0.9)
    subjects = fuse(
        [_vlm("homme", Category.PERSON, box), _vlm("homme", Category.PERSON, box, main=True)]
    )
    assert len(subjects) == 1
    assert subjects[0].main  # the repeat's main flag is kept


def test_faces_go_to_the_smallest_person_around_them() -> None:
    eyes = ((0.47, 0.16), (0.53, 0.16), (0.5, 0.19), (0.48, 0.22), (0.52, 0.22))
    subjects = fuse(
        [
            _det("person", _box(0.0, 0.0, 1.0, 1.0)),  # someone large in the foreground
            _det("person", _box(0.4, 0.1, 0.6, 0.8)),
            Detection(
                Source.FACES, "face", Category.FACE, _box(0.45, 0.12, 0.55, 0.25), 0.9, points=eyes
            ),
            _face(_box(0.8, 0.8, 0.9, 0.9), 0.8),  # only inside the large person
        ]
    )
    small = next(s for s in subjects if s.box == _box(0.4, 0.1, 0.6, 0.8))
    large = next(s for s in subjects if s.box == _box(0.0, 0.0, 1.0, 1.0))
    assert small.face == _box(0.45, 0.12, 0.55, 0.25)
    assert small.face_points == eyes
    assert large.face == _box(0.8, 0.8, 0.9, 0.9)
    assert Source.FACES in small.sources


def test_a_face_without_a_person_box_is_its_own_subject() -> None:
    subjects = fuse([_face(_box(0.4, 0.4, 0.5, 0.55))], language="en")
    assert [(s.label, s.category) for s in subjects] == [("face", Category.FACE)]
    assert subjects[0].face == subjects[0].box


def test_detector_labels_follow_the_interface_language() -> None:
    assert localized("horse", "fr") == "cheval"
    assert localized("horse", "en") == "horse"
    assert localized("unknown", "fr") == "unknown"


def test_dedupe_keeps_the_first_of_overlapping_boxes() -> None:
    a = _det("person", _box(0.1, 0.1, 0.5, 0.9), 0.9)
    b = _det("person", _box(0.11, 0.1, 0.5, 0.9), 0.7)
    c = _det("cat", _box(0.11, 0.1, 0.5, 0.9), 0.6)  # other kind of being: kept
    assert dedupe([a, b, c], threshold=0.7) == [a, c]


# ---------------------------------------------------------------- vision model answers
def _being(label: str, category: BeingCategory, box: list[int], *, main: bool = False) -> Being:
    return Being(label=label, category=category, main=main, bbox_2d=box)


def test_qwen_boxes_are_read_in_thousandths() -> None:
    answer = Grounding(
        beings=[_being("abeille", BeingCategory.INSECT, [100, 200, 300, 500], main=True)]
    )
    [bee] = to_detections(answer, XYXY_1000)
    assert bee.box == _box(0.1, 0.2, 0.3, 0.5)
    assert (bee.label, bee.category, bee.main, bee.source) == (
        "abeille", Category.INSECT, True, Source.VLM,
    )  # fmt: skip


def test_yxyx_convention_and_swapped_corners() -> None:
    answer = Grounding(beings=[_being("oiseau", BeingCategory.BIRD, [200, 100, 500, 300])])
    assert to_detections(answer, YXYX_1000)[0].box == _box(0.1, 0.2, 0.3, 0.5)
    swapped = Grounding(beings=[_being("oiseau", BeingCategory.BIRD, [300, 500, 100, 200])])
    assert to_detections(swapped, XYXY_1000)[0].box == _box(0.1, 0.2, 0.3, 0.5)


def test_bad_boxes_and_labels_are_dropped() -> None:
    invisible = f"  {chr(0x202E)} "  # nothing left once cleaned
    injected = "chien\n<untrusted>x</untrusted>" + "!" * 80
    answer = Grounding(
        beings=[
            _being("chat", BeingCategory.MAMMAL, [100, 100, 100, 400]),  # no width
            _being(invisible, BeingCategory.MAMMAL, [100, 100, 300, 400]),
            _being(injected, BeingCategory.MAMMAL, [0, 0, 1200, 1000]),
        ]
    )
    [dog] = to_detections(answer, XYXY_1000)
    assert dog.box == _box(0.0, 0.0, 1.0, 1.0)
    assert "\n" not in dog.label
    assert "untrusted" not in dog.label
    assert len(dog.label) <= 40


def test_only_models_with_a_known_convention_are_asked() -> None:
    assert convention_for("qwen/qwen3-vl-8b") == XYXY_1000
    assert convention_for("qwen/qwen3-vl-4b", "qwen3vl") == XYXY_1000
    assert convention_for("Qwen3-VL-8B-Instruct-GGUF") == XYXY_1000
    assert convention_for("qwen/qwen2.5-vl-7b", "qwen2vl") is None  # pixels, not 0-1000
    assert convention_for("google/gemma-4-12b") is None
    assert convention_for("qwen/qwen3-8b") is None  # a text model


def test_grounding_schema_is_strict_with_four_coordinates() -> None:
    schema = strict_json_schema(Grounding)
    being = schema["properties"]["beings"]["items"]
    assert being["additionalProperties"] is False
    assert being["properties"]["bbox_2d"]["minItems"] == 4
    assert being["properties"]["bbox_2d"]["maxItems"] == 4
    assert set(being["properties"]["category"]["enum"]) == {c.value for c in BeingCategory}


# ---------------------------------------------------------------- D-FINE output
LABELS = ["person", "bicycle", "car", "cat", "dog"]


def _logit(p: float) -> float:
    return float(np.log(p / (1 - p)))


def test_dfine_keeps_the_best_living_class_per_query() -> None:
    logits = np.full((1, 3, len(LABELS)), _logit(0.01), np.float32)
    logits[0, 0, 0] = _logit(0.9)  # a person
    logits[0, 1, 2] = _logit(0.95)  # a car: not a living being
    logits[0, 2, 3] = _logit(0.6)  # a cat…
    logits[0, 2, 4] = _logit(0.55)  # …or a dog: one box, the best class
    boxes = np.array(
        [[[0.5, 0.5, 0.2, 0.4], [0.2, 0.2, 0.1, 0.1], [0.9, 0.9, 0.4, 0.4]]], np.float32
    )
    found = postprocess(logits, boxes, LABELS, keep={"person", "cat", "dog"}, min_score=0.5)
    assert [(b.label, round(b.score, 2)) for b in found] == [("person", 0.9), ("cat", 0.6)]
    assert found[0].box == pytest.approx((0.4, 0.3, 0.6, 0.7))
    assert found[1].box == pytest.approx((0.7, 0.7, 1.0, 1.0))  # clipped to the image


def test_dfine_without_kept_classes_finds_nothing() -> None:
    logits = np.zeros((1, 2, len(LABELS)), np.float32)
    boxes = np.zeros((1, 2, 4), np.float32)
    assert postprocess(logits, boxes, LABELS, keep={"giraffe"}, min_score=0.1) == []
