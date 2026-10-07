"""The launchers (run.bat on Windows, run.command on macOS) and the macOS installer
(scripts/bootstrap.sh): what a new computer runs at the first start."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
SCRIPTS = ("run.command", "scripts/bootstrap.sh")


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


def test_a_new_mac_installs_with_the_pnpm_of_the_project() -> None:
    """Without pnpm, run.command runs it through npx: in the version that writes the lockfile,
    or the lockfile is refused (pnpm 10 cannot read the two-document lockfile of pnpm 12)."""
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    pinned = package["packageManager"]
    launcher = (ROOT / "run.command").read_text(encoding="utf-8")
    assert re.findall(r"npx --yes (pnpm@[\w.]+)", launcher) == [pinned]
    lockfile = (ROOT / "frontend" / "pnpm-lock.yaml").read_text(encoding="utf-8")
    recorded = re.search(r"packageManagerDependencies:\s+pnpm:\s+specifier: (\S+)", lockfile)
    assert recorded is None or f"pnpm@{recorded.group(1)}" == pinned


def test_the_scripts_start_with_a_shebang_and_use_unix_line_endings() -> None:
    """A carriage return after ``#!/bin/bash`` makes macOS look for a shell named ``bash\\r``."""
    for name in SCRIPTS:
        raw = (ROOT / name).read_bytes()
        assert raw.startswith(b"#!/bin/"), name
        assert b"\r" not in raw, name


def test_stopping_the_launcher_with_ctrl_c_is_not_an_error() -> None:
    """The server ends on the signal it caught (Ctrl+C: 130, a stop request: 143): run.command
    must not show its error message and wait for Enter then."""
    launcher = (ROOT / "run.command").read_text(encoding="utf-8")
    assert re.search(r"0 \| 130 \| 143\) exit 0", launcher)


def test_the_launcher_and_the_installer_look_in_homebrew_folders() -> None:
    """Started from the Finder, the Terminal may not have Homebrew's folder in its PATH."""
    for name in SCRIPTS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "/opt/homebrew/bin" in text, name
        assert "/usr/local/bin" in text, name
