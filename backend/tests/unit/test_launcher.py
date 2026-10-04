"""The Windows launcher (run.bat): what a new PC runs at the first start."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[3]


def test_a_new_pc_installs_with_the_pnpm_of_the_project() -> None:
    """Without pnpm, run.bat runs it through npx: in the version that writes the lockfile, or
    the lockfile is refused (pnpm 10 cannot read the two-document lockfile of pnpm 12)."""
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    pinned = package["packageManager"]
    launcher = (ROOT / "run.bat").read_text(encoding="utf-8")
    assert re.findall(r"npx\.cmd --yes (pnpm@[\w.]+)", launcher) == [pinned]
    lockfile = (ROOT / "frontend" / "pnpm-lock.yaml").read_text(encoding="utf-8")
    recorded = re.search(r"packageManagerDependencies:\s+pnpm:\s+specifier: (\S+)", lockfile)
    assert recorded is None or f"pnpm@{recorded.group(1)}" == pinned
