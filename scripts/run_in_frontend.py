"""Pre-commit helper: run a frontend tool from `frontend/` with repo-relative paths rewritten.

Usage: run_in_frontend.py <tool> [tool args...] -- <files...>

The tool is taken from `frontend/node_modules/.bin/` when it is installed there, rather than
through `pnpm exec`: Windows Smart App Control blocks `pnpm.exe` on some machines, and the shim
only needs `node`. `pnpm exec` stays as the fallback, for a tool that has no shim.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
PREFIX = "frontend/"


def local_tool(name: str) -> str | None:
    """The tool's shim in `frontend/node_modules/.bin`, if it is installed."""
    binaries = FRONTEND / "node_modules" / ".bin"
    for candidate in (name + ".cmd", name) if os.name == "nt" else (name,):
        path = binaries / candidate
        if path.is_file():
            return str(path)
    return None


def main(argv: list[str]) -> int:
    if "--" in argv:
        split = argv.index("--")
        tool_args, files = argv[:split], argv[split + 1 :]
    else:
        tool_args, files = argv, []
    rel_files = [f.replace("\\", "/").removeprefix(PREFIX) for f in files]

    shim = local_tool(tool_args[0]) if tool_args else None
    if shim:
        command = [shim, *tool_args[1:], *rel_files]
    else:
        pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm") or "pnpm"
        command = [pnpm, "exec", *tool_args, *rel_files]
    return subprocess.call(command, cwd=FRONTEND)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
