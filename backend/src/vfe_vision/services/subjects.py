"""Where the living subjects are, keyframe by keyframe.

The boxes of the CPU detector, the face detector and the vision model are stored separately
and fused here, when read: a fusion rule can change without redoing any analysis.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from vfe_vision.db.models import Detection as DetectionRow
from vfe_vision.db.models import Keyframe, SubjectScan
from vfe_vision.domain.subjects import Box, Category, Detection, Source, Subject, fuse
from vfe_vision.domain.translation import AS_WRITTEN, Dictionary
from vfe_vision.services.audio_text import StageState, _video, stage_state
from vfe_vision.services.container import AppContainer
from vfe_vision.services.reading import language_of, texts_in

_PRECEDENCE = ("running", "failed", "skipped", "not_run")


@dataclass(frozen=True, slots=True)
class FrameSubjects:
    keyframe_id: str
    t_s: float
    until_s: float | None  # next keyframe (or end of video): the span these positions stand for
    idx: int
    duplicate_of: str | None  # same image as that keyframe: its boxes are repeated here
    subjects: list[Subject]  # empty: looked at, nobody there


@dataclass(frozen=True, slots=True)
class SubjectsView:
    detector: StageState
    vision: StageState
    frames: list[FrameSubjects]
    width: int | None  # displayed size of the video, to turn 0–1 boxes into pixels
    height: int | None

    @property
    def status(self) -> str:
        """ready when some positions are known (or a stage ran fine), else the most telling state
        of the two stages."""
        states = (self.detector.status, self.vision.status)
        if any(f.subjects for f in self.frames) or "ready" in states:
            return "ready"
        return next(s for s in _PRECEDENCE if s in states)

    @property
    def run_status(self) -> str:
        states = (self.detector.status, self.vision.status)
        return next((s for s in _PRECEDENCE[:2] if s in states), states[0])

    @property
    def note(self) -> str | None:
        notes = [s.note for s in (self.detector, self.vision) if s.note]
        return " · ".join(notes) or None


def _detection(row: DetectionRow, tr: Dictionary = AS_WRITTEN) -> Detection | None:
    """A stored box; the vision model's names in the language of ``tr`` (the detectors' are
    codes, named when fused)."""
    box = Box.of(row.box)
    try:
        source, category = Source(row.source), Category(row.category)
    except ValueError:
        return None
    if box is None:
        return None
    points = tuple((float(x), float(y)) for x, y in row.points or ())
    label = tr(row.label) if source == Source.VLM else row.label
    return Detection(source, label, category, box, row.score, row.main, points)


def get_subjects(
    c: AppContainer,
    video_id: str,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
    tr: Dictionary | None = None,
) -> SubjectsView:
    """``tr``: the language of the names (default: the analysis language)."""
    tr = texts_in(c) if tr is None else tr
    language = language_of(c, tr)
    with c.db.read() as session:
        video = _video(session, video_id)
        detector = stage_state(session, video_id, "detections")
        vision = stage_state(session, video_id, "grounding")
        rows = session.execute(
            sa.select(DetectionRow)
            .where(DetectionRow.video_id == video_id)
            .order_by(DetectionRow.t_s, DetectionRow.source, DetectionRow.idx)
        ).scalars()
        by_frame: dict[str, list[Detection]] = {}
        for row in rows:
            if (d := _detection(row, tr)) is not None:
                by_frame.setdefault(row.keyframe_id, []).append(d)
        scans = session.execute(
            sa.select(SubjectScan.keyframe_id, SubjectScan.source).where(
                SubjectScan.video_id == video_id
            )
        ).all()
        keyframes = session.execute(
            sa.select(Keyframe.id, Keyframe.t_s, Keyframe.idx, Keyframe.duplicate_of)
            .where(Keyframe.video_id == video_id)
            .order_by(Keyframe.idx)
        ).all()
        width, height, duration = video.width, video.height, video.duration_s
    vlm_scanned = {frame_id for frame_id, source in scans if source == Source.VLM.value}
    looked = {frame_id for frame_id, _ in scans} | set(by_frame)
    fused = {
        # Without a scan record (older data), VLM boxes still mean the model looked.
        frame_id: fuse(
            by_frame.get(frame_id, []), language,
            vlm_scanned=True if frame_id in vlm_scanned else None,
        )
        for frame_id in looked
    }  # fmt: skip
    frames: list[FrameSubjects] = []
    for i, kf in enumerate(keyframes):
        until = keyframes[i + 1].t_s if i + 1 < len(keyframes) else duration
        # A keyframe stands for its shot until the next one: it covers the interval when its
        # span overlaps it (an interval between two keyframes gets the earlier one).
        if end_s is not None and kf.t_s > end_s:
            continue
        if start_s is not None and until is not None and until <= start_s:
            continue
        subjects = fused.get(kf.duplicate_of or kf.id)
        if subjects is not None:  # an empty list: looked at, nobody there
            frames.append(FrameSubjects(kf.id, kf.t_s, until, kf.idx, kf.duplicate_of, subjects))
    return SubjectsView(detector, vision, frames, width, height)
