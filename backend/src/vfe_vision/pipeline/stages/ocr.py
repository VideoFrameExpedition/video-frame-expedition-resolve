"""Stage ``ocr``: text visible in the keyframes (PP-OCRv6 small on the CPU).

Only distinct keyframes are read (duplicates show the same text). Lines are filtered (score,
length, stray CJK characters from the multilingual dictionary) and stored with their box in
the displayed image, normalised to 0–1. The text is untrusted content.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from vfe_vision.adapters.imaging import read_image
from vfe_vision.adapters.models.catalog import DEFAULTS, spec
from vfe_vision.adapters.ocr.ppocr import ENGINE_VERSION, PpOcr
from vfe_vision.db.models import Keyframe, OcrText
from vfe_vision.domain.ocr import OCR_FILTER_VERSION, filter_line, normalized_box
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage

MODEL_ID = DEFAULTS["ocr"]
OCR_THREADS = 2

_lock = threading.Lock()
_loaded: dict[Path, PpOcr] = {}


def _engine(model_dir: Path) -> PpOcr:
    """One pair of ONNX sessions per model folder, shared by the worker's threads."""
    with _lock:
        if model_dir not in _loaded:
            _loaded.clear()
            _loaded[model_dir] = PpOcr.from_dir(model_dir, threads=OCR_THREADS)
        return _loaded[model_dir]


class OcrStage(SyncStage):
    name = "ocr"
    version = 1
    family = StageFamily.IMAGE
    requires = ("keyframes",)
    optional = True
    facts_name_rows = True

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        store = ctx.tools.models
        return {
            "model": store.identity(spec(MODEL_ID)) if store is not None else None,
            "engine": ENGINE_VERSION,
            "filter": OCR_FILTER_VERSION,
        }

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        # The keyframe rows the lines belong to: new keyframes (a forced extraction) have new
        # ids, and their deletion took the old lines with them.
        ids = [frame_id for frame_id, _, _ in _frames(ctx)]
        return {
            "keyframes": hashlib.sha1("|".join(ids).encode(), usedforsecurity=False).hexdigest()
        }

    def run(self, ctx: StageContext) -> StageOutcome:
        store = ctx.tools.models
        model_dir = store.installed(spec(MODEL_ID)) if store is not None else None
        if model_dir is None:
            return StageOutcome.skipped(
                "Modèle OCR non installé : lancez « vfe models ocr » puis « Compléter »"
            )
        engine = _engine(model_dir)
        identity = (store.identity(spec(MODEL_ID)) if store is not None else None) or MODEL_ID
        frames = _frames(ctx)
        rows: list[OcrText] = []
        with_text = 0
        for position, (frame_id, t_s, rel) in enumerate(frames):
            ctx.cancel.raise_if_cancelled()
            image = read_image(ctx.tools.artifacts.resolve(rel))
            height, width = image.shape[:2]
            kept = 0
            for line in engine.read(image):
                text = filter_line(line.text, line.score)
                if text is None:
                    continue
                rows.append(
                    OcrText(
                        keyframe_id=frame_id, video_id=ctx.video.id, t_s=t_s, idx=kept,
                        text=text, score=round(line.score, 4),
                        box=normalized_box(line.box, width, height), engine=identity,
                    )
                )  # fmt: skip
                kept += 1
            with_text += int(kept > 0)
            ctx.progress((position + 1) / max(1, len(frames)), None)
        with ctx.tools.db.write() as session:
            session.execute(sa.delete(OcrText).where(OcrText.video_id == ctx.video.id))
            session.add_all(rows)
        return StageOutcome.ok(keyframes=len(frames), with_text=with_text, lines=len(rows))


def _frames(ctx: StageContext) -> list[tuple[str, float, str]]:
    """Distinct keyframes (id, time, image path), in time order."""
    with ctx.tools.db.read() as session:
        return [
            (row.id, row.t_s, row.image_path)
            for row in session.execute(
                sa.select(Keyframe.id, Keyframe.t_s, Keyframe.image_path)
                .where(Keyframe.video_id == ctx.video.id, Keyframe.duplicate_of.is_(None))
                .order_by(Keyframe.idx)
            )
        ]
