"""D-FINE object detector (COCO, Apache-2.0) on the CPU with onnxruntime.

The onnx-community export takes a 640×640 RGB image scaled to 0–1 (a plain resize, no
letterbox, as the Hugging Face preprocessor does) and returns, for 300 queries, class logits
(sigmoid, focal loss) and boxes ``(cx, cy, w, h)`` normalised to the input image — hence to the
original image too. Only the classes asked for are kept (living beings, here).
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
import numpy.typing as npt

ENGINE_VERSION = 1
INPUT_SIZE = 640
CPU_PROVIDER = "CPUExecutionProvider"

Image = npt.NDArray[np.uint8]  # BGR, HxWx3


class OnnxSession(Protocol):
    def get_inputs(self) -> Sequence[Any]: ...

    def get_providers(self) -> list[str]: ...

    def run(
        self, output_names: Sequence[str] | None, input_feed: Mapping[str, Any]
    ) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class RawBox:
    label: str  # COCO class name, from the model's config.json
    score: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 normalised 0–1


def preprocess(image: Image) -> npt.NDArray[np.float32]:
    """BGR image → ``[1, 3, 640, 640]`` RGB float32 in 0–1."""
    resized = cv2.resize(image, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None])


def postprocess(
    logits: npt.NDArray[np.float32],
    boxes: npt.NDArray[np.float32],
    labels: Sequence[str],
    *,
    keep: Collection[str],
    min_score: float,
) -> list[RawBox]:
    """Best kept class per query, above ``min_score``, highest score first."""
    classes = [i for i, name in enumerate(labels) if name in keep]
    if not classes:
        return []
    scores = 1.0 / (1.0 + np.exp(-logits[0][:, classes].astype(np.float64)))  # [Q, K]
    best = scores.argmax(axis=1)
    best_scores = scores[np.arange(scores.shape[0]), best]
    out: list[RawBox] = []
    for query in np.argsort(-best_scores):
        score = float(best_scores[query])
        if score < min_score:
            break
        cx, cy, w, h = (float(v) for v in boxes[0][query])
        box = (_unit(cx - w / 2), _unit(cy - h / 2), _unit(cx + w / 2), _unit(cy + h / 2))
        out.append(RawBox(labels[classes[int(best[query])]], score, box))
    return out


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def _cpu_session(path: Path, threads: int) -> OnnxSession:
    import onnxruntime as ort  # heavy native module: only loaded when detection is used

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
        raise RuntimeError(f"Session du détecteur hors CPU : {session.get_providers()}")
    return session


class DFine:
    """One ONNX session shared by the worker threads (``InferenceSession.run`` is thread-safe)."""

    def __init__(self, session: OnnxSession, labels: Sequence[str]) -> None:
        self._session = session
        self._input = session.get_inputs()[0].name
        self.labels = list(labels)

    @classmethod
    def from_dir(cls, model_dir: Path, *, threads: int = 2) -> DFine:
        """Load ``model.onnx`` and the class names of ``config.json`` from ``model_dir``."""
        config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        id2label = config["id2label"]
        labels = [str(id2label[str(i)]) for i in range(len(id2label))]
        return cls(_cpu_session(model_dir / "model.onnx", threads), labels)

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(self._session.get_providers())

    def detect(self, image: Image, *, keep: Collection[str], min_score: float) -> list[RawBox]:
        logits, boxes = self._session.run(None, {self._input: preprocess(image)})[:2]
        return postprocess(
            np.asarray(logits), np.asarray(boxes), self.labels, keep=keep, min_score=min_score
        )
