"""YAMNet (AudioSet sound events) on the CPU with ONNX Runtime, and the 16 kHz audio it needs.

The model file is a tf2onnx conversion of Google's YAMNet (Apache-2.0), numerically equivalent
to the TensorFlow SavedModel. It scores 521 classes on 0.96 s patches every 0.48 s. Long files
are scored in 60 s chunks aligned on the hop and overlapping by 7920 samples: the result equals
one call on the whole file while memory stays flat (+200 MB instead of +8 GB for an hour).

The GPU is never used: it belongs to the vision model in LM Studio. The session asks for the
CPU provider only (the default list of this wheel also holds the Azure provider).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import ExternalToolError, ServiceUnavailableError
from vfe_vision.core.procs import run_process
from vfe_vision.domain.audio_events import Rollup, build_rollup

SAMPLE_RATE = 16_000
HOP = 7_680  # 0.48 s
PATCH = 15_360  # 0.96 s
WINDOW = 15_600  # samples behind the first frame: the patch plus the STFT window tail
CHUNK_FRAMES = 125  # 60 s of frames per call
SPAN = (CHUNK_FRAMES - 1) * HOP + WINDOW  # 967,920 samples in, exactly 125 frames kept
TAIL_MIN = WINDOW - HOP  # 7,920: a shorter leftover is already covered by the overlap
NUM_CLASSES = 521
INPUT_NAME = "waveform"
OUTPUT_NAME = "output_0"  # class scores; output_1 (embeddings) and output_2 are not requested

MODEL_FILE = "yamnet.onnx"
CLASS_MAP_FILE = "yamnet_class_map.csv"
ONTOLOGY_FILE = "ontology.json"
MODEL_FILES = (MODEL_FILE, CLASS_MAP_FILE, ONTOLOGY_FILE)

Pcm = npt.NDArray[np.float32]


def missing_files(model_dir: Path) -> list[str]:
    """Names of the files ``model_dir`` still lacks (empty when YAMNet is installed)."""
    return [name for name in MODEL_FILES if not (model_dir / name).is_file()]


def load_rollup(model_dir: Path) -> Rollup:
    """Category table from the class map and the AudioSet ontology stored next to the model."""
    try:
        class_map = (model_dir / CLASS_MAP_FILE).read_text(encoding="utf-8")
        ontology = (model_dir / ONTOLOGY_FILE).read_text(encoding="utf-8")
    except OSError as exc:
        raise ServiceUnavailableError(
            f"Fichiers YAMNet absents dans {model_dir} : installez-les avec « vfe models yamnet »."
        ) from exc
    return build_rollup(class_map, ontology)


def frame_count(samples: int) -> int:
    """Frames YAMNet produces for ``samples`` samples (0 for none; the model pads short input)."""
    if samples <= 0:
        return 0
    return 1 + -(-max(0, samples - WINDOW) // HOP)


def decode_pcm16k(
    ffmpeg_path: str,
    video: Path,
    *,
    cancel: CancelToken | None = None,
    timeout_s: float = 3600,
) -> Pcm:
    """First audio stream as 16 kHz mono float32, sample 0 at container time 0.

    ``first_pts=0`` pads a late audio start (7–38 ms on Samsung phones) so that frame times
    match keyframe times. The channels are averaged (``rematrix_maxval=1.0``): the default
    float downmix adds +3 dB on correlated stereo, which changes YAMNet's top class on quiet
    rushes. The option has to sit inside the ``aresample`` filter, which does the downmix: as
    an output option alone it is ignored once an explicit ``aresample`` is in the chain (kept
    there too for any resampler ffmpeg would insert). Values above 1.0 are kept (no clipping),
    NaN becomes 0. Without an audio stream ffmpeg fails and :class:`ExternalToolError` is raised.
    """
    result = run_process(
        [
            ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(video),
            "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-af", "aresample=async=1:first_pts=0:rematrix_maxval=1.0",
            "-ac", "1", "-rematrix_maxval", "1.0", "-ar", str(SAMPLE_RATE),
            "-f", "f32le", "-",
        ],
        timeout_s=timeout_s,
        cancel=cancel,
        tool_name="ffmpeg",
    )  # fmt: skip
    raw = np.frombuffer(result.stdout, dtype=np.float32, count=len(result.stdout) // 4)
    pcm: Pcm = np.nan_to_num(raw, nan=0.0, posinf=1.0, neginf=-1.0)  # a writable copy
    return pcm


def rms_dbfs(pcm: npt.ArrayLike) -> npt.NDArray[np.float32]:
    """RMS level (dBFS) of each YAMNet frame's 0.96 s patch, aligned with ``scores``' rows."""
    wave = _clean(pcm)
    total = len(wave)
    frames = frame_count(total)
    if frames == 0:
        return np.zeros(0, dtype=np.float32)
    blocks = -(-total // HOP)
    energy = np.zeros(blocks + 1)  # +1: the second half of the last patch may lie past the end
    counts = np.zeros(blocks + 1)
    step = HOP * 1024  # a block multiple, so memory stays bounded on long files
    for first in range(0, total, step):
        part = wave[first : first + step].astype(np.float64)
        if pad := (-len(part)) % HOP:
            part = np.concatenate([part, np.zeros(pad)])
        sums = np.square(part).reshape(-1, HOP).sum(axis=1)
        energy[first // HOP : first // HOP + len(sums)] = sums
    counts[:blocks] = HOP
    counts[blocks - 1] = total - (blocks - 1) * HOP
    patch_energy = energy[:frames] + energy[1 : frames + 1]  # PATCH = two hops
    patch_count = counts[:frames] + counts[1 : frames + 1]
    rms = np.sqrt(patch_energy / np.maximum(patch_count, 1.0))
    return (20.0 * np.log10(rms + 1e-10)).astype(np.float32)


class _Session(Protocol):
    def run(self, output_names: list[str], input_feed: dict[str, Any]) -> list[Any]: ...

    def get_providers(self) -> list[str]: ...


class YamnetTagger:
    """521 AudioSet class scores per 0.48 s frame, computed on the CPU only.

    The ONNX session is created on first use (about 70 ms) and shared by the threads of the
    process (``InferenceSession.run`` is thread-safe).
    """

    def __init__(self, model_path: Path, *, threads: int = 2) -> None:
        if threads < 1:
            raise ValueError("threads doit valoir au moins 1")
        self.model_path = model_path
        self.threads = threads
        self._session: _Session | None = None
        self._lock = threading.Lock()

    @property
    def providers(self) -> list[str]:
        return list(self._get_session().get_providers())

    def scores(
        self,
        pcm: npt.ArrayLike,
        *,
        cancel: CancelToken | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> Pcm:
        """[frames, 521] scores for 16 kHz mono ``pcm``; frame ``i`` covers 0.48 i + [0, 0.96] s.

        Chunks of 125 frames are scored one after the other; ``cancel`` is checked between
        them and ``progress`` receives the share of samples done (0–1). No samples, no frames.
        """
        wave = _clean(pcm)
        total = len(wave)
        if total == 0:
            return np.zeros((0, NUM_CLASSES), dtype=np.float32)
        session = self._get_session()
        parts: list[Pcm] = []
        start = 0
        while total - start >= SPAN:
            _raise_if_cancelled(cancel)
            parts.append(_run(session, wave[start : start + SPAN], CHUNK_FRAMES))
            start += CHUNK_FRAMES * HOP
            if progress is not None:
                progress(start / total)
        rest = total - start
        if rest > TAIL_MIN or not parts:
            _raise_if_cancelled(cancel)
            parts.append(_run(session, wave[start:], frame_count(rest)))
        if progress is not None:
            progress(1.0)
        return np.concatenate(parts)

    # ------------------------------------------------------------------ session
    def _get_session(self) -> _Session:
        with self._lock:
            if self._session is None:
                self._session = self._open()
            return self._session

    def _open(self) -> _Session:
        if not self.model_path.is_file():
            raise ServiceUnavailableError(
                f"Modèle YAMNet absent : {self.model_path}. "
                "Installez-le avec « vfe models yamnet »."
            )
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.log_severity_level = 3  # errors only
        try:
            session = ort.InferenceSession(
                str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
        except Exception as exc:  # onnxruntime raises its own untyped errors
            raise ExternalToolError(
                f"Modèle YAMNet illisible ({self.model_path.name}) : {exc}", tool="onnxruntime"
            ) from exc
        inputs = [node.name for node in session.get_inputs()]
        outputs = [node.name for node in session.get_outputs()]
        if INPUT_NAME not in inputs or OUTPUT_NAME not in outputs:
            raise ExternalToolError(
                f"Modèle YAMNet inattendu : entrées {inputs}, sorties {outputs}.",
                tool="onnxruntime",
            )
        session_typed: _Session = session
        return session_typed


def self_test(tagger: YamnetTagger, names: Sequence[str]) -> list[str]:
    """Google's reference checks (yamnet_test.py); returns the failures, empty when all pass.

    A corrupted or replaced model file fails them: silence → « Silence », seeded uniform noise
    → « White noise », a 440 Hz sine in the top 10 → « Sine wave », 3 s → 6 frames.
    """
    three_s = 3 * SAMPLE_RATE
    noise = np.random.RandomState(51773).uniform(-1.0, 1.0, three_s)
    sine = np.sin(2 * np.pi * 440 * np.linspace(0, 3, three_s))
    checks = (("Silence", np.zeros(three_s), 1), ("White noise", noise, 1), ("Sine wave", sine, 10))
    failures: list[str] = []
    for expected, wave, top in checks:
        scores = tagger.scores(wave.astype(np.float32))
        if len(scores) != 6:
            failures.append(f"{expected} : {len(scores)} trames pour 3 s au lieu de 6")
        mean = scores.mean(axis=0)
        best = [names[i] for i in np.argsort(-mean, kind="stable")[:top].tolist()]
        if expected not in best:
            failures.append(f"« {expected} » absent du top {top} : {', '.join(best[:3])}")
    return failures


def _clean(pcm: npt.ArrayLike) -> Pcm:
    wave = np.asarray(pcm, dtype=np.float32)
    if wave.ndim != 1:
        raise ValueError(f"Signal mono attendu, forme reçue {wave.shape}")
    if not np.isfinite(wave).all():  # NaN in, NaN scores out
        wave = np.nan_to_num(wave, nan=0.0, posinf=1.0, neginf=-1.0)
    return np.ascontiguousarray(wave)


def _raise_if_cancelled(cancel: CancelToken | None) -> None:
    if cancel is not None:
        cancel.raise_if_cancelled()


def _run(session: _Session, chunk: Pcm, keep: int) -> Pcm:
    out = np.asarray(session.run([OUTPUT_NAME], {INPUT_NAME: chunk})[0], dtype=np.float32)
    if out.ndim != 2 or out.shape[1] != NUM_CLASSES or len(out) < keep:
        raise ExternalToolError(
            f"Sortie YAMNet inattendue : forme {out.shape} pour {len(chunk)} échantillons.",
            tool="onnxruntime",
        )
    # The graph emits one extra zero-padded frame when len - 15600 is a multiple of 7680:
    # never trust the output length, keep the frames the samples account for.
    return out[:keep]
