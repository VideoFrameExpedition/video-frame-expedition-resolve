"""PP-OCRv6 engine: geometry and decoding helpers, the pipeline on fake sessions, real models.

The helpers and the fake-session pipeline need no model and run by default. The tests marked
``models`` load the real ONNX files from ``VFE_OCR_MODEL_DIR`` (default:
``<data folder>/models/ocr/pp-ocrv6-small``, filled by ``vfe models ocr``) and are
skipped when a file is missing.
"""

from __future__ import annotations

import itertools
import math
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import given
from hypothesis import strategies as st

from vfe_vision.adapters.ocr.ppocr import (
    CPU_PROVIDER,
    DET_FILE,
    DICT_FILE,
    REC_FILE,
    OcrLine,
    PpOcr,
    crop_quad,
    ctc_greedy,
    db_boxes,
    det_input_size,
    order_quad,
    parse_character_dict,
    reading_order,
    rec_blob,
    unclip_distance,
    unclip_rect,
)
from vfe_vision.core.config import default_data_dir
from vfe_vision.domain.ocr import filter_line, fold, normalized_box

FRENCH = "àâäçéèêëîïôöùûüÿœæÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒÆ«»’€"


def _screen_area(quad: npt.NDArray[np.float32]) -> float:
    """Shoelace area with y pointing down: positive for a clockwise quad on screen."""
    x, y = quad[:, 0].astype(np.float64), quad[:, 1].astype(np.float64)
    return float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) / 2)


# ------------------------------------------------------------------ detection geometry
class TestDetectionInput:
    @pytest.mark.parametrize(
        ("height", "width", "expected"),
        [
            (720, 1280, (736, 1312)),  # stored keyframes
            (1280, 720, (1312, 736)),
            (576, 1024, (736, 1312)),  # upscaled: short tokens survive
            (1080, 1920, (1088, 1920)),  # never downscaled below 736
            (10, 10, (736, 736)),
            (720, 8000, (352, 4000)),  # the longer side is capped
        ],
    )
    def test_sizes(self, height: int, width: int, expected: tuple[int, int]) -> None:
        assert det_input_size(height, width) == expected

    @given(st.integers(1, 5000), st.integers(1, 5000))
    def test_multiples_of_32_within_the_model_limits(self, height: int, width: int) -> None:
        in_height, in_width = det_input_size(height, width)
        for size in (in_height, in_width):
            assert size % 32 == 0
            assert 32 <= size <= 4000


class TestUnclip:
    @given(
        st.floats(3, 400),
        st.floats(3, 60),
        st.floats(-90, 90),
        st.floats(1.0, 2.0),
    )
    def test_distance_is_area_times_ratio_over_perimeter(
        self, width: float, height: float, angle: float, ratio: float
    ) -> None:
        corners = cv2.boxPoints(((500.0, 300.0), (width, height), angle)).astype(np.float64)
        area = abs(_screen_area(corners.astype(np.float32)))
        perimeter = float(np.sum(np.linalg.norm(corners - np.roll(corners, 1, axis=0), axis=1)))
        assert unclip_distance(width, height, ratio) == pytest.approx(
            area * ratio / perimeter, rel=1e-3
        )

    def test_closed_form_equals_the_round_offset_polygon(self) -> None:
        """pyclipper's round-join offset is the rectangle's Minkowski sum with a disc; its
        minimum-area rectangle must be the closed-form grown rectangle."""
        rng = np.random.default_rng(5)
        circle = np.linspace(0, 2 * math.pi, 1440, endpoint=False)
        for _ in range(300):
            rect = (
                (float(rng.uniform(50, 1000)), float(rng.uniform(50, 700))),
                (float(rng.uniform(4, 400)), float(rng.uniform(3, 60))),
                float(rng.uniform(-90, 90)),
            )
            d = unclip_distance(*rect[1], 1.4)
            corners = cv2.boxPoints(rect).astype(np.float64)
            disc = np.stack([np.cos(circle), np.sin(circle)], axis=1) * d
            offset = (corners[:, None, :] + disc[None, :, :]).reshape(-1, 2).astype(np.float32)
            reference = order_quad(cv2.boxPoints(cv2.minAreaRect(offset)))
            ours = order_quad(cv2.boxPoints(unclip_rect(rect, 1.4)))
            assert np.abs(reference - ours).max() < 0.05

    def test_keeps_centre_and_angle(self) -> None:
        center, size, angle = unclip_rect(((10.0, 20.0), (100.0, 20.0), 12.0), 1.4)
        d = 100 * 20 * 1.4 / (2 * 120)
        assert center == (10.0, 20.0)
        assert angle == 12.0
        assert size == pytest.approx((100 + 2 * d, 20 + 2 * d))


