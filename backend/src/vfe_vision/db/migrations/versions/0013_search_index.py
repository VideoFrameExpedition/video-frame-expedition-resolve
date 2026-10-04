"""search_chunks, search_fts (FTS5) and chunk_vectors: the search index

Revision ID: 0013
Revises: 0012

``search_fts`` is an FTS5 table with external content (the text lives in ``search_chunks``
only), kept in step by three triggers; a video removed from the library takes its passages,
their words (the delete trigger fires on the cascade) and their vectors with it. Accents and
case are folded by the tokenizer (``remove_diacritics 2``): « ete » finds « été ».
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FTS = (
    "CREATE VIRTUAL TABLE search_fts USING fts5("
    "text, content='search_chunks', content_rowid='id', "
    "tokenize='unicode61 remove_diacritics 2')"
)
_TRIGGERS = (
    "CREATE TRIGGER search_chunks_ai AFTER INSERT ON search_chunks BEGIN "
    "INSERT INTO search_fts(rowid, text) VALUES (new.id, new.text); END",
    "CREATE TRIGGER search_chunks_ad AFTER DELETE ON search_chunks BEGIN "
    "INSERT INTO search_fts(search_fts, rowid, text) VALUES ('delete', old.id, old.text); END",
    "CREATE TRIGGER search_chunks_au AFTER UPDATE ON search_chunks BEGIN "
    "INSERT INTO search_fts(search_fts, rowid, text) VALUES ('delete', old.id, old.text); "
    "INSERT INTO search_fts(rowid, text) VALUES (new.id, new.text); END",
)


def upgrade() -> None:
    op.create_table(
        "search_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("t_start", sa.Double(), nullable=True),
        sa.Column("t_end", sa.Double(), nullable=True),
        sa.Column("shot_id", sa.String(length=32), nullable=True),
        sa.Column("keyframe_id", sa.String(length=32), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("facets", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], name=op.f("fk_search_chunks_video_id_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_chunks")),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table("search_chunks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_search_chunks_video_id"), ["video_id"], unique=False)
    op.create_table(
        "chunk_vectors",
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["search_chunks.id"],
            name=op.f("fk_chunk_vectors_chunk_id_search_chunks"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id", name=op.f("pk_chunk_vectors")),
    )
    with op.batch_alter_table("chunk_vectors", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_chunk_vectors_model"), ["model"], unique=False)
    op.execute(_FTS)
    for trigger in _TRIGGERS:
        op.execute(trigger)


def downgrade() -> None:
    for name in ("search_chunks_au", "search_chunks_ad", "search_chunks_ai"):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.execute("DROP TABLE IF EXISTS search_fts")
    with op.batch_alter_table("chunk_vectors", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_chunk_vectors_model"))
    op.drop_table("chunk_vectors")
    with op.batch_alter_table("search_chunks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_search_chunks_video_id"))
    op.drop_table("search_chunks")
