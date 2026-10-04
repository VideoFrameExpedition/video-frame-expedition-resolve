"""What is heard, said and written in a video: sound scene, transcript, on-screen text.

Every view says why a part is missing (not analysed yet, no audio, model not downloaded…),
from the stage's latest run, like the capture context does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath

import sqlalchemy as sa
from sqlalchemy.orm import Session, selectinload

from vfe_vision.adapters.models.catalog import DEFAULTS, spec
from vfe_vision.adapters.models.store import ModelStore
from vfe_vision.core.errors import NotFoundError
from vfe_vision.db.models import (
    AudioScene,
    AudioSegment,
    OcrText,
    StageRun,
    Transcript,
    TranscriptSegment,
    Video,
)
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.transcript import Cue, build_cues
from vfe_vision.domain.translation import file_suffix
from vfe_vision.services.container import AppContainer


@dataclass(frozen=True, slots=True)
class StageState:
    status: str  # ready | not_run | skipped | failed | running
    note: str | None = None


def stage_state(session: Session, video_id: str, stage: str) -> StageState:
    run = session.execute(
        sa.select(StageRun)
        .where(StageRun.video_id == video_id, StageRun.stage == stage)
        .where(StageRun.status != StageStatus.CACHED)
        .order_by(StageRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if run is None:
        return StageState("not_run")
    if run.status == StageStatus.SUCCEEDED:
        return StageState("ready")
    if run.status == StageStatus.SKIPPED:
        return StageState("skipped", run.skip_reason)
    if run.status == StageStatus.RUNNING:
        return StageState("running")
    return StageState("failed", run.error or run.status.value)


def _video(session: Session, video_id: str) -> Video:
    video = session.get(Video, video_id)
    if video is None:
        raise NotFoundError(f"Vidéo introuvable : {video_id}")
    return video


@dataclass(frozen=True, slots=True)
class AudioView:
    state: StageState
    scene: AudioScene | None = None
    segments: list[AudioSegment] = field(default_factory=list)
    language: str = "fr"  # of the sound names (AudioSet names are English)
    ced_available: bool = False  # installed now: « Update » can add its opinion


def get_audio(c: AppContainer, video_id: str, language: str | None = None) -> AudioView:
    """``language``: of the sound names (default: the analysis language)."""
    language = language or load_preferences(c.db).language
    ced = ModelStore(c.settings.models_dir).installed(spec(DEFAULTS["ced"])) is not None
    with c.db.read() as session:
        _video(session, video_id)
        state = stage_state(session, video_id, "audio_events")
        scene = session.get(AudioScene, video_id)
        segments = list(
            session.execute(
                sa.select(AudioSegment)
                .where(AudioSegment.video_id == video_id)
                .order_by(AudioSegment.start_s, AudioSegment.id)
            ).scalars()
        )
    return AudioView(state, scene, segments, language, ced)


@dataclass(frozen=True, slots=True)
class TranscriptView:
    state: StageState
    transcript: Transcript | None = None
    video: Video | None = None


def get_transcript(c: AppContainer, video_id: str) -> TranscriptView:
    with c.db.read() as session:
        video = _video(session, video_id)
        state = stage_state(session, video_id, "transcript")
        transcript = session.execute(
            sa.select(Transcript)
            .options(selectinload(Transcript.segments))
            .where(Transcript.video_id == video_id)
        ).scalar_one_or_none()
    return TranscriptView(state, transcript, video)


@dataclass(frozen=True, slots=True)
class OcrView:
    state: StageState
    lines: list[OcrText] = field(default_factory=list)


def get_ocr(c: AppContainer, video_id: str) -> OcrView:
    with c.db.read() as session:
        _video(session, video_id)
        state = stage_state(session, video_id, "ocr")
        lines = list(
            session.execute(
                sa.select(OcrText)
                .where(OcrText.video_id == video_id)
                .order_by(OcrText.t_s, OcrText.idx)
            ).scalars()
        )
    return OcrView(state, lines)


@dataclass(frozen=True, slots=True)
class _Word:
    start: float
    end: float
    text: str
    probability: float


@dataclass(frozen=True, slots=True)
class _Segment:
    start: float
    end: float
    text: str
    words: tuple[_Word, ...]
    suspect: bool


@dataclass(frozen=True, slots=True)
class Subtitles:
    name: str  # file name without extension, for the download: « <stem>_FR »
    cues: list[Cue]
    language: str | None = None  # spoken (the user's choice, else Whisper's), when known


def subtitles(c: AppContainer, video_id: str) -> Subtitles:
    """Subtitle cues of the reliable segments (suspect ones left out), times of the source."""
    with c.db.read() as session:
        video = _video(session, video_id)
        rows = session.execute(
            sa.select(TranscriptSegment)
            .where(TranscriptSegment.video_id == video_id)
            .order_by(TranscriptSegment.idx)
        ).scalars()
        segments = [
            _Segment(
                row.start_s,
                row.end_s,
                row.text,
                tuple(
                    _Word(float(w[0]), float(w[1]), str(w[2]), float(w[3]))
                    for w in row.words
                    if isinstance(w, list) and len(w) == 4
                ),
                row.suspect,
            )
            for row in rows
        ]
        spoken = (
            video.transcript_language
            or session.execute(
                sa.select(Transcript.language).where(Transcript.video_id == video_id)
            ).scalar_one_or_none()
        )
        name = PurePath(video.filename).stem + (f"_{file_suffix(spoken)}" if spoken else "")
    cues = build_cues(segments)
    if not cues:
        raise NotFoundError("Pas de transcription pour cette vidéo.")
    return Subtitles(name, cues, spoken)
