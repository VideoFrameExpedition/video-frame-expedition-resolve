"""device clock offset, HDR format, VFR, capture rate, camera OS

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("hdr_format", sa.String(length=16), nullable=True),
        sa.Column("hdr_peak_nits", sa.Double(), nullable=True),
        sa.Column("is_vfr", sa.Boolean(), nullable=True),
        sa.Column("capture_fps", sa.Double(), nullable=True),
        sa.Column("capture_utc_offset_min", sa.Integer(), nullable=True),
        sa.Column("camera_os", sa.String(length=32), nullable=True),
    ]


def upgrade() -> None:
    with op.batch_alter_table("videos", schema=None) as batch_op:
        for column in _columns():
            batch_op.add_column(column)


def downgrade() -> None:
    with op.batch_alter_table("videos", schema=None) as batch_op:
        for column in reversed(_columns()):
            batch_op.drop_column(column.name)
