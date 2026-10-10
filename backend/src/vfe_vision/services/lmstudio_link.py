"""The model server the application talks to: LM Studio on this computer, or another server
chosen on the System page (LM Studio elsewhere, or a server compatible with OpenAI's API such
as vLLM), tested before use and remembered afterwards."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import numpy as np
import sqlalchemy as sa
from pydantic import BaseModel, Field

from vfe_vision.adapters.imaging import encode_jpeg
from vfe_vision.adapters.lmstudio.client import ChatImage
from vfe_vision.core.errors import ConflictError, InvalidInputError, NotFoundError, VfeError
from vfe_vision.db.base import utcnow
from vfe_vision.db.lmstudio_link import default_target, load_link, save_link
from vfe_vision.db.models import Job
from vfe_vision.domain.enums import JobKind, JobStatus
from vfe_vision.domain.lmstudio_link import (
    MAX_ADDRESS,
    MAX_PARALLEL,
    MAX_TOKEN,
    LmStudioLink,
    LmStudioTarget,
    PastConnection,
    ServerKind,
    address_of,
    is_local,
    past_of,
    remembered,
    target_of,
)
from vfe_vision.services.container import AppContainer

# The jobs that talk to the model server: one of them running keeps the address as it is.
VISION_KINDS = (JobKind.ANALYZE_VIDEO, JobKind.PROBE_VISION, JobKind.BENCH_MODELS)
PROBE_SIDE = 64  # px: the image sent to see whether a model takes images


class LmStudioChoice(BaseModel):
    address: str | None = Field(
        default=None,
        max_length=MAX_ADDRESS,
        description="Address of the model server (192.168.1.20, 192.168.1.20:1234, "
        "http://gpu-box:8000/v1); empty: the installation's own.",
    )
    token: str | None = Field(
        default=None,
        max_length=MAX_TOKEN,
        description="API token of this server, if it asks for one. Absent: the one already "
        "saved for this address is kept; empty string: it is removed.",
    )
    kind: Literal["auto", "lmstudio", "openai"] | None = Field(
        default=None,
        description="lmstudio, openai (an OpenAI-compatible server: vLLM…), or auto: found out "
        "(LM Studio first). Absent: the one saved for this address, else auto.",
    )
    parallel: int | None = Field(
        default=None,
        ge=1,
        le=MAX_PARALLEL,
        description="OpenAI-compatible server: requests sent at once. Absent: the one saved "
        "for this address, else 4.",
    )
    vision: bool | None = Field(
        default=None,
        description="OpenAI-compatible server: whether its models see images. Absent: the one "
        "saved for this address, else yes.",
    )


class PastConnectionOut(BaseModel):
    url: str
    last_used_at: datetime
    local: bool
    has_token: bool
    kind: ServerKind | None
    parallel: int | None
    vision: bool


class LmStudioLinkOut(BaseModel):
    """The server in use and the ones used before. A token is never given back."""

    url: str
    custom: bool  # chosen in the interface (False: the installation's own address)
    default_url: str
    local: bool  # this computer: the images do not leave it
    has_token: bool
    kind: ServerKind | None  # said; None: found out (see ``found``)
    found: ServerKind | None  # the kind the application talks to, once it has answered
    parallel: int | None  # OpenAI-compatible server: requests at once
    vision: bool  # OpenAI-compatible server: its models see images
    past: list[PastConnectionOut]


class LmStudioTestOut(BaseModel):
    url: str
    ok: bool
    local: bool
    kind: ServerKind | None = None  # what answered at this address
    models: int = 0
    vision_models: int = 0
    loaded: list[str] = Field(default_factory=list)  # the vision models loaded (or served) there
    # OpenAI-compatible server: whether its first vision model took a tiny image (None: the
    # question could not be asked, or not asked).
    images: bool | None = None
    error: str | None = None


def _out(c: AppContainer, link: LmStudioLink) -> LmStudioLinkOut:
    default = default_target(c.settings)
    target = target_of(link, default)
    found = c.lmstudio.kind if c.lmstudio.base_url == target.url else target.kind
    return LmStudioLinkOut(
        url=target.url,
        custom=link.url is not None,
        default_url=default.url,
        local=is_local(target.url),
        has_token=bool(target.token),
        kind=target.kind,
        found=found,
        parallel=target.parallel,
        vision=target.vision,
        past=[
            PastConnectionOut(
                url=past.url,
                last_used_at=past.last_used_at,
                local=is_local(past.url),
                has_token=bool(past.token),
                kind=past.kind,
                parallel=past.parallel,
                vision=past.vision,
            )
            for past in link.past
        ],
    )


def _address(text: str | None, kind: ServerKind | None) -> str | None:
    """The address in full, or None for the installation's own."""
    if text is None or not text.strip():
        return None
    try:
        return address_of(text, kind)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc


