"""stage_runs: mark the skips caused by the video itself (no position, browser-readable…)

Such a skip only changes with new video data, which invalidates it explicitly. Marking them lets
an ordinary analysis (« Complete ») leave the video alone instead of re-checking it every time.
The reasons are the exact texts the stages wrote before the flag existed.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMANENT_REASONS = (
    "Pas de piste vidéo exploitable",
    "Trop peu d'images décodées",
    "Pas de piste audio",
    "Transcription désactivée pour cette vidéo",
    "Position de tournage inconnue",
    "Lisible directement par le navigateur",
    "Heure de tournage trop incertaine pour le soleil",
    "Heure de tournage trop incertaine pour la météo",
    "Date de tournage hors de la plage de calcul (1900–2100)",
    "Heure ou position de tournage inconnue",
    "Date de tournage dans le futur : horloge de l'appareil ?",
)


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE stage_runs SET summary = json_set(COALESCE(summary, '{}'), '$.permanent', "
            "json('true')) WHERE status = 'skipped' AND skip_reason IN :reasons"
        ).bindparams(sa.bindparam("reasons", expanding=True)),
        {"reasons": list(PERMANENT_REASONS)},
    )


def downgrade() -> None:
    op.execute(
        "UPDATE stage_runs SET summary = json_remove(summary, '$.permanent') "
        "WHERE status = 'skipped' AND summary IS NOT NULL"
    )