class TestQuadOrder:
    def test_axis_aligned(self) -> None:
        shuffled = [(10, 50), (200, 10), (10, 10), (200, 50)]
        assert order_quad(shuffled).tolist() == [[10, 10], [200, 10], [200, 50], [10, 50]]

    def test_tilted_text(self) -> None:
        corners = cv2.boxPoints(((100.0, 100.0), (120.0, 20.0), 30.0))
        tl, tr, br, bl = order_quad(corners)
        assert tl[0] < tr[0]
        assert bl[0] < br[0]
        assert tl[1] < bl[1]
        assert tr[1] < br[1]

    @given(
        st.floats(10, 1000),
        st.floats(10, 1000),
        st.floats(1, 300),
        st.floats(1, 300),
        st.floats(-90, 90),
    )
    def test_clockwise_rectangle_whatever_the_input_order(
        self, cx: float, cy: float, width: float, height: float, angle: float
    ) -> None:
        corners = cv2.boxPoints(((cx, cy), (width, height), angle))
        diagonal = math.hypot(width, height)
        results = [order_quad(corners[list(p)]) for p in itertools.permutations(range(4))]
        for quad in results:
            assert sorted(map(tuple, quad.tolist())) == sorted(map(tuple, corners.tolist()))
            assert _screen_area(quad) > 0
            # TL-BR and TR-BL are the diagonals: the corners go round, never across.
            assert np.linalg.norm(quad[0] - quad[2]) == pytest.approx(diagonal, rel=1e-3)
            assert np.linalg.norm(quad[1] - quad[3]) == pytest.approx(diagonal, rel=1e-3)
        xs = np.sort(corners[:, 0])
        if xs[2] - xs[1] > 1e-3:  # no tie on x between the 2nd and 3rd corners
            assert all(np.array_equal(results[0], quad) for quad in results)


class TestReadingOrder:
    @staticmethod
    def _quad(x: float, y: float) -> npt.NDArray[np.float32]:
        return np.array([[x, y], [x + 50, y], [x + 50, y + 20], [x, y + 20]], dtype=np.float32)

    def test_rows_then_left_to_right(self) -> None:
        quads = [
            self._quad(300, 104),  # same row as the next one, 4 px lower
            self._quad(20, 100),
            self._quad(20, 200),
            self._quad(20, 10),
        ]
        assert reading_order(quads) == [3, 1, 0, 2]

    def test_ten_pixels_apart_is_another_row(self) -> None:
        quads = [self._quad(300, 100), self._quad(20, 110)]
        assert reading_order(quads) == [0, 1]

    def test_empty(self) -> None:
        assert reading_order([]) == []


class TestDbBoxes:
    def test_one_box_scaled_to_the_image(self) -> None:
        prob = np.zeros((100, 200), dtype=np.float32)
        prob[40:60, 50:150] = 0.9
        (quad,) = db_boxes(prob, 2.0, 2.0, 400, 200)
        tl, tr, br, bl = quad
        # The mask covers x 50..149, y 40..59 (×2 in the image), grown by the unclip.
        d = unclip_distance(100, 20, 1.4) * 2
        assert tl[0] == pytest.approx(100 - d, abs=4)
        assert tl[1] == pytest.approx(80 - d, abs=4)
        assert br[0] == pytest.approx(300 + d, abs=4)
        assert br[1] == pytest.approx(120 + d, abs=4)
        assert tr[1] == tl[1]
        assert bl[0] == tl[0]

    def test_clipped_to_the_image(self) -> None:
        prob = np.zeros((50, 100), dtype=np.float32)
        prob[0:20, 0:100] = 0.95
        (quad,) = db_boxes(prob, 1.0, 1.0, 100, 50)
        assert quad.min() >= 0
        assert quad[:, 0].max() <= 99
        assert quad[:, 1].max() <= 49

    def test_weak_and_tiny_blobs_are_dropped(self) -> None:
        prob = np.zeros((100, 200), dtype=np.float32)
        prob[10:30, 10:120] = 0.3  # above the pixel threshold, under the box threshold
        prob[70:72, 70:72] = 0.99  # smaller than 3 px
        assert db_boxes(prob, 1.0, 1.0, 200, 100) == []


