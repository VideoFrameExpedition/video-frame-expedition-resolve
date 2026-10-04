"""stage_runs: a weather skip for a capture date in the future is not permanent

Migration 0009 listed it among the skips caused by the video itself, but it depends on today's
date too: the weather becomes available once that date has passed, so an ordinary analysis
must keep checking it. (0009 was already applied: this corrects it rather than editing it.)

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUTURE_DATE = "Date de tournage dans le futur : horloge de l'appareil ?"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE stage_runs SET summary = json_remove(summary, '$.permanent') "
            "WHERE status = 'skipped' AND skip_reason = :reason"
        ),
        {"reason": FUTURE_DATE},
    )


def downgrade() -> None:
    pass  # 0009's downgrade removes every mark anyway
