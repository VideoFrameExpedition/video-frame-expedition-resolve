"""Measure how the loaded vision model writes boxes, and keep what was measured.

Qwen3-VL needs no probe (a verified prior). For another model, the first grounding asks for a
probe: two synthetic scenes, two requests in parallel through the shared token budget, a few
seconds. The profile is stored per model file (``service_cache``) and redone after 30 days,
when the probe changes, or on request (System page, ``vfe doctor --vision``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import anyio
from pydantic import ValidationError

from vfe_vision.adapters.imaging import draw_probe_scene
from vfe_vision.adapters.lmstudio import prompts
from vfe_vision.adapters.lmstudio.budget import TokenBudget
from vfe_vision.adapters.lmstudio.catalog import LoadedInstance, ModelInfo
from vfe_vision.adapters.lmstudio.client import (
    ChatImage,
    LmStudioClient,
    LmStudioTruncatedError,
)
from vfe_vision.adapters.lmstudio.schema import field_guide
from vfe_vision.core.cancel import CancelToken, stopped_by
from vfe_vision.db.service_cache import cache_get, cache_put
from vfe_vision.db.session import Database
from vfe_vision.domain.vision_profile import (
    BOX_SENTENCES,
    PROBE_VERSION,
    SCENES,
    BoxConvention,
    BoxField,
    GroundingCalibration,
    ProbeCall,
    ProbeScene,
    VisionProfile,
    consistent,
    decide,
    fingerprint,
    pick,
    prior_for,
    probe_schema,
    raw_boxes,
)

SERVICE = "vision_profile"
PROMPT_NAME = "probe"
PROMPT_VERSION = 1
MAX_TOKENS = 600  # an answer is ~170 tokens
RETRY_MAX_TOKENS = 1500  # once, for an answer cut at MAX_TOKENS
MAX_AGE = timedelta(days=30)  # LM Studio runtime updates can change a model's behaviour
DEFAULT = (BoxConvention.XYXY_1000, BoxField.BBOX_2D)

_locks: dict[str, anyio.Lock] = {}


@dataclass(frozen=True, slots=True)
class GroundingSetup:
    """How to ask this model for positions, and how to read them."""

    enabled: bool
    convention: BoxConvention | None
    box_field: BoxField
    source: Literal["prior", "profile"]
    reason: str | None = None
    precise: bool | None = None

    @property
    def is_default(self) -> bool:
        return (self.convention, self.box_field) == DEFAULT


def model_fingerprint(model: ModelInfo) -> str:
    return fingerprint(model.key, model.architecture, model.quantization, model.size_bytes)


def _key(model: ModelInfo) -> str:
    return f"{SERVICE}:v{PROBE_VERSION}:{model_fingerprint(model)}"


def stored_profile(
    db: Database, model: ModelInfo, *, now: datetime | None = None
) -> VisionProfile | None:
    """The profile measured for this model file, while it is recent enough to trust."""
    cached = cache_get(db, _key(model))
    if cached is None:
        return None
    try:
        profile = VisionProfile.model_validate(cached.response)
    except ValidationError:
        return None
    if (now or datetime.now(UTC)) - profile.probed_at > MAX_AGE:
        return None
    return profile


def setup_from(calibration: GroundingCalibration) -> GroundingSetup:
    return GroundingSetup(
        enabled=calibration.enabled,
        convention=calibration.convention,
        box_field=calibration.box_field or BoxField.BBOX_2D,
        source="profile",
        reason=calibration.reason,
        precise=calibration.precise,
    )


def known_setup(db: Database, model: ModelInfo) -> GroundingSetup | None:
    """A measured profile first, else the verified prior; None: a probe is needed."""
    profile = stored_profile(db, model)
    if profile is not None:
        return setup_from(profile.grounding)
    prior = prior_for(model.key, model.architecture)
    if prior is not None:
        return GroundingSetup(enabled=True, convention=prior[0], box_field=prior[1], source="prior")
    return None


async def ensure_setup(
    db: Database,
    client: LmStudioClient,
    budget: TokenBudget,
    model: ModelInfo,
    instance: LoadedInstance,
    *,
    on_probe: Callable[[], None] | None = None,
    cancel: CancelToken | None = None,
) -> GroundingSetup:
    """The known setup, or one measured now (once per model file, whatever the concurrency)."""
    setup = await anyio.to_thread.run_sync(known_setup, db, model)
    if setup is not None:
        return setup
    lock = _locks.setdefault(model_fingerprint(model), anyio.Lock())
    async with lock:
        setup = await anyio.to_thread.run_sync(known_setup, db, model)  # measured meanwhile?
        if setup is not None:
            return setup
        if on_probe is not None:
            on_probe()
        profile = await probe(client, budget, model, instance, cancel=cancel)
        await anyio.to_thread.run_sync(store_profile, db, profile)
        return setup_from(profile.grounding)


async def measure(
    db: Database,
    client: LmStudioClient,
    budget: TokenBudget,
    model: ModelInfo,
    instance: LoadedInstance,
    *,
    cancel: CancelToken | None = None,
) -> VisionProfile:
    """Measure again on request (« Recalibrate », ``vfe doctor --vision --force``); an analysis
    probing the same model file meanwhile waits for this one."""
    async with _locks.setdefault(model_fingerprint(model), anyio.Lock()):
        profile = await probe(client, budget, model, instance, cancel=cancel)
        await anyio.to_thread.run_sync(store_profile, db, profile)
        return profile


def store_profile(db: Database, profile: VisionProfile) -> None:
    key = f"{SERVICE}:v{profile.probe_version}:{profile.fingerprint}"
    cache_put(db, key, SERVICE, profile.model_dump(mode="json"))


async def probe(
    client: LmStudioClient,
    budget: TokenBudget,
    model: ModelInfo,
    instance: LoadedInstance,
    *,
    cancel: CancelToken | None = None,
) -> VisionProfile:
    """Ask the loaded instance to box the shapes of both scenes, and decide (never loads a
    model: the instance is the one already loaded). A cancelled job stops at once."""
    await budget.resize(int((instance.context_length or 8192) * 0.9), instance.parallel or 1)
    started = datetime.now(UTC)
    rendered = [await anyio.to_thread.run_sync(draw_probe_scene, scene) for scene in SCENES]
    target = _Target(client, budget, model, instance)
    async with stopped_by(cancel):
        first = await _round(target, rendered, BoxField.BBOX_2D)
    calls = list(first)
    calibration = decide(first, BoxField.BBOX_2D)
    if not (calibration.enabled and consistent(calibration)) and not _reasoning_cut(first):
        # A y-first answer in the x-first field, or no usable answer: ask with box_2d. Not when
        # reasoning ate every answer's budget: another field name would not stop it.
        async with stopped_by(cancel):
            second = await _round(target, rendered, BoxField.BOX_2D)
        calls += second
        calibration = pick(calibration, decide(second, BoxField.BOX_2D))
    return VisionProfile(
        probe_version=PROBE_VERSION,
        fingerprint=model_fingerprint(model),
        model_key=model.key,
        display_name=model.display_name,
        architecture=model.architecture,
        quantization=model.quantization,
        size_bytes=model.size_bytes,
        probed_at=datetime.now(UTC),
        truncated=any(call.truncated for call in calls),
        reasoning_tokens_seen=sum(call.reasoning_tokens or 0 for call in calls),
        grounding=calibration,
        wall_ms=round((datetime.now(UTC) - started).total_seconds() * 1000),
        prompt_tokens=sum(call.prompt_tokens or 0 for call in calls),
        completion_tokens=sum(call.completion_tokens or 0 for call in calls),
        calls=calls,
    )


def _reasoning_cut(calls: list[ProbeCall]) -> bool:
    return bool(calls) and all(call.truncated and call.reasoning_tokens for call in calls)


@dataclass(frozen=True, slots=True)
class _Target:
    client: LmStudioClient
    budget: TokenBudget
    model: ModelInfo
    instance: LoadedInstance


_Rendered = tuple[ProbeScene, bytes, list[tuple[float, float, float, float]]]


async def _round(
    target: _Target, rendered: list[_Rendered], box_field: BoxField
) -> list[ProbeCall]:
    calls: list[ProbeCall | None] = [None] * len(rendered)
    errors: list[Exception] = []

    async def ask(index: int) -> None:
        try:
            calls[index] = await _ask(target, rendered[index], box_field)
        except Exception as exc:  # noqa: BLE001 - re-raised below, unwrapped from the group
            errors.append(exc)

    async with anyio.create_task_group() as group:  # both scenes at once (2 slots)
        for index in range(len(rendered)):
            group.start_soon(ask, index)
    if errors:
        raise errors[0]
    return [call for call in calls if call is not None]


async def _ask(target: _Target, item: _Rendered, box_field: BoxField) -> ProbeCall:
    client, budget, model, instance = target.client, target.budget, target.model, target.instance
    scene, jpeg, truth = item
    output = probe_schema(box_field)
    rendered = prompts.render(
        PROMPT_NAME,
        PROMPT_VERSION,
        objects=scene.object_list,
        box_sentence=BOX_SENTENCES[box_field],
        field_guide=field_guide(output),
    )
    unanswered = ProbeCall(
        scene=scene.name, box_field=box_field, width=scene.width, height=scene.height,
        truth=[list(t) for t in truth], raw_boxes=[],
    )  # fmt: skip
    cut: dict[str, int] = {}  # usage of the cut answers: reasoning may have eaten the budget
    for max_tokens in (MAX_TOKENS, RETRY_MAX_TOKENS):
        try:
            result = await client.chat_structured(
                model=instance.id,
                system=rendered.system,
                user_text=rendered.user,
                output=output,
                images=[ChatImage(jpeg, scene.width, scene.height)],
                max_tokens=max_tokens,
                temperature=0.0,
                budget=budget,
                purpose="probe",
                reasoning_off=bool(model.reasoning_options),
            )
        except LmStudioTruncatedError as exc:
            for name in ("reasoning_tokens", "prompt_tokens", "completion_tokens"):
                if isinstance(value := exc.extra.get(name), int):
                    cut[name] = cut.get(name, 0) + value
            continue  # once more with room; a second cut is a verdict
        return unanswered.model_copy(
            update={
                "raw_boxes": raw_boxes(result.data),
                "finish_reason": result.finish_reason,
                "reasoning_tokens": _plus(result.reasoning_tokens, cut.get("reasoning_tokens")),
                "prompt_tokens": _plus(result.prompt_tokens, cut.get("prompt_tokens")),
                "completion_tokens": _plus(result.completion_tokens, cut.get("completion_tokens")),
                "latency_ms": result.latency_ms,
            }
        )
    return unanswered.model_copy(update={"finish_reason": "length", "truncated": True, **cut})


def _plus(value: int | None, earlier: int | None) -> int | None:
    """A count over both attempts (the first one may have been cut)."""
    return value if earlier is None else (value or 0) + earlier


def _fr(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")


def summary(profile: VisionProfile) -> str:
    """One line for the job list and the CLI."""
    grounding = profile.grounding
    if grounding.enabled:
        order = (
            "[y1, x1, y2, x2]"
            if grounding.convention and grounding.convention.y_first
            else ("[x1, y1, x2, y2]")
        )
        return (
            f"{profile.display_name} : positions vérifiées, {order} ({grounding.convention}), "
            f"IoU {_fr(grounding.mean_iou)}, en {_fr(profile.wall_ms / 1000, 1)} s"
        )
    return f"{profile.display_name} : positions désactivées, {grounding.reason}"