# ------------------------------------------------------------------ recognition helpers
class TestRecognitionHelpers:
    def test_crop_is_straightened_and_vertical_text_rotated(self) -> None:
        image = np.full((200, 300, 3), 255, dtype=np.uint8)
        wide = np.array([[20, 30], [220, 30], [220, 70], [20, 70]], dtype=np.float32)
        tall = np.array([[100, 10], [120, 10], [120, 190], [100, 190]], dtype=np.float32)
        assert crop_quad(image, wide).shape == (40, 200, 3)
        rotated = crop_quad(image, tall)
        assert rotated.shape == (20, 180, 3)
        assert rotated.flags["C_CONTIGUOUS"]

    def test_blob_pads_to_the_widest_crop(self) -> None:
        crops = [
            np.full((48, 96, 3), 255, dtype=np.uint8),
            np.full((20, 200, 3), 255, dtype=np.uint8),
        ]
        blob = rec_blob(crops)
        assert blob.shape == (2, 3, 48, 480)
        assert blob.dtype == np.float32
        assert np.allclose(blob[0, :, :, :96], 1.0)
        assert np.allclose(blob[0, :, :, 96:], 0.0)
        assert np.allclose(blob[1], 1.0)

    def test_blob_is_at_least_320_wide(self) -> None:
        assert rec_blob([np.zeros((48, 48, 3), dtype=np.uint8)]).shape == (1, 3, 48, 320)

    def test_ctc_greedy_collapses_repeats_and_drops_blanks(self) -> None:
        classes = ["", "a", "b", " "]
        ids = [1, 1, 0, 1, 2, 2, 3, 0, 0, 2]
        probs = np.zeros((2, len(ids), len(classes)), dtype=np.float32)
        for step, class_id in enumerate(ids):
            probs[0, step, class_id] = 0.5 + step / 100
        probs[1, :, 0] = 1.0  # nothing read
        (text, score), empty = ctc_greedy(probs, classes)
        assert text == "aab b"
        kept_steps = [0, 3, 4, 6, 9]
        assert score == pytest.approx(np.mean([0.5 + s / 100 for s in kept_steps]))
        assert empty == ("", 0.0)


DICT_SAMPLE = (
    "Global:\n"
    "  model_name: PP-OCRv6_small_rec\n"
    "  character_dict:\n"  # not under PostProcess: ignored
    "  - x\n"
    "PreProcess:\n"
    "  transform_ops:\n"
    "  - DecodeImage:\n"
    "      img_mode: BGR\n"
    "PostProcess:\n"
    "  name: CTCLabelDecode\n"
    "  character_dict:\n"
    "  - '!'\n"
    "  - ''''\n"
    "  - '\"'\n"
    "  - $\n"
    "  - '0'\n"
    "  - \\\n"
    "  - é\n"
    '  - "\\u00e9\\t\\x41"\n'
    "  - «\n"
    "  - \u00a0\n"
    "  - 😀\n"
    "Other:\n"
    "  key: value\n"
)
DICT_EXPECTED = ["!", "'", '"', "$", "0", "\\", "é", "é\tA", "«", "\u00a0", "😀"]


