"""YuNet face detector (OpenCV Zoo, MIT) — face *positions* only, never who it is.

The model is handed to OpenCV as bytes: ``cv2`` cannot open a non-ASCII path on Windows.
A detector keeps the input size it was last given, so each caller builds its own
(a 230 kB model, a few milliseconds).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

ENGINE_VERSION = 3
SCORE_THRESHOLD = 0.7
NMS_THRESHOLD = 0.3
TOP_K = 5000
# 960 px: the faces found at 1280 px (and no more false ones) in half the time; at 640 px a
# hand over a pan became a "face".
MAX_SIDE = 960
MAX_FACE_SHARE = 0.5  # a "face" wider than half the image is a hand or a background
MIN_FACE_SIDE = 0.012  # of the image width: smaller faces are mostly false alarms

Image = npt.NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class Face:
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 normalised 0–1
    score: float
    # Right eye, left eye, nose tip, right and left mouth corners, normalised 0–1: the eye line
    # gives the headroom when a shot is reframed.
    points: tuple[tuple[float, float], ...] = ()


class FaceDetector:
    def __init__(self, model: npt.NDArray[np.uint8], *, score_threshold: float) -> None:
        self._model = model
        self._score_threshold = score_threshold

    @classmethod
    def from_file(cls, path: Path, *, score_threshold: float = SCORE_THRESHOLD) -> FaceDetector:
        return cls(np.fromfile(path, dtype=np.uint8), score_threshold=score_threshold)

    def detect(self, image: Image) -> list[Face]:
        height, width = image.shape[:2]
        scale = MAX_SIDE / max(height, width)
        if scale < 1:
            width, height = round(width * scale), round(height * scale)
            image = np.asarray(
                cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA), dtype=np.uint8
            )
        with _quiet_opencv():
            detector = cv2.FaceDetectorYN.create(
                "onnx", self._model, np.array([], dtype=np.uint8), (width, height),
                self._score_threshold, NMS_THRESHOLD, TOP_K,
            )  # fmt: skip
            _, found = detector.detect(image)
        rows: Any = found  # None when there is no face, whatever the stubs say
        faces: list[Face] = []
        for row in rows if rows is not None else ():
            x, y, w, h = (float(v) for v in row[:4])
            if w / width < MIN_FACE_SIDE or w / width > MAX_FACE_SHARE:
                continue
            box = (
                max(0.0, x / width),
                max(0.0, y / height),
                min(1.0, (x + w) / width),
                min(1.0, (y + h) / height),
            )
            points = tuple(
                (_unit(float(row[i]) / width), _unit(float(row[i + 1]) / height))
                for i in range(4, 14, 2)
            )
            faces.append(Face(box, float(row[-1]), points))
        return sorted(faces, key=lambda f: -f.score)


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


@contextlib.contextmanager
def _quiet_opencv() -> Iterator[None]:
    """OpenCV 5 warns on every creation that DNN targets are not supported yet: harmless."""
    logging = getattr(getattr(cv2, "utils", None), "logging", None)
    if logging is None:
        yield
        return
    previous = logging.getLogLevel()
    logging.setLogLevel(logging.LOG_LEVEL_ERROR)
    try:
        yield
    finally:
        logging.setLogLevel(previous)
