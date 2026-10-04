"""Stage ``detections``: people, animals and faces in the keyframes, on the CPU.

D-FINE (COCO) boxes the living beings it knows (people, common mammals, birds) and YuNet the
faces, on the distinct keyframes only. Boxes are normalised to 0–1 in the displayed image. The
vision model adds what COCO does not know (insects…) in the ``grounding`` stage; both are fused
when read.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from vfe_vision.adapters.detection.dfine import ENGINE_VERSION as DFINE_VERSION
from vfe_vision.adapters.detection.dfine import DFine
from vfe_vision.adapters.detection.yunet import ENGINE_VERSION as YUNET_VERSION
from vfe_vision.adapters.detection.yunet import FaceDetector
from vfe_vision.adapters.imaging import read_image
from vfe_vision.adapters.models.catalog import DEFAULTS, spec
from vfe_vision.db.models import Detection as DetectionRow
from vfe_vision.db.models import Keyframe, SubjectScan
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.subjects import COCO_LIVING, Box, Category, Detection, Source, dedupe
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage

DETECTOR_ID = DEFAULTS["detector"]
FACES_ID = DEFAULTS["faces"]
DETECTOR_THREADS = 2
MIN_SCORE = 0.4  # stored; a box alone must reach 0.5 at fusion (domain.subjects)
NMS_IOU = 0.7
INSTALL = "vfe models subjects"

_lock = threading.Lock()
_loaded: dict[Path, DFine] = {}


def _detector(model_dir: Path) -> DFine:
    """One ONNX session per model folder, shared by the worker's threads."""
    with _lock:
        if model_dir not in _loaded:
            _loaded.clear()
            _loaded[model_dir] = DFine.from_dir(model_dir, threads=DETECTOR_THREADS)
        return _loaded[model_dir]


def distinct_keyframes(ctx: StageContext) -> list[tuple[str, float, str]]:
    """Distinct keyframes (id, time, image path), in time order: duplicates show the same."""
    with ctx.tools.db.read() as session:
        return [
            (row.id, row.t_s, row.image_path)
            for row in session.execute(
                sa.select(Keyframe.id, Keyframe.t_s, Keyframe.image_path)
                .where(Keyframe.video_id == ctx.video.id, Keyframe.duplicate_of.is_(None))
                .order_by(Keyframe.idx)
            )
        ]


def keyframes_fact(ctx: StageContext) -> dict[str, Any]:
    """The keyframe rows the boxes belong to: new keyframes (a forced extraction) have new ids,
    and their deletion took the old boxes with them."""
    ids = [frame_id for frame_id, _, _ in distinct_keyframes(ctx)]
    return {"keyframes": hashlib.sha1("|".join(ids).encode(), usedforsecurity=False).hexdigest()}


def replace_detections(
    ctx: StageContext,
    sources: set[Source],
    rows: list[DetectionRow],
    scans: list[SubjectScan],
) -> None:
    """Swap this video's boxes of the given sources, and the record of the keyframes they
    looked at, in one transaction."""
    names = [s.value for s in sources]
    with ctx.tools.db.write() as session:
        session.execute(
            sa.delete(DetectionRow).where(
                DetectionRow.video_id == ctx.video.id, DetectionRow.source.in_(names)
            )
        )
        session.execute(
            sa.delete(SubjectScan).where(
                SubjectScan.video_id == ctx.video.id, SubjectScan.source.in_(names)
            )
        )
        session.add_all(rows)
        session.add_all(scans)


class DetectionsStage(SyncStage):
    name = "detections"
    version = 2
    family = StageFamily.IMAGE
    requires = ("keyframes",)
    optional = True
    facts_name_rows = True

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        store = ctx.tools.models
        return {
            "detector": store.identity(spec(DETECTOR_ID)) if store is not None else None,
            "faces": store.identity(spec(FACES_ID)) if store is not None else None,
            "engines": [DFINE_VERSION, YUNET_VERSION],
            "min_score": MIN_SCORE,
        }

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        return keyframes_fact(ctx)

    def run(self, ctx: StageContext) -> StageOutcome:
        store = ctx.tools.models
        detector_dir = store.installed(spec(DETECTOR_ID)) if store is not None else None
        faces_dir = store.installed(spec(FACES_ID)) if store is not None else None
        if store is None or detector_dir is None or faces_dir is None:
            return StageOutcome.skipped(
                f"Détecteur de sujets non installé : lancez « {INSTALL} » puis « Compléter »"
            )
        detector = _detector(detector_dir)
        faces = FaceDetector.from_file(faces_dir / "yunet.onnx")
        model = " + ".join(
            identity or item
            for item, identity in (
                (DETECTOR_ID, store.identity(spec(DETECTOR_ID))),
                (FACES_ID, store.identity(spec(FACES_ID))),
            )
        )
        frames = distinct_keyframes(ctx)
        rows: list[DetectionRow] = []
        scans: list[SubjectScan] = []
        with_subjects = face_count = unreadable = 0
        for position, (frame_id, t_s, rel) in enumerate(frames):
            ctx.cancel.raise_if_cancelled()
            try:
                image = read_image(ctx.tools.artifacts.resolve(rel))
            except (OSError, ValueError) as exc:  # missing or broken keyframe file: skip it
                unreadable += 1
                ctx.log.warning("keyframe unreadable", t_s=t_s, error=str(exc))
                continue
            found = dedupe(
                (
                    Detection(Source.DETECTOR, raw.label, COCO_LIVING[raw.label], box, raw.score)
                    for raw in detector.detect(image, keep=COCO_LIVING, min_score=MIN_SCORE)
                    if (box := Box.of(raw.box)) is not None
                ),
                threshold=NMS_IOU,
            )
            found += [
                Detection(Source.FACES, "face", Category.FACE, box, face.score, points=face.points)
                for face in faces.detect(image)
                if (box := Box.of(face.box)) is not None
            ]
            for idx, d in enumerate(found):
                rows.append(
                    DetectionRow(
                        keyframe_id=frame_id, video_id=ctx.video.id, t_s=t_s,
                        source=d.source.value, idx=idx, label=d.label, category=d.category.value,
                        box=d.box.rounded(), score=round(d.score or 0.0, 4), main=False,
                        points=[[round(x, 4), round(y, 4)] for x, y in d.points] or None,
                        model=model,
                    )
                )  # fmt: skip
            boxes = sum(1 for d in found if d.source == Source.DETECTOR)
            faces_here = len(found) - boxes
            scans += [
                SubjectScan(keyframe_id=frame_id, source=source.value, video_id=ctx.video.id,
                            model=model, found=count)
                for source, count in ((Source.DETECTOR, boxes), (Source.FACES, faces_here))
            ]  # fmt: skip
            with_subjects += int(boxes > 0)
            face_count += faces_here
            ctx.progress((position + 1) / max(1, len(frames)), None)
        replace_detections(ctx, {Source.DETECTOR, Source.FACES}, rows, scans)
        return StageOutcome.ok(
            keyframes=len(frames),
            with_subjects=with_subjects,
            boxes=sum(1 for r in rows if r.source == Source.DETECTOR.value),
            faces=face_count,
            unreadable=unreadable,
        )