class TestCharacterDict:
    def test_parses_the_exporter_scalars(self) -> None:
        assert parse_character_dict(DICT_SAMPLE) == DICT_EXPECTED

    def test_bom_and_crlf(self) -> None:
        crlf = "\ufeff" + DICT_SAMPLE.replace("\n", "\r\n")
        assert parse_character_dict(crlf) == DICT_EXPECTED

    def test_deeper_item_indentation(self) -> None:
        text = "PostProcess:\n  character_dict:\n    - a\n    - b\n  name: x\n"
        assert parse_character_dict(text) == ["a", "b"]

    def test_agrees_with_pyyaml(self) -> None:
        yaml = pytest.importorskip("yaml")
        expected = yaml.safe_load(DICT_SAMPLE)["PostProcess"]["character_dict"]
        assert parse_character_dict(DICT_SAMPLE) == expected

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("Global:\n  character_dict:\n  - a\n", "introuvable"),
            ("PostProcess:\n  character_dict:\n  name: x\n", "vide"),
            ("PostProcess:\n  character_dict:\n  -\n", "élément vide"),
            ("PostProcess:\n  character_dict:\n  - 'ab\n", "mal fermée"),
            ('PostProcess:\n  character_dict:\n  - "\\q"\n', "échappement"),
            ('PostProcess:\n  character_dict:\n  - "\\u00e"\n', "échappement"),
            ("PostProcess:\n  character_dict:\n  - a\n    - b\n", "indentation"),
        ],
    )
    def test_rejects_what_it_does_not_understand(self, text: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            parse_character_dict(text)


# ------------------------------------------------------------------ pipeline on fake sessions
class _Arg:
    def __init__(self, name: str, shape: list[int | str]) -> None:
        self.name = name
        self.shape = shape


class FakeDet:
    """Probability map with high values over the given boxes (fractions of the input)."""

    def __init__(self, boxes: Sequence[tuple[float, float, float, float]]) -> None:
        self.boxes = boxes
        self.shapes: list[tuple[int, ...]] = []

    def get_inputs(self) -> list[_Arg]:
        return [_Arg("x", ["N", 3, "H", "W"])]

    def get_outputs(self) -> list[_Arg]:
        return [_Arg("fetch_name_0", ["N", 1, "H", "W"])]

    def get_providers(self) -> list[str]:
        return [CPU_PROVIDER]

    def run(self, output_names: Sequence[str] | None, input_feed: Mapping[str, Any]) -> list[Any]:
        blob = input_feed["x"]
        self.shapes.append(blob.shape)
        _, _, height, width = blob.shape
        prob = np.zeros((1, 1, height, width), dtype=np.float32)
        for x0, y0, x1, y1 in self.boxes:
            prob[
                0, 0, round(y0 * height) : round(y1 * height), round(x0 * width) : round(x1 * width)
            ] = 0.9
        return [prob]


class FakeRec:
    """Reads the same class ids in every crop, with probability 0.97."""

    def __init__(self, ids: Sequence[int], classes: int, *, symbolic: bool = False) -> None:
        self.ids = ids
        self.classes = classes
        self.symbolic = symbolic
        self.batches: list[int] = []

    def get_inputs(self) -> list[_Arg]:
        return [_Arg("x", ["N", 3, 48, "W"])]

    def get_outputs(self) -> list[_Arg]:
        return [_Arg("softmax", ["N", "T", "C" if self.symbolic else self.classes])]

    def get_providers(self) -> list[str]:
        return [CPU_PROVIDER]

    def run(self, output_names: Sequence[str] | None, input_feed: Mapping[str, Any]) -> list[Any]:
        blob = input_feed["x"]
        self.batches.append(blob.shape[0])
        probs = np.zeros((blob.shape[0], blob.shape[3] // 8, self.classes), dtype=np.float32)
        probs[:, :, 0] = 1.0
        for k, class_id in enumerate(self.ids):
            probs[:, 2 * k + 1, 0] = 0.01
            probs[:, 2 * k + 1, class_id] = 0.97
        return [probs]


class TestPipelineOnFakeSessions:
    DICTIONARY = ("P", "2")

    def test_reads_boxes_in_reading_order(self) -> None:
        # The right box of the second row starts a few pixels higher than the left one.
        det = FakeDet([(0.55, 0.49, 0.9, 0.6), (0.1, 0.2, 0.5, 0.3), (0.1, 0.5, 0.45, 0.6)])
        rec = FakeRec([1, 2], classes=4)
        engine = PpOcr(det, rec, self.DICTIONARY)
        image = np.full((360, 640, 3), 255, dtype=np.uint8)
        lines = engine.read(image)
        assert det.shapes == [(1, 3, 736, 1312)]
        assert rec.batches == [3]
        assert [line.text for line in lines] == ["P2", "P2", "P2"]
        assert all(line.score == pytest.approx(0.97) for line in lines)
        lefts = [line.box[0][0] for line in lines]
        tops = [line.box[0][1] for line in lines]
        assert tops[0] < tops[1]
        assert tops[2] < tops[1]  # higher, but on the same row…
        assert lefts[1] < lefts[2]  # …so the left box comes first
        first = lines[0].box
        assert len(first) == 4
        assert first[0][0] < 64 < first[1][0]  # the box encloses x = 0.1 × 640
        assert first[0][1] < 72 < first[3][1]
        assert normalized_box(first, 640, 360)[2][0] > 0.5

    def test_batches_of_six(self) -> None:
        boxes = [(0.05 + 0.3 * (i % 3), 0.05 + 0.2 * (i // 3), 0.3 + 0.3 * (i % 3), 0.15 + 0.2 * (i // 3)) for i in range(8)]  # fmt: skip
        rec = FakeRec([1], classes=4)
        lines = PpOcr(FakeDet(boxes), rec, self.DICTIONARY).read(
            np.full((720, 1280, 3), 255, dtype=np.uint8)
        )
        assert len(lines) == 8
        assert rec.batches == [6, 2]

    def test_nothing_detected_skips_recognition(self) -> None:
        rec = FakeRec([1], classes=4)
        engine = PpOcr(FakeDet([]), rec, self.DICTIONARY)
        assert engine.read(np.zeros((100, 100, 3), dtype=np.uint8)) == []
        assert rec.batches == []

    def test_blank_reads_are_dropped(self) -> None:
        engine = PpOcr(FakeDet([(0.1, 0.1, 0.9, 0.3)]), FakeRec([], classes=4), self.DICTIONARY)
        assert engine.read(np.zeros((200, 400, 3), dtype=np.uint8)) == []

    def test_gray_and_bgra_images_are_accepted(self) -> None:
        engine = PpOcr(FakeDet([(0.1, 0.1, 0.9, 0.3)]), FakeRec([2], classes=4), self.DICTIONARY)
        gray = np.zeros((200, 400), dtype=np.uint8)
        bgra = np.zeros((200, 400, 4), dtype=np.uint8)
        assert [line.text for line in engine.read(gray)] == ["2"]
        assert [line.text for line in engine.read(bgra)] == ["2"]

    @pytest.mark.parametrize(
        "image",
        [
            np.zeros((0, 10, 3), dtype=np.uint8),
            np.zeros((10, 10, 3), dtype=np.float32),
            np.zeros((10, 10, 2), dtype=np.uint8),
        ],
    )
    def test_rejects_other_images(self, image: npt.NDArray[Any]) -> None:
        engine = PpOcr(FakeDet([]), FakeRec([], classes=4), self.DICTIONARY)
        with pytest.raises(ValueError, match="Image"):
            engine.read(image)

    def test_dictionary_must_match_the_model(self) -> None:
        with pytest.raises(ValueError, match="incohérent"):
            PpOcr(FakeDet([]), FakeRec([], classes=5), self.DICTIONARY)

    def test_symbolic_class_count_is_probed(self) -> None:
        rec = FakeRec([], classes=4, symbolic=True)
        engine = PpOcr(FakeDet([]), rec, self.DICTIONARY)
        assert rec.batches == [1]
        assert engine.dictionary_size == 2
        with pytest.raises(ValueError, match="incohérent"):
            PpOcr(FakeDet([]), FakeRec([], classes=5, symbolic=True), self.DICTIONARY)

    def test_providers(self) -> None:
        engine = PpOcr(FakeDet([]), FakeRec([], classes=4), self.DICTIONARY)
        assert engine.providers == (CPU_PROVIDER,)


class TestFromDir:
    def test_missing_files_are_listed(self, tmp_path: Path) -> None:
        (tmp_path / DET_FILE).write_bytes(b"")
        with pytest.raises(FileNotFoundError, match=f"{REC_FILE}, {DICT_FILE}"):
            PpOcr.from_dir(tmp_path)

    def test_thread_count_is_checked(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="threads"):
            PpOcr.from_dir(tmp_path, threads=0)


# ------------------------------------------------------------------ real models
def _model_dir() -> Path:
    configured = os.environ.get("VFE_OCR_MODEL_DIR")
    if configured:
        return Path(configured)
    return default_data_dir() / "models" / "ocr" / "pp-ocrv6-small"


@pytest.fixture(scope="module")
def model_dir() -> Path:
    directory = _model_dir()
    missing = [name for name in (DET_FILE, REC_FILE, DICT_FILE) if not (directory / name).is_file()]
    if missing:
        pytest.skip(f"OCR model files missing in {directory}: {', '.join(missing)}")
    return directory


@pytest.fixture(scope="module")
def engine(model_dir: Path) -> PpOcr:
    return PpOcr.from_dir(model_dir, threads=4)


def _sign(lines: Sequence[str], size: tuple[int, int] = (720, 1280)) -> npt.NDArray[np.uint8]:
    image = np.full((*size, 3), 255, dtype=np.uint8)
    for k, text in enumerate(lines):
        cv2.putText(
            image, text, (80, 150 + k * 140), cv2.FONT_HERSHEY_DUPLEX, 1.8, (30, 30, 30), 3,
            cv2.LINE_AA,
        )  # fmt: skip
    return image


SIGN = ["Defense de fumer", "Sortie de secours", "Ouvert du lundi au samedi", "Prix : 3,50 EUR"]


@pytest.mark.models
class TestWithModels:
    def test_cpu_only(self, engine: PpOcr) -> None:
        assert engine.providers == (CPU_PROVIDER,)

    def test_dictionary_covers_french(self, engine: PpOcr, model_dir: Path) -> None:
        dictionary = parse_character_dict((model_dir / DICT_FILE).read_text(encoding="utf-8"))
        assert engine.dictionary_size == len(dictionary) == 18_708
        assert set(FRENCH) <= set(dictionary)

    def test_dictionary_agrees_with_pyyaml(self, model_dir: Path) -> None:
        yaml = pytest.importorskip("yaml")
        text = (model_dir / DICT_FILE).read_text(encoding="utf-8")
        expected = [str(c) for c in yaml.safe_load(text)["PostProcess"]["character_dict"]]
        assert parse_character_dict(text) == expected

    def test_reads_a_french_sign(self, engine: PpOcr) -> None:
        lines = engine.read(_sign(SIGN))
        assert [line.text for line in lines] == SIGN
        for line in lines:
            assert filter_line(line.text, line.score) == line.text
            assert line.score > 0.9
            tl, tr, br, bl = line.box
            assert tl[0] < tr[0]
            assert tl[1] < bl[1]
            assert min(tl + tr + br + bl) >= 0
            assert max(tr[0], br[0]) <= 1279
        tops = [line.box[0][1] for line in lines]
        assert tops == sorted(tops)
        assert 60 <= lines[0].box[0][0] <= 90  # putText origin x = 80

    def test_reads_tilted_text(self, engine: PpOcr) -> None:
        image = _sign(SIGN[:2])
        matrix = cv2.getRotationMatrix2D((640, 360), 8, 1.0)
        tilted = cv2.warpAffine(image, matrix, (1280, 720), borderValue=(255, 255, 255))
        read = [fold(line.text) for line in engine.read(np.asarray(tilted, dtype=np.uint8))]
        assert read == [fold(text) for text in SIGN[:2]]

    def test_blank_frame_reads_nothing(self, engine: PpOcr) -> None:
        assert engine.read(np.full((720, 1280, 3), 255, dtype=np.uint8)) == []

    def test_read_file_with_a_non_ascii_path(self, engine: PpOcr, tmp_path: Path) -> None:
        folder = tmp_path / "écriteau été"
        folder.mkdir()
        path = folder / "panneau-œuvre.png"
        image = _sign(SIGN[:1], size=(360, 640))
        ok, encoded = cv2.imencode(".png", image)
        assert ok
        encoded.tofile(path)
        from_file = engine.read_file(path)
        assert from_file == engine.read(image)
        assert [line.text for line in from_file] == SIGN[:1]
        assert all(isinstance(line, OcrLine) for line in from_file)

    def test_dictionary_mismatch_is_refused(self, model_dir: Path, tmp_path: Path) -> None:
        for name in (DET_FILE, REC_FILE):
            try:
                os.link(model_dir / name, tmp_path / name)
            except OSError:
                shutil.copyfile(model_dir / name, tmp_path / name)
        text = (model_dir / DICT_FILE).read_text(encoding="utf-8")
        truncated = text.rstrip("\n").rsplit("\n", 1)[0] + "\n"  # last character removed
        (tmp_path / DICT_FILE).write_text(truncated, encoding="utf-8")
        with pytest.raises(ValueError, match="18707 caractères"):
            PpOcr.from_dir(tmp_path, threads=1)
