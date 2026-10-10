"""Async client for LM Studio: model catalogue, structured (JSON-schema) chat completions and
streamed free-text answers."""

from __future__ import annotations

import base64
import json
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
    with_other_variants,
)
from vfe_vision.adapters.lmstudio.schema import strict_json_schema
from vfe_vision.core.errors import ServiceUnavailableError, VfeError
from vfe_vision.core.logging import get_logger
from vfe_vision.domain.lmstudio_link import LmStudioTarget, is_local

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


class _ReasoningParamRejectedError(Exception):
    """The server refused ``reasoning_effort`` (HTTP 400 naming it)."""


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
        base_url: str | Callable[[], LmStudioTarget],
        *,
        token: str | None = None,
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """``base_url`` is an address, or what gives the address before each request: the one
        chosen in the interface may change while the application runs."""
        if callable(base_url):
            self._target = base_url
        else:
            fixed = LmStudioTarget(base_url.rstrip("/"), token)
            self._target = lambda: fixed
        # Per model and kind of request: learned prompt/answer sizes, so reservations match the
        # real KV usage (a list of boxes and a frame description differ in length).
        self._calibration: dict[tuple[str, str], UsageCalibration] = {}
        # Models whose server refused ``reasoning_effort``: asked without it from then on.
        self._no_reasoning_param: set[str] = set()
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=5.0), transport=transport
        )

    @property
    def base_url(self) -> str:
        return self._target().url

    def _to(self, path: str) -> tuple[str, dict[str, str]]:
        """Where a request goes, and the token of that LM Studio (never of another one)."""
        target = self._target()
        return target.url + path, (
            {"Authorization": f"Bearer {target.token}"} if target.token else {}
        )

    def _unreachable(self) -> str:
        url = self.base_url
        if is_local(url):
            return f"LM Studio ne répond pas sur {url}. Démarrez le serveur local."
        return (
            f"LM Studio ne répond pas sur {url}. Sur cet autre ordinateur, démarrez le serveur "
            "de LM Studio et autorisez les connexions du réseau (« Serve on Local Network »)."
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ catalogue
    async def list_models(self) -> list[ModelInfo]:
        url, headers = self._to("/api/v1/models")
        try:
            response = await self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise LmStudioUnavailableError(self._unreachable()) from exc
        if response.status_code in {401, 403}:
            raise LmStudioUnavailableError(
                f"LM Studio refuse l'accès sur {self.base_url} : il demande un jeton d'API "
                "(Developer › Server Settings), ou celui qui est enregistré n'est plus le bon."
            )
        if response.status_code != 200:
            raise LmStudioResponseError(f"/api/v1/models a renvoyé {response.status_code}")
        models = parse_models(response.json())
        if any(hides_its_instance(model) for model in models):
            models = with_other_variants(models, await self._older_list())
        return models

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
        model, they use the instance already loaded.
        """
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
        await self._manage(
            "/api/v1/models/unload", {"instance_id": instance_id}, timeout_s, what=instance_id
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
        }
        if reasoning_off and model not in self._no_reasoning_param:
            request["reasoning_effort"] = "none"
        retrying = AsyncRetrying(
            retry=retry_if_exception_type(_TransientError),
            wait=wait_exponential(multiplier=1.5, min=1, max=20),
            stop=stop_after_attempt(5),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    try:
                        return await self._post_once(request, budget, estimate, elapsed)
                    except _ReasoningParamRejectedError:
                        # Once per model: this server does not know the switch.
                        self._no_reasoning_param.add(model)
                        request.pop("reasoning_effort", None)
                        return await self._post_once(request, budget, estimate, elapsed)
        except _TransientError as exc:
            raise LmStudioUnavailableError(f"LM Studio sature ou ne répond plus : {exc}") from exc
        except _ReasoningParamRejectedError as exc:  # refused twice: not about the switch after all
            raise LmStudioResponseError(f"Requête refusée par LM Studio : {exc}") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def _post_once(
        self,
        request: dict[str, Any],
        budget: TokenBudget | None,
        estimate: int,
        elapsed: list[float],
    ) -> tuple[dict[str, Any], str]:
        async def post() -> httpx.Response:
            started = time.perf_counter()
            url, headers = self._to("/v1/chat/completions")
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
        raise _refusal(response.status_code, response.text, request)

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
        }
        if reasoning_off and model not in self._no_reasoning_param:
            request["reasoning_effort"] = "none"
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
                    try:
                        return await self._send_stream(request, limits)
                    except _ReasoningParamRejectedError:
                        self._no_reasoning_param.add(str(request["model"]))
                        request.pop("reasoning_effort", None)
                        return await self._send_stream(request, limits)
        except _TransientError as exc:
            raise LmStudioUnavailableError(f"LM Studio sature ou ne répond plus : {exc}") from exc
        except _ReasoningParamRejectedError as exc:
            raise LmStudioResponseError(f"Requête refusée par LM Studio : {exc}") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def _send_stream(self, request: dict[str, Any], limits: httpx.Timeout) -> httpx.Response:
        url, headers = self._to("/v1/chat/completions")
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
        raise _refusal(response.status_code, text, request)


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


def _refusal(status: int, text: str, request: dict[str, Any]) -> Exception:
    """What a non-200 answer means: a saturation worth retrying, a switch the server does not
    know, a model that is not loaded, or a refusal."""
    lowered = text.lower()
    # "failed to decode": the shared KV cache was full for a moment (other LM Studio clients
    # use it too); a retry gets through.
    if "mtmd chunk" in text or "failed to decode" in lowered or status in {429, 500, 502, 503}:
        return _TransientError(f"HTTP {status} : {text[:200]}")
    if status == 400 and "reasoning_effort" in request and "reasoning" in lowered:
        return _ReasoningParamRejectedError(text[:200])
    if status == 404 or "not loaded" in lowered or "no model" in lowered:
        return LmStudioUnavailableError(
            f"Modèle introuvable ou non chargé dans LM Studio : {request['model']}"
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
