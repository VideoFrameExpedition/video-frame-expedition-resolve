"""PP-OCRv6 text detection (DB) and recognition (CTC) on onnxruntime, CPU only.

Own pre- and post-processing with numpy and OpenCV instead of RapidOCR: rapidocr requires the GUI
build of OpenCV (a second ``cv2`` next to opencv-python-headless) plus the Shapely and pyclipper
native modules, which Smart App Control may block. The only step that needed them, the DB
« unclip », has an exact closed form: offsetting a rectangle by ``d`` with round joins and taking
the minimum-area rectangle of the result gives the same rectangle grown by ``d`` on every side.
With the same models the output matches RapidOCR 3.9.2 (14 of 16 lines identical on the sample
keyframes, corners within 2 px).

Model folder (``vfe models ocr``): ``det.onnx`` and ``rec.onnx`` from the official PaddlePaddle
Hugging Face repositories (Apache-2.0), and ``rec.yml``, the recogniser's ``inference.yml`` whose
``PostProcess.character_dict`` list is the dictionary (18,708 characters, all of French). The
normalisation and thresholds are those of the models' ``inference.yml``; the 2×2 dilation and the
736 px minimum side of the detection input come from measurements on sample keyframes
(native 576 px keyframes lost short tokens such as « P1 » and merged neighbouring lines).

The vision model owns the GPU: sessions are created with the CPU provider only, never « auto ».
Recognised text is returned raw; ``vfe_vision.domain.ocr.filter_line`` decides what to keep.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from vfe_vision.adapters.imaging import read_image

DET_FILE = "det.onnx"
REC_FILE = "rec.onnx"
DICT_FILE = "rec.yml"
CPU_PROVIDER = "CPUExecutionProvider"
ENGINE_VERSION = 1  # bump when the pre- or post-processing below changes the results

DET_MIN_SIDE = 736  # never downscaled: 1280×720 keyframes are detected at 1312×736
DET_MAX_SIDE = 4000  # largest input of the model's dynamic shapes
DET_MULTIPLE = 32
DET_MEAN = np.array((0.485, 0.456, 0.406), np.float32)  # applied to BGR, as Paddle does
DET_STD = np.array((0.229, 0.224, 0.225), np.float32)
DET_THRESH = 0.2  # pixel probability of the text mask
DET_BOX_THRESH = 0.45  # mean probability inside a candidate box
DET_UNCLIP_RATIO = 1.4
DET_MAX_CANDIDATES = 1000
DET_MIN_SIZE = 3  # px, shorter side of a box on the probability map
REC_HEIGHT = 48
REC_MIN_WIDTH = 320
REC_BATCH = 6
VERTICAL_RATIO = 1.5  # a crop this much taller than wide is vertical text: read it rotated
SAME_ROW_PX = 10  # reading order: boxes whose tops differ by less share a row

_DILATE_KERNEL = np.ones((2, 2), np.uint8)
_YAML_ESCAPES = {
    "0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f",
    "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85",
    "_": "\xa0", "L": "\u2028", "P": "\u2029",
}  # fmt: skip
_YAML_HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}
_HEX_RE = re.compile(r"[0-9A-Fa-f]+")

Image = npt.NDArray[np.uint8]  # BGR, HxWx3
Quad = npt.NDArray[np.float32]  # (4, 2) corners TL, TR, BR, BL
Rect = tuple[tuple[float, float], tuple[float, float], float]  # cv2.minAreaRect: centre, size, °


class OnnxSession(Protocol):
    """The part of ``onnxruntime.InferenceSession`` used here."""

    def get_inputs(self) -> Sequence[Any]: ...

    def get_outputs(self) -> Sequence[Any]: ...

    def get_providers(self) -> list[str]: ...

    def run(
        self, output_names: Sequence[str] | None, input_feed: Mapping[str, Any]
    ) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str  # raw recogniser output (see domain.ocr for cleaning and filtering)
    score: float  # mean CTC probability of the kept characters, 0..1
    box: tuple[tuple[float, float], ...]  # TL, TR, BR, BL in input image pixels


# ---------------------------------------------------------------- pure helpers
def det_input_size(height: int, width: int) -> tuple[int, int]:
    """Detection input ``(height, width)``: the shorter side brought up to 736 px (never down),
    the longer side kept under 4000 px, both rounded to multiples of 32."""
    scale = max(1.0, DET_MIN_SIDE / min(height, width))
    scale = min(scale, DET_MAX_SIDE / max(height, width))

    def snap(size: int) -> int:
        return max(DET_MULTIPLE, round(size * scale / DET_MULTIPLE) * DET_MULTIPLE)

    return snap(height), snap(width)


def unclip_distance(width: float, height: float, ratio: float) -> float:
    """DB unclip offset: area × ratio / perimeter of the box (Shapely's area / length)."""
    return width * height * ratio / (2 * (width + height))


def unclip_rect(rect: Rect, ratio: float) -> Rect:
    """Closed-form DB unclip: pyclipper's round-join offset by ``d`` followed by
    ``cv2.minAreaRect`` is the same rectangle grown by ``d`` on each side."""
    center, (width, height), angle = rect
    d = unclip_distance(width, height, ratio)
    return center, (width + 2 * d, height + 2 * d), angle


def order_quad(points: npt.ArrayLike) -> Quad:
    """The 4 corners clockwise from the top-left on screen (y pointing down): of the two leftmost
    points the upper one is TL and the lower one BL, likewise TR and BR on the right."""
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    by_x = pts[np.argsort(pts[:, 0], kind="stable")]
    left = by_x[:2][np.argsort(by_x[:2, 1], kind="stable")]
    right = by_x[2:][np.argsort(by_x[2:, 1], kind="stable")]
    return np.array([left[0], right[0], right[1], left[1]], dtype=np.float32)


def reading_order(quads: Sequence[Quad]) -> list[int]:
    """Indices of the boxes top to bottom, then left to right among boxes whose top-left corners
    are less than 10 px apart vertically (PaddleOCR's ``sorted_boxes``)."""
    order = sorted(range(len(quads)), key=lambda i: (float(quads[i][0, 1]), float(quads[i][0, 0])))
    for i in range(len(order) - 1):
        for j in range(i, -1, -1):
            upper, lower = quads[order[j]][0], quads[order[j + 1]][0]
            if abs(lower[1] - upper[1]) >= SAME_ROW_PX or lower[0] >= upper[0]:
                break
            order[j], order[j + 1] = order[j + 1], order[j]
    return order


def box_score(prob: npt.NDArray[np.float32], quad: Quad) -> float:
    """Mean probability inside the box (DB « fast » score, on its bounding rectangle's pixels)."""
    height, width = prob.shape
    x0, y0 = np.clip(np.floor(quad.min(axis=0)).astype(np.int32), 0, (width - 1, height - 1))
    x1, y1 = np.clip(np.ceil(quad.max(axis=0)).astype(np.int32), 0, (width - 1, height - 1))
    mask = np.zeros((y1 - y0 + 1, x1 - x0 + 1), dtype=np.uint8)
    shifted = (quad - np.array((x0, y0), dtype=np.float32)).astype(np.int32)
    cv2.fillPoly(mask, [shifted.reshape(-1, 1, 2)], 1)
    return float(cv2.mean(prob[y0 : y1 + 1, x0 : x1 + 1], mask)[0])


def db_boxes(
    prob: npt.NDArray[np.float32], scale_x: float, scale_y: float, width: int, height: int
) -> list[Quad]:
    """Text boxes of a DB probability map, in image pixels (``scale_*`` = image / map size) and
    in reading order."""
    bitmap = cv2.dilate((prob > DET_THRESH).astype(np.uint8) * 255, _DILATE_KERNEL)
    contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    quads: list[Quad] = []
    for contour in contours[:DET_MAX_CANDIDATES]:
        (cx, cy), (rw, rh), angle = cv2.minAreaRect(contour)
        if min(rw, rh) < DET_MIN_SIZE:
            continue
        rect: Rect = ((cx, cy), (rw, rh), angle)
        if box_score(prob, order_quad(cv2.boxPoints(rect))) < DET_BOX_THRESH:
            continue
        grown = unclip_rect(rect, DET_UNCLIP_RATIO)
        if min(grown[1]) < DET_MIN_SIZE + 2:
            continue
        quad = order_quad(cv2.boxPoints(grown))
        quad[:, 0] = np.clip(np.round(quad[:, 0] * scale_x), 0, width - 1)
        quad[:, 1] = np.clip(np.round(quad[:, 1] * scale_y), 0, height - 1)
        if min(np.linalg.norm(quad[0] - quad[1]), np.linalg.norm(quad[0] - quad[3])) <= 3:
            continue
        quads.append(quad)
    return [quads[i] for i in reading_order(quads)]


def crop_quad(image: Image, quad: Quad) -> Image:
    """The box straightened by a perspective warp; tall crops are rotated 90° counter-clockwise."""
    width = int(max(np.linalg.norm(quad[0] - quad[1]), np.linalg.norm(quad[2] - quad[3])))
    height = int(max(np.linalg.norm(quad[0] - quad[3]), np.linalg.norm(quad[1] - quad[2])))
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), target)
    crop = np.asarray(
        cv2.warpPerspective(
            image,
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        ),
        dtype=np.uint8,
    )
    if crop.shape[0] >= VERTICAL_RATIO * crop.shape[1]:
        crop = np.rot90(crop)
    return np.ascontiguousarray(crop)