def _wanted(c: AppContainer, link: LmStudioLink, choice: LmStudioChoice) -> LmStudioTarget:
    """The server a choice names: its address in full, with its token and settings; those the
    choice leaves out are the ones remembered for that address."""
    default = default_target(c.settings)
    typed = (choice.address or "").strip()
    if not typed:
        return default
    known = past_of(link, typed)  # a past connection is chosen again by its full address
    if choice.kind is not None:
        kind = None if choice.kind == "auto" else ServerKind(choice.kind)
    else:
        kind = known.kind if known else None
    url = _address(typed, kind)
    if url is None or url == default.url:
        return default
    past = past_of(link, url)
    if choice.kind is None and past is not None:
        kind = past.kind
    kept = past or PastConnection(url=url, last_used_at=utcnow())  # what is not said: as before
    token = kept.token if choice.token is None else choice.token.strip() or None
    parallel = kept.parallel if choice.parallel is None else choice.parallel
    vision = kept.vision if choice.vision is None else choice.vision
    return LmStudioTarget(url, token, kind, parallel, vision)


def read(c: AppContainer) -> LmStudioLinkOut:
    return _out(c, load_link(c.db))


def choose(c: AppContainer, choice: LmStudioChoice) -> LmStudioLinkOut:
    """Talk to this server from now on, and remember it among the past connections."""
    link = load_link(c.db)
    default = default_target(c.settings)
    before = target_of(link, default)
    wanted = _wanted(c, link, choice)
    if wanted == default:
        link.url = None  # the installation's address is not a choice of the interface
    else:
        link.url = wanted.url
        link.past = remembered(link.past, wanted, utcnow())
    after = target_of(link, default)
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
    """Ask the server at this address for its models, without changing anything. For an
    OpenAI-compatible server, which does not say whether its models see images, its first
    vision model is shown a tiny image."""
    wanted = _wanted(c, load_link(c.db), choice)
    client = c.lmstudio_probe(wanted)
    try:
        models = await client.list_models()
        vision = [model for model in models if model.vision]
        images = None
        if client.serves_its_models and vision:
            gray = np.full((PROBE_SIDE, PROBE_SIDE, 3), 128, dtype=np.uint8)
            image = ChatImage(encode_jpeg(gray, quality=60), PROBE_SIDE, PROBE_SIDE)
            images = await client.probe_images(vision[0].loaded_instances[0].id, image)
        kind = client.kind
    except VfeError as exc:
        return LmStudioTestOut(
            url=wanted.url, ok=False, local=is_local(wanted.url), error=exc.detail
        )
    finally:
        await client.aclose()
    return LmStudioTestOut(
        url=wanted.url,
        ok=True,
        local=is_local(wanted.url),
        kind=kind,
        models=len(models),
        vision_models=len(vision),
        loaded=[model.display_name for model in vision if model.loaded_instances],
        images=images,
    )


def _nothing_running(c: AppContainer) -> None:
    """An analysis under way describes its images with the model of the server it started
    with: the address does not change under it."""
    with c.db.read() as session:
        running = session.execute(
            sa.select(sa.func.count())
            .select_from(Job)
            .where(Job.kind.in_(VISION_KINDS), Job.status == JobStatus.RUNNING)
        ).scalar_one()
    if running:
        raise ConflictError(
            "Une analyse ou un test de modèles est en cours avec le serveur de modèles actuel : "
            "attendez sa fin, ou arrêtez-le (page Tâches), avant de changer d'adresse."
        )
