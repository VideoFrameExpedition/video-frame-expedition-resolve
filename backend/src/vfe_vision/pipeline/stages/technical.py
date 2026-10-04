"""Stage ``technical``: per-keyframe exposure, colours and measured colour temperature."""

from __future__ import annotations

import bisect
import hashlib
from typing import Any

import sqlalchemy as sa

from vfe_vision.adapters.imaging import read_image
from vfe_vision.db.models import Keyframe, Shot
from vfe_vision.domain.color import dominant_colors, estimate_cct, exposure_metrics, kelvin_label
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage

# The analysis pass samples at 8 fps, so a shot may start up to half a sample after the exact
# cut; a scene keyframe (first frame of the new shot) must still land in the new shot.
SHOT_START_TOLERANCE_S = 0.5 / 8


class TechnicalStage(SyncStage):
    name = "technical"
    version = 2
    family = StageFamily.IMAGE
    requires = ("keyframes", "analysis_pass")
    facts_name_rows = True

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        return {"shot_tolerance_s": SHOT_START_TOLERANCE_S}

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        # The shot rows it links keyframes to (a forced analysis_pass alone recreates them).
        with ctx.tools.db.read() as session:
            ids = session.execute(
                sa.select(Shot.id).where(Shot.video_id == ctx.video.id).order_by(Shot.idx)
            ).scalars()
            digest = hashlib.sha1("|".join(ids).encode(), usedforsecurity=False).hexdigest()
        return {"shots": digest}

    def run(self, ctx: StageContext) -> StageOutcome:
        with ctx.tools.db.read() as session:
            frames = [
                (row.id, row.t_s, row.image_path, row.selection_reason)
                for row in session.execute(
                    sa.select(
                        Keyframe.id, Keyframe.t_s, Keyframe.image_path, Keyframe.selection_reason
                    )
                    .where(Keyframe.video_id == ctx.video.id)
                    .order_by(Keyframe.idx)
                )
            ]
            shots = [
                (row.start_s, row.id)
                for row in session.execute(
                    sa.select(Shot.start_s, Shot.id)
                    .where(Shot.video_id == ctx.video.id)
                    .order_by(Shot.idx)
                )
            ]
        starts = [start for start, _ in shots]
        updates: list[dict[str, object]] = []
        for index, (frame_id, t_s, rel, reason) in enumerate(frames):
            ctx.cancel.raise_if_cancelled()
            image = read_image(ctx.tools.artifacts.resolve(rel))
            exposure = exposure_metrics(image)
            cct = estimate_cct(image)
            tolerance = SHOT_START_TOLERANCE_S if reason == "scene" else 0.0
            position = bisect.bisect_right(starts, t_s + tolerance + 1e-6) - 1
            updates.append(
                {
                    "id": frame_id,
                    "shot_id": shots[position][1] if position >= 0 else None,
                    "sharpness": round(exposure.sharpness, 2),
                    "metrics": {
                        **exposure.as_dict(),
                        "cct_k": round(cct) if cct else None,
                        "cct_label": kelvin_label(cct) if cct else None,
                        "colors": dominant_colors(image),
                    },
                }
            )
            ctx.progress((index + 1) / max(1, len(frames)), None)
        with ctx.tools.db.write() as session:
            for values in updates:
                session.execute(
                    sa.update(Keyframe)
                    .where(Keyframe.id == values["id"])
                    .values(
                        shot_id=values["shot_id"],
                        sharpness=values["sharpness"],
                        metrics=values["metrics"],
                    )
                )
        return StageOutcome.ok(keyframes=len(updates))