def rec_blob(crops: Sequence[Image]) -> npt.NDArray[np.float32]:
    """Recognition batch: every crop resized to 48 px high, normalised to -1..1 and padded with
    zeros to the widest aspect ratio of the batch (at least 320 px)."""
    ratio = max(REC_MIN_WIDTH / REC_HEIGHT, *(c.shape[1] / c.shape[0] for c in crops))
    batch_width = int(REC_HEIGHT * ratio)
    blob = np.zeros((len(crops), 3, REC_HEIGHT, batch_width), dtype=np.float32)
    for k, crop in enumerate(crops):
        width = min(batch_width, math.ceil(REC_HEIGHT * crop.shape[1] / crop.shape[0]))
        resized = cv2.resize(crop, (width, REC_HEIGHT)).astype(np.float32)
        blob[k, :, :, :width] = resized.transpose(2, 0, 1) / 127.5 - 1.0
    return blob


def ctc_greedy(probs: npt.NDArray[np.float32], classes: Sequence[str]) -> list[tuple[str, float]]:
    """Greedy CTC decoding of ``(batch, steps, classes)`` probabilities: best class per step,
    repeats collapsed, blanks (class 0) dropped. The score is the mean probability of the kept
    steps, 0 when nothing was read."""
    decoded: list[tuple[str, float]] = []
    for ids, best in zip(probs.argmax(axis=2), probs.max(axis=2), strict=True):
        keep = ids != 0
        keep[1:] &= ids[1:] != ids[:-1]
        text = "".join(classes[int(i)] for i in ids[keep])
        decoded.append((text, float(best[keep].mean()) if keep.any() else 0.0))
    return decoded


