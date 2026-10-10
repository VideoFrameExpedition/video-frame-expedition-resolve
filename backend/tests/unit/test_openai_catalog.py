"""What an OpenAI-compatible server's model list gives, and the token budget of an instance
whose requests share a context (LM Studio) or each have their own (vLLM)."""

from __future__ import annotations

import pytest

from vfe_vision.adapters.lmstudio.catalog import (
    LoadedInstance,
    budget_of,
    params_in,
    parse_openai_models,
    quantization_in,
    request_tokens,
)


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("Qwen3-VL-30B-A3B-Instruct", "30B-A3B"),
        ("Qwen2.5-VL-7B-Instruct-AWQ", "7B"),
        ("gemma-3-27b-it", "27B"),
        ("Llama-3.2-11B-Vision-Instruct", "11B"),
        ("Mistral-Small-3.1-24B-Instruct-2503", "24B"),
        ("SmolVLM-500M-Instruct", "500M"),
        ("phi-4-multimodal-instruct", None),
    ],
)
def test_the_size_is_read_in_the_name(name: str, params: str | None) -> None:
    assert params_in(name) == params


@pytest.mark.parametrize(
    ("name", "quantization"),
    [
        ("Qwen2.5-VL-7B-Instruct-AWQ", "AWQ"),
        ("Qwen3-VL-8B-Instruct-FP8", "FP8"),
        ("gemma-3-27b-it-GPTQ-Int4", "GPTQ-INT4"),
        ("qwen3-vl-4b-instruct-Q4_K_M.gguf", "Q4_K_M"),
        ("model-IQ2_XXS", "IQ2_XXS"),
        ("Qwen3-VL-8B-Instruct", None),  # full precision, as served
        ("Qwen3-VL-8B-Instruct-w4a16", "W4A16"),
    ],
)
def test_the_quantization_is_read_in_the_name(name: str, quantization: str | None) -> None:
    assert quantization_in(name) == quantization


def test_every_model_listed_is_served_with_its_whole_context() -> None:
    models = parse_openai_models(
        {
            "object": "list",
            "data": [
                {"id": "Qwen/Qwen3-VL-8B-Instruct", "max_model_len": 32768},
                {"id": "my-lora", "parent": "Qwen/Qwen3-VL-8B-Instruct"},  # no context said
                {"id": "BAAI/bge-m3-embedding", "max_model_len": 8192},
                {"object": "model"},  # no id: not a model
                "junk",
            ],
        },
        parallel=6,
        vision=True,
    )
    assert [m.key for m in models] == [
        "Qwen/Qwen3-VL-8B-Instruct",
        "my-lora",
        "BAAI/bge-m3-embedding",
    ]
    vlm, lora, embedding = models
    assert (vlm.publisher, vlm.display_name, vlm.params) == ("Qwen", "Qwen3-VL-8B-Instruct", "8B")
    assert vlm.loaded_instances[0] == LoadedInstance(
        id="Qwen/Qwen3-VL-8B-Instruct", context_length=32768, parallel=6, shared_context=False
    )
    assert (lora.publisher, lora.max_context_length) == (None, None)
    assert lora.vision  # the server's setting
    assert (embedding.type, embedding.vision) == ("embedding", False)
    blind = parse_openai_models({"data": [{"id": "x"}]}, parallel=4, vision=False)
    assert blind[0].vision is False


def test_a_shared_context_is_split_and_an_own_one_is_not() -> None:
    lm_studio = LoadedInstance(id="a", context_length=20480, parallel=4)
    vllm = LoadedInstance(id="b", context_length=32768, parallel=8, shared_context=False)
    # LM Studio: the requests in flight share 90 % of the context; one gets a quarter of it.
    assert budget_of(lm_studio) == (18432, 4)
    assert request_tokens(lm_studio) == 5120
    # vLLM: each request may fill its own context; the server queues what does not fit.
    assert budget_of(vllm) == (29491 * 8, 8)
    assert request_tokens(vllm) == 32768
    # Nothing said: the stages' old default (8192 tokens, one request).
    assert budget_of(LoadedInstance(id="c")) == (7372, 1)
