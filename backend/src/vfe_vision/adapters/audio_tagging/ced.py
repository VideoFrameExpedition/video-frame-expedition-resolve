"""CED-small (AudioSet sound tagging, 527 classes) on the CPU with ONNX Runtime.

CED (mispeech, Apache-2.0) is a ViT distilled from an ensemble of large AudioSet taggers. Its
ONNX export takes raw 16 kHz mono samples (the mel front-end is inside the graph) of any length
from 0.15 s and returns one sigmoid probability per class for the whole input. It is a second
opinion next to YAMNet: windows of 5 s every 2.5 s, **never zero-padded** — padding
a short input to 5 s wrecks its scores (a frog at 0.75 falls to 0.11 on 1.5 s of audio) — so
the last window is aligned on the end of the file and audio shorter than 5 s runs once at its
true length.

The GPU is never used: it belongs to the vision model in LM Studio.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import ExternalToolError, ServiceUnavailableError
from vfe_vision.domain.audio_events import Rollup, build_rollup

SAMPLE_RATE = 16_000
WINDOW = 5 * SAMPLE_RATE
HOP = WINDOW // 2
MIN_SAMPLES = SAMPLE_RATE // 2  # the graph needs 2,400 samples; below 0.5 s nothing is heard
NUM_CLASSES = 527
INPUT_NAME = "waveform"
OUTPUT_NAME = "logits"  # already sigmoid probabilities despite the name

MODEL_FILE = "model.onnx"
CLASS_MAP_FILE = "class_labels_indices.csv"
MODEL_FILES = (MODEL_FILE, CLASS_MAP_FILE)

Pcm = npt.NDArray[np.float32]


def missing_files(model_dir: Path) -> list[str]:
    """Names of the files ``model_dir`` still lacks (empty when CED is installed)."""
    return [name for name in MODEL_FILES if not (model_dir / name).is_file()]


def load_rollup(model_dir: Path, ontology: Path) -> Rollup:
    """Category table of CED's 527 classes; the AudioSet ontology comes with YAMNet."""
    try:
        class_map = (model_dir / CLASS_MAP_FILE).read_text(encoding="utf-8")
        tree = ontology.read_text(encoding="utf-8")
    except OSError as exc:
        raise ServiceUnavailableError(
            f"Fichiers CED ou ontologie AudioSet absents ({exc.filename}) : installez-les avec "
            "« vfe models sounds »."
        ) from exc
    rollup = build_rollup(class_map, tree)
    if len(rollup) != NUM_CLASSES:
        raise ServiceUnavailableError(
            f"Liste des classes CED inattendue : {len(rollup)} au lieu de {NUM_CLASSES}."
        )
    return rollup


def window_starts(samples: int) -> list[int]:
    """First sample of each window: every HOP, the last one ending exactly at ``samples``.

    One window at the true length for audio shorter than WINDOW, none below MIN_SAMPLES.
    """
    if samples < MIN_SAMPLES:
        return []
    if samples <= WINDOW:
        return [0]
    starts = list(range(0, samples - WINDOW + 1, HOP))
    if starts[-1] + WINDOW < samples:
        starts.append(samples - WINDOW)
    return starts


@dataclass(frozen=True, slots=True)
class CedScores:
    """Class probabilities of each window; window ``i`` covers [start_s[i], end_s[i]]."""

    start_s: npt.NDArray[np.float64]
    end_s: npt.NDArray[np.float64]
    probs: npt.NDArray[np.float32]  # [windows, 527], 0–1

    def __len__(self) -> int:
        return len(self.probs)


class _Session(Protocol):
    def run(self, output_names: list[str], input_feed: dict[str, Any]) -> list[Any]: ...


