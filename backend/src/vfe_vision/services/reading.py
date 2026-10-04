"""In which language the analyses are read.

The web interface asks in its own language (header ``X-VFE-Language``, or ``?lang=`` on a
link). Claude (MCP), the command line and the jobs read in the analysis language of the
application. Either way, the texts go through the dictionary of that language: a text the model
wrote in the other language reads translated too.
"""

from __future__ import annotations

from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.translation import Dictionary
from vfe_vision.services.container import AppContainer


def texts_in(c: AppContainer, language: str | None = None) -> Dictionary:
    """The dictionary of ``language`` (default: the analysis language of the application)."""
    return c.translations.get(c.db, language or load_preferences(c.db).language)


def language_of(c: AppContainer, texts: Dictionary | None) -> str:
    """The language labels computed when read (sounds, detector names) are shown in."""
    if texts is not None and texts.language:
        return texts.language
    return load_preferences(c.db).language
