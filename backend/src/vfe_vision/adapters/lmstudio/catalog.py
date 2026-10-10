"""Model catalogue: LM Studio's native REST ``GET /api/v1/models`` (completed by
``GET /api/v0/models`` for the models loaded from another variant), or an OpenAI-compatible
server's ``GET /v1/models`` (vLLM…), where every model listed is served, hence loaded."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from vfe_vision.adapters.lmstudio.budget import UNKNOWN_CONTEXT

# LM Studio's own number of parallel requests for a model it loads (and the model bench's).
DEFAULT_PARALLEL = 4
FILL_RATIO = 0.9  # of the context, reserved by the requests in flight

# In a served model's name: the size ("30B", "30B-A3B" for a mixture of experts, "500M") and
# the quantization ("AWQ", "FP8", "Q4_K_M"…), as LM Studio says them for its own models.
_PARAMS = re.compile(
    r"(?<![a-z0-9.])(\d+(?:\.\d+)?)([bm])(?:-a(\d+(?:\.\d+)?)b)?(?![a-z0-9])", re.I
)
_QUANT = re.compile(
    r"(?<![a-z0-9])(i?q\d(?:_[a-z0-9]{1,3}){0,2}|awq|gptq(?:-int\d)?|nvfp4|mxfp4|fp8|fp4|"
    r"int4|int8|w\d{1,2}a\d{1,2}|bf16|fp16|f16)(?![a-z0-9])",
    re.I,
)


class LoadedInstance(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    context_length: int | None = None
    parallel: int | None = None
    # LM Studio shares one context between its parallel requests; an OpenAI-compatible
    # server such as vLLM gives each request the whole context and queues what does not fit.
    shared_context: bool = True


class ModelInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    display_name: str
    type: str  # "llm" | "embedding"
    publisher: str | None = None
    architecture: str | None = None
    params: str | None = None
    quantization: str | None = None
    size_bytes: int | None = None
    max_context_length: int | None = None
    vision: bool = False
    reasoning_options: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()  # "qwen/qwen3-vl-4b@q4_k_m", "…@q6_k": the downloaded ones
    loaded_instances: tuple[LoadedInstance, ...] = ()

    @property
    def loaded(self) -> bool:
        return bool(self.loaded_instances)


def parse_models(payload: dict[str, Any]) -> list[ModelInfo]:
    models: list[ModelInfo] = []
    for raw in payload.get("models", []) or []:
        capabilities = raw.get("capabilities") or {}
        reasoning = capabilities.get("reasoning") or {}
        instances = tuple(
            LoadedInstance(
                id=inst["id"],
                context_length=(inst.get("config") or {}).get("context_length"),
                parallel=(inst.get("config") or {}).get("parallel"),
            )
            for inst in raw.get("loaded_instances") or []
            if inst.get("id")
        )
        models.append(
            ModelInfo(
                key=raw["key"],
                display_name=raw.get("display_name") or raw["key"],
                type=raw.get("type", "llm"),
                publisher=raw.get("publisher"),
                architecture=raw.get("architecture"),
                params=raw.get("params_string"),
                quantization=(raw.get("quantization") or {}).get("name"),
                size_bytes=raw.get("size_bytes"),
                max_context_length=raw.get("max_context_length"),
                vision=bool(capabilities.get("vision", False)),
                reasoning_options=tuple(reasoning.get("allowed_options") or ()),
                variants=tuple(raw.get("variants") or ()),
                loaded_instances=instances,
            )
        )
    return models


def parse_openai_models(payload: dict[str, Any], *, parallel: int, vision: bool) -> list[ModelInfo]:
    """The models an OpenAI-compatible server lists (``{"data": [{"id", "max_model_len"}]}``).

    Each one is served, so loaded, with its whole context per request. The server does not
    say whether a model sees images: ``vision`` is the user's setting for this server (an
    embedding model never does). Family, size and quantization are read from the name, for the
    model bench's colours.
    """
    models: list[ModelInfo] = []
    for raw in payload.get("data") or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            continue
        key = str(raw["id"])
        context = raw.get("max_model_len")
        context = context if isinstance(context, int) and context > 0 else None
        embedding = "embed" in key.lower()
        publisher, _, name = key.rpartition("/")
        models.append(
            ModelInfo(
                key=key,
                display_name=name or key,
                type="embedding" if embedding else "llm",
                publisher=publisher.split("/")[-1] or None,
                params=params_in(name or key),
                quantization=quantization_in(name or key),
                max_context_length=context,
                vision=vision and not embedding,
                loaded_instances=(
                    LoadedInstance(
                        id=key, context_length=context, parallel=parallel, shared_context=False
                    ),
                ),
            )
        )
    return models


def params_in(name: str) -> str | None:
    """The size a model's name gives: ``Qwen3-VL-30B-A3B-Instruct`` → ``30B-A3B``."""
    found = _PARAMS.search(name)
    if found is None:
        return None
    size = f"{found.group(1)}{found.group(2).upper()}"
    return f"{size}-A{found.group(3)}B" if found.group(3) else size


