"""Persistent cache of online-service answers (shared by all videos, survives restarts)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vfe_vision.db.base import utcnow
from vfe_vision.db.models import ServiceCache
from vfe_vision.db.session import Database


@dataclass(frozen=True, slots=True)
class CachedResponse:
    response: dict[str, Any]
    fetched_at: datetime


def cache_get(db: Database, key: str) -> CachedResponse | None:
    with db.read() as session:
        row = session.get(ServiceCache, key)
        return None if row is None else CachedResponse(row.response, row.fetched_at)


def cache_put(db: Database, key: str, service: str, response: dict[str, Any]) -> datetime:
    fetched_at = utcnow()
    with db.write() as session:
        row = session.get(ServiceCache, key) or ServiceCache(key=key, service=service)
        row.response = response
        row.fetched_at = fetched_at
        session.add(row)
    return fetched_at