class CedTagger:
    """527 AudioSet class probabilities per 5 s window, computed on the CPU only.

    One window per call: batching brings nothing on the CPU (measured 0.46 s for 8 windows in
    one call, 0.45 s one by one) and single calls keep memory flat and cancellation quick. The
    session is created on first use (about 0.15 s) and shared by the threads of the process.
    """

    def __init__(self, model_path: Path, *, threads: int = 2) -> None:
        if threads < 1:
            raise ValueError("threads doit valoir au moins 1")
        self.model_path = model_path
        self.threads = threads
        self._session: _Session | None = None
        self._lock = threading.Lock()

    def scores(
        self,
        pcm: npt.ArrayLike,
        *,
        cancel: CancelToken | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> CedScores:
        """Windows of 16 kHz mono ``pcm``; ``cancel`` is checked before each one."""
        wave = _clean(pcm)
        starts = window_starts(len(wave))
        probs = np.zeros((len(starts), NUM_CLASSES), dtype=np.float32)
        if starts:
            session = self._get_session()
            for n, first in enumerate(starts):
                if cancel is not None:
                    cancel.raise_if_cancelled()
                probs[n] = _run(session, wave[first : first + WINDOW])
                if progress is not None:
                    progress((n + 1) / len(starts))
        begin = np.asarray(starts, dtype=np.float64)
        end = np.minimum(begin + WINDOW, len(wave))
        return CedScores(begin / SAMPLE_RATE, end / SAMPLE_RATE, probs)

    def _get_session(self) -> _Session:
        with self._lock:
            if self._session is None:
                self._session = self._open()
            return self._session

    def _open(self) -> _Session:
        if not self.model_path.is_file():
            raise ServiceUnavailableError(
                f"Modèle CED absent : {self.model_path}. Installez-le avec « vfe models sounds »."
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
                f"Modèle CED illisible ({self.model_path.name}) : {exc}", tool="onnxruntime"
            ) from exc
        inputs = [node.name for node in session.get_inputs()]
        outputs = [node.name for node in session.get_outputs()]
        if INPUT_NAME not in inputs or OUTPUT_NAME not in outputs:
            raise ExternalToolError(
                f"Modèle CED inattendu : entrées {inputs}, sorties {outputs}.", tool="onnxruntime"
            )
        session_typed: _Session = session
        return session_typed


def self_test(tagger: CedTagger, names: Sequence[str]) -> list[str]:
    """Checks a corrupted or replaced model fails; returns the failures, empty when all pass.

    3 s of silence → « Silence », seeded uniform noise → « White noise », a 440 Hz sine →
    « Sine wave » (each first, measured 0.47, 0.33 and 0.92), and every probability in 0–1.
    """
    three_s = 3 * SAMPLE_RATE
    noise = np.random.RandomState(51773).uniform(-1.0, 1.0, three_s)
    sine = 0.5 * np.sin(2 * np.pi * 440 * np.arange(three_s) / SAMPLE_RATE)
    checks = (("Silence", np.zeros(three_s)), ("White noise", noise), ("Sine wave", sine))
    failures: list[str] = []
    for expected, wave in checks:
        result = tagger.scores(wave.astype(np.float32))
        probs = result.probs[0] if len(result) else np.zeros(NUM_CLASSES, dtype=np.float32)
        if not (probs.min() >= 0.0 and probs.max() <= 1.0):
            failures.append(f"{expected} : probabilités hors de 0–1")
        best = names[int(np.argmax(probs))]
        if best != expected:
            failures.append(f"« {expected} » attendu en premier, reçu « {best} »")
    return failures


def _clean(pcm: npt.ArrayLike) -> Pcm:
    wave = np.asarray(pcm, dtype=np.float32)
    if wave.ndim != 1:
        raise ValueError(f"Signal mono attendu, forme reçue {wave.shape}")
    if not np.isfinite(wave).all():
        wave = np.nan_to_num(wave, nan=0.0, posinf=1.0, neginf=-1.0)
    return np.ascontiguousarray(wave)


def _run(session: _Session, window: Pcm) -> npt.NDArray[np.float32]:
    try:
        raw = session.run([OUTPUT_NAME], {INPUT_NAME: window[np.newaxis, :]})[0]
    except Exception as exc:  # onnxruntime raises its own untyped errors
        raise ExternalToolError(f"Échec du modèle CED : {exc}", tool="onnxruntime") from exc
    out = np.asarray(raw, dtype=np.float32)
    if out.shape != (1, NUM_CLASSES):
        raise ExternalToolError(
            f"Sortie CED inattendue : forme {out.shape} pour {len(window)} échantillons.",
            tool="onnxruntime",
        )
    probs: npt.NDArray[np.float32] = np.nan_to_num(out[0], nan=0.0, posinf=1.0, neginf=0.0)
    return probs
