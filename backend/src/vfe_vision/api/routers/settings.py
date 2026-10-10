"""User preferences."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query

from vfe_vision.api.deps import Container
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.services import lmstudio_link
from vfe_vision.services import settings as settings_service
from vfe_vision.services.lmstudio_link import LmStudioChoice, LmStudioLinkOut, LmStudioTestOut

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/analysis")
def get_analysis_preferences(c: Container) -> AnalysisPreferences:
    return settings_service.get_preferences(c)


@router.patch("/analysis")
def update_analysis_preferences(
    c: Container,
    patch: dict[str, Any] = Body(...),
) -> AnalysisPreferences:
    return settings_service.update_preferences(c, patch)


# ------------------------------------------------------------------ where the model server runs
@router.get("/lmstudio")
def get_lmstudio_link(c: Container) -> LmStudioLinkOut:
    """The model server in use (LM Studio, or an OpenAI-compatible server such as vLLM), its
    kind and settings, and the ones used before."""
    return lmstudio_link.read(c)


@router.put("/lmstudio")
def choose_lmstudio(c: Container, choice: LmStudioChoice) -> LmStudioLinkOut:
    """Talk to the model server at this address from now on (no address: the installation's
    own), with its kind and settings."""
    return lmstudio_link.choose(c, choice)


@router.post("/lmstudio/test")
async def test_lmstudio(c: Container, choice: LmStudioChoice) -> LmStudioTestOut:
    """Whether a model server answers at this address, which kind, what it has loaded or serves,
    and whether its vision model takes an image; changes nothing."""
    return await lmstudio_link.test(c, choice)


@router.delete("/lmstudio/past")
def forget_lmstudio(
    c: Container,
    url: str = Query(max_length=400, description="The address to forget, as listed."),
) -> LmStudioLinkOut:
    """Drop one of the past connections."""
    return lmstudio_link.forget(c, url)
