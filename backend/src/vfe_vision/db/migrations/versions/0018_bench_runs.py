"""bench_runs: the runs of the model bench

Revision ID: 0018
Revises: 0017

One new table: a row per run of the model bench, with the frames of the library the vision
models were asked about, what each model answered and measured, and the blind ratings the user
gave. Empty at first; no analysis row changes. The new job kind ``bench_models`` needs no
change: the column is a plain string (no CHECK constraint), as when ``probe_vision`` was added.

Downgrade drops the table (the results of the runs go with it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bench_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("ratings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bench_runs")),
    )
    op.create_index(op.f("ix_bench_runs_job_id"), "bench_runs", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_bench_runs_job_id"), table_name="bench_runs")
    op.drop_table("bench_runs")
