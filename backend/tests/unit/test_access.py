"""Remote access: settings, token file, Tailscale discovery, listening, sessions."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from vfe_vision.adapters.tailscale import TailscaleSelf, in_tailnet, parse_status
from vfe_vision.api.access import (
    SESSION_TTL_S,
    AccessConfig,
    FailedAttempts,
    is_local_request,
    issue_session,
    verify_session,
)
from vfe_vision.api.listen import (
    Listeners,
    ListenPlan,
    access_config,
    open_listeners,
    plan_listen,
)
from vfe_vision.cli import app
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import ExternalToolError
from vfe_vision.core.tokens import current_token, read_token_file

TAILSCALE_IP = "100.101.102.103"
STATUS = {
    "BackendState": "Running",
    "Self": {
        "DNSName": "machine.tail1234.ts.net.",
        "HostName": "MACHINE",
        "TailscaleIPs": [TAILSCALE_IP, "fd7a:115c:a1e0::1"],
    },
    "CurrentTailnet": {"MagicDNSSuffix": "tail1234.ts.net", "MagicDNSEnabled": True},
}


class TestSettings:
    def test_extra_hosts_from_a_comma_separated_variable(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path, extra_hosts="100.64.0.1, 100.64.0.2;[::1]")  # type: ignore[arg-type]
        assert settings.extra_hosts == ["100.64.0.1", "100.64.0.2", "::1"]
        assert settings.remote_requested

    def test_extra_hosts_from_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VFE_EXTRA_HOSTS", "100.64.12.34")
        monkeypatch.setenv("VFE_TAILSCALE", "1")
        settings = Settings(data_dir=tmp_path)
        assert settings.extra_hosts == ["100.64.12.34"]
        assert settings.tailscale

    @pytest.mark.parametrize("bad", ["0.0.0.0", "::", "machine.example", "224.0.0.1"])  # noqa: S104
    def test_never_a_whole_network_nor_a_name(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(ValidationError):
            Settings(data_dir=tmp_path, extra_hosts=[bad])

    def test_loopback_only_by_default(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path)
        assert settings.extra_hosts == []
        assert not settings.tailscale
        assert not settings.remote_requested
        assert settings.api_token_path == tmp_path / "api-token.txt"


class TestToken:
    def test_created_once_then_kept(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path / "data")
        assert current_token(settings, create=False) == (None, None)
        token, source = current_token(settings, create=True)
        assert source == "file"
        assert token is not None
        assert len(token) >= 40
        assert current_token(settings, create=True) == (token, "file")
        assert read_token_file(settings.api_token_path) == token

    def test_the_environment_wins(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path, api_token="from-env")  # type: ignore[arg-type]  # noqa: S106
        assert current_token(settings, create=True) == ("from-env", "env")
        assert not settings.api_token_path.exists()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL")
    def test_only_the_user_may_read_it(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path)
        current_token(settings, create=True)
        acl = subprocess.run(
            ["icacls", str(settings.api_token_path)], capture_output=True, text=True, check=True
        ).stdout
        assert os.environ["USERNAME"].lower() in acl.lower()
        assert "Administrators" not in acl
        assert "(I)" not in acl  # nothing inherited

    def test_cli_shows_then_rotates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from vfe_vision.core.config import get_settings

        monkeypatch.setenv("VFE_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("VFE_API_TOKEN", raising=False)
        get_settings.cache_clear()
        try:
            runner = CliRunner()
            first = runner.invoke(app, ["token"])
            assert first.exit_code == 0, first.output
            token = first.stdout.strip()
            assert token == read_token_file(tmp_path / "api-token.txt")
            assert runner.invoke(app, ["token"]).stdout.strip() == token
            rotated = runner.invoke(app, ["token", "--rotate"])
            assert rotated.exit_code == 0
            assert rotated.stdout.strip() not in {"", token}
            assert "Redémarrez" in rotated.stderr
        finally:
            get_settings.cache_clear()


class TestTailscale:
    def test_status_of_a_running_machine(self) -> None:
        me = parse_status(STATUS)
        assert me.running
        assert me.ipv4 == (TAILSCALE_IP,)
        assert me.names == ("machine.tail1234.ts.net", "machine")

    def test_stopped_or_without_magic_dns(self) -> None:
        stopped = parse_status({**STATUS, "BackendState": "Stopped"})
        assert not stopped.running
        assert stopped.state == "Stopped"
        no_dns = parse_status({**STATUS, "CurrentTailnet": {"MagicDNSEnabled": False}})
        assert no_dns.names == ()

    def test_tailnet_ranges(self) -> None:
        assert in_tailnet("100.64.12.34")
        assert in_tailnet("fd7a:115c:a1e0::b133:641f")
        assert not in_tailnet("192.168.1.10")
        assert not in_tailnet("machine")


def _running(_cli: str) -> TailscaleSelf:
    return parse_status(STATUS)


class TestListenPlan:
    def test_tailscale_adds_its_address_and_names(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path, tailscale=True)
        plan = plan_listen(settings, find_cli=lambda _: "tailscale", read_self=_running)
        assert plan == ListenPlan(
            "127.0.0.1", (TAILSCALE_IP,), ("machine.tail1234.ts.net", "machine"), ()
        )

    def test_tailscale_down_leaves_the_loopback_only(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path, tailscale=True)

        def stopped(_cli: str) -> TailscaleSelf:
            return parse_status({**STATUS, "BackendState": "Stopped"})

        plan = plan_listen(settings, find_cli=lambda _: "tailscale", read_self=stopped)
        assert plan.extra == ()
        assert "état : Stopped" in plan.notices[0]

        def failing(_cli: str) -> TailscaleSelf:
            raise ExternalToolError("Tailscale ne répond pas : service arrêté", tool="tailscale")

        plan = plan_listen(settings, find_cli=lambda _: "tailscale", read_self=failing)
        assert plan.extra == ()
        assert plan.notices[0].startswith("Tailscale ne répond pas : service arrêté :")
        missing = plan_listen(settings, find_cli=lambda _: None, read_self=_running)
        assert "introuvable" in missing.notices[0]

    def test_explicit_address_gets_the_names_when_tailscale_answers(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path, extra_hosts=[TAILSCALE_IP])
        plan = plan_listen(settings, find_cli=lambda _: "tailscale", read_self=_running)
        assert plan.extra == (TAILSCALE_IP,)
        assert plan.dns_names == ("machine.tail1234.ts.net", "machine")
        quiet = plan_listen(settings, find_cli=lambda _: None, read_self=_running)
        assert quiet == ListenPlan("127.0.0.1", (TAILSCALE_IP,), (), ())  # no notice asked


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


class TestListeners:
    def test_an_absent_address_is_skipped_with_a_notice(self, tmp_path: Path) -> None:
        port = _free_port()
        plan = ListenPlan("127.0.0.1", ("192.0.2.1",))  # TEST-NET-1: never on this machine
        listeners = open_listeners(plan, port)
        try:
            assert len(listeners.sockets) == 1
            assert listeners.bound == []
            assert "192.0.2.1" in listeners.notices[0]
            assert "absente" in listeners.notices[0]
            settings = Settings(data_dir=tmp_path, port=port, extra_hosts=["192.0.2.1"])
            access = access_config(settings, plan, listeners)
            assert not access.remote_enabled
            assert access.token is not None  # asked for: created, ready for the next start
            assert access.notices == tuple(listeners.notices)
        finally:
            listeners.close()

    def test_a_taken_port_stops_the_start(self) -> None:
        port = _free_port()
        first = open_listeners(ListenPlan("127.0.0.1"), port)
        try:
            first.sockets[0].listen()
            with pytest.raises(OSError):  # noqa: PT011 - localised Windows message
                open_listeners(ListenPlan("127.0.0.1"), port)
        finally:
            first.close()

    def test_names_follow_a_bound_tailnet_address(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path, tailscale=True)
        plan = ListenPlan("127.0.0.1", (TAILSCALE_IP,), ("machine.tail1234.ts.net",))
        bound = access_config(settings, plan, _fake_listeners([TAILSCALE_IP]))
        assert bound.remote_hosts == (TAILSCALE_IP,)
        assert bound.dns_names == ("machine.tail1234.ts.net",)
        assert bound.token_source == "file"  # noqa: S105
        unbound = access_config(settings, plan, _fake_listeners([]))
        assert unbound.dns_names == ()


def _fake_listeners(bound: list[str]) -> Listeners:
    return Listeners([], bound=bound)


class TestAccessConfig:
    def test_hosts_origins_and_urls(self) -> None:
        config = AccessConfig(
            port=8765, token="t", remote_hosts=(TAILSCALE_IP, "fd7a:115c:a1e0::1"),  # noqa: S106
            dns_names=("machine.tail1234.ts.net", "machine"),
        )  # fmt: skip
        assert config.trusted_hosts() == sorted(
            {"127.0.0.1", "localhost", "[::1]", TAILSCALE_IP, "[fd7a:115c:a1e0::1]",
             "machine.tail1234.ts.net", "machine"}
        )  # fmt: skip
        assert config.remote_urls() == [
            f"http://{TAILSCALE_IP}:8765",
            "http://[fd7a:115c:a1e0::1]:8765",
            "http://machine.tail1234.ts.net:8765",
            "http://machine:8765",
        ]
        assert "https://machine.tail1234.ts.net" in config.remote_origins()  # tailscale serve
        assert "https://machine" not in config.remote_origins()

    def test_a_named_host_is_kept_as_it_is(self) -> None:
        config = AccessConfig(port=8765, remote_hosts=("0.0.0.0", "atelier.local"))  # noqa: S104
        assert config.remote_urls() == ["http://atelier.local:8765"]

    def test_local_by_default(self, tmp_path: Path) -> None:
        config = AccessConfig.local(Settings(data_dir=tmp_path))
        assert (config.token, config.remote_enabled, config.remote_urls()) == (None, False, [])


def _scope(client: str, host: str) -> dict[str, object]:
    return {"type": "http", "client": (client, 50000), "headers": [(b"host", host.encode())]}


@pytest.mark.parametrize(
    ("client", "host", "local"),
    [
        ("127.0.0.1", "127.0.0.1:8765", True),
        ("127.0.0.1", "localhost:5173", True),
        ("::1", "[::1]:8765", True),
        ("127.0.0.1", "machine.tail1234.ts.net", False),  # forwarded by a local proxy
        ("127.0.0.1", f"{TAILSCALE_IP}:8765", False),
        (TAILSCALE_IP, f"{TAILSCALE_IP}:8765", False),
        ("100.64.0.9", "127.0.0.1:8765", False),
        ("testclient", "127.0.0.1:8765", False),
    ],
)
def test_local_request(client: str, host: str, local: bool) -> None:
    assert is_local_request(_scope(client, host)) is local


class TestSessions:
    def test_signed_with_the_token(self) -> None:
        value = issue_session("token-a", now=1000.0)
        assert verify_session("token-a", value, now=1001.0)
        assert not verify_session("token-b", value, now=1001.0)  # token changed: ended
        assert not verify_session("token-a", value, now=1000.0 + SESSION_TTL_S + 1)
        expires, _, signature = value.partition(".")
        assert not verify_session("token-a", f"{int(expires) + 9999}.{signature}", now=1001.0)
        assert not verify_session("token-a", "garbage", now=1001.0)
        assert not verify_session("token-a", "123.é", now=1001.0)


class TestFailedAttempts:
    def test_locks_out_then_forgives(self) -> None:
        now = [0.0]
        attempts = FailedAttempts(limit=3, window_s=60, lockout_s=30, clock=lambda: now[0])
        for _ in range(2):
            attempts.failure("100.64.0.9")
        assert attempts.blocked_for("100.64.0.9") == 0
        attempts.failure("100.64.0.9")
        assert attempts.blocked_for("100.64.0.9") == 30
        assert attempts.blocked_for("100.64.0.10") == 0  # per client address
        now[0] = 31
        assert attempts.blocked_for("100.64.0.9") == 0

    def test_old_failures_and_successes_are_forgotten(self) -> None:
        now = [0.0]
        attempts = FailedAttempts(limit=2, window_s=10, lockout_s=30, clock=lambda: now[0])
        attempts.failure("a")
        now[0] = 11
        attempts.failure("a")
        assert attempts.blocked_for("a") == 0
        attempts.success("a")
        attempts.failure("a")
        assert attempts.blocked_for("a") == 0
