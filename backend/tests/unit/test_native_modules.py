"""A compiled extension refused by Windows' Smart App Control is set aside when the package has
the same module in plain Python; every other import error stands."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vfe_vision import cli
from vfe_vision.core import native_modules
from vfe_vision.core.native_modules import plain_twin, refused_by_policy, set_aside


def _package(tmp_path: Path, *, twin: bool = True) -> Path:
    """A package with a compiled module, and the same module in plain Python next to it."""
    folder = tmp_path / "pkg" / "engine"
    folder.mkdir(parents=True)
    extension = folder / "_util_cy.cp312-win_amd64.pyd"
    extension.write_bytes(b"MZ compiled")
    if twin:
        (folder / "_util_cy.py").write_text("VALUE = 1\n", encoding="utf-8")
    return extension


def _failed(path: Path | None) -> ImportError:
    return ImportError(
        "DLL load failed while importing _util_cy", name="_util_cy", path=str(path or "")
    )


def test_a_refused_extension_with_a_plain_twin_is_set_aside(tmp_path: Path) -> None:
    extension = _package(tmp_path)
    assert plain_twin(extension) == extension.with_name("_util_cy.py")
    aside = set_aside(_failed(extension), refused=lambda _path: True)
    assert aside == extension.with_name("_util_cy.cp312-win_amd64.pyd.refused")
    assert aside.read_bytes() == b"MZ compiled"  # kept, under another name
    assert not extension.exists()
    assert extension.with_name("_util_cy.py").exists()
    # Refused again after an update of the package: the older copy gives way.
    extension.write_bytes(b"MZ newer")
    again = set_aside(_failed(extension), refused=lambda _path: True)
    assert again == aside
    assert aside.read_bytes() == b"MZ newer"


def test_any_other_import_error_stands(tmp_path: Path) -> None:
    allowed = _package(tmp_path / "a")
    assert set_aside(_failed(allowed), refused=lambda _path: False) is None  # a missing DLL, say
    assert allowed.exists()
    alone = _package(tmp_path / "b", twin=False)
    assert set_aside(_failed(alone), refused=lambda _path: True) is None  # nothing to fall back on
    assert alone.exists()
    script = tmp_path / "module.py"
    script.write_text("import nothing_here\n", encoding="utf-8")
    assert set_aside(_failed(script), refused=lambda _path: True) is None  # not an extension
    assert set_aside(_failed(None), refused=lambda _path: True) is None  # a module not found
    assert set_aside(_failed(tmp_path / "gone.pyd"), refused=lambda _path: True) is None


def test_a_file_windows_loads_is_not_refused() -> None:
    if sys.platform == "win32":
        kernel32 = Path(r"C:\Windows\System32\kernel32.dll")
        assert refused_by_policy(kernel32) is False
    assert refused_by_policy(Path("no-such-file.pyd")) is False  # absent is not refused


def test_the_command_starts_again_without_the_refused_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    extension = _package(tmp_path)
    started: list[list[str]] = []

    class Again:
        def __init__(self, command: list[str]) -> None:
            started.append(command)

        def __enter__(self) -> Again:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def wait(self) -> int:
            return 7

    def broken() -> None:
        raise _failed(extension)

    monkeypatch.setattr(cli, "app", broken)
    monkeypatch.setattr(native_modules, "refused_by_policy", lambda _path: True)
    monkeypatch.setattr(
        native_modules,
        "set_aside",
        lambda error: set_aside(error, refused=lambda _path: True),
    )
    monkeypatch.setattr("subprocess.Popen", Again)
    monkeypatch.setattr(sys, "argv", ["vfe", "serve", "--port", "8765"])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 7  # the exit code of the command run again
    assert started == [[sys.executable, "-m", "vfe_vision", "serve", "--port", "8765"]]
    assert not extension.exists()
    assert "Smart App Control" in capsys.readouterr().err

    # Anything else is raised as it is, and nothing is started.
    def missing() -> None:
        raise ModuleNotFoundError("No module named 'nothing'", name="nothing")

    monkeypatch.setattr(cli, "app", missing)
    with pytest.raises(ModuleNotFoundError):
        cli.main()
    assert len(started) == 1