def parse_character_dict(text: str) -> list[str]:
    """``PostProcess.character_dict`` of a PaddlePaddle ``inference.yml``, without a YAML library.

    Only the block sequence of scalars the Paddle exporter writes is understood (plain, single-
    and double-quoted items); anything else raises ``ValueError`` rather than guessing.
    """
    lines = [line.removesuffix("\r") for line in text.removeprefix("\ufeff").split("\n")]
    in_post_process = False
    for number, line in enumerate(lines):
        if line and not line[0].isspace() and not line.startswith("#"):
            in_post_process = line.rstrip(" ") == "PostProcess:"
        elif in_post_process and line.strip(" ") == "character_dict:":
            key_indent = len(line) - len(line.lstrip(" "))
            return _yaml_sequence(lines[number + 1 :], key_indent)
    raise ValueError("Dictionnaire OCR introuvable (PostProcess.character_dict)")


def _yaml_sequence(lines: Sequence[str], key_indent: int) -> list[str]:
    items: list[str] = []
    item_indent: int | None = None
    for line in lines:
        if not line.strip(" "):
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line[indent:]
        if indent < key_indent or not (body == "-" or body.startswith("- ")):
            break
        if item_indent is None:
            item_indent = indent
        elif indent != item_indent:
            raise ValueError(f"Dictionnaire OCR : indentation inattendue ({line!r})")
        items.append(_yaml_scalar(body[1:].strip(" \t")))
    if not items:
        raise ValueError("Dictionnaire OCR vide (PostProcess.character_dict)")
    return items


