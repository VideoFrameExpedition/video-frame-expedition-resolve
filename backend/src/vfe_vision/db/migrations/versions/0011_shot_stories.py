"""shot_stories: what happens in each shot, told by the vision model

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    ref_table = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column], [target], name=op.f(f"fk_{table}_{column}_{ref_table}"), ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "shot_stories",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("shot_id", sa.String(length=32), nullable=False),
        sa.Column("part", sa.Integer(), nullable=False),
        sa.Column("parts", sa.Integer(), nullable=False),
        sa.Column("start_s", sa.Double(), nullable=False),
        sa.Column("end_s", sa.Double(), nullable=False),
        sa.Column("frame_times", sa.JSON(), nullable=False),
        sa.Column("keyframe_ids", sa.JSON(), nullable=False),
        sa.Column("frame_paths", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("answer", sa.JSON(), nullable=False),
        sa.Column("story", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _fk("shot_stories", "video_id", "videos.id"),
        _fk("shot_stories", "shot_id", "shots.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shot_stories")),
        sa.UniqueConstraint("shot_id", "part", name=op.f("uq_shot_stories_shot_id")),
    )
    with op.batch_alter_table("shot_stories", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_shot_stories_shot_id"), ["shot_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_shot_stories_video_id"), ["video_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("shot_stories", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_shot_stories_video_id"))
        batch_op.drop_index(batch_op.f("ix_shot_stories_shot_id"))
    op.drop_table("shot_stories")
