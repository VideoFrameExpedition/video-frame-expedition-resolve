"""subtitle_files: the subtitle files written next to the videos

Revision ID: 0016
Revises: 0015

One new table: each subtitle file the application wrote in a video folder, with the SHA-256 of
what it wrote, so that it replaces only its own files, unchanged. Empty at first: no such file was
written before this version.

Downgrade drops the table; the files stay where they are (an older version never touches them).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subtitle_files",
        sa.Column("path_key", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("written_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("path_key", name=op.f("pk_subtitle_files")),
    )


def downgrade() -> None:
    op.drop_table("subtitle_files")
