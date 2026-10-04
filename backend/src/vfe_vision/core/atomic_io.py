"""Atomic file writes that tolerate transient Windows locks (antivirus, indexer)."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
from pathlib import Path

_RETRY_DELAYS_S = (0.05, 0.1, 0.25, 0.5, 1.0)


def atomic_write_bytes(
    path: Path, data: bytes, *, create_parents: bool = True, in_place_fallback: bool = False
) -> None:
    """Write ``data`` to ``path`` so readers never observe a partially written file.

    ``create_parents=False``: the folder must exist (a user's folder is never created).
    ``in_place_fallback``: a folder that refuses to rename the written file even after the
    retries gets it written in place (the shared folders of Windows Sandbox refuse every rename,
    measured): for a user's folder, where a file at all matters more than its atomicity.
    """
    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            replace_with_retry(tmp, path)
        except PermissionError:
            if not in_place_fallback:
                raise
            path.write_bytes(data)
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str, *, create_parents: bool = True) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), create_parents=create_parents)


def replace_with_retry(src: Path, dst: Path) -> None:
    """``os.replace`` with short retries: Defender or the search indexer may hold ``dst`` open."""
    for delay in (*_RETRY_DELAYS_S, None):
        try:
            src.replace(dst)
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)
        else:
            return


def replace_dir(staging: Path, folder: Path) -> None:
    """Replace ``folder`` by the complete ``staging`` folder; the old one survives any failure."""
    backup = folder.with_name(f"{folder.name}.old")
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    had_old = folder.exists()
    if had_old:
        replace_with_retry(folder, backup)  # fails early: nothing lost
    try:
        replace_with_retry(staging, folder)
    except OSError:
        if had_old:
            replace_with_retry(backup, folder)
        raise
    shutil.rmtree(backup, ignore_errors=True)
