"""Parent side of the ASR child: spawn, Job Object handshake, events, cancel, watchdog.

The child is started with the current interpreter (``sys.executable``, already allowed by Smart
App Control), below-normal priority and no console window. The venv's ``python.exe`` is a uv
launcher that starts the real interpreter as its own child, possibly before the launcher joins
our Job Object: the child's ``hello`` gives the real pid, which is checked (``IsProcessInJob``)
and attached before the request is sent. Closing the job then kills the launcher, the
interpreter and ffmpeg in about 0.5 s, whatever state they are in.

A killed child can exit with code 0: success requires the ``done`` event.

A ``cuda`` request (the gate lent the GPU) keeps stdin open: after its warm-up the
child says ``warm`` and waits. The free video memory is read then, with the model resident, and
the child gets ``go`` only when enough is left; during the run it is read again every half
second. Short of memory, or when CUDA fails, the child is killed and :class:`GpuFallbackError`
tells the caller to redo the file on the CPU. On Windows an over-committed GPU does not fail:
it spills to system memory and slows the vision model down, so these checks are the guard.
"""

from __future__ import annotations

import collections
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

import structlog

from vfe_vision.adapters.asr.runner import (
    CHILD_ENV,
    GO,
    LOAD,
    PROTOCOL_VERSION,
    RUNNER_MODULE,
    device_env,
    encode_request,
)
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import CancelledError, ExternalToolError
from vfe_vision.core.procs import BELOW_NORMAL_PRIORITY_CLASS, CREATE_NO_WINDOW, KillOnCloseJob
from vfe_vision.ports.asr import (
    AsrLanguage,
    AsrProgress,
    AsrRequest,
    AsrResult,
    AsrSegment,
    AsrWord,
    GpuFallbackError,
)
from vfe_vision.ports.gpu import VramProbe

log = structlog.get_logger(__name__)

STALL_MIN_CPU_S = 5.0  # CPU seconds a silent child must use per stall window to be kept
WARM_MIN_FREE_MIB = 768  # free video memory needed once the model is loaded and warmed up
RUN_MIN_FREE_MIB = 512  # below this at any time, the GPU is given back at once
VRAM_POLL_S = 0.5
LOAD_TIMEOUT_S = 120.0  # from ``load`` to ``warm``: longer means a stuck CUDA call
_CUDA_ERRORS = frozenset({"CudaUnavailable", "CudaError"})
_POLL_S = 0.25
_STDERR_LINES = 60
_EOF = None

SMART_APP_CONTROL_HINT = (
    "Windows Smart App Control a peut-être bloqué une DLL du moteur de transcription le temps de "
    "vérifier sa réputation : relancez l'analyse ; si le blocage persiste, réinstallez le paquet "
    "concerné (faster-whisper, ctranslate2, av, tokenizers). Une exclusion de l'antivirus "
    "Defender n'a aucun effet sur Smart App Control."
)

# Phase -> (overall fraction, message). Loading covers 0-5 %, language detection 5-10 %, the
# first pass 10-100 % in proportion of the audio already transcribed.
_PHASES: dict[str, tuple[float, str]] = {
    "decode": (0.0, "Décodage de l'audio"),
    "vad": (0.01, "Détection de la parole"),
    "load": (0.02, "Chargement du modèle de transcription"),
    "language": (0.05, "Détection de la langue"),
    "transcribe": (0.10, "Transcription"),
}


