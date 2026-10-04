"""A deterministic stand-in for EmbeddingGemma: a bag of word stems hashed into 256 dimensions.

Two texts sharing words (or their first five letters: « cuisine », « cuisiner ») point the same
way; unrelated texts are orthogonal. Enough to test the search by meaning without the model.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import numpy as np

from vfe_vision.domain.search_text import STOPWORDS, words
from vfe_vision.ports.embeddings import Vectors

DIM = 256


class FakeEmbedder:
    def __init__(self, model_id: str = "fake/bag-of-stems@1") -> None:
        self._model_id = model_id
        self.documents = 0  # texts embedded as passages
        self.queries = 0
        self.fail = False  # raise like a broken model

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return DIM

    @property
    def min_similarity(self) -> float:
        return 0.05

    def embed_documents(self, texts: Sequence[str]) -> Vectors:
        self._check()
        self.documents += len(texts)
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        return np.stack([_vector(text) for text in texts]).astype(np.float32)

    def embed_query(self, text: str) -> Vectors:
        self._check()
        self.queries += 1
        return _vector(text)

    def _check(self) -> None:
        if self.fail:
            from vfe_vision.core.errors import ExternalToolError

            raise ExternalToolError("modèle cassé", tool="fake")


def _vector(text: str) -> Vectors:
    vector = np.zeros(DIM, dtype=np.float32)
    for word in words(text):
        if len(word) < 3 or word in STOPWORDS:
            continue
        digest = hashlib.sha1(word[:5].encode("utf-8"), usedforsecurity=False).digest()
        vector[int.from_bytes(digest[:4], "little") % DIM] += 1.0
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector
