"""Reading the timeline bins: which DaVinci Resolve timelines use a video, and where.

An item names its file by path key and finds its video through ``coalesce(video_key,
path_key)`` (``video_key``: the same file known to the library under another path), when read:
no foreign key ties a bin to the videos. Usable from every layer that reads the database (API,
MCP, jobs, the analysis file).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Collection, Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.db.models import TimelineBin, TimelineBinItem
from vfe_vision.domain.resolve_timeline import (
    ClipUse,
    ResolveLink,
    identity_parts,
    use_from_json,
    use_to_json,
)

# The path key of the video an item stands for.
ITEM_KEY = sa.func.coalesce(TimelineBinItem.video_key, TimelineBinItem.path_key)


def _item_key_in(keys: Collection[str]) -> sa.ColumnElement[bool]:
    """``ITEM_KEY IN keys``, written so that both indexes serve it."""
    return sa.or_(
        TimelineBinItem.video_key.in_(keys),
        sa.and_(TimelineBinItem.video_key.is_(None), TimelineBinItem.path_key.in_(keys)),
    )


def bin_video_keys(bin_id: str) -> sa.Select[str]:
    """The path keys of the videos of a timeline bin, as a subquery (``Video.path_key.in_``)."""
    return sa.select(ITEM_KEY).where(TimelineBinItem.bin_id == bin_id)


def bin_positions(bin_id: str) -> sa.Subquery:
    """The videos of a timeline bin (``key``: their path key) and where each first appears in
    the timeline (``position``), to join on ``Video.path_key``."""
    return (
        sa.select(ITEM_KEY.label("key"), sa.func.min(TimelineBinItem.position).label("position"))
        .where(TimelineBinItem.bin_id == bin_id)
        .group_by(ITEM_KEY)
        .subquery()
    )


def resolve_links(session: Session, path_keys: Collection[str]) -> dict[str, list[ResolveLink]]:
    """The Resolve links of the videos with these path keys, by path key (one query; a video
    no timeline uses is left out), in the order of their bins' labels."""
    keys = set(path_keys)
    if not keys:
        return {}
    rows = session.execute(
        sa.select(ITEM_KEY, TimelineBinItem.uses, TimelineBin)
        .join(TimelineBin, TimelineBin.id == TimelineBinItem.bin_id)
        .where(_item_key_in(keys))
        .order_by(TimelineBinItem.position)
    ).all()
    bins: dict[str, TimelineBin] = {}
    uses: dict[str, dict[str, list[ClipUse]]] = defaultdict(lambda: defaultdict(list))
    for key, stored, timeline_bin in rows:
        bins[timeline_bin.id] = timeline_bin
        # Two paths of one file (a share and its mapped drive) in a timeline: one link.
        uses[key][timeline_bin.id].extend(use_from_json(use) for use in stored)
    links: dict[str, list[ResolveLink]] = {}
    for key, by_bin in uses.items():
        ordered = sorted(by_bin, key=lambda bin_id: (bins[bin_id].label.casefold(), bin_id))
        links[key] = [_link(bins[bin_id], by_bin[bin_id]) for bin_id in ordered]
    return links


def _link(timeline_bin: TimelineBin, uses: list[ClipUse]) -> ResolveLink:
    database, project, timeline = identity_parts(timeline_bin.resolve)
    return ResolveLink(
        bin_id=timeline_bin.id,
        bin_label=timeline_bin.label,
        database=database,
        project=project,
        timeline=timeline,
        synced_at=timeline_bin.synced_at,
        uses=tuple(uses),
    )


def bins_of(session: Session, path_keys: Collection[str]) -> dict[str, list[str]]:
    """The timeline bins using each of these videos (ids, by path key; one query)."""
    keys = set(path_keys)
    if not keys:
        return {}
    rows = session.execute(
        sa.select(ITEM_KEY, TimelineBinItem.bin_id).where(_item_key_in(keys)).distinct()
    ).all()
    found: dict[str, list[str]] = defaultdict(list)
    for key, bin_id in rows:
        found[key].append(bin_id)
    return {key: sorted(ids) for key, ids in found.items()}


def links_digest(links: Iterable[ResolveLink]) -> str:
    """A short, stable hash of what the analysis file keeps of a video's links, their date
    aside: the file is written again only when it changes."""
    parts = sorted(json.dumps(_kept(link), sort_keys=True) for link in links)
    return hashlib.blake2b("\n".join(parts).encode(), digest_size=8).hexdigest()


def _kept(link: ResolveLink) -> dict[str, Any]:
    timeline = link.timeline
    return {
        "database": [link.database.type, link.database.name],
        "project": [link.project.id, link.project.name],
        "timeline": [
            timeline.id,
            timeline.name,
            timeline.fps,
            timeline.drop_frame,
            timeline.start_timecode,
        ],
        "label": link.bin_label,
        "uses": [use_to_json(use) for use in link.uses],
    }