def quantization_in(name: str) -> str | None:
    """The quantization a model's name gives: ``…-Instruct-AWQ`` → ``AWQ``; None if it says
    none (full precision, as served)."""
    found = _QUANT.search(name)
    return found.group(1).upper() if found else None


def budget_of(instance: LoadedInstance) -> tuple[int, int]:
    """The token budget of an instance: tokens the requests in flight may hold together, and
    how many run at once. LM Studio's requests share its context; each of an OpenAI-compatible
    server's has the whole of it (the server queues what does not fit its cache)."""
    context = instance.context_length or UNKNOWN_CONTEXT
    parallel = max(1, instance.parallel or 1)
    capacity = int(context * FILL_RATIO) * (1 if instance.shared_context else parallel)
    return max(1024, capacity), parallel


def request_tokens(instance: LoadedInstance) -> int:
    """What one request can hold while every other one runs: its share of a shared context,
    or the whole context when each request has its own."""
    context = instance.context_length or UNKNOWN_CONTEXT
    if not instance.shared_context:
        return context
    return context // max(1, instance.parallel or 1)


def hides_its_instance(model: ModelInfo) -> bool:
    """A model of several variants listed without an instance: it may be loaded from one of
    the others (see ``with_other_variants``)."""
    return len(model.variants) > 1 and not model.loaded_instances


def with_other_variants(models: list[ModelInfo], older: dict[str, Any]) -> list[ModelInfo]:
    """The instances ``GET /api/v1/models`` leaves out, taken from ``GET /api/v0/models``.

    LM Studio lists the instances of a model under its selected variant only: loaded from
    another one (Q6_K when Q4_K_M is selected), the model shows none. The older list still
    says it is loaded, under the model's key, with its context but not its number of parallel
    requests: LM Studio's default is taken. A request beyond the real number waits in
    LM Studio's queue, and the token budget keeps them all within the context they share.
    """
    contexts: dict[str, int | None] = {}
    for raw in older.get("data") or []:
        if isinstance(raw, dict) and raw.get("state") == "loaded" and raw.get("id"):
            context = raw.get("loaded_context_length")
            contexts[str(raw["id"])] = context if isinstance(context, int) else None
    completed: list[ModelInfo] = []
    for model in models:
        if not hides_its_instance(model) or model.key not in contexts:
            completed.append(model)
            continue
        instance = LoadedInstance(
            id=model.key, context_length=contexts[model.key], parallel=DEFAULT_PARALLEL
        )
        completed.append(model.model_copy(update={"loaded_instances": (instance,)}))
    return completed


def pick_vision_instance(
    models: list[ModelInfo], preferred: str | None = None
) -> tuple[ModelInfo, LoadedInstance] | None:
    """The loaded vision instance to use — never a model that would need loading.

    ``preferred`` may be a model key or an instance identifier.
    """
    loaded = [(m, inst) for m in models if m.vision for inst in m.loaded_instances]
    if preferred:
        for model, inst in loaded:
            if preferred in {model.key, inst.id}:
                return model, inst
    return loaded[0] if loaded else None


def pick_text_instance(
    models: list[ModelInfo], preferred: str | None = None
) -> tuple[ModelInfo, LoadedInstance] | None:
    """The loaded instance that writes answers: the vision instance, as for every other
    generation, else any loaded language model (it cannot check images). Never an embedding
    model, never one that would need loading."""
    picked = pick_vision_instance(models, preferred)
    if picked is not None:
        return picked
    loaded = [(m, inst) for m in models if m.type == "llm" for inst in m.loaded_instances]
    return loaded[0] if loaded else None
