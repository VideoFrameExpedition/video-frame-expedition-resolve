"""Access from another device over Tailscale: the client address of the ASGI scope
tells the loopback from a tailnet device."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send

from vfe_vision.api import security
from vfe_vision.api.access import SESSION_COOKIE, AccessConfig, issue_session
from vfe_vision.api.app import create_app
from vfe_vision.api.routers import access as access_routes
from vfe_vision.core.config import Settings

TOKEN = "jeton-de-test-0123456789"  # noqa: S105 - a test token
IP = "100.101.102.103"  # this machine on the tailnet
NAME = "machine.tail1234.ts.net"
LOCAL = "http://127.0.0.1:8765"
REMOTE = f"http://{IP}:8765"
PHONE = ("100.64.0.9", 50000)  # another device of the tailnet
WRITE = {"X-VFE-Client": "web", "Origin": REMOTE}
MCP_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "tests", "version": "1"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "FAILURE_DELAY_S", 0)
    monkeypatch.setattr(access_routes, "FAILURE_DELAY_S", 0)


@pytest.fixture
def access(tmp_path: Path) -> AccessConfig:
    return AccessConfig(
        port=8765, token=TOKEN, token_source="file", token_path=tmp_path / "api-token.txt",  # noqa: S106
        remote_hosts=(IP,), dns_names=(NAME, "machine"), remote_requested=True,
    )  # fmt: skip


def with_client_address(app: ASGIApp) -> ASGIApp:
    """Tests only: ``x-test-client`` sets the ASGI scope's client address (one app, one
    lifespan, seen from several devices)."""

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            forced = dict(scope["headers"]).get(b"x-test-client")
            if forced is not None:
                scope = {**scope, "client": (forced.decode(), 50000)}
        await app(scope, receive, send)

    return wrapped


class Device:
    """Requests to the app from one address, towards one base URL (cookies are shared by
    the underlying client, per host, as in a browser)."""

    def __init__(self, client: TestClient, base: str, address: str) -> None:
        self.client = client
        self.base = base
        self.address = address

    @property
    def cookies(self) -> httpx2.Cookies:
        return self.client.cookies

    def get(self, path: str, **kwargs: Any) -> httpx2.Response:
        return self.client.get(self.base + path, headers=self._headers(kwargs), **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx2.Response:
        return self.client.post(self.base + path, headers=self._headers(kwargs), **kwargs)

    def _headers(self, kwargs: dict[str, Any]) -> dict[str, str]:
        return {"x-test-client": self.address, **kwargs.pop("headers", {})}


def _app(settings: Settings, access: AccessConfig) -> ASGIApp:
    return with_client_address(create_app(settings, start_worker=False, access=access))


@pytest.fixture
def client(settings: Settings, access: AccessConfig) -> Iterator[TestClient]:
    with TestClient(_app(settings, access), base_url=LOCAL) as test_client:
        yield test_client


@pytest.fixture
def local(client: TestClient) -> Device:
    return Device(client, LOCAL, "127.0.0.1")


@pytest.fixture
def remote(client: TestClient) -> Device:
    return Device(client, REMOTE, PHONE[0])


def _bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestToken:
    def test_this_computer_needs_no_token(self, local: Device) -> None:
        assert local.get("/api/v1/system/health").status_code == 200
        session = local.get("/api/v1/access/session").json()
        assert session == {"local": True, "required": False, "authenticated": True, "remote": True}

    def test_another_device_needs_it(self, remote: Device) -> None:
        refused = remote.get("/api/v1/system/health")
        assert refused.status_code == 401
        assert refused.json()["code"] == "unauthorized"
        assert remote.get("/api/v1/system/health", headers=_bearer("faux")).status_code == 401
        assert remote.get("/api/v1/system/health", headers=_bearer()).status_code == 200
        # <video>, <img> and EventSource cannot send a header: the query parameter.
        assert remote.get(f"/api/v1/videos?access_token={TOKEN}").status_code == 200
        assert remote.get("/api/v1/videos?access_token=faux").status_code == 401

    def test_a_request_forwarded_by_a_local_proxy_is_not_local(self, client: TestClient) -> None:
        proxied = Device(client, f"https://{NAME}", "127.0.0.1")  # tailscale serve, say
        assert proxied.get("/api/v1/system/health").status_code == 401
        assert proxied.get("/api/v1/system/health", headers=_bearer()).status_code == 200

    def test_hosts_of_the_tailnet_are_accepted_others_not(self, remote: Device) -> None:
        for host in (f"{IP}:8765", f"{NAME}:8765", "machine:8765"):
            answer = remote.get("/api/v1/system/health", headers={**_bearer(), "Host": host})
            assert answer.status_code == 200, host
        evil = remote.get("/api/v1/system/health", headers={**_bearer(), "Host": "evil.example"})
        assert evil.status_code == 400

    def test_the_spa_files_are_public(self, remote: Device) -> None:
        assert remote.get("/").status_code == 200  # the login page must load

    def test_wrong_tokens_are_locked_out(self, remote: Device) -> None:
        for _ in range(10):
            assert remote.get("/api/v1/jobs", headers=_bearer("faux")).status_code == 401
        blocked = remote.get("/api/v1/jobs", headers=_bearer())
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) > 0
        assert remote.post("/api/v1/access/login", json={"token": TOKEN},
                           headers=WRITE).status_code == 429  # fmt: skip


class TestLogin:
    def test_the_open_session_endpoint_checks_the_cookie_only(self, remote: Device) -> None:
        # Open to all: a guessed Bearer token there must not be confirmed.
        answer = remote.get("/api/v1/access/session", headers=_bearer())
        assert answer.json()["authenticated"] is False

    def test_login_sets_a_strict_http_only_cookie(self, remote: Device) -> None:
        before = remote.get("/api/v1/access/session").json()
        assert before == {"local": False, "required": True, "authenticated": False, "remote": True}
        wrong = remote.post("/api/v1/access/login", json={"token": "faux"}, headers=WRITE)
        assert wrong.status_code == 401
        assert SESSION_COOKIE not in remote.cookies
        ok = remote.post("/api/v1/access/login", json={"token": f" {TOKEN} "}, headers=WRITE)
        assert ok.status_code == 204
        cookie = ok.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie
        assert "Secure" not in cookie  # plain HTTP inside the tailnet
        assert "Max-Age=2592000" in cookie
        assert remote.get("/api/v1/access/session").json()["authenticated"] is True
        assert remote.get("/api/v1/system/health").status_code == 200
        # Media and events go through the same check (cookie sent by <video>/EventSource).
        assert remote.get("/api/v1/media/absent.jpg").status_code == 404

    def test_login_needs_the_client_header_and_a_good_origin(self, remote: Device) -> None:
        body = {"token": TOKEN}
        assert remote.post("/api/v1/access/login", json=body).status_code == 403
        evil = {"X-VFE-Client": "web", "Origin": "https://evil.example"}
        assert remote.post("/api/v1/access/login", json=body, headers=evil).status_code == 403

    def test_logout_clears_the_cookie(self, remote: Device) -> None:
        remote.post("/api/v1/access/login", json={"token": TOKEN}, headers=WRITE)
        out = remote.post("/api/v1/access/logout", headers=WRITE)
        assert out.status_code == 204
        assert f'{SESSION_COOKIE}=""' in out.headers["set-cookie"]
        assert "Max-Age=0" in out.headers["set-cookie"]
        assert remote.get("/api/v1/system/health").status_code == 401

    def test_a_cookie_of_an_old_token_is_cleared_not_counted(self, remote: Device) -> None:
        for _ in range(12):
            remote.cookies.set(SESSION_COOKIE, issue_session("ancien-jeton", 4e9), domain=IP)
            stale = remote.get("/api/v1/system/health")
            assert stale.status_code == 401  # never 429: not a guess
            assert "Max-Age=0" in stale.headers["set-cookie"]
            assert "Session expirée" in stale.json()["detail"]


class TestCsrfWithCookie:
    @pytest.fixture
    def logged_in(self, remote: Device) -> Device:
        assert remote.post("/api/v1/access/login", json={"token": TOKEN},
                           headers=WRITE).status_code == 204  # fmt: skip
        return remote

    def test_writes_still_need_the_header_and_the_origin(self, logged_in: Device) -> None:
        body = {"path": "Z:/absent"}
        no_header = logged_in.post("/api/v1/library/roots", json=body, headers={"Origin": REMOTE})
        assert no_header.json()["code"] == "missing_client_header"
        no_origin = logged_in.post("/api/v1/library/roots", json=body,
                                   headers={"X-VFE-Client": "web"})  # fmt: skip
        assert no_origin.json()["code"] == "missing_origin"
        evil = logged_in.post("/api/v1/library/roots", json=body,
                              headers={"X-VFE-Client": "web", "Origin": "http://evil.example"})  # fmt: skip
        assert evil.json()["code"] == "forbidden_origin"
        for origin in (REMOTE, f"http://{NAME}:8765", f"https://{NAME}"):
            passed = logged_in.post("/api/v1/library/roots", json=body,
                                    headers={"X-VFE-Client": "web", "Origin": origin})  # fmt: skip
            assert passed.status_code not in {401, 403}, (origin, passed.text)

    def test_bearer_writes_keep_the_old_rules(self, remote: Device) -> None:
        answer = remote.post("/api/v1/library/roots", json={"path": "Z:/absent"},
                             headers={**_bearer(), "X-VFE-Client": "cli"})  # fmt: skip
        assert answer.status_code not in {401, 403}


class TestMcp:
    def test_loopback_mcp_is_unchanged(self, local: Device) -> None:
        answer = local.post("/mcp", json=MCP_INIT, headers=MCP_HEADERS)
        assert answer.status_code == 200
        assert "vfe-vision" in answer.text

    def test_remote_mcp_needs_the_bearer_header(self, remote: Device) -> None:
        refused = remote.post("/mcp", json=MCP_INIT, headers=MCP_HEADERS)
        assert refused.status_code == 401
        assert refused.headers["www-authenticate"].startswith("Bearer")
        remote.post("/api/v1/access/login", json={"token": TOKEN}, headers=WRITE)
        assert remote.post("/mcp", json=MCP_INIT, headers=MCP_HEADERS).status_code == 401
        wrong = remote.post("/mcp", json=MCP_INIT, headers={**MCP_HEADERS, **_bearer("faux")})
        assert 'error="invalid_token"' in wrong.headers["www-authenticate"]
        for host in (f"{IP}:8765", f"{NAME}:8765"):
            ok = remote.post("/mcp", json=MCP_INIT,
                             headers={**MCP_HEADERS, **_bearer(), "Host": host})  # fmt: skip
            assert ok.status_code == 200, (host, ok.text)
        foreign = remote.post("/mcp", json=MCP_INIT, headers={
            **MCP_HEADERS, **_bearer(), "Origin": "https://evil.example"})  # fmt: skip
        assert foreign.status_code == 403


class TestConnections:
    def test_the_token_is_shown_on_this_computer_only(self, local: Device,
                                                       remote: Device) -> None:  # fmt: skip
        here = local.get("/api/v1/access/connections").json()
        assert here["viewer_local"] is True
        assert here["local_url"] == LOCAL
        assert here["token"]["value"] == TOKEN
        assert here["token"]["path"].endswith("api-token.txt")
        assert here["remote"]["enabled"] is True
        assert here["remote"]["urls"][:2] == [REMOTE, f"http://{NAME}:8765"]
        assert here["stdio"]["args"][-3:] == ["-m", "vfe_vision", "mcp-stdio"]
        if sys.platform == "win32":
            assert here["platform"] == "windows"
            assert {c["kind"] for c in here["claude_desktop"]} == {"classic", "store"}
            assert here["resolve_mcp"]["path"].endswith("ResolveMCP.exe")
        else:
            assert here["platform"] == ("macos" if sys.platform == "darwin" else "linux")
            assert [c["kind"] for c in here["claude_desktop"]] == ["classic"]
            assert here["resolve_mcp"]["path"].endswith("ResolveMCP")

        there = remote.get("/api/v1/access/connections", headers=_bearer()).json()
        assert there["viewer_local"] is False
        assert there["token"] == {"available": True, "source": "file", "value": None, "path": None}
        assert remote.get("/api/v1/access/connections").status_code == 401


def test_without_remote_access_nothing_changes(settings: Settings) -> None:
    """Loopback only (the default): no token, the TestClient's own address is fine."""
    with TestClient(create_app(settings, start_worker=False), base_url=LOCAL) as client:
        assert client.get("/api/v1/system/health").status_code == 200
        info = client.get("/api/v1/access/connections").json()
        assert info["remote"] == {"requested": False, "enabled": False, "urls": [], "notices": []}
        assert info["token"]["available"] is False
