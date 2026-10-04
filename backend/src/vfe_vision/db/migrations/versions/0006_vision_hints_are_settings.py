"""vision_frames: context hints moved from input facts to settings

Input keys recorded for ``vision_frames`` included the hints; without this reset every frame
would look changed and be described again at the next ordinary analysis. A NULL input key
makes the next analysis adopt the current data as the result's baseline.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE stage_runs SET input_key = NULL WHERE stage = 'vision_frames'")


def downgrade() -> None:
    pass  # nothing to restore: NULL input keys are adopted again
