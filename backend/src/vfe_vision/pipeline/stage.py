"""Stage contract: what every analysis step declares and returns."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

import anyio
import structlog

from vfe_vision.adapters.exiftool.runner import ExifTool
from vfe_vision.adapters.ffmpeg.tools import Ffmpeg
from vfe_vision.adapters.lmstudio.budget import TokenBudget
from vfe_vision.adapters.lmstudio.client import LmStudioClient
from vfe_vision.adapters.models.store import ModelStore
from vfe_vision.core.cancel import CancelToken
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.pipeline.gpu import GpuGate
from vfe_vision.ports.asr import SpeechRecognizer
from vfe_vision.ports.embeddings import EmbedderSource, no_embedder
from vfe_vision.ports.geocoding import Gazetteer, ReverseGeocoder
from vfe_vision.ports.weather import WeatherProvider
from vfe_vision.storage.artifacts import ArtifactStore


class Resource(StrEnum):
    CPU = "cpu"  # decoding, image processing, ONNX models
    GPU = "gpu"  # reserved; the GPU belongs to the vision model, lent by GpuGate
    LMSTUDIO = "lmstudio"  # requests are admitted by the token budget
    NETWORK = "network"  # online services (Nominatim, Open-Meteo): throttled, never the CPU pool
    ASR = "asr"  # speech recognition: one child process at a time (all CPU cores, 2-4 GB RAM)


# A skip that only waits for LM Studio (``StageOutcome.waiting_for_lmstudio``): the worker
# completes the analysis on its own once a vision model answers (``jobs.resume``).
WAITS_FOR_LMSTUDIO = "lmstudio"


class StageFamily(StrEnum):
    """What a stage looks at, to group the stages where people pick them."""

    FILE = "file"  # the file itself: container, metadata, viewing copy
    CONTEXT = "context"  # place, weather and sun at the time of shooting
    IMAGE = "image"  # shots, keyframes and local image models
    SOUND = "sound"  # levels, sounds heard and speech
    VISION = "vision"  # the vision model in LM Studio
    LANGUAGE = "language"  # the analyses in French and in English
    SEARCH = "search"  # the search index, built from all of the above
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class StageOutcome:
    status: StageStatus
    summary: dict[str, Any] = field(default_factory=dict)
    skip_reason: str | None = None
    retryable: bool = False  # a skipped stage worth retrying later (e.g. LM Studio was down)

    @classmethod
    def ok(cls, **summary: Any) -> StageOutcome:
        return cls(StageStatus.SUCCEEDED, summary)

    @classmethod
    def skipped(
        cls, reason: str, *, retryable: bool = False, permanent: bool = False, **summary: Any
    ) -> StageOutcome:
        """Nothing to do. ``retryable``: for now only (LM Studio down…). ``permanent``: because
        of the video itself (no audio track, no position…): only new video data changes it,
        so an ordinary analysis never comes back for it."""
        if permanent:
            summary = {**summary, "permanent": True}
        return cls(StageStatus.SKIPPED, summary, reason, retryable)

    @classmethod
    def waiting_for_lmstudio(cls, reason: str) -> StageOutcome:
        """Skipped for now: LM Studio does not answer, or has no model loaded. Retried by the
        next analysis, and by the worker itself as soon as a vision model answers."""
        return cls.skipped(reason, retryable=True, waits_for=WAITS_FOR_LMSTUDIO)

    @classmethod
    def provisional(cls, note: str | None = None, **summary: Any) -> StageOutcome:
        """A complete result that depends on a setting (e.g. offline mode, a model not installed
        yet): kept while the settings are unchanged, redone when they change, even in an
        ordinary analysis. ``note`` says what it lacks, shown with the stage."""
        return cls(StageStatus.SUCCEEDED, {**summary, "provisional": True}, note)

    @classmethod
    def degraded(cls, note: str, **summary: Any) -> StageOutcome:
        """A usable but lesser result (e.g. an offline fallback during an outage): dependents
        run on it, and the stage runs again at the next analysis."""
        return cls(StageStatus.SUCCEEDED, summary, note, retryable=True)


@dataclass(slots=True)
class Toolbox:
    """Long-lived collaborators shared by all stages of a worker process."""

    db: Database
    artifacts: ArtifactStore
    ffmpeg: Ffmpeg
    exiftool: ExifTool
    lmstudio: LmStudioClient
    lm_budget: TokenBudget
    weather: WeatherProvider
    geocoder: ReverseGeocoder  # online (Nominatim)
    gazetteer: Gazetteer  # offline fallback (GeoNames)
    gpu: GpuGate = field(default_factory=GpuGate)  # none by default: everything on the CPU
    models: ModelStore | None = None  # downloaded models (vfe models); None: none installed
    asr: SpeechRecognizer | None = None  # speech recognition (Whisper child process)
    asr_threads: int = 4
    embedder: EmbedderSource = no_embedder  # the search by meaning (None: not installed)


@dataclass(slots=True)
class VideoRef:
    id: str
    path: Path
    filename: str
    fingerprint: str
    duration_s: float | None = None
    root_focus: str | None = None
    root_clock_offset_s: int | None = None
    root_timezone: str | None = None
    root_latitude: float | None = None
    root_longitude: float | None = None


ProgressFn = Callable[[float, str | None], None]


@dataclass(slots=True)
class StageContext:
    video: VideoRef
    prefs: AnalysisPreferences
    tools: Toolbox
    cancel: CancelToken
    progress: ProgressFn
    log: structlog.stdlib.BoundLogger
    job_focus: str | None = None
    # The current stage's input facts, as read by the runner for its keys: a stage that waited
    # for its resource works on these, so that its result matches the keys it is stored under.
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def focus(self) -> str | None:
        """Analysis focus: job > folder > global preference."""
        return self.job_focus or self.video.root_focus or self.prefs.analysis_focus


class Stage(ABC):
    name: ClassVar[str]
    version: ClassVar[int]
    requires: ClassVar[tuple[str, ...]] = ()
    # Soft dependencies only order execution (never block, never chain keys): a stage that reads
    # facts produced by one of them puts those facts in ``input_facts`` so that unrelated
    # upstream changes (e.g. a better capture-time rule) do not redo expensive work.
    after: ClassVar[tuple[str, ...]] = ()
    resource: ClassVar[Resource] = Resource.CPU
    family: ClassVar[StageFamily] = StageFamily.OTHER
    optional: ClassVar[bool] = False  # failure does not fail the job
    uses_focus: ClassVar[bool] = False  # a new analysis focus is a request to redo this stage
    # Its input facts name rows of other stages by their ids (keyframes, shots). An imported
    # analysis file may give those rows new ids: the input key of the imported result is then
    # computed again from the imported rows.
    facts_name_rows: ClassVar[bool] = False
    # Its input facts read texts of other stages (captions, place names): an analysis file
    # gives them in its own language, so its input key is computed again from them.
    facts_read_texts: ClassVar[bool] = False
    # Its result is not in the analysis file: a file it cannot hold (the viewing copy), or data
    # derived from the others (the search index). Its success is neither written there nor
    # imported (older files): the stage runs again after an import.
    rebuilt_after_import: ClassVar[bool] = False

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        """Settings that change this stage's output (part of the cache key).

        A change here alone keeps existing results unless an update is requested.
        """
        return {}

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        """Facts read from the video's own data: upstream results, file name, sidecars, the
        folder's corrections (part of the cache key).

        When they change, existing results no longer describe the video: the stage runs again
        even when earlier results are otherwise kept.
        """
        return {}

    async def resolve_config(self, ctx: StageContext) -> dict[str, Any]:
        """Settings only known at run time (e.g. the model loaded in LM Studio)."""
        return {}

    def precheck(self, ctx: StageContext) -> StageOutcome | None:
        """A cheap decision taken before waiting for the stage's resource: an outcome (a skip:
        nothing to transcribe, model missing…) ends the stage without queueing for it."""
        return None

    @abstractmethod
    async def execute(self, ctx: StageContext) -> StageOutcome: ...


class SyncStage(Stage):
    """A stage whose work is blocking (ffmpeg, OpenCV…): run in a worker thread."""

    async def execute(self, ctx: StageContext) -> StageOutcome:
        return await anyio.to_thread.run_sync(self.run, ctx)

    @abstractmethod
    def run(self, ctx: StageContext) -> StageOutcome: ...


def cache_key(
    stage: Stage, *, fingerprint: str, config: dict[str, Any], upstream: dict[str, str]
) -> str:
    """Chained key: any change upstream (or in the stage version/config) invalidates the stage."""
    material = {
        "stage": stage.name,
        "version": stage.version,
        "fingerprint": fingerprint,
        "config": config,
        "upstream": dict(sorted(upstream.items())),
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def input_key(stage: Stage, *, fingerprint: str, facts: dict[str, Any]) -> str:
    """Key of the video data a result was computed from (settings and versions excluded)."""
    material = {"stage": stage.name, "fingerprint": fingerprint, "facts": facts}
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
