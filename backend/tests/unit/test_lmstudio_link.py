"""The address of LM Studio: what a user may type, and the past connections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vfe_vision.domain.lmstudio_link import (
    MAX_PAST,
    LmStudioLink,
    LmStudioTarget,
    PastConnection,
    address_of,
    is_local,
    remembered,
    target_of,
)

NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


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


def test_only_this_computer_is_local() -> None:
    assert is_local("http://127.0.0.1:1234")
    assert is_local("http://localhost:1234")
    assert is_local("http://[::1]:1234")
    assert not is_local("http://192.168.1.20:1234")
    assert not is_local("http://pc-salon:1234")


def test_a_token_only_goes_to_its_own_lm_studio() -> None:
    other = PastConnection(
        url="http://192.168.1.20:1234",
        last_used_at=NOW,
        token="theirs",  # noqa: S106
    )
    link = LmStudioLink(past=[other])
    # The installation's address, with the installation's token.
    assert target_of(link, "http://127.0.0.1:1234/", "mine") == LmStudioTarget(
        "http://127.0.0.1:1234", "mine"
    )
    link.url = other.url
    assert target_of(link, "http://127.0.0.1:1234", "mine") == LmStudioTarget(other.url, "theirs")
    link.url = "http://192.168.1.30:1234"  # never seen: no token at all
    assert target_of(link, "http://127.0.0.1:1234", "mine").token is None


def test_the_last_connection_comes_first_and_old_ones_go() -> None:
    past: list[PastConnection] = []
    for index in range(MAX_PAST + 2):
        past = remembered(
            past, f"http://192.168.1.{index}:1234", NOW + timedelta(hours=index), None
        )
    assert len(past) == MAX_PAST
    assert past[0].url == f"http://192.168.1.{MAX_PAST + 1}:1234"
    assert all(connection.url != "http://192.168.1.0:1234" for connection in past)
    # Used again: back to the top, once.
    again = remembered(past, past[3].url, NOW + timedelta(days=1), "token")
    assert [again[0].url, again[0].token] == [past[3].url, "token"]
    assert len(again) == MAX_PAST
    assert sum(connection.url == past[3].url for connection in again) == 1
