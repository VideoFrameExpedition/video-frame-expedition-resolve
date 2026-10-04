"""The disposable speech-recognition child: ``python -m vfe_vision.adapters.asr.runner``.

One transcription per process, so the ~2-4 GB of the model go back to Windows at exit and a
cancellation is a plain Job Object kill. Protocol (version :data:`PROTOCOL_VERSION`):

- request: one UTF-8 JSON line on stdin (:func:`encode_request`), read after ``hello``, so the
  parent can put this very process in its Job Object before any work (the venv ``python.exe``
  is a launcher whose child is the real interpreter);
- events: JSON lines on a private copy of stdout, each ``{"v": 3, "event": ...}``, in order
  ``hello{pid}`` -> ``phase{name}`` -> ``audio{duration_s, speech_s}`` -> ``no_speech`` |
  [``ready`` -> the parent's ``load`` line] -> ``loaded`` -> [``warm`` -> the parent's ``go``
  line] -> ``info`` -> (``segment`` + ``progress``)* -> [``pass2`` -> ``segment``*] ->
  ``done{stats}``, or ``error{type, message}`` at any point (exit code 2).

Everything else a library prints (tqdm, huggingface_hub, warnings, native DLLs) ends up on stderr:
file descriptor 1 is re-pointed to stderr before numpy or faster-whisper are imported.

CPU by default: ``device="cpu"`` and ``CUDA_VISIBLE_DEVICES=-1`` (the CTranslate2 wheel is built
with CUDA and would pick the GPU the vision model needs). A ``cuda`` request comes only when the
parent's gate lent the GPU. Once the audio is decoded, ``ready`` lets the parent
check the free memory again (decoding a long file takes a while) and answer ``load``; cuBLAS 12
is then loaded from the model store, the model runs in int8_float16 with the
``cuda_malloc_async`` allocator (the caching one grabbed 2.1 GB at load), and a warm-up runs
before ``warm``: the parent checks the memory once more, then says ``go``. A CUDA failure is
reported as ``CudaUnavailable`` (a runtime or device that cannot work) or ``CudaError``
(transient, out of memory included): the parent redoes the file on the CPU. Offline:
``local_files_only`` and ``HF_HUB_OFFLINE``.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import traceback
import wave
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from vfe_vision.domain import transcript as guards
from vfe_vision.ports.asr import AsrRequest

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    Audio = npt.NDArray[np.float32]

PROTOCOL_VERSION = 3
RUNNER_MODULE = "vfe_vision.adapters.asr.runner"
FORCED_VAD_MIN_S = 1.0  # below this much detected speech, a forced run skips the VAD
SAMPLE_RATE = 16_000
TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
MIN_SPEECH_MS = 250  # removes the VAD blips that became « ありがとう » on phone noise
SPEECH_PAD_MS = 400
ERROR_EXIT_CODE = 2

# Set by the parent too: KMP_BLOCKTIME must be known before libiomp5 loads (spin-waiting OpenMP
# threads burnt a third of the CPU time for nothing).
CHILD_ENV = {
    "HF_HUB_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "KMP_BLOCKTIME": "0",
    "CT2_CUDA_ALLOCATOR": "cuda_malloc_async",
}
CUBLAS_DLLS = ("cublasLt64_12.dll", "cublas64_12.dll")  # cublas64_12 needs cublasLt64_12 first
GO = b"go"
LOAD = b"load"
# A CUDA failure that will not go away by itself: the parent stops trying the GPU for a while.
_PERMANENT_CUDA = (
    "no cuda-capable device", "driver version is insufficient", "do not support",
    "no kernel image", "arch_mismatch", "not_supported", "cannot load", "could not load",
)  # fmt: skip
_dll_dirs: list[Any] = []  # os.add_dll_directory handles, kept for the life of the process


def device_env(device: str) -> dict[str, str]:
    """GPU 0 for a ``cuda`` request, none otherwise (set before CUDA can initialise). PCI bus
    order, so CUDA's device 0 is the card nvidia-smi and NVML call 0 (what the gate reads)."""
    if device == "cuda":
        return {"CUDA_VISIBLE_DEVICES": "0", "CUDA_DEVICE_ORDER": "PCI_BUS_ID"}
    return {"CUDA_VISIBLE_DEVICES": "-1"}


