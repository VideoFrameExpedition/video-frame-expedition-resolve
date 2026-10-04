"""Stored artifacts (keyframes, thumbnails) served from the data directory only."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from vfe_vision.api.deps import Container
from vfe_vision.core.errors import NotFoundError

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/{rel_path:path}", response_class=FileResponse)
def artifact(c: Container, rel_path: str) -> FileResponse:
    path = c.artifacts.resolve(rel_path)  # rejects traversal outside the store
    if not path.is_file():
        raise NotFoundError("Fichier introuvable")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=60"})
