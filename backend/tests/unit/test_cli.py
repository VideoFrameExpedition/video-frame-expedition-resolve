"""Smoke tests for the command-line interface."""

import io
import sys

import pytest
from typer.testing import CliRunner

from vfe_vision import __version__, cli
from vfe_vision.cli import app


def test_version_command_prints_package_version() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


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
