"""Video memory as reported by the driver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class VramProbe(Protocol):
    def free_mib(self) -> int | None:
        """Free memory of the first GPU in MiB; ``None`` when unknown (no NVIDIA GPU or driver)."""
        ...


@dataclass(frozen=True, slots=True)
class VramUsage:
    """Memory of the first GPU, in MiB."""

    used: int
    free: int
    total: int


class VramMeter(Protocol):
    def usage(self) -> VramUsage | None:
        """Used, free and total memory of the first GPU; ``None`` when unknown."""
        ...
