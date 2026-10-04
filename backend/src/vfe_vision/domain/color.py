"""Colour and exposure measurements on decoded frames (pure numpy/OpenCV computations).

The legacy application derived a "colour temperature" from the sun elevation only; here the
white balance is *measured* on the pixels (gray-world on near-neutral pixels, McCamy's CCT
approximation). The theoretical daylight temperature from the sun is computed separately.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np
import numpy.typing as npt

Image = npt.NDArray[np.uint8]

# sRGB (D65) → CIE XYZ
_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)

# Kelvin ranges → descriptive labels (corrected version of the chart used by the legacy app,
# which had overlapping and missing ranges).
KELVIN_LABELS: tuple[tuple[int, str], ...] = (
    (2000, "flamme / bougie"),
    (3000, "tungstène / lever-coucher de soleil"),
    (4000, "halogène / heure dorée"),
    (5000, "fluorescent / matin"),
    (6500, "lumière du jour"),
    (8000, "ciel couvert"),
    (10000, "ombre"),
    (99999, "ciel bleu / crépuscule"),
)


def kelvin_label(kelvin: float) -> str:
    return next(label for limit, label in KELVIN_LABELS if kelvin < limit)


def _srgb_to_linear(values: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def cct_mccamy(x: float, y: float) -> float:
    """Correlated colour temperature from CIE 1931 xy chromaticity (McCamy 1992)."""
    n = (x - 0.3320) / (0.1858 - y)
    return float(449 * n**3 + 3525 * n**2 + 6823.3 * n + 5520.33)


def estimate_cct(image: Image) -> float | None:
    """Scene white balance in kelvin, from near-neutral mid-tone pixels (None if too few)."""
    small = cv2.resize(image, (160, int(160 * image.shape[0] / image.shape[1]) or 1))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = (hsv[..., 1] < 70) & (hsv[..., 2] > 40) & (hsv[..., 2] < 235)
    if mask.sum() < 50:
        mask = (hsv[..., 2] > 40) & (hsv[..., 2] < 235)  # fall back to gray-world
        if mask.sum() < 50:
            return None
    rgb = small[..., ::-1][mask].astype(np.float64) / 255.0
    xyz = _RGB_TO_XYZ @ _srgb_to_linear(rgb).mean(axis=0)
    total = xyz.sum()
    if total <= 0:
        return None
    cct = cct_mccamy(xyz[0] / total, xyz[1] / total)
    return float(np.clip(cct, 1500, 15000))


@dataclass(frozen=True, slots=True)
class ExposureMetrics:
    luma_mean: float  # 0–1
    luma_median: float
    contrast: float  # luma standard deviation, 0–1
    clipped_shadows: float  # fraction of pixels ≤ 2 %
    clipped_highlights: float  # fraction of pixels ≥ 98 %
    saturation: float  # mean HSV saturation, 0–1
    sharpness: float  # Laplacian variance on a 512 px version

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in asdict(self).items()}


def exposure_metrics(image: Image) -> ExposureMetrics:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    luma = gray.astype(np.float64) / 255.0
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    scale = 512 / max(image.shape[:2])
    sharp_src = cv2.resize(gray, None, fx=scale, fy=scale) if scale < 1 else gray
    return ExposureMetrics(
        luma_mean=float(luma.mean()),
        luma_median=float(np.median(luma)),
        contrast=float(luma.std()),
        clipped_shadows=float((gray <= 5).mean()),
        clipped_highlights=float((gray >= 250).mean()),
        saturation=float(hsv[..., 1].mean() / 255.0),
        sharpness=float(cv2.Laplacian(sharp_src, cv2.CV_64F).var()),
    )


def dominant_colors(image: Image, k: int = 5) -> list[dict[str, float | str]]:
    """k-means in Lab space on a small version → ``[{"hex": "#aabbcc", "share": 0.31}, …]``."""
    small = cv2.resize(
        image, (96, max(1, int(96 * image.shape[0] / image.shape[1]))), interpolation=cv2.INTER_AREA
    )
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    k = min(k, len(lab))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    cv2.setRNGSeed(7)  # deterministic clustering
    _, labels, centres = cv2.kmeans(
        lab,
        k,
        None,
        criteria,
        3,
        cv2.KMEANS_PP_CENTERS,  # type: ignore[call-overload]
    )
    counts = np.bincount(labels.flatten(), minlength=k)
    bgr = cv2.cvtColor(centres.reshape(1, -1, 3).astype(np.uint8), cv2.COLOR_LAB2BGR).reshape(-1, 3)
    total = float(counts.sum())
    # Empty or identical k-means clusters give duplicate colours: merge them, drop dust.
    shares: dict[str, float] = {}
    for i in np.argsort(-counts):
        if counts[i] > 0:
            key = "#{:02x}{:02x}{:02x}".format(*bgr[i][::-1])
            shares[key] = shares.get(key, 0.0) + float(counts[i]) / total
    return [
        {"hex": key, "share": round(share, 3)}
        for key, share in sorted(shares.items(), key=lambda item: -item[1])
        if share >= 0.005
    ]
