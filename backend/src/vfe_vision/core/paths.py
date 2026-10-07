"""Path helpers: normalised keys, containment checks, volume identity, cloud placeholders."""

from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path, PurePosixPath

VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".mxf", ".webm",
        ".wmv", ".3gp", ".mpg", ".mpeg", ".ts", ".insv", ".lrv",
    }
)  # fmt: skip

# The file systems of Windows (NTFS) and macOS (APFS, HFS+) ignore letter case by default;
# Linux's do not.
CASE_INSENSITIVE_PATHS = sys.platform in {"win32", "darwin"}
# An example of a video folder, in the messages that ask for an absolute path.
EXAMPLE_FOLDER = r"D:\Vidéos\Tournage" if sys.platform == "win32" else "/Volumes/Rushs/Tournage"
# Where macOS mounts disks and network shares: a folder missing there is unmounted, not deleted.
MACOS_VOLUMES = "/Volumes"

# Windows file attributes that denote OneDrive/Files-On-Demand placeholders (content not local).
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
# macOS file flag of a file whose content is in the cloud only (iCloud Drive, File Provider).
_SF_DATALESS = 0x40000000
_DISKUTIL = "/usr/sbin/diskutil"


def normalize_text(path: str) -> str:
    """One Unicode spelling for the accented letters of a path: macOS (HFS+ volumes, SMB shares,
    programs such as DaVinci Resolve) may write « é » as « e » plus an accent, while a browser or
    a database writes it as one character; the two must compare equal."""
    return unicodedata.normalize("NFC", path)


def path_key(path: Path | str) -> str:
    """Stable comparison key for a path: absolute, normalised, forward slashes, accents in one
    spelling; case-folded on Windows and macOS, whose file systems ignore case."""
    normalised = os.path.normpath(os.path.abspath(os.fspath(path)))
    key = normalize_text(normalised.replace("\\", "/"))
    return key.casefold() if CASE_INSENSITIVE_PATHS else key


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
    """True for a file whose content would be downloaded on read: a OneDrive placeholder on
    Windows, an iCloud Drive (or other File Provider) file not on the disk on macOS."""
    if sys.platform == "win32":
        try:
            attrs = path.stat(follow_symlinks=False).st_file_attributes
        except OSError:
            return False
        flags = _FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN
        flags |= _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
        return bool(attrs & flags)
    elif sys.platform == "darwin":
        try:
            flags = int(getattr(path.stat(follow_symlinks=False), "st_flags", 0))
        except OSError:
            return False
        return bool(flags & _SF_DATALESS)
    else:
        return False


def is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except OSError:
        return False


def mount_point(path: Path) -> Path:
    """The mount point of the volume that holds ``path`` (``/`` for the start-up disk)."""
    current = Path(os.path.abspath(path))
    while not os.path.ismount(current) and current.parent != current:
        current = current.parent
    return current


def unmounted_volume(path: Path | str) -> bool:
    """On macOS, a path under ``/Volumes/<name>`` whose volume is not mounted: the disk or the
    share is absent, its files are not gone."""
    if sys.platform == "darwin":
        parts = PurePosixPath(os.fspath(path)).parts
        if len(parts) < 3 or parts[0] != "/" or parts[1] != MACOS_VOLUMES.strip("/"):
            return False
        return not Path(*parts[:3]).exists()
    else:
        return False


def volume_serial(path: Path) -> str | None:
    """Identifier of the volume holding ``path``: the volume serial on Windows (it survives a
    drive-letter change), the volume's UUID on macOS (it survives a remount, unlike the device
    number), the device number elsewhere."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        anchor = Path(os.path.abspath(path)).anchor or "C:\\"
        serial = wintypes.DWORD()
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(anchor), None, 0, ctypes.byref(serial), None, None, None, 0
        )
        return f"{serial.value:08X}" if ok else None
    elif sys.platform == "darwin":
        return macos_volume_id(path)
    else:
        try:
            return str(path.stat().st_dev)
        except OSError:
            return None


def macos_volume_id(path: Path, *, diskutil: str = _DISKUTIL) -> str | None:
    """The UUID of the local volume holding ``path``, read with ``diskutil``. A network share
    has none: its mount point (the share's name under ``/Volumes``) stands for it, so two cards
    mounted under the same name are only told apart when they are local disks."""
    mount = mount_point(path)
    try:
        done = subprocess.run(
            [diskutil, "info", "-plist", str(mount)], capture_output=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return f"mount:{mount}"
    if done.returncode != 0 or not done.stdout.strip():
        return f"mount:{mount}"
    try:
        info = plistlib.loads(done.stdout)
    except (plistlib.InvalidFileException, ValueError):
        return f"mount:{mount}"
    uuid = info.get("VolumeUUID") if isinstance(info, dict) else None
    return str(uuid) if uuid else f"mount:{mount}"