class SubprocessRecognizer:
    """:class:`~vfe_vision.ports.asr.SpeechRecognizer` running each request in a new process.

    ``argv`` replaces the arguments given to ``python`` (default ``-X utf8 -m <runner>``):
    tests run a scripted child with it. The watchdog kills the child when no event arrives for
    ``stall_timeout_s`` (segments come once per 30 s window, 6-25 s apart on a busy CPU) or when
    the whole run exceeds ``timeout_base_s + timeout_per_audio_s × duration``.
    """

    def __init__(
        self,
        python: str = sys.executable,
        *,
        stall_timeout_s: float = 180.0,
        env: Mapping[str, str] | None = None,
        argv: Sequence[str] | None = None,
        timeout_base_s: float = 60.0,
        timeout_per_audio_s: float = 1.0,
        vram: VramProbe | None = None,
        vram_poll_s: float = VRAM_POLL_S,
    ) -> None:
        self.python = python
        self.stall_timeout_s = stall_timeout_s
        self.env = env
        self.argv = list(argv) if argv is not None else ["-X", "utf8", "-m", RUNNER_MODULE]
        self.timeout_base_s = timeout_base_s
        self.timeout_per_audio_s = timeout_per_audio_s
        self.vram = vram
        self.vram_poll_s = vram_poll_s

    def _child_env(self, request: AsrRequest) -> dict[str, str]:
        env = dict(self.env if self.env is not None else os.environ)
        env.update(CHILD_ENV)
        env.update(device_env(request.device))
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        return env

    def transcribe(
        self, request: AsrRequest, *, cancel: CancelToken, progress: AsrProgress
    ) -> AsrResult:
        cancel.raise_if_cancelled()
        if request.device == "cuda" and self.vram is None:
            raise GpuFallbackError("mémoire graphique illisible", disable=True)
        job = KillOnCloseJob()
        try:
            proc = subprocess.Popen(
                [self.python, *self.argv],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._child_env(request),
                creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS,
            )
        except OSError as exc:
            job.close()
            raise ExternalToolError(
                f"Impossible de lancer le moteur de transcription : {exc}", tool="asr"
            ) from exc
        job.assign(proc)
        session = _Session(self, proc, job, request=request, cancel=cancel, progress=progress)
        try:
            return session.run()
        except ExternalToolError as exc:
            if request.device != "cuda" or not session.gpu_touched:
                raise  # the audio or the model files: the CPU would fail the same way
            # Whatever broke on the GPU (a crash, a stall), the CPU can still do the file.
            raise GpuFallbackError(exc.detail) from exc
        finally:
            session.shutdown()


