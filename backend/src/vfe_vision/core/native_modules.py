"""Compiled extensions Windows refuses to load.

Smart App Control, on by default on a new Windows 11, lets an unsigned file run only when
Microsoft's cloud knows it well enough. A rarely seen extension module (``.pyd``) is refused, and
Python then fails the whole import, even when the package ships the same module in plain Python
next to it (SQLAlchemy does). Setting the refused file aside lets Python take the plain module:
slower, same behaviour. A refused file is never run.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from pathlib import Path

POLICY_VIOLATION = 4551  # ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION
ASIDE_SUFFIX = ".refused"
_LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008


def refused_by_policy(path: Path) -> bool:
    """Whether Windows' application control refuses to load this file (elsewhere: never)."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LoadLibraryExW.restype = ctypes.c_void_p
        kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
        kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
        handle = kernel32.LoadLibraryExW(str(path), None, _LOAD_WITH_ALTERED_SEARCH_PATH)
        if handle:
            kernel32.FreeLibrary(handle)
            return False
        return ctypes.get_last_error() == POLICY_VIOLATION
    else:
        return False


def plain_twin(path: Path) -> Path | None:
    """The same module in plain Python next to a compiled one: ``_util_cy.py`` for
    ``_util_cy.cp312-win_amd64.pyd``."""
    twin = path.with_name(path.name.split(".", 1)[0] + ".py")
    return twin if twin.is_file() else None


def set_aside(
    error: ImportError, *, refused: Callable[[Path], bool] = refused_by_policy
) -> Path | None:
    """After a failed import: when what failed is a compiled extension Windows refuses, and the
    package has it in plain Python too, rename the extension so that the next import takes the
    plain module. Returns the file as renamed; None when the error is something else (a
    missing module, an extension without a twin) and stands."""
    path = Path(error.path) if error.path else None
    if path is None or path.suffix.lower() != ".pyd" or not path.is_file():
        return None
    if plain_twin(path) is None or not refused(path):
        return None
    aside = path.with_name(path.name + ASIDE_SUFFIX)
    try:
        aside.unlink(missing_ok=True)
        path.rename(aside)
    except OSError:
        return None
    return aside
