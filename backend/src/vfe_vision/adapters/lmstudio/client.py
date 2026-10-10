"""Async client for the model server: LM Studio, or a server compatible with OpenAI's API
(vLLM, llama.cpp's server…). Model catalogue, structured (JSON-schema) chat completions and
streamed free-text answers; loading and unloading models with LM Studio only."""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from vfe_vision.adapters.lmstudio.budget import (
    TokenBudget,
    UsageCalibration,
    estimate_image_tokens,
    estimate_text_tokens,
)
from vfe_vision.adapters.lmstudio.catalog import (
    ModelInfo,
    hides_its_instance,
    parse_models,
    parse_openai_models,
    with_other_variants,
)
from vfe_vision.adapters.lmstudio.schema import strict_json_schema
from vfe_vision.core.errors import ServiceUnavailableError, VfeError
from vfe_vision.core.logging import get_logger
from vfe_vision.domain.lmstudio_link import DEFAULT_PARALLEL, LmStudioTarget, ServerKind, is_local

log = get_logger(__name__)

STREAM_READ_TIMEOUT_S = 120.0  # between two chunks, the first one included


class LmStudioUnavailableError(ServiceUnavailableError):
    code = "lmstudio_unavailable"
    title = "LM Studio injoignable"


class LmStudioResponseError(VfeError):
    code = "lmstudio_bad_response"
    status = 502
    title = "Réponse LM Studio invalide"


class LmStudioTruncatedError(LmStudioResponseError):
    """The answer hit ``max_tokens`` (a loop, or reasoning eating the budget): a repair round
    cannot finish a cut JSON object."""

    code = "lmstudio_truncated"
    title = "Réponse LM Studio tronquée"


class LmStudioLoadError(VfeError):
    """LM Studio refused to load or unload a model (unknown model, not enough memory…)."""

    code = "lmstudio_load_failed"
    status = 502
    title = "Chargement du modèle impossible"


LOAD_TIMEOUT_S = 600.0  # a large model read from a slow disk
UNLOAD_TIMEOUT_S = 120.0


class _TransientError(Exception):
    """Retryable condition (KV cache saturated, server hiccup, timeout)."""


class _ParamRejectedError(Exception):
    """The server refused an optional field of the request (``reasoning_effort``,
    ``chat_template_kwargs``): sent again without it."""

    def __init__(self, param: str, detail: str) -> None:
        super().__init__(detail)
        self.param = param


# Thinking switched off on an OpenAI-compatible server: the templates of the models that
# reason (Qwen3…) read it; the others ignore it.
NO_THINKING = {"enable_thinking": False}
IMAGE_PROBE_TIMEOUT_S = 60.0  # one tiny image, but the server may be busy


@dataclass(frozen=True, slots=True)
class ChatImage:
    jpeg: bytes
    width: int
    height: int
    label: str | None = None  # text sent just before the image ("Image 2 - 4.0 s into…")

    def data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode("ascii")


@dataclass(frozen=True, slots=True)
class StructuredResult[T: BaseModel]:
    data: T
    raw_content: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int
    repaired: bool
    finish_reason: str | None = None  # "stop", or "length" when max_tokens cut it
    reasoning_tokens: int | None = None  # thinking the answer paid for (should be 0 or None)


@dataclass(frozen=True, slots=True)
class LoadedModel:
    """An instance LM Studio has just loaded, with the settings it was given."""

    instance_id: str
    load_time_s: float | None = None
    context_length: int | None = None
    parallel: int | None = None


