"""Persistence of user preferences (single JSON document per key in ``settings``)."""

from __future__ import annotations

from typing import Any

from vfe_vision.db.models import Setting
from vfe_vision.db.session import Database
from vfe_vision.domain.preferences import AnalysisPreferences

ANALYSIS_KEY = "analysis"


def load_preferences(db: Database) -> AnalysisPreferences:
    with db.read() as session:
        row = session.get(Setting, ANALYSIS_KEY)
        return AnalysisPreferences.model_validate(row.value if row else {})


def update_preferences(db: Database, patch: dict[str, Any]) -> AnalysisPreferences:
    """Merge ``patch`` into the stored preferences (validated before saving)."""
    with db.write() as session:
        row = session.get(Setting, ANALYSIS_KEY)
        current = row.value if row else {}
        merged = AnalysisPreferences.model_validate({**current, **patch})
        value = merged.model_dump(mode="json")
        if row is None:
            session.add(Setting(key=ANALYSIS_KEY, value=value))
        else:
            row.value = value
        return merged
