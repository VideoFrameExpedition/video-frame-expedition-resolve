"""detections: where the living subjects are in the keyframes

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    ref_table = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column], [target], name=op.f(f"fk_{table}_{column}_{ref_table}"), ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "detections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("keyframe_id", sa.String(length=32), nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("t_s", sa.Double(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("box", sa.JSON(), nullable=False),
        sa.Column("score", sa.Double(), nullable=True),
        sa.Column("main", sa.Boolean(), nullable=False),
        sa.Column("points", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=False),
        _fk("detections", "keyframe_id", "keyframes.id"),
        _fk("detections", "video_id", "videos.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detections")),
    )
    with op.batch_alter_table("detections", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_detections_keyframe_id"), ["keyframe_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_detections_video_id"), ["video_id"], unique=False)

    op.create_table(
        "subject_scans",
        sa.Column("keyframe_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("found", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _fk("subject_scans", "keyframe_id", "keyframes.id"),
        _fk("subject_scans", "video_id", "videos.id"),
        sa.PrimaryKeyConstraint("keyframe_id", "source", name=op.f("pk_subject_scans")),
    )
    with op.batch_alter_table("subject_scans", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_subject_scans_video_id"), ["video_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("subject_scans", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_subject_scans_video_id"))
    op.drop_table("subject_scans")
    with op.batch_alter_table("detections", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_detections_video_id"))
        batch_op.drop_index(batch_op.f("ix_detections_keyframe_id"))
    op.drop_table("detections")
