"""The launchers (run.bat on Windows, run.command on macOS) and the macOS installers
(install.sh, the one line typed in the Terminal; scripts/bootstrap.sh): what a new computer
runs at the first start."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
SCRIPTS = ("run.command", "scripts/bootstrap.sh", "install.sh")


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


def test_the_readme_gives_the_line_that_installs_on_a_mac() -> None:
    """The one line of the README fetches install.sh from the repository's main branch, and
    install.sh hands over to scripts/bootstrap.sh, then adds the application to the
    Applications folder."""
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    line = re.search(r'/bin/sh -c "\$\(curl -fsSL (\S+)\)"', installer)
    assert line
    assert line.group(1).endswith("/main/install.sh")
    for readme in ("README.md", "README.fr.md"):
        if (ROOT / readme).is_file():
            assert line.group(0) in (ROOT / readme).read_text(encoding="utf-8"), readme
    assert 'sh "$DIR/scripts/bootstrap.sh" "$@"' in installer
    assert "osacompile" in installer


def test_the_installers_to_double_click_hand_over_to_the_scripts() -> None:
    """install.bat and install.command, next to run.bat and run.command, for the application's
    folder downloaded as a ZIP or cloned: the same installation as the scripts, nothing
    downloaded again; run.bat and run.command offer them when nothing is installed yet."""
    command = (ROOT / "install.command").read_bytes()
    assert command.startswith(b"#!/bin/bash")
    assert b"\r" not in command
    assert b'exec /bin/sh ./install.sh "$@"' in command
    assert "*install.sh)" in (ROOT / "install.sh").read_text(encoding="utf-8")  # run as a file

    bat = (ROOT / "install.bat").read_bytes()
    assert bat.count(b"\n") == bat.count(b"\r\n")  # a batch file with LF lines loses its labels
    assert b'-File "scripts\\bootstrap.ps1" %*' in bat
    assert b"Unblock-File" in bat  # the mark of the Web of a ZIP, removed
    # Once installed, the application starts in the same window if wanted.
    assert b'"%~dp0run.bat"' in bat
    assert 'exec /bin/bash "$DIR/run.command"' in (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "VFE_NO_OPEN=1 /bin/sh ./install.sh" in (ROOT / "run.command").read_text(
        encoding="utf-8"
    )
    assert 'call "%~dp0install.bat"' in (ROOT / "run.bat").read_text(encoding="utf-8")


def test_node_comes_from_its_zip_when_its_installer_is_refused() -> None:
    """Smart App Control refuses Node.js's installer (error 1723, nodejs/node#63005): the
    official ZIP, checked against its published checksum, then found by run.bat."""
    script = (ROOT / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    # Under Smart App Control, Node.js's ZIP comes first; ExifTool's only when winget fails.
    assert '"OpenJS.NodeJS.LTS" { Install-NodeFromZip } -OtherwiseFirst:$Sac' in script
    assert '"OliverBetz.ExifTool" { Install-ExifToolFromZip }\n' in script
    assert "$states -contains $policy.VerifiedAndReputablePolicyState" in script
    assert "SHASUMS256.txt" in script
    assert "exiftool.org/checksums.txt" in script
    assert "Get-FileHash $zip -Algorithm SHA256" in script
    assert r"%LOCALAPPDATA%\Programs\nodejs" in (ROOT / "run.bat").read_text(encoding="utf-8")


def test_the_windows_installation_asks_windows_about_every_compiled_file() -> None:
    """Smart App Control may refuse a compiled file of a package: the installation says which,
    right after the packages, rather than the first start."""
    script = (ROOT / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    check = script.index("python -m vfe_vision doctor --binaries")
    assert script.index("uv sync ") < check < script.index("models $pack")
    assert "$Refused = $LASTEXITCODE -ne 0" in script
    assert "if ($Refused) {" in script


def test_under_smart_app_control_the_application_runs_on_a_signed_python() -> None:
    """The Python uv downloads is not signed, and a build a few days old may be refused in part
    (its _sqlite3.pyd, 08/10): under Smart App Control, on or in evaluation, python.org's."""
    script = (ROOT / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    assert "if (Test-SmartAppControl -OrEvaluation) {" in script
    assert '"Python.Python.3.12" -Scope user' in script
    assert '(Get-AuthenticodeSignature $python).Status -eq "Valid"' in script
    assert "--project backend --python $SignedPython" in script
    assert "@(1, 2)" in script  # evaluation: Windows may turn it on by itself


def test_the_windows_installation_puts_the_application_in_the_start_menu() -> None:
    """As in the Applications folder of a Mac: a shortcut to run.bat, with the application's
    icon (an .ico rendered with the other icons of the logo)."""
    script = (ROOT / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    assert 'GetFolderPath("Programs")' in script
    assert 'Join-Path $Root "run.bat"' in script
    assert r"docs\brand\icons\app-icon.ico" in script
    icon = (ROOT / "docs" / "brand" / "icons" / "app-icon.ico").read_bytes()
    assert icon[:4] == bytes([0, 0, 1, 0])  # an icon, not a cursor
    assert int.from_bytes(icon[4:6], "little") >= 4  # several sizes, 16 to 256 px


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


QUOTED = r'"(?:[^"\\`]|[\\`].)*"'  # a double-quoted text, escapes of sh (\") and PowerShell (`")


def _statements(text: str) -> str:
    """One statement per line: the lines continued with a backslash joined."""
    return re.sub(r"\\\n\s*", " ", text)


def test_every_message_of_the_launchers_and_installers_exists_in_both_languages() -> None:
    """French on a French system, English otherwise (core.language): a message given in one
    language only would show up untranslated."""
    for name in ("run.bat", "install.bat"):
        bat = (ROOT / name).read_text(encoding="utf-8")
        calls = re.findall(r"call :say\b.*", bat)
        assert calls, name
        for call in calls:
            assert re.fullmatch(rf"call :say {QUOTED} +{QUOTED}", call.strip()), (name, call)

    for name in SCRIPTS:
        text = _statements((ROOT / name).read_text(encoding="utf-8"))
        uses = re.findall(r"(?<![\w-])say\s.*", text)
        assert uses, name
        for use in uses:
            assert re.match(rf"say {QUOTED} +{QUOTED}", use), (name, use)
        # install.sh runs before the application's folder is there: it decides it itself.
        assert ". scripts/language.sh" in text or name == "install.sh", name

    ps1 = (ROOT / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    uses = re.findall(r"\(T\s.*", ps1)
    assert uses
    for use in uses:
        assert re.match(rf"\(T {QUOTED} +{QUOTED}\)", use), use
