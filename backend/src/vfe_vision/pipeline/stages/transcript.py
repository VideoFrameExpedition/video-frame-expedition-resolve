"""Stage ``transcript``: what is said (faster-whisper in a disposable child process).

A gate (Silero VAD, in the child) stops before loading the model when nobody speaks. The user
chooses per video: automatic (default), always (even when the gate hears no speech) or never.
Suspect segments (likely hallucinations) are kept but greyed, and left out of the search text,
the subtitles and the hints given to other models.

On the CPU by default. With the « Transcribe speech on the GPU » setting and cuBLAS 12 installed,
the GPU is borrowed when the vision model leaves enough memory free; any GPU
trouble redoes the file on the CPU. The device is provenance, never part of the cache key.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from typing import Any

import sqlalchemy as sa
import structlog

from vfe_vision.adapters.models.catalog import DEFAULTS, spec
from vfe_vision.db.models import Transcript, TranscriptSegment, Video
from vfe_vision.domain.languages import whisper_code
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.transcript import TRANSCRIPT_GUARD_VERSION
from vfe_vision.pipeline.stage import Resource, StageContext, StageFamily, StageOutcome, SyncStage
from vfe_vision.ports.asr import AsrRequest, AsrResult, GpuFallbackError, SpeechRecognizer

log = structlog.get_logger(__name__)

TRANSCRIPT_SCHEMA_VERSION = 1
BEAM_SIZE = 5
VAD_MIN_SILENCE_MS = 500
SPEECH_GATE_S = 1.0  # less detected speech than this: no transcription (unless "always")
COVERAGE_PASS = True  # re-transcribe speech the first pass dropped (mixed-language rushes)
DISABLED = "Transcription désactivée dans les réglages"  # the only skip "force" can lift
CUDA_RUNTIME = DEFAULTS["cuda_runtime"]
# Video memory each model takes on the GPU, device-wide (int8_float16, cuda_malloc_async):
# turbo measured 1,244 MiB loaded plus an estimated 190-380 MiB while decoding.
GPU_NEED_MIB = {"whisper/large-v3-turbo": 1664, "whisper/small": 768, "whisper/tiny": 512}
GPU_HEADROOM_MIB = {"whisper/large-v3-turbo": 384, "whisper/small": 192, "whisper/tiny": 128}
# After a failure the GPU is left alone for a while: a runtime that does not load is not tried
# again for hours; short of memory, the next videos do not repeat load, kill and CPU redo.
PAUSE_UNAVAILABLE_S = 6 * 3600.0
PAUSE_SHORT_S = 600.0

_gpu_lock = threading.Lock()
_gpu_pause: tuple[float, str] | None = None  # (monotonic end, reason)


def gpu_paused() -> str | None:
    """Why the GPU is not tried at the moment in this worker (None: it may be)."""
    with _gpu_lock:
        if _gpu_pause is None or time.monotonic() >= _gpu_pause[0]:
            return None
        return _gpu_pause[1]


def _pause_gpu(reason: str, seconds: float) -> None:
    global _gpu_pause  # noqa: PLW0603 - one pause for the worker
    with _gpu_lock:
        _gpu_pause = (time.monotonic() + seconds, reason)


class TranscriptStage(SyncStage):
    name = "transcript"
    version = 1
    family = StageFamily.SOUND
    requires = ("probe",)
    resource = Resource.ASR
    optional = True

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        store = ctx.tools.models
        return {
            "enabled": prefs.transcription,
            # A model installed later changes the key: a "not installed" skip is redone then.
            "model": store.identity(spec(prefs.whisper_model)) if store is not None else None,
            "beam": BEAM_SIZE,
            "vad_min_silence_ms": VAD_MIN_SILENCE_MS,
            "gate_s": SPEECH_GATE_S,
            "coverage": COVERAGE_PASS,
            "guards": TRANSCRIPT_GUARD_VERSION,
        }

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        with ctx.tools.db.read() as session:
            video = session.get_one(Video, ctx.video.id)
            return {
                "has_audio": bool(video.has_audio),
                "mode": video.transcript_mode,  # the user's choice for this video
                "language": video.transcript_language,
            }

    def precheck(self, ctx: StageContext) -> StageOutcome | None:
        """Everything decided without the engine: the stage never queues for it in vain."""
        facts = ctx.facts or self.input_facts(ctx)
        mode = facts["mode"]
        if not facts["has_audio"]:
            _clear(ctx)
            return StageOutcome.skipped("Pas de piste audio", permanent=True)
        if mode == "never":
            _clear(ctx)  # the user's explicit choice for this video
            return StageOutcome.skipped("Transcription désactivée pour cette vidéo", permanent=True)
        if not ctx.prefs.transcription and mode != "always":
            return StageOutcome.skipped(DISABLED)  # transcripts already made are kept
        model = spec(ctx.prefs.whisper_model)
        store = ctx.tools.models
        if store is None or store.installed(model) is None:
            return StageOutcome.skipped(
                f"Modèle Whisper non installé : lancez « {install_command(model.id)} » "
                "puis « Compléter »"
            )
        if ctx.tools.asr is None:
            return StageOutcome.skipped("Moteur de transcription indisponible", retryable=True)
        return None

    def run(self, ctx: StageContext) -> StageOutcome:
        if (skip := self.precheck(ctx)) is not None:
            return skip
        facts = ctx.facts or self.input_facts(ctx)
        mode = facts["mode"]
        model = spec(ctx.prefs.whisper_model)
        store, recognizer = ctx.tools.models, ctx.tools.asr
        model_dir = store.installed(model) if store is not None else None
        if model_dir is None or recognizer is None:  # removed while waiting for the engine
            return StageOutcome.skipped("Modèle ou moteur de transcription indisponible")
        gate = 0.0 if mode == "always" else SPEECH_GATE_S
        request = AsrRequest(
            audio_path=None,
            video_path=ctx.video.path,
            model_dir=model_dir,
            language=whisper_code(facts["language"]),
            threads=ctx.tools.asr_threads,
            beam_size=BEAM_SIZE,
            vad_min_silence_ms=VAD_MIN_SILENCE_MS,
            speech_gate_s=gate,
            coverage_pass=COVERAGE_PASS,
            ffmpeg_path=ctx.tools.ffmpeg.ffmpeg_path,
        )
        result = _transcribe(ctx, recognizer, request, model.id)
        params = {
            "schema_version": TRANSCRIPT_SCHEMA_VERSION,
            "identity": store.identity(model) if store is not None else None,
            "beam_size": BEAM_SIZE,
            "vad_min_silence_ms": VAD_MIN_SILENCE_MS,
            "speech_gate_s": gate,
            "coverage_pass": COVERAGE_PASS,
            "guards": TRANSCRIPT_GUARD_VERSION,
            "forced_language": request.language,
            "stats": result.stats,
        }
        save_transcript(ctx, result, model=model.id, params=params)
        if result.status == "no_speech":
            return StageOutcome.ok(status="no_speech", speech_s=round(result.speech_s, 1))
        return StageOutcome.ok(
            language=result.language,
            segments=len(result.segments),
            suspect=sum(1 for s in result.segments if s.suspect),
            speech_s=round(result.speech_s, 1),
        )


def _transcribe(
    ctx: StageContext, recognizer: SpeechRecognizer, request: AsrRequest, model_id: str
) -> AsrResult:
    """On the GPU when allowed and lent, else (or after any GPU trouble) on the CPU."""
    store = ctx.tools.models
    runtime = store.installed(spec(CUDA_RUNTIME)) if store is not None else None
    paused = gpu_paused() if ctx.prefs.gpu_transcription else None
    wanted = ctx.prefs.gpu_transcription and runtime is not None and paused is None
    fallback: str | None = None
    with ctx.tools.gpu.borrow(GPU_NEED_MIB.get(model_id, 1664), enabled=wanted) as lent:
        if lent:
            need = GPU_NEED_MIB.get(model_id, 1664)
            gpu_request = dataclasses.replace(
                request, device="cuda", cuda_dll_dir=runtime,
                gpu_headroom_mib=GPU_HEADROOM_MIB.get(model_id, 384),
                gpu_min_free_mib=need + ctx.tools.gpu.margin_mib,  # checked again before CUDA
            )  # fmt: skip
            try:
                return recognizer.transcribe(gpu_request, cancel=ctx.cancel, progress=ctx.progress)
            except GpuFallbackError as exc:
                fallback = exc.reason
                log.warning("asr gpu fallback", reason=exc.reason, disable=exc.disable)
                _pause_gpu(exc.reason, PAUSE_UNAVAILABLE_S if exc.disable else PAUSE_SHORT_S)
    if fallback is not None:
        ctx.progress(0.0, "Transcription reprise sur le processeur")
    result = recognizer.transcribe(request, cancel=ctx.cancel, progress=ctx.progress)
    if fallback is not None:
        result.stats["gpu_fallback"] = fallback
    elif paused is not None:
        result.stats["gpu_skipped"] = paused  # the doctor tells why the GPU is left alone
    return result


def install_command(model_id: str) -> str:
    size = model_id.rsplit("/", 1)[-1].replace("large-v3-", "")
    return f"vfe models whisper --size {size}"


def save_transcript(
    ctx: StageContext, result: AsrResult, *, model: str, params: dict[str, Any]
) -> None:
    trusted = [s for s in result.segments if not s.suspect]
    row = Transcript(
        video_id=ctx.video.id,
        source="asr",
        status=result.status,
        model=model,
        language=result.language,
        language_probability=_round(result.language_probability, 4),
        languages=[
            {"language": lang.code, "p": round(lang.probability, 4)} for lang in result.languages
        ],
        duration_s=round(result.duration_s, 3),
        speech_s=round(result.speech_s, 2),
        text=result.text,
        segment_count=len(result.segments),
        word_count=sum(len(s.words) or len(s.text.split()) for s in trusted),
        params=params,
        segments=[
            TranscriptSegment(
                video_id=ctx.video.id,
                idx=index,
                start_s=round(s.start, 3),
                end_s=round(s.end, 3),
                text=s.text.strip(),
                language=s.language,
                avg_logprob=_round(s.avg_logprob, 4),
                no_speech_prob=_round(s.no_speech_prob, 4),
                compression_ratio=_round(s.compression_ratio, 3),
                temperature=s.temperature,
                suspect=s.suspect,
                second_pass=s.second_pass,
                words=[
                    [round(w.start, 3), round(w.end, 3), w.text, round(w.probability, 3)]
                    for w in s.words
                ],
            )
            for index, s in enumerate(sorted(result.segments, key=lambda seg: seg.start))
        ],
    )
    with ctx.tools.db.write() as session:
        _delete(session, ctx.video.id)
        session.add(row)


def _round(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


def _delete(session: sa.orm.Session, video_id: str) -> None:
    # Core deletes: the new segments reuse the same (video_id, idx) pairs.
    session.execute(sa.delete(TranscriptSegment).where(TranscriptSegment.video_id == video_id))
    session.execute(sa.delete(Transcript).where(Transcript.video_id == video_id))


def _clear(ctx: StageContext) -> None:
    with ctx.tools.db.write() as session:
        _delete(session, ctx.video.id)
