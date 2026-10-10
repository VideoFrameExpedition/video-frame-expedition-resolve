"""The address of the model server: what a user may type, and the past connections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vfe_vision.domain.lmstudio_link import (
    MAX_PAST,
    LmStudioLink,
    LmStudioTarget,
    PastConnection,
    ServerKind,
    address_of,
    is_local,
    remembered,
    target_of,
)

NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
HERE = LmStudioTarget("http://127.0.0.1:1234", "mine")


@pytest.mark.parametrize(
    ("typed", "full"),
    [
        ("192.168.1.20", "http://192.168.1.20:1234"),
        (" 192.168.1.20:5000 ", "http://192.168.1.20:5000"),
        ("http://100.64.12.34:1234/", "http://100.64.12.34:1234"),
        ("http://192.168.1.20:1234/v1", "http://192.168.1.20:1234"),  # as LM Studio shows it
        ("PC-Salon", "http://pc-salon:1234"),
        ("pc-salon.tail1234.ts.net:1234", "http://pc-salon.tail1234.ts.net:1234"),
        ("https://lm.example.org", "https://lm.example.org"),
        ("localhost", "http://localhost:1234"),
        ("[fd7a:115c:a1e0::1]:1234", "http://[fd7a:115c:a1e0::1]:1234"),
    ],
)
def test_an_address_is_completed(typed: str, full: str) -> None:
    assert address_of(typed) == full
    assert address_of(typed, ServerKind.LMSTUDIO) == full


@pytest.mark.parametrize(
    ("typed", "full"),
    [
        ("gpu-box:8000", "http://gpu-box:8000/v1"),  # no path: OpenAI's own
        ("http://gpu-box:8000/v1/", "http://gpu-box:8000/v1"),
        ("192.168.1.20", "http://192.168.1.20/v1"),  # no port imposed
        ("https://gpu-box.lan/vllm/v1", "https://gpu-box.lan/vllm/v1"),  # behind a proxy
        ("127.0.0.1:8000/openai/v1", "http://127.0.0.1:8000/openai/v1"),
    ],
)
def test_an_openai_compatible_address_keeps_its_path_and_port(typed: str, full: str) -> None:
    assert address_of(typed, ServerKind.OPENAI) == full


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "   ",
        "ftp://192.168.1.20",
        "192.168.1.20:port",
        "192.168.1.20:99999",
        "http://user:secret@192.168.1.20",
        "192.168.1.20/models/list",
        "192.168.1.20?x=1",
        "mon pc",
        "pc..salon",
        "0.0.0.0",  # noqa: S104 - an address typed by a user, refused
        "http://",
        "x" * 400,
    ],
)
def test_what_is_not_an_address_is_refused(typed: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        address_of(typed)


@pytest.mark.parametrize(
    "typed",
    ["gpu-box:8000/v1?key=1", "gpu-box:8000/v1#top", "gpu-box:8000/a b", "gpu-box:8000/%2e%2e"],
)
def test_an_openai_compatible_path_is_plain(typed: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        address_of(typed, ServerKind.OPENAI)


def test_only_this_computer_is_local() -> None:
    assert is_local("http://127.0.0.1:1234")
    assert is_local("http://localhost:1234")
    assert is_local("http://[::1]:1234")
    assert is_local("http://127.0.0.1:8000/v1")
    assert not is_local("http://192.168.1.20:1234")
    assert not is_local("http://pc-salon:1234")


def test_a_token_only_goes_to_its_own_server() -> None:
    other = PastConnection(
        url="http://192.168.1.20:1234",
        last_used_at=NOW,
        token="theirs",  # noqa: S106
    )
    link = LmStudioLink(past=[other])
    # The installation's address, with the installation's token.
    assert target_of(link, LmStudioTarget("http://127.0.0.1:1234/", "mine")) == HERE
    link.url = other.url
    assert target_of(link, HERE) == LmStudioTarget(other.url, "theirs")
    link.url = "http://192.168.1.30:1234"  # never seen: no token at all
    assert target_of(link, HERE).token is None


def test_a_past_server_comes_back_with_its_kind_and_settings() -> None:
    vllm = PastConnection(
        url="http://gpu-box:8000/v1",
        last_used_at=NOW,
        kind=ServerKind.OPENAI,
        parallel=8,
        vision=False,
    )
    link = LmStudioLink(url=vllm.url, past=[vllm])
    assert target_of(link, HERE) == LmStudioTarget(
        vllm.url, None, ServerKind.OPENAI, 8, vision=False
    )
    # The installation's server keeps the installation's settings.
    default = LmStudioTarget("http://127.0.0.1:8000/v1", None, ServerKind.OPENAI, 6)
    assert target_of(LmStudioLink(past=[vllm]), default) == default


def test_the_last_connection_comes_first_and_old_ones_go() -> None:
    past: list[PastConnection] = []
    for index in range(MAX_PAST + 2):
        past = remembered(
            past, LmStudioTarget(f"http://192.168.1.{index}:1234"), NOW + timedelta(hours=index)
        )
    assert len(past) == MAX_PAST
    assert past[0].url == f"http://192.168.1.{MAX_PAST + 1}:1234"
    assert all(connection.url != "http://192.168.1.0:1234" for connection in past)
    # Used again: back to the top, once, with what it is now.
    again = remembered(
        past,
        LmStudioTarget(past[3].url, "token", ServerKind.OPENAI, 6),
        NOW + timedelta(days=1),
    )
    assert [again[0].url, again[0].token, again[0].kind, again[0].parallel] == [
        past[3].url,
        "token",
        ServerKind.OPENAI,
        6,
    ]
    assert len(again) == MAX_PAST
    assert sum(connection.url == past[3].url for connection in again) == 1
