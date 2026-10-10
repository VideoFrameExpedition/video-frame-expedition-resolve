"""LM Studio model catalogue (native REST ``GET /api/v1/models``, completed by
``GET /api/v0/models`` for the models loaded from another variant)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

# LM Studio's own number of parallel requests for a model it loads (and the model bench's).
DEFAULT_PARALLEL = 4


class LoadedInstance(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    context_length: int | None = None
    parallel: int | None = None


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
