"""Versioned prompt templates (English instructions, configurable output language)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_DIR = Path(__file__).parent

LANGUAGE_NAMES = {"fr": "French", "en": "English", "es": "Spanish", "de": "German", "it": "Italian"}


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    version: str
    system: str
    user: str


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(_DIR),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        autoescape=False,  # noqa: S701 - plain-text prompts, not HTML
    )


def render(name: str, version: int, **context: Any) -> RenderedPrompt:
    """Render ``<name>.v<version>.system.j2`` and ``.user.j2`` with ``context``."""
    env = _environment()
    context.setdefault("language_name", LANGUAGE_NAMES.get(context.get("language", "fr"), "French"))
    system = env.get_template(f"{name}.v{version}.system.j2").render(**context).strip()
    user = env.get_template(f"{name}.v{version}.user.j2").render(**context).strip()
    return RenderedPrompt(version=f"{name}.v{version}", system=system, user=user)
