"""Core helpers: paths, atomic writes, processes, configuration."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from vfe_vision.core import atomic_io
from vfe_vision.core.atomic_io import atomic_write_bytes, atomic_write_text
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings, is_loopback
from vfe_vision.core.errors import CancelledError, ExternalToolError
from vfe_vision.core.paths import (
    CASE_INSENSITIVE_PATHS,
    is_video_file,
    is_within,
    joined_inside,
    normalize_text,
    path_key,
)
from vfe_vision.core.procs import run_process


class TestPaths:
    def test_is_within(self, tmp_path: Path) -> None:
        assert is_within(tmp_path / "a" / "b.mp4", tmp_path)
        assert is_within(tmp_path, tmp_path)
        assert not is_within(tmp_path.parent / (tmp_path.name + "-other"), tmp_path)

    def test_joined_inside_keeps_relative_paths(self, tmp_path: Path) -> None:
        assert joined_inside(tmp_path, "videos/a/1.jpg") == tmp_path / "videos" / "a" / "1.jpg"
        assert joined_inside(tmp_path, "") == tmp_path

    @pytest.mark.parametrize(
        "text",
        [
            r"\\attacker.example\share\x",  # a network path: Windows would log in to it
            "//attacker.example/share/x",
            "/etc/passwd",
            "C:/Windows/win.ini",
            "C:relative",
            "videos/../../vfe.sqlite3",
            r"videos\..\..\vfe.sqlite3",
            "x\0y",
        ],
    )
    def test_joined_inside_refuses_what_could_leave_the_root(
        self, tmp_path: Path, text: str
    ) -> None:
        assert joined_inside(tmp_path, text) is None

    @pytest.mark.skipif(not CASE_INSENSITIVE_PATHS, reason="case-insensitive paths")
    def test_path_key_is_case_insensitive_on_windows_and_macos(self, tmp_path: Path) -> None:
        upper, lower = tmp_path / "Vidéos" / "Été.MP4", tmp_path / "vidéos" / "été.mp4"
        assert path_key(upper) == path_key(lower)

    def test_path_key_spells_accents_one_way(self, tmp_path: Path) -> None:
        """A Mac (HFS+, a share, Resolve) may give « é » as « e » plus a combining accent."""
        composed, decomposed = tmp_path / "Rushs été", tmp_path / "Rushs été"
        assert path_key(composed) == path_key(decomposed)
        assert normalize_text("é") == "é"

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("clip.MP4", True),
            ("clip.mov", True),
            ("._clip.mp4", False),
            (".temp-20260622_201056811_BACK_SECOND_TELE.mp4", False),
            ("notes.txt", False),
        ],
    )
    def test_is_video_file(self, name: str, expected: bool) -> None:
        assert is_video_file(Path(name)) is expected


def test_atomic_write_replaces_content(tmp_path: Path) -> None:
    target = tmp_path / "sous dossier" / "données.json"
    atomic_write_text(target, "un")
    atomic_write_text(target, "deux")
    assert target.read_text(encoding="utf-8") == "deux"
    assert list(target.parent.iterdir()) == [target]  # no temp file left behind


def test_a_folder_that_refuses_renames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """As the shared folders of Windows Sandbox: every rename refused, even once the file is
    closed. A user's folder gets the file written in place; the data folder never does."""

    def refused(self: Path, target: Path) -> Path:
        raise PermissionError(32, "used by another process")

    monkeypatch.setattr(Path, "replace", refused)
    monkeypatch.setattr(atomic_io, "_RETRY_DELAYS_S", ())
    target = tmp_path / "clip_FR.srt"
    target.write_bytes(b"avant")
    atomic_write_bytes(target, b"apres", create_parents=False, in_place_fallback=True)
    assert target.read_bytes() == b"apres"
    assert list(tmp_path.iterdir()) == [target]  # no temp file left behind
    with pytest.raises(PermissionError):
        atomic_write_bytes(target, b"jamais")
    assert target.read_bytes() == b"apres"
    assert list(tmp_path.iterdir()) == [target]


class TestProcesses:
    def test_run_process_captures_output(self) -> None:
        result = run_process([sys.executable, "-c", "print('bonjour')"])
        assert result.stdout_text.strip() == "bonjour"

    def test_missing_tool_is_reported(self) -> None:
        with pytest.raises(ExternalToolError, match="introuvable"):
            run_process(["definitely-not-a-real-tool-xyz"])

    def test_non_zero_exit_includes_stderr(self) -> None:
        with pytest.raises(ExternalToolError, match="boom"):
            run_process([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])

    def test_cancellation_kills_the_process(self) -> None:
        token = CancelToken()
        token.cancel("stop")
        with pytest.raises(CancelledError):
            run_process([sys.executable, "-c", "import time; time.sleep(30)"], cancel=token)

    def test_timeout(self) -> None:
        with pytest.raises(ExternalToolError, match="n'a pas terminé"):
            run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.5)


class TestSettings:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [("127.0.0.1", True), ("localhost", True), ("::1", True), ("0.0.0.0", False)],  # noqa: S104
    )
    def test_is_loopback(self, host: str, expected: bool) -> None:
        assert is_loopback(host) is expected

    def test_token_required_off_loopback(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="VFE_API_TOKEN"):
            Settings(host="0.0.0.0", data_dir=tmp_path)  # noqa: S104

    def test_derived_paths(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path)
        assert settings.db_path.parent == tmp_path
        assert settings.base_url == "http://127.0.0.1:8765"


def test_browser_disconnects_are_not_logged_as_errors() -> None:
    from vfe_vision.core.logging import _PeerResetFilter

    def record(message: str, error: BaseException) -> logging.LogRecord:
        return logging.LogRecord(
            "asyncio", logging.ERROR, __file__, 1, message, None, (type(error), error, None)
        )

    lost = "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)"
    assert not _PeerResetFilter().filter(record(lost, ConnectionResetError(10054, "reset")))
    assert _PeerResetFilter().filter(record(lost, OSError("disk full")))
    assert _PeerResetFilter().filter(record("Task exception", ConnectionResetError()))
