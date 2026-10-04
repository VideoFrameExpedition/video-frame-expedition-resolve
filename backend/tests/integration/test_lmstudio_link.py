"""Where LM Studio runs: the address chosen in the interface is followed by the
clients without a restart, tested before use, remembered, and never changed under an analysis."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.conftest import FakeLmStudio
from vfe_vision.adapters.lmstudio.client import LmStudioClient
from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings
from vfe_vision.db.lmstudio_link import LinkReader, load_link
from vfe_vision.db.models import Job
from vfe_vision.domain.enums import JobKind, JobStatus
from vfe_vision.domain.lmstudio_link import LmStudioTarget
from vfe_vision.jobs.worker import Worker
from vfe_vision.services.container import AppContainer

HEADERS = {"X-VFE-Client": "tests"}
LINK = "/api/v1/settings/lmstudio"
OTHER = "http://192.168.1.20:1234"


class Network:
    """Every LM Studio of the tests: what was asked to which address, with which token."""

    def __init__(self) -> None:
        self.fake = FakeLmStudio()
        self.asked: list[tuple[str, str | None]] = []
        self.off: set[str] = set()  # computers that do not answer
        self.locked: dict[str, str] = {}  # host: the token it asks for

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        token = request.headers.get("Authorization")
        self.asked.append((f"{request.url.scheme}://{request.url.netloc.decode()}", token))
        if host in self.off:
            raise httpx.ConnectError("connection refused", request=request)
        if host in self.locked and token != f"Bearer {self.locked[host]}":
            return httpx.Response(401, json={"error": "unauthorized"})
        return self.fake.handler(request)

    def client(self, target: object) -> LmStudioClient:
        if isinstance(target, LmStudioTarget):
            return LmStudioClient(
                target.url, token=target.token, transport=httpx.MockTransport(self.handler)
            )
        assert isinstance(target, LinkReader)
        return LmStudioClient(target, transport=httpx.MockTransport(self.handler))


@pytest.fixture
def network() -> Network:
    return Network()


@pytest.fixture
def client(settings: Settings, network: Network) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        container: AppContainer = test_client.app.state.container  # type: ignore[attr-defined]
        container.lmstudio = network.client(container.lmstudio_link)
        container.lmstudio_probe = network.client
        yield test_client


def _container(client: TestClient) -> AppContainer:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    return container


def test_the_address_is_chosen_followed_and_remembered(
    client: TestClient, network: Network
) -> None:
    link = client.get(LINK).json()
    assert link == {
        "url": "http://lmstudio.test",
        "custom": False,
        "default_url": "http://lmstudio.test",
        "local": False,
        "has_token": False,
        "past": [],
    }

    # Tried first: nothing changes.
    tried = client.post(f"{LINK}/test", json={"address": "192.168.1.20"}, headers=HEADERS).json()
    assert tried["url"] == OTHER
    assert tried["ok"] is True
    assert tried["local"] is False
    assert tried["models"] > 0
    assert tried["vision_models"] > 0
    assert tried["loaded"]
    assert client.get(LINK).json()["custom"] is False
    network.off.add("192.168.1.99")
    absent = client.post(f"{LINK}/test", json={"address": "192.168.1.99"}, headers=HEADERS).json()
    assert absent["ok"] is False
    assert "Serve on Local Network" in absent["error"]

    # Chosen: the client of the application talks to it at once.
    chosen = client.put(LINK, json={"address": "192.168.1.20"}, headers=HEADERS).json()
    assert chosen["url"] == OTHER
    assert chosen["custom"] is True
    assert [past["url"] for past in chosen["past"]] == [OTHER]
    network.asked.clear()
    assert client.get("/api/v1/system/lmstudio/models").status_code == 200
    assert network.asked == [(OTHER, None)]
    checks = {check["id"]: check for check in client.get("/api/v1/system/doctor").json()["checks"]}
    assert OTHER in checks["lmstudio_remote"]["detail"]

    # Another one, then back to this computer: both stay in the past connections.
    client.put(LINK, json={"address": "pc-salon:5000"}, headers=HEADERS)
    back = client.put(LINK, json={"address": None}, headers=HEADERS).json()
    assert back["url"] == "http://lmstudio.test"
    assert back["custom"] is False
    assert [past["url"] for past in back["past"]] == ["http://pc-salon:5000", OTHER]
    network.asked.clear()
    client.get("/api/v1/system/lmstudio/models")
    assert network.asked == [("http://lmstudio.test", None)]

    # Forgotten on request.
    left = client.delete(f"{LINK}/past", params={"url": OTHER}, headers=HEADERS).json()
    assert [past["url"] for past in left["past"]] == ["http://pc-salon:5000"]
    assert client.delete(f"{LINK}/past", params={"url": OTHER}, headers=HEADERS).status_code == 404


def test_this_computer_typed_by_hand_is_not_another_one(
    settings: Settings, network: Network
) -> None:
    settings.lmstudio_url = "http://127.0.0.1:1234"
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        same = client.put(LINK, json={"address": "127.0.0.1"}, headers=HEADERS).json()
        assert same == {
            "url": "http://127.0.0.1:1234",
            "custom": False,
            "default_url": "http://127.0.0.1:1234",
            "local": True,
            "has_token": False,
            "past": [],
        }
        # Another port of this computer is a choice, and still this computer.
        other = client.put(LINK, json={"address": "localhost:5000"}, headers=HEADERS).json()
        assert [other["url"], other["custom"], other["local"]] == [
            "http://localhost:5000",
            True,
            True,
        ]


def test_what_is_not_an_address_is_refused(client: TestClient) -> None:
    for wrong in ("192.168.1.20/admin", "ftp://192.168.1.20", "mon pc"):
        refused = client.put(LINK, json={"address": wrong}, headers=HEADERS)
        assert refused.status_code == 422, wrong
        tried = client.post(f"{LINK}/test", json={"address": wrong}, headers=HEADERS)
        assert tried.status_code == 422, wrong
    assert client.get(LINK).json()["past"] == []
    assert client.put(LINK, json={"address": "192.168.1.20"}).status_code == 403  # no header


def test_a_token_goes_with_its_address_and_is_never_shown(
    settings: Settings, network: Network
) -> None:
    settings.lmstudio_token = SecretStr("installation")
    network.locked["192.168.1.20"] = "theirs"
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        container = _container(client)
        container.lmstudio = network.client(container.lmstudio_link)
        container.lmstudio_probe = network.client

        # Without its token, the other LM Studio refuses: said in plain words.
        refused = client.post(f"{LINK}/test", json={"address": OTHER}, headers=HEADERS).json()
        assert refused["ok"] is False
        assert "jeton" in refused["error"]
        assert network.asked[-1] == (OTHER, None)  # the installation's token did not go there

        typed = {"address": OTHER, "token": " theirs "}
        assert client.post(f"{LINK}/test", json=typed, headers=HEADERS).json()["ok"] is True
        chosen = client.put(LINK, json=typed, headers=HEADERS)
        assert chosen.json()["has_token"] is True
        assert chosen.json()["past"][0]["has_token"] is True
        assert "theirs" not in chosen.text
        assert "theirs" not in client.get(LINK).text
        network.asked.clear()
        assert client.get("/api/v1/system/lmstudio/models").status_code == 200
        assert network.asked == [(OTHER, "Bearer theirs")]

        # Back to this computer: its own token, and the other one kept for the next time.
        client.put(LINK, json={"address": ""}, headers=HEADERS)
        network.asked.clear()
        client.get("/api/v1/system/lmstudio/models")
        assert network.asked == [("http://lmstudio.test", "Bearer installation")]
        again = client.put(LINK, json={"address": OTHER}, headers=HEADERS).json()
        assert again["has_token"] is True
        # An empty token removes it.
        removed = client.put(LINK, json={"address": OTHER, "token": ""}, headers=HEADERS).json()
        assert removed["has_token"] is False
        assert load_link(container.db).past[0].token is None


def test_the_address_does_not_change_under_an_analysis(client: TestClient) -> None:
    container = _container(client)
    with container.db.write() as session:
        job = Job(kind=JobKind.BENCH_MODELS, status=JobStatus.RUNNING)
        session.add(job)
        session.flush()
        job_id = job.id
    refused = client.put(LINK, json={"address": OTHER}, headers=HEADERS)
    assert refused.status_code == 409
    assert "en cours" in refused.json()["detail"]
    assert client.get(LINK).json()["custom"] is False
    # Saving what is already in use changes nothing: allowed.
    assert client.put(LINK, json={"address": None}, headers=HEADERS).status_code == 200
    # A queued analysis has not started talking to LM Studio.
    with container.db.write() as session:
        waiting = session.get(Job, job_id)
        assert waiting is not None
        waiting.status = JobStatus.QUEUED
    assert client.put(LINK, json={"address": OTHER}, headers=HEADERS).status_code == 200
    # The address in use cannot be forgotten.
    assert client.delete(f"{LINK}/past", params={"url": OTHER}, headers=HEADERS).status_code == 409


def test_the_worker_follows_the_choice_without_a_restart(
    client: TestClient, settings: Settings
) -> None:
    container = _container(client)
    reader = LinkReader(container.db, settings.lmstudio_url)  # as the worker builds its own
    assert reader().url == "http://lmstudio.test"
    client.put(LINK, json={"address": OTHER}, headers=HEADERS)
    assert reader().url == "http://lmstudio.test"  # read again once a second at most
    reader.forget()
    assert reader() == LmStudioTarget(OTHER, None)
    # The model bench measures this computer's card: not for a model loaded elsewhere.
    worker = Worker(settings)
    assert worker._meter(OTHER) is None
    assert worker._meter("http://127.0.0.1:1234") is not None
