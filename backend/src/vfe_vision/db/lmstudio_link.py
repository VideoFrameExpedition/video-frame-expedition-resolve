"""Persistence of the model server chosen in the interface: one JSON document in ``settings``,
read by the API process and by the worker."""

from __future__ import annotations

import time

from vfe_vision.core.config import Settings
from vfe_vision.db.models import Setting
from vfe_vision.db.session import Database
from vfe_vision.domain.lmstudio_link import LmStudioLink, LmStudioTarget, ServerKind, target_of

LINK_KEY = "lmstudio"
REFRESH_S = 1.0


def load_link(db: Database) -> LmStudioLink:
    with db.read() as session:
        row = session.get(Setting, LINK_KEY)
        return LmStudioLink.model_validate(row.value if row else {})


def save_link(db: Database, link: LmStudioLink) -> None:
    value = link.model_dump(mode="json")
    with db.write() as session:
        row = session.get(Setting, LINK_KEY)
        if row is None:
            session.add(Setting(key=LINK_KEY, value=value))
        else:
            row.value = value


def default_target(settings: Settings) -> LmStudioTarget:
    """The installation's model server (``VFE_LMSTUDIO_URL``, ``VFE_MODEL_SERVER``…)."""
    token = settings.lmstudio_token.get_secret_value() if settings.lmstudio_token else None
    kind = None if settings.model_server == "auto" else ServerKind(settings.model_server)
    return LmStudioTarget(
        settings.lmstudio_url.rstrip("/"),
        token,
        kind,
        settings.model_server_parallel,
        settings.model_server_vision,
    )


class LinkReader:
    """The model server to talk to, as the clients ask for it before each request.

    The choice is read again at most once a second: the worker, a separate process, follows a
    change made in the interface without being restarted.
    """

    def __init__(self, db: Database, default: LmStudioTarget) -> None:
        self._db = db
        self._default = default
        self._read_at: float | None = None
        self._target = target_of(LmStudioLink(), default)

    @property
    def default(self) -> LmStudioTarget:
        return self._default

    def __call__(self) -> LmStudioTarget:
        now = time.monotonic()
        if self._read_at is None or now - self._read_at >= REFRESH_S:
            self._target = target_of(load_link(self._db), self._default)
            self._read_at = now
        return self._target

    def forget(self) -> None:
        """Read the choice again at the next request (it has just been changed here)."""
        self._read_at = None
