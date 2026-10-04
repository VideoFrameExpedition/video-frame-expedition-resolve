"""Smoke tests for the command-line interface."""

from typer.testing import CliRunner

from vfe_vision import __version__
from vfe_vision.cli import app


def test_version_command_prints_package_version() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