_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_STD_OUTPUT_HANDLE = 0xFFFFFFF5  # (DWORD) -11
_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")  # no tokenizer: network fallback

Emit = Callable[..., None]


class ChildError(Exception):
    """A failure reported to the parent with a stable ``type``."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------- request (parent <-> child)
def encode_request(request: AsrRequest) -> bytes:
    payload = {
        "v": PROTOCOL_VERSION,
        "audio_path": str(request.audio_path) if request.audio_path else None,
        "video_path": str(request.video_path) if request.video_path else None,
        "model_dir": str(request.model_dir),
        "language": request.language,
        "threads": request.threads,
        "beam_size": request.beam_size,
        "vad_min_silence_ms": request.vad_min_silence_ms,
        "speech_gate_s": request.speech_gate_s,
        "audio_stream": request.audio_stream,
        "coverage_pass": request.coverage_pass,
        "ffmpeg_path": request.ffmpeg_path,
        "device": request.device,
        "cuda_dll_dir": str(request.cuda_dll_dir) if request.cuda_dll_dir else None,
        "gpu_headroom_mib": request.gpu_headroom_mib,
        "gpu_min_free_mib": request.gpu_min_free_mib,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"


def decode_request(data: bytes) -> AsrRequest:
    raw = json.loads(data.decode("utf-8"))
    if not isinstance(raw, dict) or raw.get("v") != PROTOCOL_VERSION:
        raise ChildError("BadRequest", "requête de transcription illisible")
    audio, video = raw.get("audio_path"), raw.get("video_path")
    dll_dir = raw.get("cuda_dll_dir")
    return AsrRequest(
        audio_path=Path(audio) if audio else None,
        video_path=Path(video) if video else None,
        model_dir=Path(raw["model_dir"]),
        language=raw.get("language") or None,
        threads=int(raw.get("threads") or 4),
        beam_size=int(raw.get("beam_size", 5)),
        vad_min_silence_ms=int(raw.get("vad_min_silence_ms", 500)),
        speech_gate_s=float(raw.get("speech_gate_s", 1.0)),
        audio_stream=int(raw.get("audio_stream", 0)),
        coverage_pass=bool(raw.get("coverage_pass", True)),
        ffmpeg_path=str(raw.get("ffmpeg_path") or "ffmpeg"),
        device="cuda" if raw.get("device") == "cuda" else "cpu",
        cuda_dll_dir=Path(dll_dir) if dll_dir else None,
        gpu_headroom_mib=int(raw.get("gpu_headroom_mib") or 0),
        gpu_min_free_mib=int(raw.get("gpu_min_free_mib") or 0),
    )


# ---------------------------------------------------------------- process hygiene
def protect_stdout() -> IO[bytes]:
    """Keep a private duplicate of stdout for the protocol and send everything else to stderr
    (Python writers, fd 1 users and, on Windows, DLLs whose C runtime starts later)."""
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        kernel32.SetStdHandle(_STD_OUTPUT_HANDLE, msvcrt.get_osfhandle(2))
    return os.fdopen(proto_fd, "wb", buffering=0)


def make_emitter(stream: IO[bytes]) -> Emit:
    def emit(event: str, **fields: Any) -> None:
        payload = {"v": PROTOCOL_VERSION, "event": event, **fields}
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        stream.write(line.encode("utf-8") + b"\n")

    return emit


def _num(value: float | None, digits: int = 3) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _process_stats() -> dict[str, Any]:
    """Peak memory and CPU time of this process (Windows; empty elsewhere)."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
    me = kernel32.GetCurrentProcess()
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    stats: dict[str, Any] = {}
    if kernel32.K32GetProcessMemoryInfo(me, ctypes.byref(counters), counters.cb):
        stats["peak_working_set_mb"] = round(counters.PeakWorkingSetSize / 2**20)
        stats["peak_private_mb"] = round(counters.PeakPagefileUsage / 2**20)
    times = [wintypes.FILETIME() for _ in range(4)]
    if kernel32.GetProcessTimes(me, *(ctypes.byref(t) for t in times)):
        kernel, user = times[2], times[3]
        ticks = sum((t.dwHighDateTime << 32) | t.dwLowDateTime for t in (kernel, user))
        stats["cpu_s"] = round(ticks / 1e7, 1)
    return stats


