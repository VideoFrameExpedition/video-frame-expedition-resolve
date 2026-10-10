"""LM Studio client against a fake server (httpx.MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.conftest import FRAME_ANSWER, OLDER_PAYLOAD, VARIANTS_PAYLOAD, FakeLmStudio
from vfe_vision.adapters.lmstudio.catalog import LoadedInstance
from vfe_vision.adapters.lmstudio.client import (
    ChatImage,
    LmStudioClient,
    LmStudioResponseError,
    LmStudioTruncatedError,
    LmStudioUnavailableError,
    StreamStats,
    StructuredResult,
)
from vfe_vision.domain.vision import FrameAnalysis

pytestmark = pytest.mark.anyio

IMAGE = ChatImage(jpeg=b"\xff\xd8fakejpeg\xff\xd9", width=1024, height=576)


async def _ask(fake: FakeLmStudio) -> FrameAnalysis:
    client = fake.client()
    try:
        result = await client.chat_structured(
            model="qwen/qwen3-vl-8b",
            system="sys",
            user_text="user",
            output=FrameAnalysis,
            images=[IMAGE],
        )
    finally:
        await client.aclose()
    return result.data


async def test_structured_request_shape(fake_lmstudio: FakeLmStudio) -> None:
    data = await _ask(fake_lmstudio)
    assert data.caption == FRAME_ANSWER["caption"]
    body = fake_lmstudio.chat_requests[0]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    parts = body["messages"][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_invalid_json_is_repaired_once(fake_lmstudio: FakeLmStudio) -> None:
    fake_lmstudio.answers = ['{"caption": 1}', json.dumps(FRAME_ANSWER)]
    data = await _ask(fake_lmstudio)
    assert data.shot_type.value == "wide"
    assert len(fake_lmstudio.chat_requests) == 2
    assert "did not match" in fake_lmstudio.chat_requests[1]["messages"][-1]["content"]


async def test_persistent_invalid_json_raises(fake_lmstudio: FakeLmStudio) -> None:
    fake_lmstudio.answers = ["not json", "still not json"]
    with pytest.raises(LmStudioResponseError):
        await _ask(fake_lmstudio)


async def test_server_down_is_unavailable(fake_lmstudio: FakeLmStudio) -> None:
    fake_lmstudio.down = True
    with pytest.raises(LmStudioUnavailableError):
        await _ask(fake_lmstudio)


async def test_list_models(fake_lmstudio: FakeLmStudio) -> None:
    client = fake_lmstudio.client()
    try:
        models = await client.list_models()
    finally:
        await client.aclose()
    assert [m.key for m in models if m.vision] == ["qwen/qwen3-vl-8b"]
    assert fake_lmstudio.requested_paths == ["/api/v1/models"]  # nothing hidden: one list


@pytest.mark.parametrize("older", [200, 404, "down"])
async def test_a_model_loaded_from_another_variant(older: int | str) -> None:
    """The main list leaves its instance out: the older list says it is loaded. Without it (a
    future LM Studio, a server gone between two requests), the model stays unloaded."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json=VARIANTS_PAYLOAD)
        if older == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if older == 200:
            return httpx.Response(200, json=OLDER_PAYLOAD)
        return httpx.Response(404, text="not found")

    client = LmStudioClient("http://lmstudio.test", transport=httpx.MockTransport(handler))
    try:
        models = await client.list_models()
    finally:
        await client.aclose()
    assert paths == ["/api/v1/models", "/api/v0/models"]
    loaded = {m.key: m.loaded_instances for m in models if m.loaded_instances}
    if older == 200:
        instance = LoadedInstance(id="qwen/qwen3-vl-4b", context_length=20224, parallel=4)
        assert loaded == {"qwen/qwen3-vl-4b": (instance,)}
    else:
        assert loaded == {}


