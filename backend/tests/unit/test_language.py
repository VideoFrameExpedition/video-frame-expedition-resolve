"""Language of the terminal's messages: VFE_LANG, then the .env file, then the system."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from vfe_vision.core import language
from vfe_vision.core.errors import ExternalToolError
from vfe_vision.core.procs import ProcessResult


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Each test decides again, from a folder without a .env file; then back to French."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VFE_LANG", raising=False)
    language.terminal_language.cache_clear()
    yield
    language.terminal_language.cache_clear()


@pytest.mark.parametrize(
    ("value", "expected"),
    [("fr", "fr"), ("FR-fr", "fr"), ('"fr"', "fr"), ("en", "en"), ("de", "en")],
)
def test_vfe_lang_decides(monkeypatch: pytest.MonkeyPatch, value: str, expected: str) -> None:
    monkeypatch.setenv("VFE_LANG", value)
    monkeypatch.setattr(language, "system_language", lambda: "fr")
    assert language.terminal_language() == expected


def test_then_the_env_file_of_the_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "VFE_PORT=8765\n# VFE_LANG=fr\nVFE_LANG = en\n", encoding="utf-8"
    )
    monkeypatch.setattr(language, "system_language", lambda: "fr-FR")
    assert language.terminal_language() == "en"


@pytest.mark.parametrize(
    ("system", "expected"), [("fr-FR", "fr"), ("fr_BE.UTF-8", "fr"), ("en-US", "en"), ("", "en")]
)
def test_otherwise_the_system_and_english_unless_french(
    monkeypatch: pytest.MonkeyPatch, system: str, expected: str
) -> None:
    monkeypatch.setattr(language, "system_language", lambda: system)
    assert language.terminal_language() == expected


def test_decided_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VFE_LANG", "en")
    assert language.terminal_language() == "en"
    monkeypatch.setenv("VFE_LANG", "fr")
    assert language.terminal_language() == "en"


def test_tr_picks_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VFE_LANG", "fr")
    assert language.tr("bonjour", "hello") == "bonjour"
    language.terminal_language.cache_clear()
    monkeypatch.setenv("VFE_LANG", "en")
    assert language.tr("bonjour", "hello") == "hello"


def test_the_first_preferred_language_of_a_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    out = b'(\n    "fr-FR",\n    "en-FR"\n)\n'
    monkeypatch.setattr(language, "run_process", lambda *a, **k: ProcessResult(0, out, b"", 0.01))
    assert language._first_apple_language() == "fr-FR"


def test_a_mac_that_cannot_tell_gives_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> ProcessResult:
        raise ExternalToolError("defaults absent", tool="defaults")

    monkeypatch.setattr(language, "run_process", refuse)
    assert language._first_apple_language() == ""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows asks the system, not the locale")
def test_elsewhere_the_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(language, "_first_apple_language", lambda: "")
    for name in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert language.system_language() == "fr_FR.UTF-8"


@pytest.mark.skipif(sys.platform != "win32", reason="the display language of Windows")
def test_windows_answers_french_or_english() -> None:
    assert language.system_language() in {"fr", "en"}