def model_label(model_dir: Path) -> str:
    """The model's identity from its manifest (``MODEL.json`` of the model store, or the
    research ``vfe-model.json``), else the folder name."""
    for name in ("MODEL.json", "vfe-model.json"):
        try:
            manifest = json.loads((model_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict):
            if manifest.get("id"):
                return str(manifest["id"])
            if manifest.get("repo"):
                revision = str(manifest.get("revision") or "")
                return f"{manifest['repo']}@{revision[:7]}" if revision else str(manifest["repo"])
    return model_dir.name


# ---------------------------------------------------------------- audio
def _read_speech_wav(path: Path) -> Audio | None:
    """A 16 kHz mono 16-bit WAV read directly; None for any other format (ffmpeg decodes it)."""
    import numpy as np

    try:
        with wave.open(str(path), "rb") as wav:
            if (
                wav.getframerate() != SAMPLE_RATE
                or wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getcomptype() != "NONE"
            ):
                return None
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError):
        return None
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    samples /= 32768.0
    return samples


def ffmpeg_args(request: AsrRequest, source: Path) -> list[str]:
    """Decode one audio track to 16 kHz mono float32 on stdout, on the CPU (never -hwaccel).

    ``first_pts=0`` pads the track's start offset (7-38 ms on Samsung rushes), so sample 0 is
    the container's time 0, the timeline of the keyframes.
    """
    return [
        request.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(source), "-map", f"0:a:{request.audio_stream}", "-vn", "-sn", "-dn",
        "-ac", "1", "-rematrix_maxval", "1.0", "-af", "aresample=async=1:first_pts=0",
        "-ar", str(SAMPLE_RATE), "-f", "f32le", "-acodec", "pcm_f32le", "-",
    ]  # fmt: skip


