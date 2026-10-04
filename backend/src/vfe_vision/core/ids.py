"""Time-ordered identifiers (UUIDv7 layout, 32 lowercase hex characters)."""

from __future__ import annotations

import os
import time

_MS_MASK = (1 << 48) - 1
_RAND_A_MASK = (1 << 12) - 1
_RAND_B_MASK = (1 << 62) - 1


def new_id() -> str:
    """Return a new UUIDv7-compatible identifier as 32 hex characters.

    The 48 most significant bits are the Unix time in milliseconds, so identifiers sort by
    creation time: SQLite B-tree inserts stay append-only and listings are chronological.
    """
    millis = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (
        ((millis & _MS_MASK) << 80)
        | (0x7 << 76)  # version 7
        | (((rand >> 62) & _RAND_A_MASK) << 64)
        | (0b10 << 62)  # RFC 9562 variant
        | (rand & _RAND_B_MASK)
    )
    return f"{value:032x}"
