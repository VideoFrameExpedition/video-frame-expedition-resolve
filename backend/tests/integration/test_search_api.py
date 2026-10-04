"""Search over HTTP: the REST endpoints, and the MCP tools, in memory and through
a real server."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client

from tests.fakes.embeddings import FakeEmbedder
from tests.fakes.library import Library, build_library, stage_context
from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings
from vfe_vision.db.models import SearchChunk
from vfe_vision.db.session import Database
from vfe_vision.db.translations import store_entries, video_texts
from vfe_vision.mcp.server import build_mcp_server
from vfe_vision.pipeline.stages.search_index import IndexStage
from vfe_vision.services.container import AppContainer

HEADERS = {"X-VFE-Client": "tests"}


def _index(db: Database, library: Library, fake: FakeEmbedder) -> None:
    for video_id in (library.holiday, library.mountain):
        IndexStage().run(stage_context(db, video_id, lambda: fake))


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


@pytest.fixture
def library(client: TestClient, tmp_path: Path) -> Library:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    fake = FakeEmbedder()
    container.embedder = lambda: fake
    library = build_library(container.db, tmp_path / "Rushs")
    _index(container.db, library, fake)
    return library


# ---------------------------------------------------------------- REST
def test_search_endpoint(client: TestClient, library: Library) -> None:
    body = client.get("/api/v1/search", params={"q": "riz casserole"}).json()
    assert body["retrievers"] == ["words", "meaning"]
    assert body["index"] == {
        "videos": 2, "indexed": 2, "passages": body["index"]["passages"],
        "vectors": body["index"]["passages"], "semantic": True, "model": "fake/bag-of-stems@1",
    }  # fmt: skip
    top = body["hits"][0]
    assert top["video_id"] == library.holiday
    assert top["title"] == "Vacances à Hyères"  # the synthesis' when the user wrote none
    assert top["thumb_url"].startswith("/api/v1/media/")
    marked = [top["snippet"][s:e].lower() for s, e in top["highlights"]]
    assert "riz" in marked
    assert (body["limit"], body["offset"]) == (30, 0)


def test_filters_in_the_address(client: TestClient, library: Library) -> None:
    params: dict[str, Any] = {"weather": "snow", "kind": ["shot"], "shot_type": "medium"}
    body = client.get("/api/v1/search", params=params).json()
    assert [(h["video_id"], h["kind"], h["shot_idx"]) for h in body["hits"]] == [
        (library.mountain, "shot", 1)
    ]
    body = client.get(
        "/api/v1/search",
        params={"subject": ["chat"], "place": "hyères", "has_speech": "false", "min_rating": 4},
    ).json()
    assert {h["video_id"] for h in body["hits"]} == {library.holiday}
    folder = client.get(
        "/api/v1/search", params={"root_id": library.root_id, "folder": "Sommets"}
    ).json()
    assert {h["video_id"] for h in folder["hits"]} == {library.mountain}


@pytest.mark.parametrize(
    "params",
    [{"min_usability": 500}, {"weather": "sunny"}, {"limit": 0}, {"kind": "scene"},
     {"q": "x" * 501}, {"date_from": "hier"}],
)  # fmt: skip
def test_invalid_filters_are_refused(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get("/api/v1/search", params=params).status_code == 422


def test_facets_endpoint(client: TestClient, library: Library) -> None:
    body = client.get("/api/v1/search/facets").json()
    assert {f["value"] for f in body["devices"]} == {
        "samsung Galaxy S26 Ultra",
        "Apple iPhone 15 Pro",
    }
    assert body["places"][0] == {"value": "France", "count": 2}
    assert {"chat", "chien"} <= {f["value"] for f in body["subjects"]}
    assert (body["date_min"], body["date_max"]) == ("2025-12-24", "2026-07-01")
    assert body["index"]["indexed"] == 2


def test_a_new_title_is_searchable_at_once(client: TestClient, library: Library) -> None:
    response = client.patch(
        f"/api/v1/videos/{library.mountain}", json={"title": "Réveillon à la montagne"},
        headers=HEADERS,
    )  # fmt: skip
    assert response.status_code == 200
    hits = client.get("/api/v1/search", params={"q": "réveillon"}).json()["hits"]
    assert hits[0]["video_id"] == library.mountain
    assert hits[0]["kind"] == "video"


def test_an_empty_library_answers(client: TestClient) -> None:
    body = client.get("/api/v1/search", params={"q": "chat"}).json()
    assert body["hits"] == []
    assert body["index"]["indexed"] == 0
    facets = client.get("/api/v1/search/facets").json()
    assert facets["devices"] == []
    assert facets["date_min"] is None


# ---------------------------------------------------------------- MCP
@pytest.fixture
async def container(
    settings: Settings, db: Database, tmp_path: Path
) -> AsyncIterator[AppContainer]:
    c = AppContainer.create(settings)
    fake = FakeEmbedder()
    c.embedder = lambda: fake
    library = build_library(c.db, tmp_path / "Rushs")
    _index(c.db, library, fake)
    yield c
    await c.aclose()


@pytest.mark.anyio
async def test_search_memory_fences_the_passages(container: AppContainer) -> None:
    async with Client(build_mcp_server(lambda: container)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert tools["search_memory"].output_schema is not None
        assert tools["find_clips"].output_schema is not None
        result = await client.call_tool("search_memory", {"query": "riz", "limit": 3})
    data = result.structured_content
    assert data is not None
    assert data["index"] == {"library_videos": 2, "indexed_videos": 2, "by_meaning": True}
    first = data["passages"][0]
    assert first["filename"] == "vacances.mp4"
    assert first["path"].endswith("vacances.mp4")
    assert "riz" in first["text"].lower()
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith("Recherche « riz » : 3 passages sur")
    assert "(mots + sens)" in text
    assert "BEGIN UNTRUSTED VIDEO CONTENT" in text
    fenced = text.split("BEGIN UNTRUSTED VIDEO CONTENT", 1)[1]
    assert "[1] " in fenced  # the passages' words are inside the fence only
    listing = text.split("BEGIN UNTRUSTED VIDEO CONTENT", 1)[0]
    assert "casserole" not in listing


@pytest.mark.anyio
async def test_find_clips_for_resolve(container: AppContainer) -> None:
    async with Client(build_mcp_server(lambda: container)) as client:
        snowy = await client.call_tool("find_clips", {"weather": ["snow"], "shot_type": ["medium"]})
        boat = await client.call_tool("find_clips", {"text": "barque sur le lac", "limit": 1})
        nothing = await client.call_tool("find_clips", {"place": "Tombouctou"})
    data = snowy.structured_content
    assert data is not None
    [clip] = data["clips"]
    assert clip["filename"] == "montagne.mp4"
    assert (clip["shot"], clip["start_s"], clip["end_s"], clip["duration_s"]) == (
        2,
        20.0,
        40.0,
        20.0,
    )
    assert (clip["fps"], clip["in_frame"], clip["out_frame"]) == (25.0, 500, 1000)
    assert (clip["timecode_in"], clip["timecode_out"]) == ("00:00:20:00", "00:00:40:00")
    assert clip["weather"] == ["snow"]
    assert "chien" in clip["subjects"]
    assert "timecode_in" in data["convention"]
    text = snowy.content[0].text  # type: ignore[union-attr]
    assert "montagne.mp4 — plan 2 [00:20.000 → 00:40.000] 20,0 s" in text
    assert "BEGIN UNTRUSTED VIDEO CONTENT" in text
    top = boat.structured_content
    assert top is not None
    [found] = top["clips"]
    assert (found["filename"], found["shot"]) == ("vacances.mp4", 5)
    assert (found["timecode_in"], found["timecode_out"]) == ("10:01:20:00", "10:01:40:00")
    assert found["matched_by"]
    assert nothing.structured_content is not None
    assert nothing.structured_content["clips"] == []


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture
def server(settings: Settings, tmp_path: Path) -> Iterator[tuple[str, FastAPI]]:
    """The whole application on a real port (no worker), as Claude Code reaches it."""
    port = _free_port()
    live = settings.model_copy(update={"port": port})
    app = create_app(live, start_worker=False)
    runner = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not runner.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert runner.started
    try:
        yield f"http://127.0.0.1:{port}/mcp", app
    finally:
        runner.should_exit = True
        thread.join(timeout=10)


@pytest.mark.anyio
async def test_search_tools_over_real_http(server: tuple[str, FastAPI], tmp_path: Path) -> None:
    url, app = server
    container: AppContainer = app.state.container
    fake = FakeEmbedder()
    container.embedder = lambda: fake
    library = build_library(container.db, tmp_path / "Rushs")
    _index(container.db, library, fake)
    async with Client(url) as client:
        memory = await client.call_tool("search_memory", {"query": "neige", "kinds": ["shot"]})
        clips = await client.call_tool("find_clips", {"sun_phase": ["golden_hour"], "limit": 2})
    assert not memory.is_error
    passages = (memory.structured_content or {})["passages"]
    assert passages
    assert {p["video_id"] for p in passages} == {library.mountain}
    assert {p["kind"] for p in passages} == {"shot"}
    found = (clips.structured_content or {})["clips"]
    assert len(found) == 2
    assert {c["video_id"] for c in found} == {library.holiday}
    usable = [c["usability"] for c in found]
    assert usable == sorted(usable, reverse=True)  # without text: the most usable first


# ---------------------------------------------------------------- in French and in English
def test_the_passages_of_the_interface_language(client: TestClient, tmp_path: Path) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    fake = FakeEmbedder()
    container.embedder = lambda: fake
    library = build_library(container.db, tmp_path / "Rushs")
    with container.db.read() as session:
        texts = [source.text for source in video_texts(session, library.holiday)]
    with container.db.write() as session:  # what the translation stage leaves
        store_entries(session, [(text, "en", f"EN {text}") for text in texts], model="m")
    _index(container.db, library, fake)

    def hits(query: str, language: str) -> list[dict[str, Any]]:
        response = client.get(
            "/api/v1/search", params={"q": query}, headers={"X-VFE-Language": language}
        )
        found: list[dict[str, Any]] = response.json()["hits"]
        return found

    def languages(found: list[dict[str, Any]]) -> set[str]:
        ids = [hit["chunk_id"] for hit in found if hit["video_id"] == library.holiday]
        with container.db.read() as session:
            rows = session.execute(
                sa.select(SearchChunk.language, SearchChunk.kind).where(SearchChunk.id.in_(ids))
            ).all()
        return {str(language) for language, kind in rows if kind != "transcript"}

    english = hits("barque lac", "en")
    assert languages(english) == {"en"}
    assert next(h for h in english if h["video_id"] == library.holiday)["title"] == (
        "EN Vacances à Hyères"
    )
    french = hits("barque lac", "fr")
    assert languages(french) == {"fr"}
    # Each passage once: the French one in French, the English one in English.
    keys = [(h["video_id"], h["kind"], h["t_start"]) for h in french]
    assert len(keys) == len(set(keys))
    # The mountain was not translated: its passages are in both, with its French texts.
    assert any(hit["video_id"] == library.mountain for hit in hits("sommet enneigé", "en"))
    facets = client.get("/api/v1/search/facets", headers={"X-VFE-Language": "en"}).json()
    subjects = [item["value"] for item in facets["subjects"]]
    assert "en chat" in subjects  # folded, as the filter compares them
    assert "chat" not in subjects
