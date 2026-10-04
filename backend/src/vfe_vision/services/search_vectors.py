"""The vectors of the search index as one matrix, kept by the API process.

Brute force is enough: a library of a few thousand passages is a few megabytes (1 KiB each),
and one matrix product per query takes milliseconds. The matrix is read again when the index
changed, which a cheap signature tells: the number of vectors of the model and the largest
passage id (ids only grow, so a rebuilt video always changes it, a removed one the count).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import sqlalchemy as sa

from vfe_vision.db.models import ChunkVector
from vfe_vision.db.session import Database
from vfe_vision.pipeline.search_store import unpack


@dataclass(frozen=True, slots=True)
class VectorMatrix:
    key: tuple[str, int, int]  # model, vectors, largest passage id
    ids: npt.NDArray[np.int64]  # passage id of each row
    matrix: npt.NDArray[np.float32]  # [passages, dim], unit rows

    def __len__(self) -> int:
        return len(self.ids)


class VectorCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: VectorMatrix | None = None
        self.loads = 0  # how many times the matrix was read (tests)

    def get(self, db: Database, model: str) -> VectorMatrix:
        """The model's vectors, read again only when the index changed since the last call."""
        with self._lock, db.read() as session:  # one snapshot for the signature and the rows
            count, top = session.execute(
                sa.select(sa.func.count(), sa.func.max(ChunkVector.chunk_id)).where(
                    ChunkVector.model == model
                )
            ).one()
            key = (model, int(count), int(top or 0))
            if self._current is not None and self._current.key == key:
                return self._current
            rows = session.execute(
                sa.select(ChunkVector.chunk_id, ChunkVector.dim, ChunkVector.vector)
                .where(ChunkVector.model == model)
                .order_by(ChunkVector.chunk_id)
            ).all()
            dims = {row.dim for row in rows}
            dim = dims.pop() if len(dims) == 1 else 0
            usable = [row for row in rows if row.dim == dim]
            matrix = (
                np.vstack([unpack(row.vector, dim) for row in usable])
                if usable
                else np.zeros((0, dim), dtype=np.float32)
            )
            self._current = VectorMatrix(
                key, np.array([row.chunk_id for row in usable], dtype=np.int64), matrix
            )
            self.loads += 1
            return self._current

    def clear(self) -> None:
        with self._lock:
            self._current = None