class _Session:
    """One child process from spawn to reaping."""

    def __init__(
        self,
        owner: SubprocessRecognizer,
        proc: subprocess.Popen[bytes],
        job: KillOnCloseJob,
        *,
        request: AsrRequest,
        cancel: CancelToken,
        progress: AsrProgress,
    ) -> None:
        self.owner = owner
        self.proc = proc
        self.job = job
        self.request = request
        self.cancel = cancel
        self.progress = progress
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=_STDERR_LINES)
        self.threads: list[threading.Thread] = []
        self.started = time.monotonic()
        self.last_event = self.started
        self.cpu_mark: float | None = None  # the job's CPU time at the last sign of life
        self.deadline: float | None = None
        self.fraction = 0.0
        self.message: str | None = None
        self.child_pid: int | None = None
        self.stray_lines = 0
        self.request_sent = False
        self.gpu_running = False  # a cuda child: its memory is watched until its result
        self.gpu_touched = False  # the child was allowed to start CUDA
        self.load_deadline: float | None = None  # ``load`` sent, ``warm`` not received yet
        self.vram_checked = 0.0
        self.vram_min_free: int | None = None
        # collected results
        self.audio: dict[str, Any] = {}
        self.info: dict[str, Any] = {}
        self.loaded: dict[str, Any] = {}
        self.segments: list[dict[str, Any]] = []
        self.no_speech = False
        self.done: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None

    # ------------------------------------------------------------ plumbing
    def _start_threads(self) -> None:
        stdout, stderr = self.proc.stdout, self.proc.stderr
        if stdout is None or stderr is None:  # pragma: no cover - always PIPE
            raise ExternalToolError("Sorties du moteur de transcription indisponibles", tool="asr")

        def pump() -> None:
            try:
                for raw in iter(stdout.readline, b""):
                    self.lines.put(raw)
            except (OSError, ValueError):
                pass
            finally:
                self.lines.put(_EOF)

        def drain() -> None:
            with contextlib.suppress(OSError, ValueError):
                for raw in iter(stderr.readline, b""):
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    if line:
                        self.stderr_tail.append(line[:500])

        for target, name in ((pump, "asr-stdout"), (drain, "asr-stderr")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self.threads.append(thread)

    def _kill(self) -> None:
        self.job.close()  # the launcher, the real interpreter and ffmpeg
        if self.proc.poll() is None:
            with contextlib.suppress(OSError):
                self.proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(10)

    def shutdown(self) -> None:
        self._kill()
        for thread in self.threads:
            thread.join(2)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()

    def _report(self, fraction: float, message: str | None) -> None:
        """Forward progress: never backwards, and only when something changed."""
        fraction = min(1.0, max(self.fraction, fraction))
        if fraction == self.fraction and message in {None, self.message}:
            return
        self.fraction = fraction
        if message is not None:
            self.message = message
        self.progress(fraction, self.message)

    def _stderr_text(self) -> str:
        return " | ".join(list(self.stderr_tail)[-6:])

    def _failure(self, detail: str) -> ExternalToolError:
        blob = detail + "\n" + "\n".join(self.stderr_tail)
        hint = SMART_APP_CONTROL_HINT if "DLL load failed" in blob else None
        message = f"{detail} — {hint}" if hint else detail
        return ExternalToolError(message, tool="asr", hint=hint, returncode=self.proc.poll())

    # ------------------------------------------------------------ main loop
    def run(self) -> AsrResult:
        self._start_threads()
        while True:
            try:
                raw = self.lines.get(timeout=_POLL_S)
            except queue.Empty:
                self._watch()
                continue
            if raw is _EOF:
                break
            event = _parse(raw)
            if event is None:
                self.stray_lines += 1
                log.debug("asr_stray_output", line=raw[:200].decode("utf-8", errors="replace"))
                continue
            self._alive()
            self._handle(event)
            self._watch()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(10)
        for thread in self.threads:  # the stderr tail is complete before any error message
            thread.join(2)
        self.cancel.raise_if_cancelled()
        return self._result()

    def _alive(self) -> None:
        self.last_event = time.monotonic()
        self.cpu_mark = self.job.cpu_seconds()

    def _still_working(self) -> bool:
        """Silent but busy: the child (low priority) is only slowed down by other work."""
        cpu = self.job.cpu_seconds()
        if cpu is None or self.cpu_mark is None or cpu - self.cpu_mark < STALL_MIN_CPU_S:
            return False
        self._alive()
        return True

    def _watch(self) -> None:
        now = time.monotonic()
        if self.cancel.cancelled:
            self._kill()
            raise CancelledError(self.cancel.reason or "Annulé")
        if self.load_deadline is not None and now > self.load_deadline:
            self._kill()
            raise GpuFallbackError(
                f"le modèle ne s'est pas chargé sur le GPU en {LOAD_TIMEOUT_S:.0f} s"
            )
        if self.gpu_running and now - self.vram_checked >= self.owner.vram_poll_s:
            self.vram_checked = now
            free = self._free_vram()
            if free is None or free < RUN_MIN_FREE_MIB:
                self._kill()
                raise GpuFallbackError(
                    "mémoire graphique presque pleine pendant la transcription "
                    f"({free if free is not None else '?'} Mio libres)"
                )
        if now - self.last_event > self.owner.stall_timeout_s and not self._still_working():
            self._kill()
            raise self._failure(
                "Le moteur de transcription ne répond plus depuis "
                f"{self.owner.stall_timeout_s:.0f} s ; il a été arrêté"
            )
        if self.deadline is not None and now > self.deadline:
            self._kill()
            raise self._failure(
                f"La transcription dépasse la durée maximale ({self.deadline - self.started:.0f} s)"
            )

    def _handle(self, event: dict[str, Any]) -> None:
        kind = event["event"]
        if kind == "hello":
            self._handshake(event)
        elif kind == "phase":
            fraction, message = _PHASES.get(str(event.get("name")), (self.fraction, None))
            self._report(fraction, message)
        elif kind == "audio":
            self.audio = event
            duration = float(event.get("duration_s") or 0.0)
            self.deadline = (
                self.started + self.owner.timeout_base_s + self.owner.timeout_per_audio_s * duration
            )
        elif kind == "no_speech":
            self.no_speech = True
            self._report(1.0, "Aucune parole détectée")
        elif kind == "loaded":
            self.loaded = event
            self._report(0.05, "Détection de la langue")
        elif kind == "ready":
            self._load()
        elif kind == "warm":
            self.load_deadline = None
            self._go()
        elif kind == "info":
            self.info = event
            self._report(0.10, "Transcription")
        elif kind == "segment":
            self.segments.append(event)
        elif kind == "progress":
            fraction = float(event.get("fraction") or 0.0)
            self._report(0.10 + 0.90 * min(1.0, max(0.0, fraction)), None)
        elif kind == "pass2":
            self._report(self.fraction, "Seconde passe sur la parole manquée")
        elif kind == "done":
            self.gpu_running = False  # the result is in: nothing left to protect
            stats = event.get("stats")
            self.done = stats if isinstance(stats, dict) else {}
            self._report(1.0, None if self.no_speech else "Transcription terminée")
        elif kind == "error":
            self.gpu_running = False
            self.error = event

    def _free_vram(self) -> int | None:
        free = self.owner.vram.free_mib() if self.owner.vram is not None else None
        if free is not None:
            self.vram_min_free = (
                free if self.vram_min_free is None else min(self.vram_min_free, free)
            )
        return free

    def _answer(self, line: bytes, *, close: bool) -> None:
        stdin = self.proc.stdin
        if stdin is None:
            return
        try:
            stdin.write(line + b"\n")
            stdin.flush()
        except (OSError, ValueError):
            pass  # the child already died: its stderr and exit code tell why
        finally:
            if close:
                with contextlib.suppress(OSError, ValueError):
                    stdin.close()

    def _load(self) -> None:
        """The audio is decoded: the lending rule is checked again before CUDA starts."""
        if self.request.device != "cuda":
            return
        free = self._free_vram()
        needed = max(RUN_MIN_FREE_MIB, self.request.gpu_min_free_mib)
        if free is None or free < needed:
            self._kill()
            raise GpuFallbackError(
                "mémoire graphique insuffisante avant le chargement "
                f"({free if free is not None else '?'} Mio libres, {needed} nécessaires)"
            )
        self.gpu_touched = True
        self.load_deadline = time.monotonic() + LOAD_TIMEOUT_S
        self._answer(LOAD, close=False)

    def _go(self) -> None:
        """The model is on the GPU and warmed up: start only if enough memory is left."""
        stdin = self.proc.stdin
        if self.request.device != "cuda" or stdin is None:
            return
        free = self._free_vram()
        # What decoding will still take must fit above the floor kept during the run.
        needed = max(WARM_MIN_FREE_MIB, RUN_MIN_FREE_MIB + self.request.gpu_headroom_mib)
        if free is None or free < needed:
            self._kill()
            raise GpuFallbackError(
                "mémoire graphique insuffisante une fois le modèle chargé "
                f"({free if free is not None else '?'} Mio libres, {needed} nécessaires)"
            )
        self._answer(GO, close=True)

    def _handshake(self, event: dict[str, Any]) -> None:
        pid = event.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool):
            self.child_pid = pid
            if self.job.contains(pid) is False and not self.job.ensure_pid(pid):
                log.warning("asr_child_not_in_job", pid=pid)
        stdin = self.proc.stdin
        if stdin is None or self.request_sent:
            return
        self.request_sent = True
        keep_open = self.request.device == "cuda"  # for the ``go`` after the warm-up
        if keep_open:  # the memory is watched from now on: loading is when it can overshoot
            self.gpu_running = True
            self.vram_checked = time.monotonic()
        try:
            stdin.write(encode_request(self.request))
            stdin.flush()
        except (OSError, ValueError):
            keep_open = False  # the child already died: its stderr and exit code tell why
        finally:
            if not keep_open:
                with contextlib.suppress(OSError, ValueError):
                    stdin.close()

    # ------------------------------------------------------------ result
    def _result(self) -> AsrResult:
        if self.error is not None:
            kind = str(self.error.get("type") or "Error")
            message = str(self.error.get("message") or "").strip()
            if kind in _CUDA_ERRORS and self.request.device == "cuda":
                raise GpuFallbackError(message, disable=kind == "CudaUnavailable")
            raise self._failure(f"Échec de la transcription ({kind}) : {message}")
        if self.done is None:
            code = self.proc.poll()
            tail = self._stderr_text()
            raise self._failure(
                "Le moteur de transcription s'est arrêté sans terminer"
                + (f" (code {code})" if code is not None else "")
                + (f" : {tail}" if tail else "")
            )
        stats = dict(self.done)
        stats["stray_lines"] = self.stray_lines
        stats["child_pid"] = self.child_pid
        if self.request.device == "cuda":
            stats["vram_min_free_mib"] = self.vram_min_free
        model = str(self.loaded.get("model") or stats.get("model") or self.request.model_dir.name)
        duration = float(self.audio.get("duration_s") or 0.0)
        speech = float(self.audio.get("speech_s") or 0.0)
        if self.no_speech:
            return AsrResult("no_speech", None, None, (), duration, speech, (), model, stats)
        segments = tuple(
            _segment(i, raw)
            for i, raw in enumerate(
                sorted(self.segments, key=lambda s: (_float(s.get("start")), _int(s.get("idx"))))
            )
        )
        top = self.info.get("top") or []
        languages = tuple(
            AsrLanguage(str(item[0]), _float(item[1]))
            for item in top
            if isinstance(item, list) and len(item) == 2
        )
        language = self.info.get("language")
        probability = self.info.get("probability")
        return AsrResult(
            status="ok",
            language=str(language) if language else None,
            language_probability=_float(probability) if probability is not None else None,
            languages=languages,
            duration_s=duration,
            speech_s=speech,
            segments=segments,
            model=model,
            stats=stats,
        )


