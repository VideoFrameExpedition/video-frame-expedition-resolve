"""User preferences and the LM Studio model catalogue for the settings page."""

from __future__ import annotations

from typing import Any

from vfe_vision.adapters.lmstudio.catalog import ModelInfo
from vfe_vision.db import preferences
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.services.container import AppContainer


def get_preferences(c: AppContainer) -> AnalysisPreferences:
    return preferences.load_preferences(c.db)


def update_preferences(c: AppContainer, patch: dict[str, Any]) -> AnalysisPreferences:
    return preferences.update_preferences(c.db, patch)


async def list_models(c: AppContainer) -> list[ModelInfo]:
    return await c.lmstudio.list_models()
