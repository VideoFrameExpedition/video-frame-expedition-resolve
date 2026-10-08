"""RFC 9457 problem details for every error response."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from vfe_vision.core import native_modules
from vfe_vision.core.errors import VfeError
from vfe_vision.core.logging import get_logger

log = get_logger(__name__)
PROBLEM_JSON = "application/problem+json"


def problem(
    status: int, title: str, detail: str, *, code: str, instance: str, **extra: Any
) -> JSONResponse:
    body = {
        "type": f"https://vfe-vision.local/problems/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "code": code,
        **extra,
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(VfeError)
    async def _vfe_error(request: Request, exc: VfeError) -> JSONResponse:
        if exc.status >= 500:  # a tool or a service failed: what the user read, for later
            log.warning(
                "request failed",
                path=request.url.path,
                status=exc.status,
                code=exc.code,
                detail=exc.detail,
                **{f"extra_{k}": str(v) for k, v in exc.extra.items()},
            )
        return problem(
            exc.status,
            exc.title,
            exc.detail,
            code=exc.code,
            instance=request.url.path,
            **{k: v for k, v in exc.extra.items() if isinstance(v, str | int | float | bool)},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return problem(
            422,
            "Requête invalide",
            "Certains champs sont invalides.",
            code="validation_error",
            instance=request.url.path,
            errors=errors,
        )

    @app.exception_handler(ValidationError)
    async def _model_validation_error(request: Request, exc: ValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return problem(
            422,
            "Donnée invalide",
            "; ".join(f"{'.'.join(map(str, e['loc']))} : {e['msg']}" for e in errors),
            code="invalid_input",
            instance=request.url.path,
            errors=errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(
            exc.status_code,
            "Erreur HTTP",
            str(exc.detail),
            code=f"http_{exc.status_code}",
            instance=request.url.path,
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error", path=request.url.path, exc_info=exc)
        refusal = native_modules.describe(exc)  # a compiled module imported on demand
        if refusal is not None:
            return problem(
                503,
                "Fichier refusé par Windows",
                refusal,
                code="refused_by_windows",
                instance=request.url.path,
            )
        return problem(
            500,
            "Erreur interne",
            "Une erreur inattendue est survenue ; consultez les journaux.",
            code="internal_error",
            instance=request.url.path,
        )
