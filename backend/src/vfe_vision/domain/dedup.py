"""Near-duplicate frame detection on small grayscale thumbnails (pure numpy).

A 64-bit difference hash (dHash) of each frame is compared against a window of the frames kept
last: a frame already seen does not come back because another one slipped in between (A-B-A
alternation).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

HASH_SIZE = 8  # 64-bit hash


def dhash(gray: npt.NDArray[np.uint8]) -> int:
    """64-bit difference hash of a grayscale image already resized to 9×8 pixels."""
    if gray.shape != (HASH_SIZE, HASH_SIZE + 1):
        raise ValueError(f"dhash attend une image 9×8, reçu {gray.shape[::-1]}")
    bits = gray[:, 1:] > gray[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclass
class DuplicateFilter:
    """Keep a frame unless it is within ``max_distance`` bits of one of the last kept frames."""

    max_distance: int = 6
    window: int = 4
    _recent: deque[int] = field(default_factory=deque, init=False)

    def is_duplicate(self, frame_hash: int) -> bool:
        return any(hamming(frame_hash, kept) <= self.max_distance for kept in self._recent)

    def keep(self, frame_hash: int) -> None:
        self._recent.append(frame_hash)
        while len(self._recent) > self.window:
            self._recent.popleft()
