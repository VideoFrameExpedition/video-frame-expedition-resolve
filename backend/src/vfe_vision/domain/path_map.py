"""The same folders seen from two computers: this one, where the library and its
analyses live, and the one where DaVinci Resolve runs (a Mac reading the rushes through a network
share, say). A path goes from one side to the other through the longest folder pair that holds
it, letter case and separators aside; the rest of the path is kept as it is."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

_SEPARATORS = re.compile(r"[\\/]+")
_WINDOWS = re.compile(r"^(?:[A-Za-z]:|\\\\)")


@dataclass(frozen=True, slots=True)
class FolderPair:
    here: str  # a folder of this computer: D:\cats 2026
    there: str  # the same folder as Resolve sees it: /Volumes/cats 2026


def to_resolve(path: str, pairs: Iterable[FolderPair]) -> str | None:
    """A path of this computer as Resolve sees it (None: no pair holds it)."""
    return _carry(path, pairs, "here")


def from_resolve(path: str, pairs: Iterable[FolderPair]) -> str | None:
    """A path given by Resolve as this computer sees it (None: no pair holds it)."""
    return _carry(path, pairs, "there")


def _parts(path: str) -> list[str]:
    return [part for part in _SEPARATORS.split(path.strip()) if part]


def _carry(path: str, pairs: Iterable[FolderPair], side: Literal["here", "there"]) -> str | None:
    parts = _parts(path)
    folded = [part.casefold() for part in parts]
    best: tuple[int, FolderPair] | None = None
    for pair in pairs:
        prefix = [part.casefold() for part in _parts(getattr(pair, side))]
        if prefix and folded[: len(prefix)] == prefix and (best is None or len(prefix) > best[0]):
            best = (len(prefix), pair)
    if best is None:
        return None
    size, pair = best
    target = (pair.there if side == "here" else pair.here).strip().rstrip("\\/")
    separator = "\\" if _WINDOWS.match(target) or "\\" in target else "/"
    rest = parts[size:]
    return separator.join([target, *rest]) if rest else target
