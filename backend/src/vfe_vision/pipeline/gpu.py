"""Lending the GPU to short tasks without ever squeezing the vision model.

The vision model loaded in LM Studio owns the video memory. Another task may use
the GPU only when the memory the model leaves free covers the task plus a safety margin, one
task at a time; otherwise it runs on the CPU. On Windows an over-committed GPU silently moves
memory to system RAM, which would slow the vision model down: the margin is what prevents it.
On a Mac the memory is unified and no probe reads it: nothing is lent, the CPU decodes, and
the vision model keeps whatever LM Studio gives it.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import structlog

from vfe_vision.db.models import VideoMetadata
from vfe_vision.ports.gpu import VramProbe

if TYPE_CHECKING:
    from vfe_vision.pipeline.stage import StageContext

log = structlog.get_logger(__name__)

MIB = 1024 * 1024
# Video decoding (NVDEC): CUDA context and decoder session, plus the surfaces holding reference
# and queued frames, counted at 16 bits per sample (10-bit sources) to stay on the safe side.
DECODE_OVERHEAD_MIB = 300
DECODE_SURFACES = 12


def decode_need_mib(width: int | None, height: int | None) -> int:
    """Video memory a hardware decode of a ``width``×``height`` video may take."""
    pixels = (width or 3840) * (height or 2160)
    frame_bytes = pixels * 3  # 4:2:0 = 1.5 samples per pixel, 2 bytes per sample
    return DECODE_OVERHEAD_MIB + math.ceil(DECODE_SURFACES * frame_bytes / MIB)


class GpuGate:
    def __init__(self, probe: VramProbe | None = None, *, margin_mib: int = 1024) -> None:
        self.probe = probe
        self.margin_mib = margin_mib
        self._lock = threading.Lock()

    @contextmanager
    def borrow(self, need_mib: int, *, enabled: bool = True) -> Iterator[bool]:
        """``True``: the caller may use the GPU for up to ``need_mib`` until it exits the block.
        ``False``: it must use the CPU. A second borrower never waits: it gets the CPU."""
        if not enabled or self.probe is None or not self._lock.acquire(blocking=False):
            yield False
            return
        try:
            free = self.probe.free_mib()
            allowed = free is not None and free >= need_mib + self.margin_mib
            log.info(
                "gpu decision",
                gpu=allowed,
                free_mib=free,
                need_mib=need_mib,
                margin_mib=self.margin_mib,
            )
            yield allowed
        finally:
            self._lock.release()


def video_stream_facts(ctx: StageContext) -> dict[str, Any]:
    """Codec, pixel format and rotation of the video stream, as the probe stage stored them."""
    with ctx.tools.db.read() as session:
        meta = session.get(VideoMetadata, ctx.video.id)
        probe = ((meta.normalized or {}) if meta else {}).get("probe") or {}
    return {key: probe.get(key) for key in ("video_codec", "pix_fmt", "rotation", "flipped")}