class LmStudioClient:
    def __init__(
        self,
        base_url: str | LmStudioTarget | Callable[[], LmStudioTarget],
        *,
        token: str | None = None,
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """``base_url`` is an address, a server, or what gives the server before each request:
        the one chosen in the interface may change while the application runs."""
        if isinstance(base_url, LmStudioTarget):
            fixed = base_url
            self._target: Callable[[], LmStudioTarget] = lambda: fixed
        elif callable(base_url):
            self._target = base_url
        else:
            plain = LmStudioTarget(base_url.rstrip("/"), token)
            self._target = lambda: plain
        # Per model and kind of request: learned prompt/answer sizes, so reservations match the
        # real KV usage (a list of boxes and a frame description differ in length).
        self._calibration: dict[tuple[str, str], UsageCalibration] = {}
        # Optional fields a server refused, per model: asked without them from then on.
        self._rejected: dict[str, set[str]] = {}
        # The kind of server found at an address whose kind was not said (LM Studio first).
        self._found: dict[str, ServerKind] = {}
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=5.0), transport=transport
        )

    @property
    def base_url(self) -> str:
        return self._target().url

    @property
    def kind(self) -> ServerKind | None:
        """The kind of server in use: said, or found by the last catalogue read (None until
        then, when it was not said)."""
        target = self._target()
        return target.kind or self._found.get(target.url)

    @property
    def serves_its_models(self) -> bool:
        """An OpenAI-compatible server: the models it serves are always there, and the
        application can neither load nor unload one (LM Studio can, for the model bench)."""
        return self.kind is ServerKind.OPENAI

    def _to(self, path: str) -> tuple[str, dict[str, str]]:
        """Where a request goes, and the token of that server (never of another one)."""
        target = self._target()
        return target.url + path, (
            {"Authorization": f"Bearer {target.token}"} if target.token else {}
        )

    def _chat(self) -> tuple[str, dict[str, str]]:
        """The chat completions endpoint. A server said to be OpenAI-compatible has its API
        path in its address (``…/v1``); LM Studio, or a server found to be OpenAI-compatible
        at a plain address, answers under ``/v1``."""
        if self._target().kind is ServerKind.OPENAI:
            return self._to("/chat/completions")
        return self._to("/v1/chat/completions")

    def _server(self) -> str:
        """The server, as the messages name it."""
        return "le serveur de modèles" if self.serves_its_models else "LM Studio"

    def server_title(self) -> str:
        """The same, at the start of a sentence."""
        return "Le serveur de modèles" if self.serves_its_models else "LM Studio"

    def unavailable_note(self, *, vision: bool = True) -> str:
        """Why an analysis step waits: the server is down, or has no model to use."""
        what = "de vision " if vision else ""
        if self.serves_its_models:
            return f"Serveur de modèles injoignable ou aucun modèle {what}servi"
        return f"LM Studio injoignable ou aucun modèle {what}chargé"

    def no_vision_model(self) -> str:
        if self.serves_its_models:
            return "Le serveur de modèles ne sert aucun modèle de vision."
        return "Aucun modèle de vision chargé dans LM Studio."

    def _unreachable(self) -> str:
        url = self.base_url
        if self.serves_its_models:
            return (
                f"Le serveur de modèles ne répond pas sur {url}. Vérifiez qu'il tourne (vLLM : "
                "vllm serve …) et que cet ordinateur peut le joindre."
            )
        if is_local(url):
            return f"LM Studio ne répond pas sur {url}. Démarrez le serveur local."
        return (
            f"LM Studio ne répond pas sur {url}. Sur cet autre ordinateur, démarrez le serveur "
            "de LM Studio et autorisez les connexions du réseau (« Serve on Local Network »)."
        )

    def _refused_access(self, openai: bool) -> str:
        if openai:
            return (
                f"Le serveur de modèles refuse l'accès sur {self.base_url} : il demande un jeton "
                "d'API (--api-key de vLLM), ou celui qui est enregistré n'est plus le bon."
            )
        return (
            f"LM Studio refuse l'accès sur {self.base_url} : il demande un jeton d'API "
            "(Developer › Server Settings), ou celui qui est enregistré n'est plus le bon."
        )

    def _extras(self, model: str, *, reasoning_off: bool) -> dict[str, Any]:
        """The optional fields of a chat request, minus those this model's server refused:
        LM Studio's reasoning switch, or thinking switched off in an OpenAI-compatible
        server's chat template."""
        refused = self._rejected.get(model, set())
        extras: dict[str, Any] = {}
        if reasoning_off and "reasoning_effort" not in refused:
            extras["reasoning_effort"] = "none"
        if self.serves_its_models and "chat_template_kwargs" not in refused:
            extras["chat_template_kwargs"] = dict(NO_THINKING)
        return extras

    def _without(self, request: dict[str, Any], rejected: _ParamRejectedError) -> None:
        """Once per model and field: this server does not know it."""
        self._rejected.setdefault(str(request["model"]), set()).add(rejected.param)
        request.pop(rejected.param, None)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ catalogue
    async def list_models(self) -> list[ModelInfo]:
        """The models of the server. One whose kind was not said is asked as LM Studio first:
        an address without LM Studio's own API (404) is then asked as an OpenAI-compatible
        server, and remembered as such."""
        target = self._target()
        if target.kind is ServerKind.OPENAI:
            return await self._openai_models("/models", target)
        url, headers = self._to("/api/v1/models")
        try:
            response = await self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise LmStudioUnavailableError(self._unreachable()) from exc
        if response.status_code == 404 and target.kind is None:
            models = await self._openai_models("/v1/models", target)
            self._found[target.url] = ServerKind.OPENAI
            return models
        if response.status_code in {401, 403}:
            raise LmStudioUnavailableError(self._refused_access(openai=False))
        if response.status_code != 200:
            raise LmStudioResponseError(f"/api/v1/models a renvoyé {response.status_code}")
        if target.kind is None:
            self._found[target.url] = ServerKind.LMSTUDIO
        models = parse_models(response.json())
        if any(hides_its_instance(model) for model in models):
            models = with_other_variants(models, await self._older_list())
        return models

    async def _openai_models(self, path: str, target: LmStudioTarget) -> list[ModelInfo]:
        """``GET /v1/models`` of an OpenAI-compatible server."""
        url, headers = self._to(path)
        try:
            response = await self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise LmStudioUnavailableError(
                f"Le serveur de modèles ne répond pas sur {target.url}. Vérifiez qu'il tourne "
                "et que cet ordinateur peut le joindre."
            ) from exc
        if response.status_code in {401, 403}:
            raise LmStudioUnavailableError(self._refused_access(openai=True))
        if response.status_code == 404 and target.kind is None:
            raise LmStudioResponseError(
                f"Ni LM Studio ni un serveur compatible OpenAI ne répondent sur {target.url} : "
                "vérifiez l'adresse et le port."
            )
        if response.status_code != 200:
            raise LmStudioResponseError(f"{path} a renvoyé {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise LmStudioResponseError(f"Réponse illisible de {path}") from exc
        return parse_openai_models(
            body if isinstance(body, dict) else {},
            parallel=target.parallel or DEFAULT_PARALLEL,
            vision=target.vision,
        )

    async def _older_list(self) -> dict[str, Any]:
        """``GET /api/v0/models``, or nothing: it only completes the list above."""
        url, headers = self._to("/api/v0/models")
        try:
            response = await self._http.get(url, headers=headers)
            body = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    # ------------------------------------------------------------------ loading (model bench)
    async def load_model(
        self,
        key: str,
        *,
        context_length: int | None = None,
        parallel: int | None = None,
        timeout_s: float = LOAD_TIMEOUT_S,
    ) -> LoadedModel:
        """Load a model in LM Studio and wait until it is ready.

        Only the model bench does, on the user's request: the analyses never load a
        model, they use the instance already loaded. An OpenAI-compatible server serves its
        models itself: nothing to load there.
        """
        self._manages_models(key)
        request: dict[str, Any] = {"model": key, "echo_load_config": True}
        if context_length is not None:
            request["context_length"] = context_length
        if parallel is not None:
            request["parallel"] = parallel
        body = await self._manage("/api/v1/models/load", request, timeout_s, what=key)
        raw = body.get("load_config")
        config: dict[str, Any] = raw if isinstance(raw, dict) else {}
        seconds = body.get("load_time_seconds")
        return LoadedModel(
            instance_id=str(body.get("instance_id") or key),
            load_time_s=float(seconds) if isinstance(seconds, int | float) else None,
            context_length=_whole(config.get("context_length")),
            parallel=_whole(config.get("parallel")),
        )

    async def unload_model(self, instance_id: str, *, timeout_s: float = UNLOAD_TIMEOUT_S) -> None:
        """Unload a loaded instance (the model bench only)."""
        self._manages_models(instance_id)
        await self._manage(
            "/api/v1/models/unload", {"instance_id": instance_id}, timeout_s, what=instance_id
        )

    def _manages_models(self, what: str) -> None:
        if self.serves_its_models:
            raise LmStudioLoadError(
                "Ce serveur sert ses modèles lui-même : l'application ne peut ni les charger ni "
                "les décharger.",
                model=what,
            )

    async def _manage(
        self, path: str, request: dict[str, Any], timeout_s: float, *, what: str
    ) -> dict[str, Any]:
        url, headers = self._to(path)
        try:
            response = await self._http.post(
                url, json=request, headers=headers, timeout=httpx.Timeout(timeout_s, connect=5.0)
            )
        except httpx.ConnectError as exc:
            raise LmStudioUnavailableError(self._unreachable()) from exc
        except httpx.TimeoutException as exc:
            raise LmStudioLoadError(f"LM Studio n'a pas répondu à temps pour {what}.") from exc
        except httpx.TransportError as exc:
            raise LmStudioUnavailableError(
                f"Connexion à LM Studio interrompue ({type(exc).__name__})"
            ) from exc
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError as exc:
                raise LmStudioResponseError("Réponse de LM Studio illisible") from exc
            return body if isinstance(body, dict) else {}
        raise LmStudioLoadError(_problem(response) or f"HTTP {response.status_code}", model=what)

    # ------------------------------------------------------------------ structured chat
    async def chat_structured[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        user_text: str,
        output: type[T],
        images: Sequence[ChatImage] = (),
        max_tokens: int = 900,
        temperature: float = 0.1,
        budget: TokenBudget | None = None,
        purpose: str = "default",
        reasoning_off: bool = False,
    ) -> StructuredResult[T]:
        """Ask for a JSON object validated against ``output``; one repair round on failure.

        ``reasoning_off``: the model can reason (``reasoning_options``); it is asked not to, since
        thinking tokens only slow a structured answer down and can eat ``max_tokens``. Whether
        it obeyed is in ``reasoning_tokens``: the switch is a per-model user setting.
        """
        schema = strict_json_schema(output)
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for img in images:
            if img.label:
                content.append({"type": "text", "text": img.label})
            content.append({"type": "image_url", "image_url": {"url": img.data_url()}})
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        # The JSON schema constrains sampling (grammar) but is not part of the prompt: only the
        # messages and images occupy the context.
        estimated_prompt = (
            estimate_text_tokens(system)
            + estimate_text_tokens(user_text)
            + sum(
                estimate_image_tokens(i.width, i.height)
                + (estimate_text_tokens(i.label) if i.label else 0)
                for i in images
            )
        )
        calibration = self._calibration.setdefault((model, purpose), UsageCalibration())
        estimate = calibration.prompt(estimated_prompt) + calibration.completion(max_tokens)
        elapsed: list[float] = []

        async def complete(extra_tokens: int = 0) -> tuple[dict[str, Any], str]:
            return await self._complete(
                model=model,
                messages=messages,
                schema=schema,
                max_tokens=max_tokens,
                temperature=temperature,
                budget=budget,
                estimate=estimate + extra_tokens,
                elapsed=elapsed,
                reasoning_off=reasoning_off,
            )

        body, reply = await complete()
        reasoning_tokens = _reasoning_tokens(body)
        first_usage = body.get("usage") or {}
        calibration.observe(
            estimated_prompt,
            first_usage.get("prompt_tokens"),
            first_usage.get("completion_tokens"),
        )
        repaired = False
        try:
            data = output.model_validate_json(reply)
        except ValidationError as first_error:
            if _finish_reason(body) == "length":
                # The usage says whether reasoning ate the budget: kept for the diagnosis.
                raise LmStudioTruncatedError(
                    f"Réponse de {model} coupée à {max_tokens} jetons (boucle ou raisonnement)",
                    reasoning_tokens=reasoning_tokens,
                    prompt_tokens=first_usage.get("prompt_tokens"),
                    completion_tokens=first_usage.get("completion_tokens"),
                ) from first_error
            log.warning("invalid structured output, asking for a repair", model=model)
            messages += [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": "Your previous answer did not match the JSON schema: "
                    f"{first_error.errors()[:3]}. Return the corrected JSON object only.",
                },
            ]
            body, reply = await complete(extra_tokens=max_tokens)
            reasoning_tokens = _sum(reasoning_tokens, _reasoning_tokens(body))
            try:
                data = output.model_validate_json(reply)
            except ValidationError as exc:
                raise LmStudioResponseError(
                    f"Le modèle {model} n'a pas produit un JSON valide", errors=exc.errors()[:5]
                ) from exc
            repaired = True
        usage = body.get("usage") or {}
        return StructuredResult(
            data=data,
            raw_content=reply,
            model=str(body.get("model") or model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=round(sum(elapsed) * 1000),  # server time only, not our own queue
            repaired=repaired,
            finish_reason=_finish_reason(body),
            reasoning_tokens=reasoning_tokens,
        )

    async def _complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
        budget: TokenBudget | None,
        estimate: int,
        elapsed: list[float],
        reasoning_off: bool = False,
    ) -> tuple[dict[str, Any], str]:
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": schema},
            },
            **self._extras(model, reasoning_off=reasoning_off),
        }
        retrying = AsyncRetrying(
            retry=retry_if_exception_type(_TransientError),
            wait=wait_exponential(multiplier=1.5, min=1, max=20),
            stop=stop_after_attempt(5),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    return await self._post_dropping_refused(request, budget, estimate, elapsed)
        except _TransientError as exc:
            raise LmStudioUnavailableError(
                f"{self.server_title()} sature ou ne répond plus : {exc}"
            ) from exc
        except _ParamRejectedError as exc:  # refused once more: not about that field after all
            raise LmStudioResponseError(f"Requête refusée par {self._server()} : {exc}") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def _post_dropping_refused(
        self,
        request: dict[str, Any],
        budget: TokenBudget | None,
        estimate: int,
        elapsed: list[float],
    ) -> tuple[dict[str, Any], str]:
        """One request; an optional field the server refuses is dropped, and the request sent
        again (at most once per field)."""
        for _ in range(3):
            try:
                return await self._post_once(request, budget, estimate, elapsed)
            except _ParamRejectedError as rejected:
                if rejected.param not in request:
                    raise
                self._without(request, rejected)
        return await self._post_once(request, budget, estimate, elapsed)

    async def _post_once(
        self,
        request: dict[str, Any],
        budget: TokenBudget | None,
        estimate: int,
        elapsed: list[float],
    ) -> tuple[dict[str, Any], str]:
        async def post() -> httpx.Response:
            started = time.perf_counter()
            url, headers = self._chat()
            try:
                return await self._http.post(url, json=request, headers=headers)
            finally:
                elapsed.append(time.perf_counter() - started)

        try:
            if budget is not None:
                async with budget.reserve(estimate):
                    response = await post()
            else:
                response = await post()
        except httpx.ConnectError as exc:
            raise LmStudioUnavailableError(self._unreachable()) from exc
        except httpx.TimeoutException as exc:
            raise _TransientError("délai dépassé") from exc
        except httpx.TransportError as exc:  # connection dropped mid-request, model ejected
            raise _TransientError(f"connexion interrompue ({type(exc).__name__})") from exc

        if response.status_code == 200:
            try:
                body: dict[str, Any] = response.json()
                return body, str(body["choices"][0]["message"]["content"] or "")
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise LmStudioResponseError("Réponse sans contenu JSON lisible") from exc
        raise _refusal(response.status_code, response.text, request, self._server())

    # ------------------------------------------------------------------ streamed chat
    @asynccontextmanager
    async def chat_stream(
        self,
        *,
        model: str,
        system: str,
        user_text: str,
        max_tokens: int,
        temperature: float = 0.2,
        reasoning_off: bool = False,
        read_timeout_s: float = STREAM_READ_TIMEOUT_S,
    ) -> AsyncIterator[ChatStream]:
        """A free-text answer streamed as it is written (``stream: true``).

        Iterate over the ``ChatStream`` for the text; its ``stats`` hold the usage and finish
        reason once it ends. Leaving the block closes the upstream response, which makes LM
        Studio stop generating (stop button, browser gone). A saturated server is retried
        before the first token only: once text went out, a failure is the caller's to report.
        """
        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._extras(model, reasoning_off=reasoning_off),
        }
        # The first token may wait for a slot while the analyses use them all.
        limits = httpx.Timeout(read_timeout_s, connect=5.0)
        started = time.perf_counter()
        response = await self._open_stream(request, limits)
        stream = ChatStream(response, model=model, started=started)
        try:
            yield stream
        finally:
            await response.aclose()

    async def _open_stream(self, request: dict[str, Any], limits: httpx.Timeout) -> httpx.Response:
        retrying = AsyncRetrying(
            retry=retry_if_exception_type(_TransientError),
            wait=wait_exponential(multiplier=1.5, min=1, max=10),
            stop=stop_after_attempt(4),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    return await self._stream_dropping_refused(request, limits)
        except _TransientError as exc:
            raise LmStudioUnavailableError(
                f"{self.server_title()} sature ou ne répond plus : {exc}"
            ) from exc
        except _ParamRejectedError as exc:
            raise LmStudioResponseError(f"Requête refusée par {self._server()} : {exc}") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def _stream_dropping_refused(
        self, request: dict[str, Any], limits: httpx.Timeout
    ) -> httpx.Response:
        for _ in range(3):
            try:
                return await self._send_stream(request, limits)
            except _ParamRejectedError as rejected:
                if rejected.param not in request:
                    raise
                self._without(request, rejected)
        return await self._send_stream(request, limits)

    async def _send_stream(self, request: dict[str, Any], limits: httpx.Timeout) -> httpx.Response:
        url, headers = self._chat()
        prepared = self._http.build_request(
            "POST", url, json=request, headers=headers, timeout=limits
        )
        try:
            response = await self._http.send(prepared, stream=True)
        except httpx.ConnectError as exc:
            raise LmStudioUnavailableError(self._unreachable()) from exc
        except httpx.TimeoutException as exc:
            raise _TransientError("délai dépassé") from exc
        except httpx.TransportError as exc:
            raise _TransientError(f"connexion interrompue ({type(exc).__name__})") from exc
        if response.status_code == 200:
            return response
        try:
            text = (await response.aread()).decode("utf-8", "replace")
        finally:
            await response.aclose()
        raise _refusal(response.status_code, text, request, self._server())

    # ------------------------------------------------------------------ images (server test)
    async def probe_images(self, model: str, image: ChatImage) -> bool | None:
        """Whether a model takes images: one tiny image, one token asked. True when it answers,
        False when the server refuses images for it, None when nothing can be said (the server
        is busy, the request failed for another reason)."""
        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What colour is this image? One word."},
                        {"type": "image_url", "image_url": {"url": image.data_url()}},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 1,
            "stream": False,
        }
        url, headers = self._chat()
        try:
            response = await self._http.post(
                url,
                json=request,
                headers=headers,
                timeout=httpx.Timeout(IMAGE_PROBE_TIMEOUT_S, connect=5.0),
            )
        except httpx.HTTPError:
            return None
        if response.status_code == 200:
            return True
        refusal = _refusal(response.status_code, response.text, request, self._server())
        return False if isinstance(refusal, ImagesRefusedError) else None


