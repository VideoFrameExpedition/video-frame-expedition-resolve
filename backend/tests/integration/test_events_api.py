"""The live events stream (``/api/v1/events``)."""

from __future__ import annotations

import anyio
import httpx
import pytest
from sse_starlette.sse import AppStatus

from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings


@pytest.mark.anyio
async def test_the_event_stream_ends_whole_when_the_server_stops(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AppStatus, "should_exit", True)  # the server is stopping (Ctrl+C)
    app = create_app(settings, start_worker=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765"
        ) as client:
            with anyio.fail_after(10):
                # Cut short, the response would be left incomplete, which httpx refuses.
                response = await client.get("/api/v1/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
