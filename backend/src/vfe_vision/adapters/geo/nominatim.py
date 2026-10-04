"""Nominatim reverse geocoding (OpenStreetMap), within its usage policy.

Policy: at most one request per second from the single worker process, an identifying
User-Agent (never the HTTP library's), a permanent local cache (kept by the caller, « not found »
included), no bulk or periodic queries. The base URL is configurable (self-hosted or commercial
Nominatim-compatible service); the contact e-mail (``email`` parameter, as the policy asks for
heavier use) is only sent when the user entered one in the settings.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx

from vfe_vision import __version__
from vfe_vision.core.errors import ServiceUnavailableError, VfeError
from vfe_vision.core.ratelimit import MinInterval

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
MIN_INTERVAL_S = 1.1


class GeocoderBlockedError(VfeError):
    """HTTP 403: the service refused us; stop asking for the rest of the session."""

    code = "geocoder_blocked"
    status = 502


class NominatimClient:
    def __init__(
        self,
        *,
        base_url: str = NOMINATIM_URL,
        timeout_s: float = 10.0,
        min_interval_s: float = MIN_INTERVAL_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._throttle = MinInterval(min_interval_s)
        self._blocked = threading.Event()
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            headers={"User-Agent": f"vfe-vision/{__version__} (local video analysis application)"},
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def reverse(
        self,
        latitude: float,
        longitude: float,
        *,
        language: str,
        natural: bool = False,
        email: str | None = None,
    ) -> dict[str, Any]:
        if self._blocked.is_set():
            raise GeocoderBlockedError("Nominatim a refusé nos requêtes pendant cette session")
        params: dict[str, str] = {
            "format": "jsonv2",
            "lat": f"{latitude:.3f}",
            "lon": f"{longitude:.3f}",
            "addressdetails": "1",
            "accept-language": language,
        }
        params |= {"layer": "natural", "zoom": "16"} if natural else {"zoom": "18"}
        if email:
            params["email"] = email
        self._throttle.wait()
        try:
            response = self._http.get(f"{self.base_url}/reverse", params=params)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(f"Nominatim injoignable : {exc}") from exc
        if response.status_code == 403:
            self._blocked.set()
            raise GeocoderBlockedError("Nominatim a refusé la requête (HTTP 403)")
        if response.status_code == 429 or response.status_code >= 500:
            raise ServiceUnavailableError(f"Nominatim indisponible (HTTP {response.status_code})")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ServiceUnavailableError("Réponse Nominatim illisible") from exc
        if response.status_code >= 400 or not isinstance(payload, dict):
            raise VfeError(f"Nominatim a refusé la requête (HTTP {response.status_code})")
        return payload
