"""A compiled extension refused by Windows' Smart App Control is set aside when the package has
the same module in plain Python; every other import error stands. A refusal with
nothing to stand in is said plainly, wherever it shows, and every compiled file of the
application can be checked up front."""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vfe_vision import cli
from vfe_vision.api.errors import install_error_handlers
from vfe_vision.core import native_modules
from vfe_vision.core.config import Settings
from vfe_vision.core.native_modules import (
    REFUSED_EXIT,
    Refused,
    consequence,
    describe,
    is_refusal,
    plain_twin,
    refused_behind,
    refused_by_policy,
    runtime_distributions,
    scan,
    set_aside,
)
from vfe_vision.jobs.supervisor import WorkerSupervisor
from vfe_vision.services import system
from vfe_vision.services.system import CheckStatus

BLOCKED = "An Application Control policy has blocked this file."


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


def test_a_cython_source_is_not_a_plain_twin(tmp_path: Path) -> None:
    # PyAV ships the Cython sources of its extensions next to them: only Cython runs those.
    extension = _package(tmp_path, twin=False)
    extension.with_name("_util_cy.py").write_text(
        "import cython\nimport cython.cimports.libav as lib\n", encoding="utf-8"
    )
    assert plain_twin(extension) is None
    assert set_aside(_failed(extension), refused=lambda _path: True) is None
    assert extension.exists()


def test_a_refusal_is_known_by_windows_words_or_by_its_file(tmp_path: Path) -> None:
    extension = _package(tmp_path, twin=False)
    english = ImportError(f"DLL load failed while importing memory: {BLOCKED}", name="memory")
    french = ImportError(
        "DLL load failed while importing memory: Une stratégie de contrôle d’application a "
        "bloqué ce fichier.",
        name="memory",
    )
    assert is_refusal(english, refused=lambda _path: False)
    assert is_refusal(french, refused=lambda _path: False)
    assert is_refusal(_failed(extension), refused=lambda _path: True)  # asked to Windows
    assert not is_refusal(_failed(extension), refused=lambda _path: False)  # a missing DLL, say
    assert not is_refusal(ModuleNotFoundError("No module named 'h3'", name="h3"))
    assert not is_refusal(ValueError(BLOCKED))
    if sys.platform == "win32":
        assert is_refusal(OSError(0, "blocked", "ffmpeg.exe", native_modules.POLICY_VIOLATION))


def _installed(
    site: Path, name: str, version: str, files: dict[str, bytes]
) -> metadata.Distribution:
    """A package installed in ``site`` with these files (its RECORD lists them)."""
    for relative, content in files.items():
        (site / relative).parent.mkdir(parents=True, exist_ok=True)
        (site / relative).write_bytes(content)
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
    )
    record = [*files, f"{info.name}/METADATA", "../../Scripts/tool.exe"]
    (info / "RECORD").write_text("".join(f"{line},,\n" for line in record), encoding="utf-8")
    return metadata.Distribution.at(info)


def _av(site: Path) -> metadata.Distribution:
    return _installed(site, "av", "18.1.0", {"av/_core.pyd": b"MZ", "av.libs/avcodec.dll": b"MZ"})


def test_the_scan_names_the_refused_files_of_each_package(tmp_path: Path) -> None:
    h3 = _installed(
        tmp_path,
        "h3",
        "4.5.0",
        {
            "h3/__init__.py": b"",
            "h3/_cy/cells.cp312-win_amd64.pyd": b"MZ",
            "h3/_cy/memory.cp312-win_amd64.pyd": b"MZ",
        },
    )
    checked, refused = scan(
        [h3, _av(tmp_path)], refused=lambda path: path.stem.startswith(("memory", "avcodec"))
    )
    assert checked == 4  # the launchers in Scripts are never run: not checked
    assert [(r.path.name, r.package) for r in refused] == [
        ("memory.cp312-win_amd64.pyd", "h3 4.5.0"),
        ("avcodec.dll", "av 18.1.0"),
    ]


def test_the_files_behind_a_failed_import_are_found_in_its_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    av = _av(tmp_path)
    core = tmp_path / "av" / "_core.pyd"
    failed = ImportError(f"DLL load failed while importing _core: {BLOCKED}", path=str(core))
    # The extension itself loads; a library it needs is the refused one.
    monkeypatch.setattr(native_modules, "_owner", lambda _path: av)
    found = refused_behind(failed, refused=lambda path: path.name == "avcodec.dll")
    assert found == [Refused(tmp_path / "av.libs" / "avcodec.dll", "av 18.1.0")]
    # Nobody owns the file: it is the one named.
    monkeypatch.setattr(native_modules, "_owner", lambda _path: None)
    assert refused_behind(failed, refused=lambda _path: True) == [Refused(core, None)]


