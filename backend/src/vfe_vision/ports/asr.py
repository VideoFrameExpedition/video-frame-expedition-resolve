"""Port: speech recognition of one audio track (faster-whisper on the CPU).

The implementation runs in a disposable child process (adapters/asr); the pipeline only sees
this interface, so tests inject a fake. Times are seconds of the source file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from vfe_vision.core.cancel import CancelToken

AsrStatus = Literal["ok", "no_speech"]
AsrProgress = Callable[[float, str | None], None]  # fraction 0..1, French message (or None)


@dataclass(frozen=True, slots=True)
class AsrRequest:
    """One transcription.

    Give ``audio_path`` (a WAV already decoded for speech: 16 kHz mono PCM, read as is) or
    ``video_path`` (the child decodes track ``audio_stream`` with ffmpeg, aligned on the
    container's time 0). ``language`` is a Whisper code to force (a folder hint), never the
    container tag; ``None`` lets Whisper detect it. Under ``speech_gate_s`` seconds of speech
    (Silero VAD) the result is ``no_speech`` and the model is not even loaded.
    """

    audio_path: Path | None
    video_path: Path | None
    model_dir: Path
    language: str | None
    threads: int
    beam_size: int = 5
    vad_min_silence_ms: int = 500
    speech_gate_s: float = 1.0
    audio_stream: int = 0
    coverage_pass: bool = True  # re-transcribe speech the first pass dropped
    ffmpeg_path: str = "ffmpeg"
    # GPU: only when the gate lent it; cuBLAS 12 comes from the model store.
    device: Literal["cpu", "cuda"] = "cpu"
    cuda_dll_dir: Path | None = None
    gpu_headroom_mib: int = 0  # memory the run still takes after the warm-up (decoding)
    gpu_min_free_mib: int = 0  # free memory required right before CUDA starts (need + margin)

    @property
    def compute_type(self) -> str:
        return "int8_float16" if self.device == "cuda" else "int8"


@dataclass(frozen=True, slots=True)
class AsrWord:
    start: float
    end: float
    text: str  # as Whisper spells it, with its leading space
    probability: float


@dataclass(frozen=True, slots=True)
class AsrSegment:
    idx: int
    start: float
    end: float
    text: str
    avg_logprob: float  # per 30 s window, like the next three
    no_speech_prob: float
    compression_ratio: float
    temperature: float
    language: str | None
    words: tuple[AsrWord, ...]
    suspect: bool  # domain.transcript guards: kept, but out of search/subtitles/hints
    second_pass: bool


@dataclass(frozen=True, slots=True)
class AsrLanguage:
    code: str  # Whisper code
    probability: float


@dataclass(frozen=True, slots=True)
class AsrResult:
    status: AsrStatus
    language: str | None  # None when no speech
    language_probability: float | None
    languages: tuple[AsrLanguage, ...]  # file-level detection, best first (top 5)
    duration_s: float
    speech_s: float  # VAD speech, unpadded
    segments: tuple[AsrSegment, ...]
    model: str
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """The trusted text (suspect segments left out)."""
        return " ".join(s.text.strip() for s in self.segments if not s.suspect and s.text.strip())


class GpuFallbackError(Exception):
    """A GPU transcription gave up before its result: run the same request on the CPU.

    ``disable`` asks the caller not to try the GPU again in this session (the CUDA runtime
    does not load); otherwise the GPU was only short of memory this time.
    """

    def __init__(self, reason: str, *, disable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.disable = disable


class SpeechRecognizer(Protocol):
    def transcribe(
        self, request: AsrRequest, *, cancel: CancelToken, progress: AsrProgress
    ) -> AsrResult:
        """Raise ``CancelledError`` when cancelled, ``VfeError`` (``ExternalToolError``) on
        failure, ``GpuFallbackError`` when a ``cuda`` request must be redone on the CPU; nothing
        is left running either way."""
        ...
