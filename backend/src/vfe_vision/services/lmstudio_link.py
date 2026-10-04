"""The LM Studio the application talks to: the one of this computer, or another one
chosen on the System page, tested before use and remembered afterwards."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from pydantic import BaseModel, Field

from vfe_vision.core.errors import ConflictError, InvalidInputError, NotFoundError, VfeError
from vfe_vision.db.base import utcnow
from vfe_vision.db.lmstudio_link import load_link, save_link
from vfe_vision.db.models import Job
from vfe_vision.domain.enums import JobKind, JobStatus
from vfe_vision.domain.lmstudio_link import (
    MAX_ADDRESS,
    MAX_TOKEN,
    LmStudioLink,
    LmStudioTarget,
    address_of,
    is_local,
    remembered,
    target_of,
    token_of,
)
from vfe_vision.services.container import AppContainer

# The jobs that talk to LM Studio: one of them running keeps the address as it is.
VISION_KINDS = (JobKind.ANALYZE_VIDEO, JobKind.PROBE_VISION, JobKind.BENCH_MODELS)


class LmStudioChoice(BaseModel):
    address: str | None = Field(
        default=None,
        max_length=MAX_ADDRESS,
        description="Address of LM Studio (192.168.1.20, 192.168.1.20:1234, "
        "http://living-room-pc:1234); empty: the one on this computer.",
    )
    token: str | None = Field(
        default=None,
        max_length=MAX_TOKEN,
        description="API token of this LM Studio, if it asks for one. Absent: the one already "
        "saved for this address is kept; empty string: it is removed.",
    )


class PastConnectionOut(BaseModel):
    url: str
    last_used_at: datetime
    local: bool
    has_token: bool


class LmStudioLinkOut(BaseModel):
    """The address in use and the ones used before. A token is never given back."""

    url: str
    custom: bool  # chosen in the interface (False: the installation's own address)
    default_url: str
    local: bool  # this computer: the images do not leave it
    has_token: bool
    past: list[PastConnectionOut]


class LmStudioTestOut(BaseModel):
    url: str
    ok: bool
    local: bool
    models: int = 0
    vision_models: int = 0
    loaded: list[str] = Field(default_factory=list)  # the vision models loaded there
    error: str | None = None


def _default(c: AppContainer) -> LmStudioTarget:
    token = c.settings.lmstudio_token.get_secret_value() if c.settings.lmstudio_token else None
    return LmStudioTarget(c.settings.lmstudio_url.rstrip("/"), token)


def _out(c: AppContainer, link: LmStudioLink) -> LmStudioLinkOut:
    default = _default(c)
    target = target_of(link, default.url, default.token)
    return LmStudioLinkOut(
        url=target.url,
        custom=link.url is not None,
        default_url=default.url,
        local=is_local(target.url),
        has_token=bool(target.token),
        past=[
            PastConnectionOut(
                url=past.url,
                last_used_at=past.last_used_at,
                local=is_local(past.url),
                has_token=bool(past.token),
            )
            for past in link.past
        ],
    )


def _address(text: str | None) -> str | None:
    """The address in full, or None for the installation's own."""
    if text is None or not text.strip():
        return None
    try:
        return address_of(text)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc


def read(c: AppContainer) -> LmStudioLinkOut:
    return _out(c, load_link(c.db))


def choose(c: AppContainer, choice: LmStudioChoice) -> LmStudioLinkOut:
    """Talk to this LM Studio from now on, and remember it among the past connections."""
    link = load_link(c.db)
    default = _default(c)
    before = target_of(link, default.url, default.token)
    url = _address(choice.address)
    if url == default.url:
        url = None  # the installation's address is not a choice of the interface
    if url is None:
        link.url = None
    else:
        typed = None if choice.token is None else choice.token.strip() or None
        token = token_of(link, url) if choice.token is None else typed
        link.url = url
        link.past = remembered(link.past, url, utcnow(), token)
    after = target_of(link, default.url, default.token)
    if after != before:
        _nothing_running(c)
    save_link(c.db, link)
    c.lmstudio_link.forget()
    return _out(c, link)


def forget(c: AppContainer, url: str) -> LmStudioLinkOut:
    """Drop a past connection (and its token)."""
    link = load_link(c.db)
    if all(past.url != url for past in link.past):
        raise NotFoundError("Cette adresse n'est pas dans les connexions passées.")
    if link.url == url:
        raise ConflictError(
            "Cette adresse est celle utilisée en ce moment : choisissez-en une autre d'abord."
        )
    link.past = [past for past in link.past if past.url != url]
    save_link(c.db, link)
    return _out(c, link)


async def test(c: AppContainer, choice: LmStudioChoice) -> LmStudioTestOut:
    """Ask the LM Studio at this address for its models, without changing anything."""
    link = load_link(c.db)
    default = _default(c)
    url = _address(choice.address) or default.url
    if choice.token is not None:
        token = choice.token.strip() or None
    else:
        token = default.token if url == default.url else token_of(link, url)
    client = c.lmstudio_probe(LmStudioTarget(url, token))
    try:
        models = await client.list_models()
    except VfeError as exc:
        return LmStudioTestOut(url=url, ok=False, local=is_local(url), error=exc.detail)
    finally:
        await client.aclose()
    vision = [model for model in models if model.vision]
    return LmStudioTestOut(
        url=url,
        ok=True,
        local=is_local(url),
        models=len(models),
        vision_models=len(vision),
        loaded=[model.display_name for model in vision if model.loaded_instances],
    )


def _nothing_running(c: AppContainer) -> None:
    """An analysis under way describes its images with the model of the LM Studio it started
    with: the address does not change under it."""
    with c.db.read() as session:
        running = session.execute(
            sa.select(sa.func.count())
            .select_from(Job)
            .where(Job.kind.in_(VISION_KINDS), Job.status == JobStatus.RUNNING)
        ).scalar_one()
    if running:
        raise ConflictError(
            "Une analyse ou un test de modèles est en cours avec le LM Studio actuel : "
            "attendez sa fin, ou arrêtez-le (page Tâches), avant de changer d'adresse."
        )
