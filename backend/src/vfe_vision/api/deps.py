"""FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, Header, Query, Request

from vfe_vision.domain.translation import Dictionary
from vfe_vision.services.container import AppContainer
from vfe_vision.services.reading import texts_in


def get_container(request: Request) -> AppContainer:
    container: AppContainer = request.app.state.container
    return container


Container = Annotated[AppContainer, Depends(get_container)]

LANGUAGE_HEADER = "X-VFE-Language"


def display_language(
    lang: Annotated[
        Literal["fr", "en"] | None,
        Query(description="Language of the analysis texts (direct link, download)."),
    ] = None,
    header: Annotated[
        str | None,
        Header(alias=LANGUAGE_HEADER, description="Language of the requesting interface."),
    ] = None,
) -> str | None:
    """The language the analyses are read in: the interface's; None: the analysis
    language of the application."""
    if lang:
        return lang
    wanted = (header or "").strip().lower()[:2]
    return wanted if wanted in {"fr", "en"} else None


DisplayLanguage = Annotated[str | None, Depends(display_language)]


def reading(c: Container, language: DisplayLanguage) -> Dictionary:
    return texts_in(c, language)


# The analyses' texts in the language asked for: ``tr(text)``.
Texts = Annotated[Dictionary, Depends(reading)]
