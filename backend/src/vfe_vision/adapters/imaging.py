"""Image I/O and small image operations (OpenCV + numpy).

``cv2.imread``/``cv2.imwrite`` fail on non-ASCII paths on Windows: all file access
goes through ``np.fromfile``/``cv2.imdecode`` and ``cv2.imencode`` + an atomic Python write.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from vfe_vision.core.atomic_io import atomic_write_bytes
from vfe_vision.domain.dedup import HASH_SIZE, dhash
from vfe_vision.domain.vision_profile import Box, ProbeScene

Image = npt.NDArray[np.uint8]  # BGR, HxWx3


def read_image(path: Path) -> Image:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Image illisible : {path}")
    return np.asarray(image, dtype=np.uint8)


def encode_jpeg(image: Image, quality: int = 90) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("Encodage JPEG impossible")
    return bytes(buffer.tobytes())


def write_jpeg(path: Path, image: Image, quality: int = 90) -> None:
    atomic_write_bytes(path, encode_jpeg(image, quality))


def resize_long_side(image: Image, long_side: int, *, multiple: int = 1) -> Image:
    """Resize so the longest side is ``long_side`` (never upscales); optionally snap both
    dimensions to a multiple (32 for Qwen-VL grounding)."""
    height, width = image.shape[:2]
    scale = min(1.0, long_side / max(width, height))
    new_w = max(multiple, round(width * scale / multiple) * multiple)
    new_h = max(multiple, round(height * scale / multiple) * multiple)
    if (new_w, new_h) == (width, height):
        return image
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return np.asarray(
        cv2.resize(image, (new_w, new_h), interpolation=interpolation), dtype=np.uint8
    )


def frame_dhash(image: Image) -> int:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (HASH_SIZE + 1, HASH_SIZE), interpolation=cv2.INTER_AREA)
    return dhash(small.astype(np.uint8))


def sharpness(image: Image) -> float:
    """Variance of the Laplacian on a 512 px version (comparable across resolutions)."""
    gray = cv2.cvtColor(resize_long_side(image, 512), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


_PROBE_COLORS = {  # BGR
    "red": (40, 40, 215),
    "blue": (205, 95, 25),
    "green": (45, 165, 45),
    "purple": (170, 45, 160),
    "black": (25, 25, 25),
}


def draw_probe_scene(scene: ProbeScene, seed: int = 0) -> tuple[ProbeScene, bytes, list[Box]]:
    """The calibration scene as a JPEG, with the true box of each shape measured on its mask
    (not the drawing target): light noisy gradient, five filled shapes."""
    width, height = scene.width, scene.height
    rng = np.random.default_rng(seed)
    gx = np.linspace(0, 1, width)[None, :, None]
    gy = np.linspace(0, 1, height)[:, None, None]
    base = 222 + 18 * gx - 12 * gy
    image = np.clip(base + rng.normal(0, 4, (height, width, 3)), 0, 255).astype(np.uint8)
    truth: list[Box] = []
    for obj in scene.objects:
        mask = np.zeros((height, width), np.uint8)
        _draw_shape(mask, obj.kind, obj.target, width, height)
        ys, xs = np.nonzero(mask)
        truth.append(
            (xs.min() / width, ys.min() / height, (xs.max() + 1) / width, (ys.max() + 1) / height)
        )
        image[mask > 0] = _PROBE_COLORS[obj.color]
    return scene, encode_jpeg(np.asarray(image, dtype=np.uint8), quality=90), truth


def _draw_shape(mask: Image, kind: str, target: Box, width: int, height: int) -> None:
    x1, y1, x2, y2 = target[0] * width, target[1] * height, target[2] * width, target[3] * height
    w, h = x2 - x1, y2 - y1

    def p(x: float, y: float) -> tuple[int, int]:
        return round(x), round(y)

    if kind == "rectangle":
        cv2.rectangle(mask, p(x1, y1), p(x2 - 1, y2 - 1), 255, -1)
    elif kind == "ellipse":
        cv2.ellipse(mask, p((x1 + x2) / 2, (y1 + y2) / 2), p(w / 2, h / 2), 0, 0, 360, 255, -1)
    elif kind == "triangle":
        points = np.array([p(x1, y2), p(x2, y2), p((x1 + x2) / 2, y1)], np.int32)
        cv2.fillPoly(mask, [points], 255)
    elif kind == "star":
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2 + h * 0.05
        star = []
        for k in range(10):
            angle = -math.pi / 2 + k * math.pi / 5
            r = 1.0 if k % 2 == 0 else 0.42
            star.append(p(cx + r * w / 2 * math.cos(angle), cy + r * h / 2 * math.sin(angle)))
        cv2.fillPoly(mask, [np.array(star, np.int32)], 255)
    elif kind == "cat":  # sitting, seen from the side, facing left
        s = min(w, h)
        cv2.ellipse(
            mask, p(x1 + w * 0.58, y1 + h * 0.68), p(w * 0.30, h * 0.30), 0, 0, 360, 255, -1
        )
        head = (x1 + w * 0.30, y1 + h * 0.36)
        cv2.circle(mask, p(*head), int(s * 0.19), 255, -1)
        r = s * 0.19
        for dx in (-0.75, 0.35):
            ear = np.array(
                [p(head[0] + dx * r, head[1] - 0.55 * r),
                 p(head[0] + (dx + 0.45) * r, head[1] - 0.55 * r),
                 p(head[0] + (dx + 0.2) * r, y1 + h * 0.06)],
                np.int32,
            )  # fmt: skip
            cv2.fillPoly(mask, [ear], 255)
        cv2.ellipse(
            mask, p(x1 + w * 0.40, y1 + h * 0.55), p(w * 0.12, h * 0.16), 0, 0, 360, 255, -1
        )
        tail = np.array(
            [p(x1 + w * 0.85, y1 + h * 0.80), p(x1 + w * 0.97, y1 + h * 0.55),
             p(x1 + w * 0.93, y1 + h * 0.30)],
            np.int32,
        )  # fmt: skip
        cv2.polylines(mask, [tail], isClosed=False, color=255, thickness=max(3, int(s * 0.07)))
    elif kind == "person":  # standing figure
        cx = (x1 + x2) / 2
        thick = max(3, int(w * 0.13))
        cv2.circle(mask, p(cx, y1 + h * 0.09), int(min(w * 0.22, h * 0.085)), 255, -1)
        body = np.array(
            [p(cx - w * 0.2, y1 + h * 0.19), p(cx + w * 0.2, y1 + h * 0.19),
             p(cx + w * 0.16, y1 + h * 0.55), p(cx - w * 0.16, y1 + h * 0.55)],
            np.int32,
        )  # fmt: skip
        cv2.fillPoly(mask, [body], 255)
        cv2.line(mask, p(cx - w * 0.2, y1 + h * 0.22), p(x1 + w * 0.06, y1 + h * 0.50), 255, thick)
        cv2.line(mask, p(cx + w * 0.2, y1 + h * 0.22), p(x2 - w * 0.06, y1 + h * 0.50), 255, thick)
        cv2.line(mask, p(cx - w * 0.1, y1 + h * 0.54), p(cx - w * 0.2, y2 - thick / 2), 255, thick)
        cv2.line(mask, p(cx + w * 0.1, y1 + h * 0.54), p(cx + w * 0.2, y2 - thick / 2), 255, thick)
