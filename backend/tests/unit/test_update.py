"""The update (update.bat on Windows, update.command on macOS): the latest version in the
application's folder, keeping what is the user's. On Windows, scripts/update.ps1 is run for real
on an application's folder made up for the test, with a version given as a ZIP."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
UPDATE_PS1 = ROOT / "scripts" / "update.ps1"
QUOTED = r'"(?:[^"\\`]|[\\`].)*"'  # a double-quoted text, escapes of sh (\") and PowerShell (`")

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell 5.1 and cmd.exe")


def test_update_bat_is_read_in_one_go_before_the_update_replaces_it() -> None:
    """cmd.exe reads a batch file line by line as it runs it: everything after the start of the
    update is one block in parentheses, with no label to jump to."""
    bat = (ROOT / "update.bat").read_bytes()
    assert bat.count(b"\n") == bat.count(b"\r\n")  # a batch file with LF lines loses its labels
    text = bat.decode("utf-8")
    block = text[text.index("(\r\n") :]
    assert 'powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\\update.ps1" %*' in block
    assert block.rstrip().endswith(")")
    assert "call :" not in block
    assert "goto" not in block
    # "exit /b 1" inside a block ends the batch file with the code 0: the code goes through
    # delayed expansion.
    assert "exit /b !CODE!" in block
    assert 'set "PSModulePath="' in text[: text.index("(\r\n")]


def test_update_command_hands_over_to_a_script_read_in_one_go() -> None:
    """sh also reads as it goes: scripts/update.sh is one block { ... exit; }, read before the
    update replaces it."""
    command = (ROOT / "update.command").read_bytes()
    assert command.startswith(b"#!/bin/bash")
    assert b"\r" not in command
    assert b'exec /bin/sh scripts/update.sh "$@"' in command
    script = (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh")
    assert "\r" not in script
    body = [line for line in script.splitlines() if line and not line.startswith("#")]
    assert body[0] == "{"
    assert body[-2:] == ["exit", "}"]


def test_the_update_is_offered_next_to_the_application() -> None:
    """In the Start menu and the Applications folder, next to the application; the new version's
    installation runs without asking about LM Studio again."""
    bootstrap = (ROOT / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    assert 'New-Shortcut $UpdateName (Join-Path $Root "update.bat")' in bootstrap
    assert "-not $Update -and (Test-Keyboard)" in bootstrap
    update = UPDATE_PS1.read_text(encoding="utf-8-sig")
    assert 'scripts\\bootstrap.ps1") -Update' in update

    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'make_app "$UPDATE_APP" "$DIR/update.command"' in installer
    mac_bootstrap = (ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
    assert "--update | --mise-a-jour)" in mac_bootstrap
    assert 'exec /bin/sh "$ROOT/install.sh" --update' in (ROOT / "scripts" / "update.sh").read_text(
        encoding="utf-8"
    )


def test_both_updates_keep_the_same_files() -> None:
    """What is written next to the application's files, and hidden files (.env), are never
    removed: the same list on Windows and on macOS."""
    update = UPDATE_PS1.read_text(encoding="utf-8-sig")
    kept = re.search(r"\$Kept = '\^\((.+)\)\$'", update)
    assert kept
    assert kept.group(1) == r"\..*|node_modules|dist|__pycache__|.*\.egg-info"
    script = (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")
    excluded = re.findall(r"--exclude '([^']+)'", script)
    assert excluded == [".*", "node_modules", "dist", "__pycache__", "*.egg-info"]


def test_every_message_of_the_updates_exists_in_both_languages() -> None:
    for use in re.findall(r"\(T\s.*", UPDATE_PS1.read_text(encoding="utf-8-sig")):
        assert re.match(rf"\(T {QUOTED} +{QUOTED}\)", use), use
    script = re.sub(r"\\\n\s*", " ", (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8"))
    uses = re.findall(r"(?<![\w-])say\s.*", script)
    assert uses
    for use in uses:
        assert re.match(rf"say {QUOTED} +{QUOTED}", use), use


# --- update.ps1, run for real ----------------------------------------------------------------


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _installed(root: Path) -> Path:
    """An application's folder, version 1.0.0, as the user has it after using it."""
    _write(
        root,
        {
            "run.bat": "@echo off\r\n",
            "backend/pyproject.toml": '[project]\nname = "vfe-vision"\nversion = "1.0.0"\n',
            "backend/src/app/kept.py": "old = True\n",
            "backend/src/app/gone.py": "gone = True\n",
            "backend/src/app/__pycache__/gone.cpython-312.pyc": "cache",
            "backend/.venv/pyvenv.cfg": "home = C:\\Python312\n",
            "backend/src/vfe_vision/web/dist/index.html": "<html>built</html>",
            "frontend/pnpm-lock.yaml": "lock: 1\n",
            "frontend/node_modules/vite/bin/vite.js": "// vite",
            "frontend/src/old.tsx": "export {};\n",
            "docs/old.md": "old\n",
            ".env": "VFE_LMSTUDIO_URL=http://192.168.1.20:1234\n",
            "my notes.txt": "mine\n",
        },
    )
    (root / "scripts").mkdir(exist_ok=True)
    shutil.copy(UPDATE_PS1, root / "scripts" / "update.ps1")
    shutil.copy(ROOT / "update.bat", root / "update.bat")
    return root


