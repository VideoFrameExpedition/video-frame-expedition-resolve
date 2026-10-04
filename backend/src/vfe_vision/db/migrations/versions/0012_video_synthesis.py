"""video_synthesis: title, summary, chapters and highlights of each video

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_synthesis",
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("rules_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("strategy", sa.String(length=16), nullable=False),
        sa.Column("input_variant", sa.String(length=8), nullable=False),
        sa.Column("proofread", sa.Boolean(), nullable=False),
        sa.Column("input_key", sa.String(length=64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], name=op.f("fk_video_synthesis_video_id_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_video_synthesis")),
    )


def downgrade() -> None:
    op.drop_table("video_synthesis")
