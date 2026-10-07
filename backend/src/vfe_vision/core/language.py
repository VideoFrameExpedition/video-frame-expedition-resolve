"""Language of the messages printed in the terminal: French or English.

The web interface has its own language switch; this one is for what a terminal shows (the
launchers, the installation scripts and the ``vfe`` commands). ``VFE_LANG=fr`` or ``en`` (the
environment, then the ``.env`` file of the application's folder) decides; otherwise the
system's display language does, and anything other than French gives English. The launchers
and the installation scripts set ``VFE_LANG`` for the commands they start, so a window never
mixes two languages.
"""

from __future__ import annotations

import os
import sys
from functools import cache
from pathlib import Path
from typing import Literal

from vfe_vision.core.errors import ExternalToolError
from vfe_vision.core.procs import run_process

Language = Literal["fr", "en"]

LANG_FRENCH = 0x0C  # primary language of a Windows LANGID (its low 10 bits)


@cache
def terminal_language() -> Language:
    """French or English, decided once per process (see the module's docstring)."""
    wanted = os.environ.get("VFE_LANG") or _from_env_file(Path(".env")) or system_language()
    return "fr" if wanted.strip().strip("\"'").lower().startswith("fr") else "en"


def _from_env_file(path: Path) -> str:
    """The last ``VFE_LANG=`` line of a ``.env`` file, "" without one."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    found = ""
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip() == "VFE_LANG":
            found = value.strip()
    return found


def system_language() -> str:
    """The system's display language as a code ("fr", "fr-FR", "en-US"…), "" when unknown.

    Windows: the user's display language. macOS: the first of the preferred languages (System
    Settings › General › Language & Region), which the Terminal's locale may not follow.
    Elsewhere, and as a fallback: the locale's environment variables.
    """
    if sys.platform == "win32":
        import ctypes

        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "fr" if langid & 0x3FF == LANG_FRENCH else "en"
    else:
        if sys.platform == "darwin":
            first = _first_apple_language()
            if first:
                return first
        for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
            value = os.environ.get(name, "")
            if value:
                return value
        return ""


def _first_apple_language() -> str:
    """``defaults read -g AppleLanguages`` prints a list, one quoted code per line."""
    try:
        result = run_process(
            ["/usr/bin/defaults", "read", "-g", "AppleLanguages"], timeout_s=5, tool_name="defaults"
        )
    except ExternalToolError:
        return ""
    for line in result.stdout_text.splitlines():
        code = line.strip().strip('",')
        if code and code not in ("(", ")"):
            return code
    return ""


def tr(fr: str, en: str) -> str:
    """The text in the terminal's language."""
    return fr if terminal_language() == "fr" else en