@dataclass(slots=True)
class StreamStats:
    """What a streamed answer cost, known once it ends (``stream_options.include_usage``)."""

    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    finish_reason: str | None = None  # "stop", or "length" when max_tokens cut it
    first_token_ms: int | None = None  # from the request: the wait for a slot and the prompt
    latency_ms: int = 0  # from the request to the last chunk read


class ChatStream:
    """The text of one streamed completion, piece by piece (server-sent ``data:`` lines)."""

    def __init__(self, response: httpx.Response, *, model: str, started: float) -> None:
        self._response = response
        self._started = started
        self.stats = StreamStats(model=model)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._pieces()

    def _elapsed_ms(self) -> int:
        return round((time.perf_counter() - self._started) * 1000)

    async def _pieces(self) -> AsyncIterator[str]:
        stats = self.stats
        try:
            async for line in self._response.aiter_lines():
                if not line.startswith("data:"):
                    continue  # blank separators, comments, ``event:`` names
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError as exc:
                    raise LmStudioResponseError("Flux de réponse illisible") from exc
                if not isinstance(chunk, dict):
                    continue
                for text in self._read(chunk):
                    if stats.first_token_ms is None:
                        stats.first_token_ms = self._elapsed_ms()
                    yield text
        except httpx.TimeoutException as exc:
            raise LmStudioUnavailableError("LM Studio ne répond plus pendant la réponse") from exc
        except httpx.TransportError as exc:
            raise LmStudioUnavailableError(
                f"Connexion à LM Studio interrompue pendant la réponse ({type(exc).__name__})"
            ) from exc
        finally:
            stats.latency_ms = self._elapsed_ms()

    def _read(self, chunk: dict[str, Any]) -> list[str]:
        """The text of one chunk; its usage and finish reason go to ``stats``."""
        if chunk.get("error"):
            error = chunk["error"]
            message = error.get("message") if isinstance(error, dict) else error
            raise LmStudioResponseError(f"LM Studio a interrompu la réponse : {str(message)[:300]}")
        stats = self.stats
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            stats.prompt_tokens = usage.get("prompt_tokens")
            stats.completion_tokens = usage.get("completion_tokens")
            stats.reasoning_tokens = _reasoning_tokens(chunk)
        if chunk.get("model"):
            stats.model = str(chunk["model"])
        texts: list[str] = []
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                stats.finish_reason = str(choice["finish_reason"])
            content = (choice.get("delta") or {}).get("content")  # never ``reasoning_content``
            if isinstance(content, str) and content:
                texts.append(content)
        return texts


