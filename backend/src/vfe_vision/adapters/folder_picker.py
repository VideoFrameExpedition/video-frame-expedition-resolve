"""The Windows folder picker, opened on the desktop of the machine running the app.

A web page cannot learn the full path of a folder the user picks, so the server opens the native
dialog itself, in a short-lived child process (Tk's ``askdirectory`` is the modern Explorer
dialog on Windows): the app's own process never hosts a GUI event loop.
"""

from __future__ import annotations

import os
import sys
from functools import cache
from pathlib import Path

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.procs import KillOnCloseJob, run_process

TIMEOUT_S = 15 * 60  # left open this long, the dialog is closed and nothing is picked

_SCRIPT = r"""
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


def default_start() -> Path | None:
    """Where the dialog opens: the user's Videos folder when there is one."""
    for candidate in (Path.home() / "Videos", Path.home() / "Vidéos", Path.home()):
        if candidate.is_dir():
            return candidate
    return None


@cache
def _app_job() -> KillOnCloseJob:
    """Held by the app's process for its lifetime: a dialog never outlives the app."""
    return KillOnCloseJob()


def pick_folder(
    title: str, start: Path | None = None, *, cancel: CancelToken | None = None
) -> Path | None:
    """The folder the user chose, or None when the dialog was cancelled. ``cancel`` closes
    the dialog (page closed, app stopping)."""
    result = run_process(
        [sys.executable, "-c", _SCRIPT, title, str(start or default_start() or "")],
        timeout_s=TIMEOUT_S,
        cancel=cancel,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=True,
        tool_name="sélecteur de dossier",
        job=_app_job(),
    )
    picked = result.stdout.decode("utf-8", errors="replace").strip()
    return Path(picked) if picked else None
