"""timeline_bins, library_roots.kind: DaVinci Resolve timelines as bins of the library

Revision ID: 0015
Revises: 0014

Two new tables, and two columns on library_roots: every existing root becomes ``kind = folder``
(what it always was); ``files`` is only filled for the new roots of chosen files. One column on
videos, ``sidecar_links``: what the analysis file last written says of the video's timelines
(empty for every existing video: their files name none). The new job
kind ``sync_timeline`` needs no change: job kinds are stored as plain strings (no native enum,
no CHECK constraint), as when ``probe_vision`` was added.

Downgrade drops the tables and the columns, and the ``sync_timeline`` jobs that the older
version could not read. A root of chosen files then behaves as a non-recursive folder: its next
scan adds the other videos of its folder.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_roots", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "kind",
                sa.Enum("folder", "files", name="rootkind", native_enum=False, length=32),
                server_default="folder",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("files", sa.JSON(), nullable=True))
    with op.batch_alter_table("videos", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sidecar_links", sa.String(length=16), nullable=True))

    op.create_table(
        "timeline_bins",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column("resolve", sa.JSON(), nullable=False),
        sa.Column("auto_analyze", sa.Boolean(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timeline_bins")),
        sa.UniqueConstraint("source_key", name=op.f("uq_timeline_bins_source_key")),
    )
    op.create_table(
        "timeline_bin_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bin_id", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("path_key", sa.Text(), nullable=False),
        sa.Column("video_key", sa.Text(), nullable=True),
        sa.Column("uses", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["bin_id"], ["timeline_bins.id"], name=op.f("fk_timeline_bin_items_bin_id_timeline_bins"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timeline_bin_items")),
        sa.UniqueConstraint("bin_id", "path_key", name=op.f("uq_timeline_bin_items_bin_id")),
    )
    with op.batch_alter_table("timeline_bin_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_timeline_bin_items_path_key"), ["path_key"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_timeline_bin_items_video_key"), ["video_key"], unique=False
        )


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'sync_timeline'")
    with op.batch_alter_table("timeline_bin_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_timeline_bin_items_video_key"))
        batch_op.drop_index(batch_op.f("ix_timeline_bin_items_path_key"))
    op.drop_table("timeline_bin_items")
    op.drop_table("timeline_bins")
    with op.batch_alter_table("videos", schema=None) as batch_op:
        batch_op.drop_column("sidecar_links")
    with op.batch_alter_table("library_roots", schema=None) as batch_op:
        batch_op.drop_column("files")
        batch_op.drop_column("kind")
