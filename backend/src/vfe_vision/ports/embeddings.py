"""Port: text embeddings for the search by meaning.

The implementation is EmbeddingGemma-300m on the CPU (adapters/embeddings); tests inject a
fake. Vectors are unit length: the cosine of two texts is their dot product.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

import numpy as np
import numpy.typing as npt

Vectors = npt.NDArray[np.float32]


class TextEmbedder(Protocol):
    @property
    def model_id(self) -> str:
        """What the vectors are recorded with (model and revision): vectors of another model
        are never compared with this one's."""
        ...

    @property
    def dim(self) -> int: ...

    @property
    def min_similarity(self) -> float:
        """Below this cosine, a passage has nothing to do with a query (the model's own scale)."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> Vectors:
        """One unit vector per text ``[len(texts), dim]``, as stored in the index."""
        ...

    def embed_query(self, text: str) -> Vectors:
        """The unit vector ``[dim]`` of a search query (queries and passages are embedded with
        different prefixes)."""
        ...


# The installed embedder, or None while its model is not installed (checked at each call: a
# model installed while the application runs is used without a restart).
EmbedderSource = Callable[[], TextEmbedder | None]


def no_embedder() -> TextEmbedder | None:
    """No search by meaning: the index keeps its words only."""
    return None
