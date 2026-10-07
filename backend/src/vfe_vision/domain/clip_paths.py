"""Comparing a file path written by another program with the library's.

DaVinci Resolve gives a clip's ``File Path`` as its system writes it (``C:\\Rushs\\clip.MP4`` on
Windows, ``/Volumes/Rushs/clip.MP4`` on macOS); a timeline or a script may give it with forward
slashes, doubled separators, a long-path prefix (``\\\\?\\C:\\…``, ``\\\\?\\UNC\\server\\share\\…``)
or another case or spelling of its accents. :func:`match_key` turns all of these into one
comparison form, without touching the disk.

The fixed Resolve script (``adapters/resolve/apply_payload_v1.py``) carries a copy of this rule,
written without ``re`` for Resolve's sandbox; a test checks that both agree.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

PathStyle = Literal["windows", "posix"]
# How this computer writes its paths: what a path « of this computer » looks like.
HOST_STYLE: PathStyle = "windows" if sys.platform == "win32" else "posix"

_LONG_UNC = "//?/unc/"
_LONG = ("//?/", "//./")


def match_key(path: str, *, case_insensitive: bool = True) -> str:
    """Comparison form of a path: forward slashes, no long-path prefix, no doubled separator,
    no ``.`` or ``..`` segment, no trailing slash, accents in one spelling (NFC); case-folded
    when the file system ignores case (Windows and macOS, the default)."""
    text = unicodedata.normalize("NFC", path.strip().strip('"')).replace("\\", "/")
    if text[: len(_LONG_UNC)].lower() == _LONG_UNC:
        text = "//" + text[len(_LONG_UNC) :]
    elif text[:4] in _LONG:
        text = text[4:]
    lead = "//" if text.startswith("//") else "/" if text.startswith("/") else ""
    parts: list[str] = []
    for part in text[len(lead) :].split("/"):
        if part in {"", "."}:
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
            continue
        parts.append(part)
    key = lead + "/".join(parts)
    return key.casefold() if case_insensitive else key


def file_name(path: str) -> str:
    """The last part of a path written with either separator."""
    return PureWindowsPath(path.strip().strip('"')).name


def canonical_path(path: str, *, style: PathStyle = HOST_STYLE) -> str | None:
    """The path as the library of a computer of that ``style`` writes it, for a path given by
    another program such as DaVinci Resolve: backslashes and no long-path prefix on
    Windows, forward slashes on macOS. None when it is not an absolute path of that kind — a
    drive letter or a network share for Windows, a leading slash for macOS —, e.g. a path from
    a project shared with a computer of the other system."""
    key = match_key(path, case_insensitive=False)
    if style == "posix":
        if not key.startswith("/") or key.startswith("//"):
            return None
        return str(PurePosixPath(key))
    windows = PureWindowsPath(key.replace("/", "\\"))
    is_share = key.startswith("//") and len(windows.parts) > 1
    if not (windows.is_absolute() or is_share):
        return None
    return str(windows)