@pytest.mark.skipif(sys.platform != "win32", reason="extensions are .pyd files on Windows")
def test_an_installed_extension_is_owned_by_its_package() -> None:
    import numpy._core._multiarray_umath as compiled  # type: ignore[import-not-found]

    owner = native_modules._owner(Path(str(compiled.__file__)))
    assert owner is not None
    assert owner.metadata["Name"] == "numpy"


def test_the_runtime_packages_follow_the_requirements_and_their_extras() -> None:
    names = {d.metadata["Name"].lower() for d in runtime_distributions()}
    assert {"vfe-vision", "numpy", "timezonefinder", "h3", "uvicorn", "httptools"} <= names
    assert not names & {"pytest", "mypy", "ruff", "hypothesis"}  # development tools


def test_what_the_application_does_without_a_refused_file(tmp_path: Path) -> None:
    twin = _package(tmp_path)
    assert consequence(Refused(twin, "SQLAlchemy 2.1.1")) is not None  # its Python version
    zones = consequence(Refused(tmp_path / "memory.cp312-win_amd64.pyd", "h3 4.5.0"))
    assert zones is not None
    assert "fuseaux" in zones
    assert consequence(Refused(tmp_path / "_multiarray_umath.pyd", "numpy 2.5.3")) is None
    assert consequence(Refused(tmp_path / "x.pyd", None)) is None


def test_a_refusal_is_described_for_a_failed_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    named = [Refused(Path("xxhash.pyd"), "xxhash 4.0.1")]
    monkeypatch.setattr(native_modules, "refused_behind", lambda _error: named)
    text = describe(ImportError(f"DLL load failed while importing xxhash: {BLOCKED}"))
    assert text is not None
    assert "xxhash.pyd (xxhash 4.0.1)" in text
    assert native_modules.HINT in text
    assert describe(RuntimeError("boom")) is None


def test_a_refused_file_without_a_twin_is_said_plainly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    extension = _package(tmp_path, twin=False)
    failed = ImportError(
        f"DLL load failed while importing _util_cy: {BLOCKED}", name="_util_cy", path=str(extension)
    )

    def broken() -> None:
        raise failed

    def started(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("nothing is started again")

    monkeypatch.setattr(cli, "app", broken)
    named = [Refused(extension, "pkg 1.0")]
    monkeypatch.setattr(native_modules, "refused_behind", lambda _error: named)
    monkeypatch.setattr("subprocess.Popen", started)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == REFUSED_EXIT
    said = capsys.readouterr().err
    assert "Smart App Control" in said
    assert f"{extension}  (pkg 1.0)" in said
    assert cli.ISSUES_URL in said
    assert "Traceback" not in said


def test_a_worker_refused_by_windows_is_not_started_again(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Ended:
        pid = 1

        def wait(self) -> int:
            return REFUSED_EXIT

        def poll(self) -> int:
            return REFUSED_EXIT

    supervisor = WorkerSupervisor(settings)
    supervisor._proc = Ended()  # type: ignore[assignment]
    spawned: list[bool] = []
    monkeypatch.setattr(supervisor, "_spawn", lambda: spawned.append(True))
    supervisor._watch()  # returns at once: started again, it would be refused again
    assert spawned == []


def test_an_api_request_reports_a_refusal_plainly() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/fingerprint")
    def fingerprint() -> None:
        raise ImportError(f"DLL load failed while importing xxhash: {BLOCKED}", name="xxhash")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/fingerprint")
    assert response.status_code == 503
    assert response.json()["code"] == "refused_by_windows"
    assert "Smart App Control" in response.json()["detail"]


def test_the_check_says_what_windows_refuses_and_what_follows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    twin = _package(tmp_path)
    zones = tmp_path / "memory.cp312-win_amd64.pyd"
    found: list[Refused] = [Refused(twin, "SQLAlchemy 2.1.1"), Refused(zones, "h3 4.5.0")]
    monkeypatch.setattr(native_modules, "scan", lambda: (197, found))
    monkeypatch.setattr(system, "_failed_imports", lambda _modules: [])
    tolerated = system.binaries()
    assert tolerated.status == CheckStatus.WARNING
    assert "memory.cp312-win_amd64.pyd (h3 4.5.0)" in tolerated.detail
    assert tolerated.hint == native_modules.HINT

    found.append(Refused(tmp_path / "_multiarray_umath.pyd", "numpy 2.5.3"))
    serious = system.binaries()
    assert serious.status == CheckStatus.ERROR
    assert "Refusés par Windows : _multiarray_umath.pyd (numpy 2.5.3)" in serious.detail

    found.clear()
    accepted = system.binaries()
    assert accepted.status == CheckStatus.OK
    assert accepted.detail.startswith("197 fichiers compilés acceptés par Windows")