def _yaml_scalar(raw: str) -> str:
    if raw[:1] == "'":
        if len(raw) < 2 or raw[-1] != "'":
            raise ValueError(f"Dictionnaire OCR : chaîne mal fermée ({raw!r})")
        return raw[1:-1].replace("''", "'")
    if raw[:1] == '"':
        if len(raw) < 2 or raw[-1] != '"':
            raise ValueError(f"Dictionnaire OCR : chaîne mal fermée ({raw!r})")
        return _yaml_unescape(raw[1:-1])
    value = raw.split(" #", 1)[0].rstrip(" \t")
    if not value:
        raise ValueError("Dictionnaire OCR : élément vide")
    return value


def _yaml_unescape(body: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(body):
        if body[i] != "\\":
            out.append(body[i])
            i += 1
            continue
        code = body[i + 1 : i + 2]
        if code in _YAML_HEX_ESCAPES:
            digits = body[i + 2 : i + 2 + _YAML_HEX_ESCAPES[code]]
            if len(digits) != _YAML_HEX_ESCAPES[code] or not _HEX_RE.fullmatch(digits):
                raise ValueError(f"Dictionnaire OCR : échappement invalide ({body!r})")
            out.append(chr(int(digits, 16)))
            i += 2 + len(digits)
        elif code in _YAML_ESCAPES:
            out.append(_YAML_ESCAPES[code])
            i += 2
        else:
            raise ValueError(f"Dictionnaire OCR : échappement invalide ({body!r})")
    return "".join(out)


def _as_bgr(image: npt.NDArray[Any]) -> Image:
    if image.dtype != np.uint8 or image.size == 0:
        raise ValueError("Image 8 bits non vide attendue pour l'OCR")
    if image.ndim == 2:
        return np.asarray(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    if image.ndim == 3 and image.shape[2] == 4:
        return np.asarray(cv2.cvtColor(image, cv2.COLOR_BGRA2BGR), dtype=np.uint8)
    if image.ndim == 3 and image.shape[2] == 3:
        return np.asarray(image, dtype=np.uint8)
    raise ValueError(f"Image BGR attendue pour l'OCR, forme {image.shape}")


def _cpu_session(path: Path, threads: int) -> OnnxSession:
    import onnxruntime as ort  # heavy native module: only loaded when OCR is used

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.log_severity_level = 3  # errors only
    session: OnnxSession = ort.InferenceSession(
        str(path), sess_options=options, providers=[CPU_PROVIDER]
    )
    if session.get_providers() != [CPU_PROVIDER]:  # the GPU belongs to the vision model
        raise RuntimeError(f"Session OCR hors CPU : {session.get_providers()}")
    return session


def _class_count(rec: OnnxSession) -> int:
    size = rec.get_outputs()[0].shape[-1]
    if isinstance(size, int):
        return size
    # Symbolic output size: ask the model with a blank crop.
    probe = np.zeros((1, 3, REC_HEIGHT, REC_MIN_WIDTH), dtype=np.float32)
    return int(np.asarray(rec.run(None, {rec.get_inputs()[0].name: probe})[0]).shape[-1])


# ---------------------------------------------------------------- engine
class PpOcr:
    """PP-OCRv6 detector and recogniser. Load once and reuse: onnxruntime sessions are
    thread-safe, and loading takes about 0.2 s."""

    def __init__(self, det: OnnxSession, rec: OnnxSession, dictionary: Sequence[str]) -> None:
        # CTC classes: 0 is the blank, then the dictionary, then the space the exporter appends.
        self._classes = ("", *dictionary, " ")
        model_classes = _class_count(rec)
        if model_classes != len(self._classes):
            raise ValueError(
                f"Dictionnaire OCR incohérent : {len(dictionary)} caractères + 2 pour "
                f"{model_classes} classes dans le modèle de reconnaissance"
            )
        self._det = det
        self._rec = rec
        self._det_input = det.get_inputs()[0].name
        self._rec_input = rec.get_inputs()[0].name

    @classmethod
    def from_dir(cls, model_dir: Path, *, threads: int = 4) -> PpOcr:
        """Load ``det.onnx``, ``rec.onnx`` and the dictionary in ``rec.yml`` from ``model_dir``."""
        if threads < 1:
            raise ValueError(f"nombre de threads OCR invalide : {threads}")
        paths = [model_dir / name for name in (DET_FILE, REC_FILE, DICT_FILE)]
        missing = [path.name for path in paths if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                f"Modèle OCR incomplet dans {model_dir} : {', '.join(missing)} absent(s)"
            )
        dictionary = parse_character_dict(paths[2].read_text(encoding="utf-8"))
        return cls(_cpu_session(paths[0], threads), _cpu_session(paths[1], threads), dictionary)

    @property
    def providers(self) -> tuple[str, ...]:
        """Execution providers of both sessions (always the CPU one only)."""
        return tuple(dict.fromkeys([*self._det.get_providers(), *self._rec.get_providers()]))

    @property
    def dictionary_size(self) -> int:
        return len(self._classes) - 2

    def read_file(self, path: Path) -> list[OcrLine]:
        """OCR of an image file (non-ASCII paths supported)."""
        return self.read(read_image(path))

    def read(self, image_bgr: npt.NDArray[np.uint8]) -> list[OcrLine]:
        """Lines of text in reading order, with their raw text, score and box.

        Meant for the stored 1280 px keyframes (about 0.3–0.6 s on 4 threads): images are never
        downscaled for detection, so a 4K frame costs several times more. Grey and BGRA images
        are converted.
        """
        image = _as_bgr(image_bgr)
        quads = self.detect(image)
        if not quads:
            return []
        results = self.recognize([crop_quad(image, quad) for quad in quads])
        return [
            OcrLine(text, score, tuple((float(x), float(y)) for x, y in quad))
            for quad, (text, score) in zip(quads, results, strict=True)
            if text.strip()
        ]

    def detect(self, image: Image) -> list[Quad]:
        """Text boxes (TL, TR, BR, BL in image pixels) in reading order."""
        height, width = image.shape[:2]
        in_height, in_width = det_input_size(height, width)
        resized = cv2.resize(image, (in_width, in_height)).astype(np.float32)
        normalized = (resized / 255.0 - DET_MEAN) / DET_STD
        blob = np.ascontiguousarray(normalized.transpose(2, 0, 1)[None], dtype=np.float32)
        output = self._det.run(None, {self._det_input: blob})[0]
        prob = np.asarray(output, dtype=np.float32)[0, 0]
        return db_boxes(prob, width / in_width, height / in_height, width, height)

    def recognize(self, crops: Sequence[Image]) -> list[tuple[str, float]]:
        """``(text, score)`` per crop, batched by similar aspect ratio."""
        results: list[tuple[str, float]] = [("", 0.0)] * len(crops)
        order = sorted(range(len(crops)), key=lambda i: crops[i].shape[1] / crops[i].shape[0])
        for start in range(0, len(order), REC_BATCH):
            batch = order[start : start + REC_BATCH]
            blob = rec_blob([crops[i] for i in batch])
            output = self._rec.run(None, {self._rec_input: blob})[0]
            probs = np.asarray(output, dtype=np.float32)
            for i, decoded in zip(batch, ctc_greedy(probs, self._classes), strict=True):
                results[i] = decoded
        return results
