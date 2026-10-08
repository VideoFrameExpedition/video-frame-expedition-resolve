"""Smoke tests for the command-line interface."""

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from vfe_vision import __version__, cli
from vfe_vision.cli import app


def test_version_command_prints_package_version() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_geonames_already_there_is_not_downloaded_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An installation run again (bootstrap) leaves the places as they are, as the models;
    --refresh downloads them again."""
    from vfe_vision.adapters.geo import geonames_build
    from vfe_vision.adapters.geo.offline import AREAS_FILE, PLACES_FILE
    from vfe_vision.core import config

    folder = tmp_path / "geonames"
    folder.mkdir()
    for name in (PLACES_FILE, AREAS_FILE):
        (folder / name).write_bytes(b"")
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(geonames_dir=folder))
    downloads: list[Path] = []

    def download(folder: Path) -> int:
        downloads.append(folder)
        return 7

    monkeypatch.setattr(geonames_build, "download_gazetteer", download)
    monkeypatch.setattr(cli, "_online_or_exit", lambda: None)

    result = CliRunner().invoke(app, ["models", "geonames"])
    assert result.exit_code == 0, result.output
    assert "GeoNames : déjà installé" in result.output
    assert downloads == []

    result = CliRunner().invoke(app, ["models", "geonames", "--refresh"])
    assert result.exit_code == 0, result.output
    assert downloads == [folder]
    assert "7 lieux enregistrés" in result.output


def test_a_redirected_output_never_fails_on_a_character(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirected to a file on Windows, the output takes the system's code page (cp1252), which
    has no « → »: written « ? », the command goes on (``vfe doctor > diagnostic.txt``)."""
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    cli._never_fail_on_a_character()
    print("LM Studio → modèle chargé")
    out.flush()
    assert raw.getvalue() == "LM Studio ? modèle chargé\n".encode("cp1252")
