"""Uvicorn factory entry point (``vfe serve`` uses it so ``--reload`` works)."""

from __future__ import annotations

from fastapi import FastAPI

from vfe_vision.api.app import create_app
from vfe_vision.core.config import get_settings
from vfe_vision.core.logging import configure_logging


def app_from_env() -> FastAPI:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        log_dir=settings.logs_dir,
        process_name="api",
    )
    return create_app(settings)
