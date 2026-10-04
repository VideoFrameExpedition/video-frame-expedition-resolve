"""stage runs: key of the video data each result was computed from

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("stage_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("input_key", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("stage_runs", schema=None) as batch_op:
        batch_op.drop_column("input_key")
