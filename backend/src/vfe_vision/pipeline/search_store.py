"""Write a video's passages, their words (FTS5, by trigger) and their vectors.

A video's passages are replaced in one transaction: a search never sees half an index. New
passages always get new ids (AUTOINCREMENT), which tells a process holding a copy of the
vectors that it is out of date.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt
import sqlalchemy as sa

from vfe_vision.db.models import ChunkVector, SearchChunk
from vfe_vision.db.session import Database
from vfe_vision.domain.search_chunks import Chunk, ChunkKind


def pack(vector: npt.ArrayLike) -> bytes:
    """A vector as stored: little-endian float32."""
    return np.asarray(vector, dtype="<f4").tobytes()


def unpack(blob: bytes, dim: int) -> npt.NDArray[np.float32]:
    vector = np.frombuffer(blob, dtype="<f4")
    if vector.shape != (dim,):
        raise ValueError(f"vecteur de {vector.shape[0]} dimensions au lieu de {dim}")
    return vector.astype(np.float32)


def _row(video_id: str, chunk: Chunk) -> SearchChunk:
    return SearchChunk(
        video_id=video_id,
        kind=chunk.kind.value,
        t_start=chunk.t_start,
        t_end=chunk.t_end,
        shot_id=chunk.shot_id,
        keyframe_id=chunk.keyframe_id,
        language=chunk.language,
        text=chunk.text,
        facets=chunk.facets,
    )


def _vectors(
    rows: Sequence[SearchChunk], vectors: npt.NDArray[np.float32] | None, model: str | None
) -> list[ChunkVector]:
    if vectors is None or model is None:
        return []
    return [
        ChunkVector(chunk_id=row.id, model=model, dim=int(vector.shape[0]), vector=pack(vector))
        for row, vector in zip(rows, vectors, strict=True)
    ]


def replace_chunks(
    db: Database,
    video_id: str,
    chunks: Sequence[Chunk],
    vectors: npt.NDArray[np.float32] | None = None,
    model: str | None = None,
) -> int:
    """Swap every passage of the video (and its vectors, when given); returns how many."""
    with db.write() as session:
        session.execute(sa.delete(SearchChunk).where(SearchChunk.video_id == video_id))
        rows = [_row(video_id, chunk) for chunk in chunks]
        session.add_all(rows)
        session.flush()
        session.add_all(_vectors(rows, vectors, model))
    return len(chunks)


def replace_video_chunk(
    db: Database,
    video_id: str,
    chunks: Sequence[Chunk],
    vectors: npt.NDArray[np.float32] | None = None,
    model: str | None = None,
) -> bool:
    """Swap the passages about the whole video only (one per language); False when the video is
    not indexed yet (the ``index`` stage will write all of its passages)."""
    with db.write() as session:
        indexed = session.execute(
            sa.select(SearchChunk.id).where(SearchChunk.video_id == video_id).limit(1)
        ).first()
        if indexed is None:
            return False
        session.execute(
            sa.delete(SearchChunk).where(
                SearchChunk.video_id == video_id, SearchChunk.kind == ChunkKind.VIDEO.value
            )
        )
        rows = [_row(video_id, chunk) for chunk in chunks]
        session.add_all(rows)
        session.flush()
        session.add_all(_vectors(rows, vectors, model))
    return True