class ImagesRefusedError(LmStudioResponseError):
    """The server refuses the images of a request: a model that does not see images, or more
    images in one request than the server allows."""

    code = "model_server_images_refused"
    title = "Images refusées par le serveur de modèles"


_TOO_MANY_IMAGES = re.compile(r"at most (\d+) image", re.I)


def _refusal(status: int, text: str, request: dict[str, Any], server: str) -> Exception:
    """What a non-200 answer means: a saturation worth retrying, an optional field the server
    does not know, images it refuses, a model that is not loaded, or a refusal. ``server`` is
    how the messages name it."""
    lowered = text.lower()
    # "failed to decode": the shared KV cache was full for a moment (other LM Studio clients
    # use it too); a retry gets through.
    if "mtmd chunk" in text or "failed to decode" in lowered or status in {429, 500, 502, 503}:
        return _TransientError(f"HTTP {status} : {text[:200]}")
    if status in {400, 422}:
        if "reasoning_effort" in request and "reasoning" in lowered:
            return _ParamRejectedError("reasoning_effort", text[:200])
        if "chat_template_kwargs" in request and "chat_template" in lowered:
            return _ParamRejectedError("chat_template_kwargs", text[:200])
        too_many = _TOO_MANY_IMAGES.search(text)
        if too_many and int(too_many.group(1)) > 0:
            # 4: the frames of a shot story (domain.shot_story.FRAMES) and of the visual check
            # of an answer (domain.ask.MAX_CHECK_FRAMES).
            return ImagesRefusedError(
                f"Le serveur de modèles n'accepte que {too_many.group(1)} image(s) par requête, "
                "et l'application en envoie jusqu'à 4 : avec vLLM, relancez-le avec "
                """--limit-mm-per-prompt '{"image": 4}'."""
            )
        # vLLM: "… is not a multimodal model", or "At most 0 image(s) may be provided".
        if (
            too_many
            or "multimodal" in lowered
            or (
                "image" in lowered
                and any(word in lowered for word in ("not support", "does not accept"))
            )
        ):
            if server == "LM Studio":
                return ImagesRefusedError(
                    f"Le modèle {request['model']} ne voit pas les images : chargez un modèle de "
                    "vision dans LM Studio."
                )
            return ImagesRefusedError(
                f"Le modèle {request['model']} ne voit pas les images : servez un modèle de "
                "vision, ou décochez « Ce serveur voit les images » (page Système)."
            )
    if status == 404 or "not loaded" in lowered or "no model" in lowered:
        where = "dans LM Studio" if server == "LM Studio" else "sur le serveur de modèles"
        return LmStudioUnavailableError(
            f"Modèle introuvable ou non chargé {where} : {request['model']}"
        )
    return LmStudioResponseError(f"HTTP {status} : {text[:300]}")


def _problem(response: httpx.Response) -> str:
    """What LM Studio says went wrong (``{"error": {"message": …}}``), in one line."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return " ".join(response.text.split())[:300]
    message = error.get("message") if isinstance(error, dict) else error
    return " ".join(str(message or "").split())[:300]


def _whole(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _finish_reason(body: dict[str, Any]) -> str | None:
    try:
        reason = body["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return str(reason) if reason is not None else None


def _reasoning_tokens(body: dict[str, Any]) -> int | None:
    details = (body.get("usage") or {}).get("completion_tokens_details") or {}
    value = details.get("reasoning_tokens") if isinstance(details, dict) else None
    return int(value) if isinstance(value, int | float) else None


def _sum(a: int | None, b: int | None) -> int | None:
    return None if a is None and b is None else (a or 0) + (b or 0)
