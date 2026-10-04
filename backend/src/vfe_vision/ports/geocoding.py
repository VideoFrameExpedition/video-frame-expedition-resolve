"""Ports: online reverse geocoding (raw answers, cached by the caller) and offline gazetteer."""

from __future__ import annotations

from typing import Any, Protocol

from vfe_vision.domain.place import Place


class ReverseGeocoder(Protocol):
    def reverse(
        self,
        latitude: float,
        longitude: float,
        *,
        language: str,
        natural: bool = False,
        email: str | None = None,
    ) -> dict[str, Any]:
        """Raw ``jsonv2`` answer for an already-rounded point (``{"error": …}`` when nothing is
        there). ``email`` is the user's contact address, when entered. Raise
        ``ServiceUnavailableError`` (retry later) or ``GeocoderBlockedError``."""
        ...


class Gazetteer(Protocol):
    @property
    def version(self) -> str | None:
        """Identity of the installed data (part of cache keys), ``None`` when absent."""
        ...

    def nearest(self, latitude: float, longitude: float) -> Place:
        """Nearest populated place, country and « at sea » without any network access."""
        ...