def load_audio(request: AsrRequest) -> Audio:
    import numpy as np

    if request.audio_path is not None:
        direct = _read_speech_wav(request.audio_path)
        if direct is not None:
            return direct
    source = request.audio_path or request.video_path
    if source is None:
        raise ChildError("BadRequest", "aucun fichier audio ni vidéo dans la requête")
    try:
        done = subprocess.run(
            ffmpeg_args(request, source),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ChildError(
            "FfmpegMissing", f"ffmpeg est introuvable : {request.ffmpeg_path}"
        ) from exc
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", errors="replace").strip()
        if "matches no streams" in detail:
            raise ChildError("NoAudioStream", "aucune piste audio dans ce fichier")
        tail = " | ".join(detail.splitlines()[-4:])
        raise ChildError("AudioDecodeError", f"décodage audio impossible ({tail})")
    return np.frombuffer(done.stdout, dtype=np.float32).copy()


def speech_runs(audio: Audio, min_silence_ms: int) -> list[guards.Span]:
    """Silero VAD speech runs without padding: the gate's measure and the coverage reference."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    if audio.shape[0] < SAMPLE_RATE // 10:
        return []
    options = VadOptions(
        threshold=0.5,
        min_silence_duration_ms=min_silence_ms,
        min_speech_duration_ms=MIN_SPEECH_MS,
        speech_pad_ms=0,
    )
    chunks = get_speech_timestamps(audio, options, sampling_rate=SAMPLE_RATE)
    return [(c["start"] / SAMPLE_RATE, c["end"] / SAMPLE_RATE) for c in chunks]


# ---------------------------------------------------------------- transcription
def _words(raw: Any, offset: float = 0.0) -> list[tuple[float, float, str, float]]:
    return [
        (float(w.start) + offset, float(w.end) + offset, str(w.word), float(w.probability))
        for w in raw or []
    ]


def _segment_event(
    idx: int,
    *,
    start: float,
    end: float,
    text: str,
    seg: Any,
    words: list[tuple[float, float, str, float]],
    language: str | None,
    reasons: tuple[str, ...],
    second_pass: bool,
) -> dict[str, Any]:
    return {
        "idx": idx,
        "start": _num(start),
        "end": _num(end),
        "text": text,
        "avg_logprob": _num(seg.avg_logprob, 4),
        "no_speech_prob": _num(seg.no_speech_prob, 4),
        "compression_ratio": _num(seg.compression_ratio),
        "temperature": _num(seg.temperature if seg.temperature is not None else 0.0, 2),
        "language": language,
        "words": [[_num(s), _num(e), w, _num(p)] for s, e, w, p in words],
        "suspect": bool(reasons),
        "reasons": list(reasons),
        "pass": 2 if second_pass else 1,
    }


def preload_cuda(dll_dir: Path | None) -> None:
    """Load cuBLAS 12 by full path before CTranslate2 needs it (the driver alone has no cuBLAS)."""
    if dll_dir is None:
        raise ChildError("CudaUnavailable", "cuBLAS 12 absent : lancez « vfe models cuda-runtime »")
    import ctypes

    if hasattr(os, "add_dll_directory"):
        _dll_dirs.append(os.add_dll_directory(str(dll_dir)))
    for name in CUBLAS_DLLS:
        try:
            ctypes.WinDLL(str(dll_dir / name)) if sys.platform == "win32" else ctypes.CDLL(
                str(dll_dir / name)
            )
        except OSError as exc:
            raise ChildError("CudaUnavailable", f"{name} ne se charge pas : {exc}") from exc


class Transcriber:
    """One request, run step by step; ``emit`` sends the protocol events, ``wait_go`` blocks
    until the parent answers with the expected line (``load`` before CUDA starts, ``go`` once
    the model is warmed up)."""

    def __init__(
        self, request: AsrRequest, emit: Emit, wait_go: Callable[[bytes], None] | None = None
    ) -> None:
        self.request = request
        self.emit = emit
        self.wait_go = wait_go
        self.stats: dict[str, Any] = {
            "model": model_label(request.model_dir),
            "device": request.device,
            "compute_type": request.compute_type,
            "threads": request.threads,
            "beam_size": request.beam_size,
            "guard_version": guards.TRANSCRIPT_GUARD_VERSION,
        }
        self.segments: list[dict[str, Any]] = []

    def run(self) -> None:
        request = self.request
        missing = [name for name in _MODEL_FILES if not (request.model_dir / name).is_file()]
        if missing:
            raise ChildError(
                "ModelMissing",
                f"modèle de transcription incomplet ({', '.join(missing)}) : {request.model_dir}",
            )
        self.emit("phase", name="decode")
        started = time.perf_counter()
        audio = load_audio(request)
        duration = audio.shape[0] / SAMPLE_RATE
        self.stats["decode_s"] = round(time.perf_counter() - started, 2)

        self.emit("phase", name="vad")
        started = time.perf_counter()
        runs = speech_runs(audio, request.vad_min_silence_ms)
        speech_s = sum(end - start for start, end in runs)
        self.stats["vad_s"] = round(time.perf_counter() - started, 2)
        self.stats["speech_chunks"] = len(runs)
        self.emit(
            "audio",
            duration_s=round(duration, 3),
            speech_s=round(speech_s, 3),
            chunks=len(runs),
            decode_s=self.stats["decode_s"],
            vad_s=self.stats["vad_s"],
        )
        if speech_s < request.speech_gate_s:
            self.emit("no_speech", speech_s=round(speech_s, 3))
            return

        model = self._load()
        # Forced ("always") with next to no speech heard: the same VAD inside Whisper would
        # hand it nothing. Whisper then reads the whole file (its guards flag what it invents).
        vad_filter = not (request.speech_gate_s <= 0 and speech_s < FORCED_VAD_MIN_S)
        self.stats["vad_filter"] = vad_filter
        language, probability = self._first_pass(model, audio, duration, vad_filter=vad_filter)
        if request.coverage_pass:
            self._second_pass(model, audio, duration, runs, language)
        self.stats["segments"] = len(self.segments)
        self.stats["suspect"] = sum(1 for s in self.segments if s["suspect"])
        self.stats["words"] = sum(len(s["words"]) for s in self.segments)
        self.stats["language"] = language
        self.stats["language_probability"] = probability

    def _load(self) -> Any:
        self.emit("phase", name="load")
        started = time.perf_counter()
        request = self.request
        cuda = request.device == "cuda"
        if cuda:
            if self.wait_go is not None:
                self.emit("ready")
                self.wait_go(LOAD)  # the parent checks the free memory before CUDA starts
            preload_cuda(request.cuda_dll_dir)
            self.stats["cublas"] = request.cuda_dll_dir.name if request.cuda_dll_dir else None
        import ctranslate2  # type: ignore[import-untyped]
        from faster_whisper import WhisperModel

        try:
            model = WhisperModel(
                str(request.model_dir),
                # Never "auto": the GPU belongs to the vision model unless the gate lent it.
                device=request.device,
                device_index=0,
                compute_type=request.compute_type,
                cpu_threads=max(1, request.threads),
                num_workers=1,
                local_files_only=True,
            )
            ctranslate2.set_random_seed(0)  # reproducible temperature fallback (VLM cache keys)
            if cuda:  # a missing kernel or library shows here, before any real work
                import numpy as np

                model.detect_language(audio=np.zeros(SAMPLE_RATE, dtype=np.float32))
        except ChildError:
            raise
        except Exception as exc:
            if cuda:
                # Short of memory this time (CudaError) is not a device that cannot work.
                text = str(exc).lower()
                permanent = any(sign in text for sign in _PERMANENT_CUDA)
                kind = "CudaUnavailable" if permanent else "CudaError"
                raise ChildError(kind, f"{type(exc).__name__}: {exc}") from exc
            raise
        self.stats["load_s"] = round(time.perf_counter() - started, 2)
        self.emit("loaded", load_s=self.stats["load_s"], model=self.stats["model"])
        if cuda and self.wait_go is not None:
            self.emit("warm", load_s=self.stats["load_s"])
            self.wait_go(GO)  # the parent checks the free memory now that the model is resident
        return model

    def _options(self) -> dict[str, Any]:
        return {
            "task": "transcribe",
            "beam_size": self.request.beam_size,
            "best_of": 5,
            "temperature": TEMPERATURES,
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "no_speech_threshold": 0.6,
            "condition_on_previous_text": False,
            "word_timestamps": True,
            "hallucination_silence_threshold": None,  # it translated instead of cleaning
        }

    def _first_pass(
        self, model: Any, audio: Audio, duration: float, *, vad_filter: bool = True
    ) -> tuple[str, float]:
        from faster_whisper.vad import VadOptions

        request = self.request
        self.emit("phase", name="language")
        started = time.perf_counter()
        vad = VadOptions(
            threshold=0.5,
            min_silence_duration_ms=request.vad_min_silence_ms,
            min_speech_duration_ms=MIN_SPEECH_MS,
            speech_pad_ms=SPEECH_PAD_MS,
        )
        segments, info = model.transcribe(
            audio,
            language=request.language,
            # Per-window language (no extra cost): a first window in another language no
            # longer turns the rest into loops or translations. A forced language is kept.
            multilingual=request.language is None,
            vad_filter=vad_filter,
            vad_parameters=vad if vad_filter else None,
            language_detection_segments=1,
            initial_prompt=None,
            **self._options(),
        )
        language, probability = str(info.language), float(info.language_probability)
        ranked = sorted(info.all_language_probs or [(language, probability)], key=lambda x: -x[1])
        self.stats["info_s"] = round(time.perf_counter() - started, 2)
        self.emit(
            "info",
            language=language,
            probability=_num(probability, 4),
            top=[[code, _num(p, 4)] for code, p in ranked[:5]],
            duration_after_vad=_num(info.duration_after_vad, 2),
        )
        self.emit("phase", name="transcribe")
        started = time.perf_counter()
        previous: str | None = None
        for seg in segments:
            words = _words(seg.words)
            text = str(seg.text).strip()
            reasons = guards.suspect_reasons(
                text,
                avg_logprob=float(seg.avg_logprob),
                word_probabilities=[w[3] for w in words],
                temperature=seg.temperature,
                compression_ratio=float(seg.compression_ratio),
                previous_text=previous,
            )
            previous = text
            self._send_segment(
                start=float(seg.start),
                end=float(seg.end),
                text=text,
                seg=seg,
                words=words,
                language=language,
                reasons=reasons,
                second_pass=False,
            )
            fraction = min(1.0, float(seg.end) / duration) if duration > 0 else 1.0
            self.emit("progress", fraction=round(fraction, 4))
        elapsed = time.perf_counter() - started
        self.stats["transcribe_s"] = round(elapsed, 2)
        self.stats["rtf"] = round(elapsed / duration, 3) if duration > 0 else None
        return language, probability

    def _send_segment(self, **fields: Any) -> None:
        event = _segment_event(len(self.segments), **fields)
        self.segments.append(event)
        self.emit("segment", **event)

    def _second_pass(
        self,
        model: Any,
        audio: Audio,
        duration: float,
        runs: list[guards.Span],
        file_language: str,
    ) -> None:
        """Re-transcribe speech no word covers (dropped at 30 s window boundaries or in a
        language switch), with ±1 s of context and the previous text as prompt."""
        covered = [(w[0], w[1]) for s in self.segments for w in s["words"] if None not in w[:2]]
        gaps = guards.uncovered_runs(runs, covered)
        clips = guards.context_clips(gaps, duration_s=duration)
        self.stats["pass2_gaps"] = [list(g) for g in gaps]
        if not clips:
            return
        self.emit("pass2", clips=[[c.start, c.end] for c in clips], gaps=[list(g) for g in gaps])
        started = time.perf_counter()
        first_pass = list(self.segments)
        for clip in clips:
            piece = audio[int(clip.start * SAMPLE_RATE) : int(clip.end * SAMPLE_RATE)]
            if self.request.language is not None:
                detected, accepted = self.request.language, self.request.language
            else:
                detected, p_detected, _ = model.detect_language(audio=piece)
                accepted = detected if p_detected >= guards.PASS2_LANG_MIN_P else file_language
            before = [
                s for s in first_pass if s["start"] is not None and s["start"] < clip.gaps[0][0]
            ]
            previous = before[-1]["text"] if before else None
            segments, _info = model.transcribe(
                piece,
                language=accepted,
                multilingual=False,
                vad_filter=False,
                initial_prompt=previous or None,
                **self._options(),
            )
            for seg in segments:
                words = [
                    w
                    for w in _words(seg.words, clip.start)
                    if guards.midpoint_within(w[0], w[1], clip.gaps)
                ]
                if not words:
                    continue
                text = "".join(w[2] for w in words).strip()
                reasons = guards.suspect_reasons(
                    text,
                    avg_logprob=float(seg.avg_logprob),
                    word_probabilities=[w[3] for w in words],
                    temperature=seg.temperature,
                    compression_ratio=float(seg.compression_ratio),
                    previous_text=previous,
                    language_mismatch=detected != accepted,
                )
                previous = text
                self._send_segment(
                    start=words[0][0],
                    end=words[-1][1],
                    text=text,
                    seg=seg,
                    words=words,
                    language=accepted,
                    reasons=reasons,
                    second_pass=True,
                )
        self.stats["pass2_clips"] = len(clips)
        self.stats["pass2_s"] = round(time.perf_counter() - started, 2)


# ---------------------------------------------------------------- entry point
def _force_environment(names: Iterable[tuple[str, str]]) -> None:
    """Forced, not setdefault: an empty CUDA_VISIBLE_DEVICES still exposes the GPU on Windows."""
    for key, value in names:
        os.environ[key] = value


def _wait_go(expected: bytes) -> None:
    line = sys.stdin.buffer.readline()
    if line.strip() != expected:
        raise ChildError("Stopped", "le parent n'a pas autorisé la transcription sur le GPU")


def _looks_like_cuda(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(word in text for word in ("cuda", "cublas", "out of memory", "gpu"))


def main() -> int:
    proto = protect_stdout()  # first: nothing may write to the protocol channel but us
    _force_environment(CHILD_ENV.items())
    emit = make_emitter(proto)
    started = time.perf_counter()
    device = "cpu"
    try:
        emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION, python=sys.version.split()[0])
        request = decode_request(sys.stdin.buffer.readline())
        device = request.device
        _force_environment(device_env(device).items())  # before anything initialises CUDA
        job = Transcriber(request, emit, _wait_go)
        job.run()
        job.stats["wall_s"] = round(time.perf_counter() - started, 2)
        job.stats.update(_process_stats())
        emit("done", stats=job.stats)
    except ChildError as exc:
        emit("error", type=exc.kind, message=str(exc)[:2000])
        return ERROR_EXIT_CODE
    except Exception as exc:  # noqa: BLE001 - the parent must learn why, whatever broke
        traceback.print_exc()
        kind = "CudaError" if device == "cuda" and _looks_like_cuda(exc) else type(exc).__name__
        emit("error", type=kind, message=str(exc)[:2000])
        return ERROR_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
