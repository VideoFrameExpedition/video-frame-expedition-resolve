"""EmbeddingGemma-300m (Google, Gemma Terms of Use) on the CPU with ONNX Runtime.

The onnx-community q4 export takes ``input_ids`` and ``attention_mask`` and returns a pooled,
768-dimension ``sentence_embedding``. An evaluation found it best on French and English queries
over French descriptions (R@1 1.00 in both), with its task prefixes, and unchanged when the
vectors are cut to their first 256 dimensions (Matryoshka) then brought back to unit length:
that is what the index stores (1 KiB per passage).

The GPU is never used: it belongs to the vision model in LM Studio. The session is created on
first use (about a second) and shared by the threads of the process.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from vfe_vision.adapters.models.catalog import DEFAULTS, spec
from vfe_vision.adapters.models.store import ModelStore
from vfe_vision.core.errors import ExternalToolError, ServiceUnavailableError
from vfe_vision.ports.embeddings import TextEmbedder, Vectors

MODEL_FILE = "model_q4.onnx"  # its weights are in model_q4.onnx_data, found by name
TOKENIZER_FILE = "tokenizer.json"
QUERY_PREFIX = "task: search result | query: "
DOCUMENT_PREFIX = "title: none | text: "
OUTPUT = "sentence_embedding"
DIM = 256  # Matryoshka truncation
MAX_TOKENS = 512  # a passage is cut to this (chunks are written to fit, ~1,800 characters)
BATCH = 8  # texts per call, sorted by length so that little padding is computed
EMBED_THREADS = 2
# Measured: the right passages at 0.47–0.61 from their query, the others at 0.06–0.31.
MIN_SIMILARITY = 0.30
CPU_PROVIDER = "CPUExecutionProvider"
INSTALL = "vfe models search"


class _Session(Protocol):
    def get_inputs(self) -> Sequence[Any]: ...

    def get_outputs(self) -> Sequence[Any]: ...

    def run(
        self, output_names: Sequence[str] | None, input_feed: Mapping[str, Any]
    ) -> list[Any]: ...


def unit_prefix(vectors: npt.ArrayLike, dim: int = DIM) -> Vectors:
    """The first ``dim`` dimensions of each vector, at unit length (a null vector stays null)."""
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix[np.newaxis, :]
    cut = np.ascontiguousarray(matrix[:, :dim], dtype=np.float32)
    norms = np.linalg.norm(cut, axis=1, keepdims=True)
    unit: Vectors = np.divide(cut, norms, out=np.zeros_like(cut), where=norms > 0)
    return unit


def missing_files(model_dir: Path) -> list[str]:
    """Names of the files ``model_dir`` still lacks (empty when the model is installed)."""
    names = (MODEL_FILE, f"{MODEL_FILE}_data", TOKENIZER_FILE)
    return [name for name in names if not (model_dir / name).is_file()]


class GemmaEmbedder:
    """Passages and queries as 256-dimension unit vectors, on the CPU only."""

    def __init__(self, model_dir: Path, model_id: str, *, threads: int = EMBED_THREADS) -> None:
        if threads < 1:
            raise ValueError("threads doit valoir au moins 1")
        self.model_dir = model_dir
        self._model_id = model_id
        self.threads = threads
        self._session: _Session | None = None
        self._tokenizer: Any = None
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return DIM

    @property
    def min_similarity(self) -> float:
        return MIN_SIMILARITY

    def embed_documents(self, texts: Sequence[str]) -> Vectors:
        return self._embed([DOCUMENT_PREFIX + text for text in texts])

    def embed_query(self, text: str) -> Vectors:
        vector: Vectors = self._embed([QUERY_PREFIX + text])[0]
        return vector

    # ------------------------------------------------------------ internals
    def _embed(self, texts: list[str]) -> Vectors:
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        if not texts:
            return out
        session, tokenizer = self._load()
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        for first in range(0, len(order), BATCH):
            chosen = order[first : first + BATCH]
            encoded = tokenizer.encode_batch([texts[i] for i in chosen])
            ids = np.array([e.ids for e in encoded], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            try:
                raw = session.run([OUTPUT], {"input_ids": ids, "attention_mask": mask})[0]
            except Exception as exc:  # onnxruntime raises its own untyped errors
                raise ExternalToolError(
                    f"Échec du modèle de recherche : {exc}", tool="onnxruntime"
                ) from exc
            vectors = np.asarray(raw, dtype=np.float32)
            if vectors.ndim != 2 or vectors.shape[0] != len(chosen) or vectors.shape[1] < DIM:
                raise ExternalToolError(
                    f"Sortie inattendue du modèle de recherche : forme {vectors.shape}.",
                    tool="onnxruntime",
                )
            out[chosen] = unit_prefix(np.nan_to_num(vectors))
        return out

    def _load(self) -> tuple[_Session, Any]:
        with self._lock:
            if self._session is None:
                self._tokenizer = self._open_tokenizer()
                self._session = self._open_session()
            return self._session, self._tokenizer

    def _open_tokenizer(self) -> Any:
        from tokenizers import Tokenizer

        path = self.model_dir / TOKENIZER_FILE
        try:
            tokenizer = Tokenizer.from_file(str(path))
        except Exception as exc:  # a missing or damaged file: the tokenizers error is untyped
            raise ServiceUnavailableError(
                f"Tokeniseur du modèle de recherche illisible ({path.name}) : installez-le avec "
                f"« {INSTALL} »."
            ) from exc
        tokenizer.enable_truncation(max_length=MAX_TOKENS)
        tokenizer.enable_padding(pad_id=0, pad_token="<pad>")  # noqa: S106 - a token, no secret
        return tokenizer

    def _open_session(self) -> _Session:
        path = self.model_dir / MODEL_FILE
        if missing_files(self.model_dir):
            raise ServiceUnavailableError(
                f"Modèle de recherche absent : {self.model_dir}. Installez-le avec « {INSTALL} »."
            )
        import onnxruntime as ort  # heavy native module: only loaded when search is used

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.log_severity_level = 3  # errors only
        try:
            session: _Session = ort.InferenceSession(
                str(path), sess_options=options, providers=[CPU_PROVIDER]
            )
        except Exception as exc:  # onnxruntime raises its own untyped errors
            raise ExternalToolError(
                f"Modèle de recherche illisible ({path.name}) : {exc}", tool="onnxruntime"
            ) from exc
        outputs = [node.name for node in session.get_outputs()]
        if OUTPUT not in outputs:
            raise ExternalToolError(
                f"Modèle de recherche inattendu : sorties {outputs}.", tool="onnxruntime"
            )
        return session


@dataclass(slots=True)
class EmbedderLoader:
    """The installed embedding model (``EmbedderSource``): one per process, loaded on first use,
    None while ``vfe models search`` has not installed it."""

    store: ModelStore | None
    threads: int = EMBED_THREADS
    _loaded: GemmaEmbedder | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __call__(self) -> TextEmbedder | None:
        if self.store is None:
            return None
        item = spec(DEFAULTS["embeddings"])
        folder = self.store.installed(item)
        identity = self.store.identity(item)
        if folder is None or identity is None:
            return None
        with self._lock:
            loaded = self._loaded
            if loaded is None or loaded.model_dir != folder or loaded.model_id != identity:
                loaded = GemmaEmbedder(folder, identity, threads=self.threads)
                self._loaded = loaded
            return loaded
