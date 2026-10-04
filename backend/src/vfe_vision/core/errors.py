"""Application error hierarchy, mapped to RFC 9457 problem details by the API layer."""

from __future__ import annotations

from typing import Any, ClassVar


class VfeError(Exception):
    """Base class for expected, user-facing errors."""

    code: ClassVar[str] = "internal_error"
    status: ClassVar[int] = 500
    title: ClassVar[str] = "Erreur interne"

    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class NotFoundError(VfeError):
    code = "not_found"
    status = 404
    title = "Ressource introuvable"


class ConflictError(VfeError):
    code = "conflict"
    status = 409
    title = "Conflit"


class InvalidInputError(VfeError):
    code = "invalid_input"
    status = 422
    title = "Donnée invalide"


class PathNotAllowedError(VfeError):
    code = "path_not_allowed"
    status = 403
    title = "Chemin non autorisé"


class NotAllowedError(VfeError):
    """The user has not allowed this (a setting of the application, off by default)."""

    code = "not_allowed"
    status = 403
    title = "Non autorisé"


class ExternalToolError(VfeError):
    """An external program (ffmpeg, ffprobe, exiftool…) failed or is missing."""

    code = "external_tool_error"
    status = 502
    title = "Échec d'un outil externe"


class ServiceUnavailableError(VfeError):
    """A required local or remote service (LM Studio, Open-Meteo…) cannot be reached."""

    code = "service_unavailable"
    status = 503
    title = "Service indisponible"


class ResolveUnavailableError(ServiceUnavailableError):
    """DaVinci Resolve cannot be read: not installed, not running, scripts refused, no project
    (``reason`` says which)."""

    code = "resolve_unavailable"
    title = "DaVinci Resolve indisponible"


class CancelledError(VfeError):
    """Raised inside long-running work when the user cancelled the job."""

    code = "cancelled"
    status = 409
    title = "Opération annulée"
