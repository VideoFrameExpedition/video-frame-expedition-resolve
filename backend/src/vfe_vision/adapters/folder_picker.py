"""The desktop's folder picker, opened on the machine running the app.

A web page cannot learn the full path of a folder the user picks, so the server opens the native
dialog itself, in a short-lived child process: AppleScript's ``choose folder`` (the Finder's
dialog) on macOS, Tk's ``askdirectory`` (the modern Explorer dialog) on Windows. The app's own
process never hosts a GUI event loop.
"""

from __future__ import annotations

import os
import sys
from functools import cache
from pathlib import Path

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import ExternalToolError
from vfe_vision.core.procs import KillOnCloseJob, run_process

TIMEOUT_S = 15 * 60  # left open this long, the dialog is closed and nothing is picked
TOOL = "sélecteur de dossier"
OSASCRIPT = "/usr/bin/osascript"
_CANCELLED = "-128"  # AppleScript's « User canceled » error number

_TK_SCRIPT = r"""
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)  # above the browser that asked for it
root.update()
picked = filedialog.askdirectory(
    parent=root, title=sys.argv[1], initialdir=sys.argv[2] or None, mustexist=True
)
root.destroy()
sys.stdout.write(picked or "")
"""

# Run by osascript; its own process is activated so that the dialog comes above the browser
# (asking another application to activate would need an automation permission).
_APPLESCRIPT = b"""
on run argv
    set msg to item 1 of argv
    set origin to item 2 of argv
    tell me to activate
    if origin is "" then
        set picked to choose folder with prompt msg
    else
        set picked to choose folder with prompt msg default location (POSIX file origin)
    end if
    return POSIX path of picked
end run
"""


def default_start() -> Path | None:
    """Where the dialog opens: the user's videos folder when there is one."""
    for name in ("Videos", "Vidéos", "Movies"):
        candidate = Path.home() / name
        if candidate.is_dir():
            return candidate
    return Path.home() if Path.home().is_dir() else None


@cache
def _app_job() -> KillOnCloseJob:
    """Held by the app's process for its lifetime: a dialog never outlives the app."""
    return KillOnCloseJob()


def picker_command(title: str, start: str) -> tuple[list[str], bytes | None]:
    """The dialog's process for this system, and what it reads on its standard input."""
    if sys.platform == "darwin":
        return [OSASCRIPT, "-", title, start], _APPLESCRIPT
    else:
        return [sys.executable, "-c", _TK_SCRIPT, title, start], None


def pick_folder(
    title: str, start: Path | None = None, *, cancel: CancelToken | None = None
) -> Path | None:
    """The folder the user chose, or None when the dialog was cancelled. ``cancel`` closes
    the dialog (page closed, app stopping)."""
    args, script = picker_command(title, str(start or default_start() or ""))
    result = run_process(
        args,
        timeout_s=TIMEOUT_S,
        cancel=cancel,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        input_bytes=script,
        check=False,
        tool_name=TOOL,
        job=_app_job(),
    )
    if result.returncode != 0:
        if _CANCELLED in result.stderr_text:  # the Finder's dialog, closed with « Cancel »
            return None
        tail = " | ".join(result.stderr_text.strip().splitlines()[-4:])
        raise ExternalToolError(f"{TOOL} a échoué (code {result.returncode}) : {tail}", tool=TOOL)
    picked = result.stdout.decode("utf-8", errors="replace").strip()
    return Path(picked) if picked else None
