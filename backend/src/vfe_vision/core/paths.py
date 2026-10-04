"""Path helpers: normalised keys, containment checks, volume identity, cloud placeholders."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path, PurePosixPath

VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".mxf", ".webm",
        ".wmv", ".3gp", ".mpg", ".mpeg", ".ts", ".insv", ".lrv",
    }
)  # fmt: skip

# Windows file attributes that denote OneDrive/Files-On-Demand placeholders (content not local).
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000


def path_key(path: Path | str) -> str:
    """Stable comparison key for a path: absolute, normalised, forward slashes.

    On Windows the key is case-folded, because NTFS paths are case-insensitive.
    """
    normalised = os.path.normpath(os.path.abspath(os.fspath(path)))
    key = normalised.replace("\\", "/")
    return key.casefold() if sys.platform == "win32" else key


def is_within(child: Path | str, parent: Path | str) -> bool:
    """True if ``child`` is ``parent`` itself or located below it (no symlink resolution)."""
    child_key = path_key(child)
    parent_key = path_key(parent).rstrip("/")
    return child_key == parent_key or child_key.startswith(parent_key + "/")


def joined_inside(root: Path, relative: str) -> Path | None:
    """``root`` joined with a "/"-separated relative path taken from a URL, or None when the
    text could lead out of it.

    Decided on the text alone, before the disk is touched: on Windows a backslash, a drive or a
    leading slash would turn the join into another absolute path, and ``\\\\host\\share`` is a
    network path that Windows logs in to merely to look at it.
    """
    if any(mark in relative for mark in ("\\", ":", "\0")) or relative.startswith("/"):
        return None
    parts = PurePosixPath(relative).parts
    if ".." in parts:
        return None
    return root.joinpath(*parts)


def is_video_file(path: Path) -> bool:
    """A video by its extension, hidden files aside: macOS's ``._`` companions, the ``.temp-…``
    files a Samsung camera writes while it records, other tools' partial files."""
    return path.suffix.lower() in VIDEO_EXTENSIONS and not path.name.startswith(".")


def is_cloud_placeholder(path: Path) -> bool:
    """True for OneDrive-style placeholders whose content would be downloaded on read."""
    if sys.platform != "win32":
        return False
    try:
        attrs = path.stat(follow_symlinks=False).st_file_attributes
    except OSError:
        return False
    flags = _FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN
    flags |= _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    return bool(attrs & flags)


def is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except OSError:
        return False


def volume_serial(path: Path) -> str | None:
    """Identifier of the volume holding ``path`` (survives a drive-letter change on Windows)."""
    if sys.platform != "win32":
        try:
            return str(path.stat().st_dev)
        except OSError:
            return None
    import ctypes
    from ctypes import wintypes

    anchor = Path(os.path.abspath(path)).anchor or "C:\\"
    serial = wintypes.DWORD()
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(anchor), None, 0, ctypes.byref(serial), None, None, None, 0
    )
    return f"{serial.value:08X}" if ok else None
