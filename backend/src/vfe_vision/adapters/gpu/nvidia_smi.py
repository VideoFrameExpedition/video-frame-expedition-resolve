"""Free VRAM read with ``nvidia-smi``: signed by NVIDIA and installed with the driver, so nothing
new is loaded into our processes (Smart App Control) and no CUDA context is created."""

from __future__ import annotations

import time

from vfe_vision.core.errors import ExternalToolError
from vfe_vision.core.procs import run_process
from vfe_vision.ports.gpu import VramUsage

TIMEOUT_S = 5
RETRY_AFTER_S = 300  # after a failure (hung driver…), the CPU is used without asking again


class NvidiaSmi:
    def __init__(self, path: str = "nvidia-smi") -> None:
        self.path = path
        self._absent = False
        self._retry_at = 0.0

    def free_mib(self) -> int | None:
        """Free VRAM of GPU 0 in MiB; None when it cannot be told (then the CPU decodes)."""
        if self._absent or time.monotonic() < self._retry_at:
            return None
        try:
            result = run_process(
                [self.path, "--id=0", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                timeout_s=TIMEOUT_S,
                tool_name="nvidia-smi",
            )
        except ExternalToolError as exc:
            if "introuvable" in exc.detail:  # no NVIDIA driver: never ask again
                self._absent = True
            self._retry_at = time.monotonic() + RETRY_AFTER_S
            return None
        except OSError:  # not startable (access denied…): same as no answer
            self._retry_at = time.monotonic() + RETRY_AFTER_S
            return None
        try:
            return int(result.stdout_text.strip().splitlines()[0])
        except (IndexError, ValueError):
            return None

    def usage(self) -> VramUsage | None:
        """Used, free and total VRAM of GPU 0 in MiB (the model bench measures what a model
        takes); None when it cannot be told. Never cached: asked a few times per
        model, and a failure here must not stop the GPU lending of ``free_mib``."""
        if self._absent:
            return None
        try:
            result = run_process(
                [
                    self.path,
                    "--id=0",
                    "--query-gpu=memory.used,memory.free,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                timeout_s=TIMEOUT_S,
                tool_name="nvidia-smi",
            )
        except (ExternalToolError, OSError):
            return None
        try:
            used, free, total = (
                int(value) for value in result.stdout_text.strip().splitlines()[0].split(",")
            )
        except (IndexError, ValueError):
            return None
        return VramUsage(used=used, free=free, total=total)