async def test_a_connection_dropped_mid_request_is_unavailable(
    fake_lmstudio: FakeLmStudio, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LM Studio quits or ejects the model during a request: retried, then "unavailable" (a
    retryable skip for the stage), never a raw httpx error."""
    import httpx
    from tenacity import wait_none

    from vfe_vision.adapters.lmstudio import client as client_module

    monkeypatch.setattr(client_module, "wait_exponential", lambda **_: wait_none())
    calls = []

    def dropped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.RemoteProtocolError("peer closed connection", request=request)

    fake_lmstudio.handler = dropped  # type: ignore[method-assign]
    with pytest.raises(LmStudioUnavailableError):
        await _ask(fake_lmstudio)
    assert len(calls) == 5


async def test_a_non_json_body_is_a_response_error(fake_lmstudio: FakeLmStudio) -> None:
    import httpx

    fake_lmstudio.handler = lambda request: httpx.Response(200, text="<html>")  # type: ignore[method-assign]
    with pytest.raises(LmStudioResponseError):
        await _ask(fake_lmstudio)


async def _result(
    fake: FakeLmStudio, *, reasoning_off: bool = False
) -> StructuredResult[FrameAnalysis]:
    client = fake.client()
    try:
        return await client.chat_structured(
            model="qwen/qwen3.5-9b", system="sys", user_text="user", output=FrameAnalysis,
            images=[IMAGE], reasoning_off=reasoning_off,
        )  # fmt: skip
    finally:
        await client.aclose()


async def test_a_truncated_answer_is_not_repaired(fake_lmstudio: FakeLmStudio) -> None:
    fake_lmstudio.answers = ['{"caption": "Une chenille verte flott']
    fake_lmstudio.finish_reason = "length"
    with pytest.raises(LmStudioTruncatedError):
        await _ask(fake_lmstudio)
    assert len(fake_lmstudio.chat_requests) == 1  # a cut JSON object cannot be repaired


async def test_reasoning_is_switched_off_and_what_it_cost_is_reported(
    fake_lmstudio: FakeLmStudio,
) -> None:
    fake_lmstudio.finish_reason = "stop"
    fake_lmstudio.reasoning_tokens = 0
    result = await _result(fake_lmstudio, reasoning_off=True)
    assert fake_lmstudio.chat_requests[0]["reasoning_effort"] == "none"
    assert result.finish_reason == "stop"
    assert result.reasoning_tokens == 0
    plain = await _result(fake_lmstudio)
    assert "reasoning_effort" not in fake_lmstudio.chat_requests[1]  # a model without options
    assert plain.reasoning_tokens == 0


async def test_a_server_refusing_the_switch_is_asked_without_it(
    fake_lmstudio: FakeLmStudio,
) -> None:
    fake_lmstudio.replies = [
        httpx.Response(400, text='{"error": "Unrecognized key: reasoning_effort"}')
    ]
    client = fake_lmstudio.client()
    try:
        for _ in range(2):
            await client.chat_structured(
                model="old/model", system="s", user_text="u", output=FrameAnalysis,
                images=[IMAGE], reasoning_off=True,
            )  # fmt: skip
    finally:
        await client.aclose()
    sent = ["reasoning_effort" in body for body in fake_lmstudio.chat_requests]
    assert sent == [True, False, False]  # refused once, then never sent again for that model


async def test_a_full_shared_cache_is_retried(fake_lmstudio: FakeLmStudio) -> None:
    fake_lmstudio.replies = [httpx.Response(400, text="Failed to decode the batch")]
    data = await _ask(fake_lmstudio)
    assert data.caption == FRAME_ANSWER["caption"]
    assert len(fake_lmstudio.chat_requests) == 2


# ---------------------------------------------------------------- streamed answers
async def _stream(
    fake: FakeLmStudio, *, reasoning_off: bool = False
) -> tuple[list[str], StreamStats]:
    client = fake.client()
    try:
        async with client.chat_stream(
            model="qwen/qwen3-vl-4b", system="s", user_text="u", max_tokens=300,
            reasoning_off=reasoning_off,
        ) as stream:  # fmt: skip
            pieces = [piece async for piece in stream]
        return pieces, stream.stats
    finally:
        await client.aclose()


async def test_a_streamed_answer_and_what_it_cost(fake_lmstudio: FakeLmStudio) -> None:
    fake_lmstudio.stream_text = "Un chat dort [1]."
    pieces, stats = await _stream(fake_lmstudio, reasoning_off=True)
    assert "".join(pieces) == "Un chat dort [1]."
    assert len(pieces) > 1
    body = fake_lmstudio.chat_requests[0]
    assert (body["stream"], body["reasoning_effort"]) == (True, "none")
    assert "response_format" not in body
    assert stats.finish_reason == "stop"
    assert (stats.prompt_tokens, stats.completion_tokens) == (1500, 40)
    assert stats.first_token_ms is not None


async def test_a_full_cache_is_retried_before_the_first_token(
    fake_lmstudio: FakeLmStudio, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tenacity import wait_none

    from vfe_vision.adapters.lmstudio import client as client_module

    monkeypatch.setattr(client_module, "wait_exponential", lambda **_: wait_none())
    fake_lmstudio.replies = [httpx.Response(500, text="Failed to decode the batch")]
    pieces, _ = await _stream(fake_lmstudio)
    assert "".join(pieces) == fake_lmstudio.stream_text
    assert len(fake_lmstudio.chat_requests) == 2
    fake_lmstudio.replies = [httpx.Response(404, text="model not loaded")]
    with pytest.raises(LmStudioUnavailableError, match="non chargé"):
        await _stream(fake_lmstudio)
