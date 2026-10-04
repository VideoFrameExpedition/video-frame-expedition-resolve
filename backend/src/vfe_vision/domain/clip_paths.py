"""Comparing a file path written by another program with the library's.

DaVinci Resolve gives a clip's ``File Path`` as Windows writes it (``C:\\Rushs\\clip.MP4``); a
timeline or a script may give it with forward slashes, doubled separators, a long-path prefix
(``\\\\?\\C:\\…``, ``\\\\?\\UNC\\server\\share\\…``) or another case. :func:`match_key` turns all
of these into one comparison form, without touching the disk.

The fixed Resolve script (``adapters/resolve/apply_payload_v1.py``) carries a copy of this rule,
written without ``re`` for Resolve's sandbox; a test checks that both agree.
"""

from __future__ import annotations

from pathlib import PureWindowsPath

_LONG_UNC = "//?/unc/"
_LONG = ("//?/", "//./")


def match_key(path: str, *, case_insensitive: bool = True) -> str:
    """Comparison form of a path: forward slashes, no long-path prefix, no doubled separator,
    no ``.`` or ``..`` segment, no trailing slash; case-folded when the file system ignores case
    (Windows, the default)."""
    text = path.strip().strip('"').replace("\\", "/")
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


def canonical_path(path: str) -> str | None:
    """The path as the library writes it (backslashes, no long-path prefix), for a path given
    by another program such as DaVinci Resolve; None when it is not an absolute
    Windows path (a drive letter or a network share), e.g. a macOS path from a shared project."""
    key = match_key(path, case_insensitive=False)
    windows = PureWindowsPath(key.replace("/", "\\"))
    is_share = key.startswith("//") and len(windows.parts) > 1
    if not (windows.is_absolute() or is_share):
        return None
    return str(windows)
