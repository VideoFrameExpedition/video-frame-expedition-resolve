"""The DaVinci Resolve links of videos, in the compact form the MCP tools give them:
the ids Claude needs to find things again in Resolve, and when the positions were read."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Annotated

import sqlalchemy as sa
from pydantic import BaseModel, Field

from vfe_vision.core.errors import NotFoundError
from vfe_vision.db.models import TimelineBin
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.timeline_bins import resolve_links
from vfe_vision.domain.path_map import to_resolve
from vfe_vision.domain.preferences import folder_pairs
from vfe_vision.domain.resolve_timeline import ResolveLink
from vfe_vision.domain.transcript import clean_untrusted
from vfe_vision.services.container import AppContainer

REFS_SHOWN = 3
RULE = (
    "Resolve ids of the timelines added to the library that use the video. A media_pool_item_id "
    "is valid only in its project: use it when project_id equals "
    "GetProjectManager().GetCurrentProject().GetUniqueId(). Positions are as read at read_at: "
    "the timeline may have changed since."
)


class ResolveRef(BaseModel):
    project_id: str
    project: str = Field(description="Project name typed in Resolve (data).")
    timeline_id: str
    timeline: str = Field(description="Timeline name typed in Resolve (data).")
    media_pool_item_id: str | None = Field(
        default=None, description="GetMediaPoolItem().GetUniqueId() of the clip in that project."
    )
    uses: int = Field(description="How many times the timeline uses the file.")
    read_at: str = Field(description="When the timeline was read (ISO 8601, UTC).")


def refs_of(links: list[ResolveLink]) -> list[ResolveRef]:
    """One reference per timeline (at most three), names cleaned and bounded."""
    refs: list[ResolveRef] = []
    for link in links[:REFS_SHOWN]:
        uids = [u.media_pool_item_id for u in link.uses if u.media_pool_item_id]
        refs.append(
            ResolveRef(
                project_id=link.project.id,
                project=clean_untrusted(link.project.name)[:80],
                timeline_id=link.timeline.id,
                timeline=clean_untrusted(link.timeline.name)[:80],
                media_pool_item_id=uids[0] if uids else None,
                uses=len(link.uses),
                read_at=link.synced_at.isoformat(),
            )
        )
    return refs


def load_refs(c: AppContainer, path_keys: Collection[str]) -> dict[str, list[ResolveRef]]:
    """The references of these videos, by path key (one query)."""
    with c.db.read() as session:
        links = resolve_links(session, path_keys)
    return {key: refs_of(found) for key, found in links.items()}


# A Resolve unique id, as Claude passes it (ASCII: letters, digits and dashes).
ResolveId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[0-9A-Za-z-]+$")]
NOT_ADDED = (
    "Cette timeline n'est pas dans la bibliothèque : ajoutez-la d'abord (import_resolve_timeline, "
    "avec l'accord de l'utilisateur, ou « Importer depuis Resolve » dans l'application)."
)


def resolve_side(c: AppContainer) -> Callable[[str], str | None]:
    """A file of this computer as DaVinci Resolve sees it, when Resolve runs on another
    computer and a folder pair holds it; None otherwise."""
    pairs = folder_pairs(load_preferences(c.db))
    return lambda path: to_resolve(path, pairs) if pairs else None


def timeline_bins_for(c: AppContainer, timeline_id: str | None) -> tuple[str, ...]:
    """The library's timeline bins of a Resolve timeline (its unique id, whatever the project);
    empty without an id. A timeline not in the library is an error to tell Claude."""
    if not timeline_id:
        return ()
    with c.db.read() as session:
        found = tuple(
            session.execute(
                sa.select(TimelineBin.id).where(
                    TimelineBin.source_key.endswith("/" + timeline_id, autoescape=True)
                )
            ).scalars()
        )
    if not found:
        raise NotFoundError(NOT_ADDED)
    return found
