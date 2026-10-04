"""The analysis files next to the videos, written on demand (« Export »)."""

from __future__ import annotations

from vfe_vision.pipeline.sidecar.writer import SidecarResult, write_sidecar
from vfe_vision.services.container import AppContainer


def export_sidecars(c: AppContainer, video_ids: list[str]) -> list[SidecarResult]:
    """Write the analysis file of each video now, whatever the setting (an explicit request).
    One result per video, in the order asked; a failure never stops the others."""
    return [write_sidecar(c.db, video_id) for video_id in dict.fromkeys(video_ids)]