def _release(tmp: Path, *, lock: str = "lock: 1\n") -> Path:
    """Version 1.0.1 as GitHub gives it: a ZIP holding one folder named after the version."""
    folder = tmp / "video-frame-expedition-resolve-1.0.1"
    _write(
        folder,
        {
            "run.bat": "@echo off\r\n",
            "backend/pyproject.toml": '[project]\nname = "vfe-vision"\nversion = "1.0.1"\n',
            "backend/src/app/kept.py": "new = True\n",
            "backend/src/app/added.py": "added = True\n",
            "frontend/pnpm-lock.yaml": lock,
            "frontend/src/new.tsx": "export {};\n",
            "docs/new.md": "new\n",
            ".github/workflows/checks.yml": "on: push\n",
        },
    )
    (folder / "scripts").mkdir()
    shutil.copy(UPDATE_PS1, folder / "scripts" / "update.ps1")
    shutil.copy(ROOT / "update.bat", folder / "update.bat")
    archive = tmp / "v1.0.1.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in folder.rglob("*"):
            if path.is_file():
                zipped.write(path, path.relative_to(tmp).as_posix())
    shutil.rmtree(folder)
    return archive


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _environment() -> dict[str, str]:
    """As update.bat passes it on: without PSModulePath (the tests may run under PowerShell 7,
    whose module folders hide some commands of Windows PowerShell's)."""
    environment = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    return {**environment, "VFE_LANG": "en"}


def _update(root: Path, *args: str, port: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / "update.ps1"),
            *args,
            "-Port",
            str(port or _free_port()),
        ],
        cwd=root,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


@windows_only
def test_an_update_replaces_the_application_and_keeps_what_is_the_users(tmp_path: Path) -> None:
    root = _installed(tmp_path / "app")
    result = _update(root, "-From", str(_release(tmp_path)), "-NoInstall")
    assert result.returncode == 0, result.stdout + result.stderr

    assert 'version = "1.0.1"' in (root / "backend/pyproject.toml").read_text(encoding="utf-8")
    assert (root / "backend/src/app/kept.py").read_text(encoding="utf-8") == "new = True\n"
    assert (root / "backend/src/app/added.py").is_file()
    assert (root / "update.bat").is_file()
    # What the new version no longer has, in the application's own folders: removed.
    assert not (root / "backend/src/app/gone.py").exists()
    assert not (root / "frontend/src/old.tsx").exists()
    assert not (root / "docs/old.md").exists()
    # What is the user's, or written next to the application: kept.
    assert (root / ".env").read_text(encoding="utf-8").startswith("VFE_LMSTUDIO_URL=")
    assert (root / "my notes.txt").is_file()
    assert (root / "backend/.venv/pyvenv.cfg").is_file()
    assert (root / "backend/src/app/__pycache__/gone.cpython-312.pyc").is_file()
    assert (root / "backend/src/vfe_vision/web/dist/index.html").is_file()
    assert (root / "frontend/node_modules/vite/bin/vite.js").is_file()  # same lockfile
    assert "removed: 3" in result.stdout


@windows_only
def test_new_dependencies_of_the_interface_are_installed_again(tmp_path: Path) -> None:
    root = _installed(tmp_path / "app")
    result = _update(root, "-From", str(_release(tmp_path, lock="lock: 2\n")), "-NoInstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "frontend/node_modules").exists()  # run.bat installs them again


@windows_only
def test_nothing_is_replaced_while_the_application_runs(tmp_path: Path) -> None:
    root = _installed(tmp_path / "app")
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = int(server.getsockname()[1])
        result = _update(root, "-From", str(_release(tmp_path)), "-NoInstall", port=port)
    assert result.returncode == 1
    assert "The application is running" in result.stdout
    assert 'version = "1.0.0"' in (root / "backend/pyproject.toml").read_text(encoding="utf-8")


@windows_only
def test_a_folder_from_git_is_updated_with_git(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git")
    origin = tmp_path / "origin"
    _installed(origin)

    def git(cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

    git(origin, "init", "-q", "-b", "main")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "1.0.0")
    git(tmp_path, "clone", "-q", str(origin), "app")
    root = tmp_path / "app"

    result = _update(root, "-NoInstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "You already have the latest version (1.0.0)" in result.stdout

    project = origin / "backend/pyproject.toml"
    project.write_text(project.read_text(encoding="utf-8").replace("1.0.0", "1.0.1"), "utf-8")
    git(origin, "commit", "-q", "-am", "1.0.1")
    result = _update(root, "-NoInstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'version = "1.0.1"' in (root / "backend/pyproject.toml").read_text(encoding="utf-8")


@windows_only
def test_update_bat_says_when_the_update_failed(tmp_path: Path) -> None:
    """Its exit code is the update's: a window opened by another program can tell."""
    root = _installed(tmp_path / "app")
    release = str(_release(tmp_path))
    missing = str(tmp_path / "no-such-version.zip")
    for args, expected in ((["-From", release], 0), (["-From", missing], 1)):
        result = subprocess.run(
            [
                "cmd",
                "/c",
                str(root / "update.bat"),
                *args,
                "-NoInstall",
                "-Port",
                str(_free_port()),
            ],
            cwd=root,
            env={**_environment(), "VFE_NO_PAUSE": "1"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        assert result.returncode == expected, (args, result.stdout + result.stderr)
