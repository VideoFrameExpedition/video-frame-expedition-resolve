"""translations: the texts of the analyses in the other language

Revision ID: 0017
Revises: 0016

One new table, a dictionary: each text of the analyses (descriptions, stories, syntheses, subject
labels, place names) and what it reads in French or in English, keyed by the SHA-256 of the text.
Empty at first: the « Translation » stage fills it; no analysis row changes.

Downgrade drops the table (the analyses read again as they were written).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "translations",
        sa.Column("source_sha", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=8), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("source_sha", "target", name=op.f("pk_translations")),
    )


def downgrade() -> None:
    op.drop_table("translations")
