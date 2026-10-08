"""The interface in the package is built again when its sources changed since (an update)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).parents[3]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "copy_frontend_build", ROOT / "scripts" / "copy_frontend_build.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_interface_is_built_again_once_its_sources_changed(tmp_path: Path) -> None:
    build = _script()
    frontend = tmp_path / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "src" / "main.tsx").write_text("one", encoding="utf-8")
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (frontend / "dist").mkdir()
    (frontend / "dist" / "index.html").write_text("<html>", encoding="utf-8")
    target = tmp_path / "web" / "dist"
    assert not build.up_to_date(target, frontend)  # never built

    build.copy_build(frontend / "dist", target, frontend)
    assert (target / "index.html").is_file()
    assert build.up_to_date(target, frontend)
    # What a build writes, or the dependencies installed, are not sources.
    (frontend / "tsconfig.tsbuildinfo").write_text("x", encoding="utf-8")
    (frontend / "node_modules" / "vite").mkdir(parents=True)
    (frontend / "node_modules" / "vite" / "index.js").write_text("x", encoding="utf-8")
    assert build.up_to_date(target, frontend)

    (frontend / "src" / "main.tsx").write_text("two", encoding="utf-8")  # an update
    assert not build.up_to_date(target, frontend)
    build.copy_build(frontend / "dist", target, frontend)
    assert build.up_to_date(target, frontend)
    (frontend / "src" / "help.html").write_text("new", encoding="utf-8")  # a new file
    assert not build.up_to_date(target, frontend)


def test_an_interface_built_by_an_earlier_version_is_built_again(tmp_path: Path) -> None:
    build = _script()
    frontend = tmp_path / "frontend"
    (frontend / "src").mkdir(parents=True)
    target = tmp_path / "web" / "dist"
    target.mkdir(parents=True)
    (target / "index.html").write_text("<html>", encoding="utf-8")  # no stamp
    assert not build.up_to_date(target, frontend)


def test_the_launchers_check_the_interface_before_starting() -> None:
    for launcher in ("run.bat", "run.command"):
        text = (ROOT / launcher).read_text(encoding="utf-8")
        assert "copy_frontend_build.py --up-to-date" in text, launcher