def _parse(raw: bytes) -> dict[str, Any] | None:
    """A protocol event, or None for anything else the child printed (tolerated, logged)."""
    try:
        event = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    if (
        not isinstance(event, dict)
        or event.get("v") != PROTOCOL_VERSION
        or not isinstance(event.get("event"), str)
    ):
        return None
    return event


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _word(item: Any) -> AsrWord | None:
    if not isinstance(item, list) or len(item) != 4:
        return None
    start, end, text, probability = item
    return AsrWord(_float(start), _float(end), str(text), _float(probability))


def _segment(idx: int, raw: dict[str, Any]) -> AsrSegment:
    words = tuple(w for w in (_word(item) for item in raw.get("words") or []) if w is not None)
    language = raw.get("language")
    return AsrSegment(
        idx=idx,
        start=_float(raw.get("start")),
        end=_float(raw.get("end")),
        text=str(raw.get("text") or ""),
        avg_logprob=_float(raw.get("avg_logprob")),
        no_speech_prob=_float(raw.get("no_speech_prob")),
        compression_ratio=_float(raw.get("compression_ratio")),
        temperature=_float(raw.get("temperature")),
        language=str(language) if language else None,
        words=words,
        suspect=bool(raw.get("suspect")),
        second_pass=raw.get("pass") == 2,
    )
