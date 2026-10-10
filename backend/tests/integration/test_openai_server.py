"""The client against a server compatible with OpenAI's API, as vLLM answers (a fake):
found out at a plain address, or said with its own path; thinking switched off; images it
refuses said in plain words; nothing loaded nor unloaded there."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import FakeLmStudio, FakeVllm
from vfe_vision.adapters.lmstudio.client import (
    ChatImage,
    ImagesRefusedError,
    LmStudioClient,
    LmStudioLoadError,
    LmStudioResponseError,
    LmStudioUnavailableError,
)
from vfe_vision.domain.lmstudio_link import LmStudioTarget, ServerKind
from vfe_vision.domain.vision import FrameAnalysis

pytestmark = pytest.mark.anyio

IMAGE = ChatImage(jpeg=b"\xff\xd8fakejpeg\xff\xd9", width=1024, height=576)
MODEL = "Qwen/Qwen3-VL-8B-Instruct"


async def _describe(client: LmStudioClient) -> FrameAnalysis:
    result = await client.chat_structured(
        model=MODEL, system="sys", user_text="user", output=FrameAnalysis, images=[IMAGE]
    )
    return result.data


async def test_a_plain_address_without_lm_studio_is_found_to_be_openai_compatible(
    fake_vllm: FakeVllm,
) -> None:
    client = fake_vllm.client()
    try:
        before = client.kind
        assert before is None  # not said, not found yet
        models = await client.list_models()
        assert fake_vllm.requested_paths == ["/api/v1/models", "/v1/models"]
        assert client.kind is ServerKind.OPENAI
        assert client.serves_its_models
        await _describe(client)
    finally:
        await client.aclose()
    [model] = models
    assert (model.key, model.display_name, model.publisher) == (
        MODEL,
        "Qwen3-VL-8B-Instruct",
        "Qwen",
    )
    assert (model.params, model.quantization, model.max_context_length) == ("8B", None, 32768)
    assert model.vision
    [instance] = model.loaded_instances  # served: always there
    assert (instance.id, instance.context_length, instance.parallel) == (MODEL, 32768, 4)
    assert instance.shared_context is False
    # The chat is under /v1, thinking switched off, LM Studio's switch never sent.
    assert fake_vllm.requested_paths[-1] == "/v1/chat/completions"
    body = fake_vllm.chat_requests[-1]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in body
    assert body["response_format"]["json_schema"]["strict"] is True


async def test_a_server_said_openai_compatible_keeps_its_path_and_settings() -> None:
    fake = FakeVllm(prefix="/vllm/v1")
    target = LmStudioTarget(
        "https://gpu-box.lan/vllm/v1", "secret", ServerKind.OPENAI, 8, vision=False
    )
    tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tokens.append(request.headers.get("Authorization"))
        return fake.handler(request)

    client = LmStudioClient(target, transport=httpx.MockTransport(handler))
    try:
        [model] = await client.list_models()
        await client.chat_structured(model=MODEL, system="s", user_text="u", output=FrameAnalysis)
    finally:
        await client.aclose()
    assert fake.requested_paths == ["/vllm/v1/models", "/vllm/v1/chat/completions"]
    assert set(tokens) == {"Bearer secret"}
    assert model.vision is False  # the user's word for this server
    assert model.loaded_instances[0].parallel == 8


async def test_a_refused_template_switch_is_dropped_for_that_model(fake_vllm: FakeVllm) -> None:
    fake_vllm.refuse_template_kwargs = True
    client = fake_vllm.client()
    try:
        await client.list_models()
        await _describe(client)
        await _describe(client)
    finally:
        await client.aclose()
    sent = [("chat_template_kwargs" in body) for body in fake_vllm.chat_requests]
    assert sent == [True, False, False]  # refused once, then never sent again


async def test_images_a_model_does_not_see_are_said_in_plain_words(fake_vllm: FakeVllm) -> None:
    fake_vllm.refuse_images = True
    client = fake_vllm.client()
    try:
        await client.list_models()
        with pytest.raises(ImagesRefusedError, match="ne voit pas les images"):
            await _describe(client)
        assert await client.probe_images(MODEL, IMAGE) is False
    finally:
        await client.aclose()


async def test_too_many_images_for_the_server_names_the_setting(fake_vllm: FakeVllm) -> None:
    fake_vllm.max_images = 1
    client = fake_vllm.client()
    try:
        await client.list_models()
        with pytest.raises(ImagesRefusedError, match="limit-mm-per-prompt"):
            await client.chat_structured(
                model=MODEL, system="s", user_text="u", output=FrameAnalysis, images=[IMAGE] * 3
            )
        assert await client.probe_images(MODEL, IMAGE) is True  # one image fits
    finally:
        await client.aclose()


async def test_nothing_is_loaded_nor_unloaded_on_a_server_that_serves_its_models(
    fake_vllm: FakeVllm,
) -> None:
    client = fake_vllm.client()
    try:
        await client.list_models()
        with pytest.raises(LmStudioLoadError, match="sert ses modèles"):
            await client.load_model(MODEL)
        with pytest.raises(LmStudioLoadError, match="sert ses modèles"):
            await client.unload_model(MODEL)
    finally:
        await client.aclose()
    assert not any("load" in path for path in fake_vllm.requested_paths)


async def test_lm_studio_found_at_a_plain_address_is_asked_as_before(
    fake_lmstudio: FakeLmStudio,
) -> None:
    client = fake_lmstudio.client()
    try:
        await client.list_models()
        assert client.kind is ServerKind.LMSTUDIO
        assert not client.serves_its_models
        await _describe(client)
    finally:
        await client.aclose()
    assert "chat_template_kwargs" not in fake_lmstudio.chat_requests[-1]


async def test_neither_lm_studio_nor_an_openai_server_at_an_address() -> None:
    def nothing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    client = LmStudioClient("http://nas.test:8080", transport=httpx.MockTransport(nothing))
    try:
        with pytest.raises(LmStudioResponseError, match="Ni LM Studio ni un serveur compatible"):
            await client.list_models()
        assert client.kind is None
    finally:
        await client.aclose()


async def test_a_server_that_does_not_answer_is_named_as_such(fake_vllm: FakeVllm) -> None:
    client = LmStudioClient(
        LmStudioTarget("http://gpu-box:8000/v1", None, ServerKind.OPENAI),
        transport=httpx.MockTransport(fake_vllm.handler),
    )
    fake_vllm.down = True
    try:
        with pytest.raises(LmStudioUnavailableError, match="Le serveur de modèles ne répond pas"):
            await client.list_models()
        with pytest.raises(LmStudioUnavailableError, match="Le serveur de modèles ne répond pas"):
            await _describe(client)
    finally:
        await client.aclose()
