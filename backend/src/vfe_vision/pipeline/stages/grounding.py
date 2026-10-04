"""Stage ``grounding``: the vision model boxes every living being of the keyframes.

The model already loaded in LM Studio is asked, frame by frame, for the living beings with their
boxes: it sees what the CPU detector does not know (insects, caterpillars, lizards…) and says
which one is the main subject. Only models whose box convention is known are asked: Qwen3-VL (a
verified prior), or a model a probe measured (done here the first time). The answers
are cached per image like the descriptions. Runs after the descriptions: they come first.
"""

from __future__ import annotations

import hashlib
import json
from functools import partial
from typing import Any

import anyio

from vfe_vision.adapters.imaging import encode_jpeg, read_image, resize_long_side
from vfe_vision.adapters.lmstudio import prompts
from vfe_vision.adapters.lmstudio.catalog import LoadedInstance, ModelInfo
from vfe_vision.adapters.lmstudio.client import ChatImage, LmStudioUnavailableError
from vfe_vision.adapters.lmstudio.schema import field_guide
from vfe_vision.core.errors import CancelledError, VfeError
from vfe_vision.db.models import Detection as DetectionRow
from vfe_vision.db.models import SubjectScan
from vfe_vision.domain.grounding import (
    GROUNDING_SCHEMA_VERSION,
    Grounding,
    GroundingYFirst,
    grounding_schema,
    label_examples,
    to_detections,
)
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.subjects import Source, dedupe
from vfe_vision.domain.vision_profile import BOX_SENTENCES, BoxField, prior_for
from vfe_vision.pipeline.stage import Resource, Stage, StageContext, StageFamily, StageOutcome
from vfe_vision.pipeline.stages.detections import (
    distinct_keyframes,
    keyframes_fact,
    replace_detections,
)
from vfe_vision.pipeline.stages.vision_frames import (
    GROUNDING_MULTIPLE,
    cached_response,
    pick_vision_model,
    run_requests,
    store_call,
)
from vfe_vision.pipeline.vision_profile import GroundingSetup, ensure_setup

PROMPT_NAME = "subjects"
PROMPT_VERSION = 3  # v2: label examples in the output language; v3: category guidance
MAX_TOKENS = 1200  # a crowd of twelve boxes is ~700 tokens


def subjects_prompt(language: str, box_field: BoxField) -> prompts.RenderedPrompt:
    """What the model is asked to locate the living beings of a frame (shared with the model
    bench: the same text, to the character, as the analyses send)."""
    return prompts.render(
        PROMPT_NAME, PROMPT_VERSION, language=language,
        label_examples=label_examples(language),
        field_guide=field_guide(grounding_schema(box_field)),
        box_sentence=BOX_SENTENCES[box_field],
    )  # fmt: skip


class GroundingStage(Stage):
    name = "grounding"
    version = 1
    family = StageFamily.VISION
    requires = ("keyframes",)
    after = ("vision_frames",)
    resource = Resource.LMSTUDIO
    optional = True
    facts_name_rows = True

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        return {
            "prompt": f"{PROMPT_NAME}.v{PROMPT_VERSION}",
            "schema": GROUNDING_SCHEMA_VERSION,
            "language": prefs.language,
            "long_side": prefs.vision_image_long_side,
        }

    async def resolve_config(self, ctx: StageContext) -> dict[str, Any]:
        picked = await pick_vision_model(ctx)
        if picked is None:
            return {"model": None}
        model = picked[0]
        setup = await self._setup(ctx, *picked)
        config: dict[str, Any] = {"model": model.key}
        if not setup.enabled:
            config["positions"] = "off"
        elif not setup.is_default or prior_for(model.key, model.architecture) is None:
            # Qwen3-VL keys stay as they were before profiles. Another model's measured
            # convention is always in the key: its old « convention inconnue » skip is not reused.
            config |= {"convention": setup.convention, "box_field": setup.box_field}
        return config

    @staticmethod
    async def _setup(
        ctx: StageContext, model: ModelInfo, instance: LoadedInstance
    ) -> GroundingSetup:
        """The prior or the measured profile; measured now the first time (a few seconds)."""
        try:
            return await ensure_setup(
                ctx.tools.db,
                ctx.tools.lmstudio,
                ctx.tools.lm_budget,
                model,
                instance,
                on_probe=lambda: ctx.progress(0.0, "Calibrage du modèle de vision"),
                cancel=ctx.cancel,
            )
        except (LmStudioUnavailableError, CancelledError):
            raise
        except VfeError as exc:
            raise VfeError(f"Calibrage du modèle de vision impossible : {exc.detail}") from exc

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        return keyframes_fact(ctx)

    async def execute(self, ctx: StageContext) -> StageOutcome:
        picked = await pick_vision_model(ctx)
        if picked is None:
            return StageOutcome.skipped(
                "LM Studio injoignable ou aucun modèle de vision chargé", retryable=True
            )
        model, instance = picked
        try:
            setup = await self._setup(ctx, model, instance)
        except CancelledError:
            raise
        except VfeError as exc:  # LM Studio gone, or unusable answers: try again next time
            return StageOutcome.skipped(exc.detail, retryable=True)
        if not setup.enabled or setup.convention is None:
            return StageOutcome.skipped(
                f"{model.key} : {setup.reason or 'positions non vérifiées'} ; "
                "recalibrez depuis la page Système"
            )
        convention = setup.convention
        await ctx.tools.lm_budget.resize(
            int((instance.context_length or 8192) * 0.9), instance.parallel or 1
        )
        frames = await anyio.to_thread.run_sync(distinct_keyframes, ctx)
        rows: list[DetectionRow] = []
        scans: list[SubjectScan] = []
        counts = {"located": 0, "cached": 0, "failed": 0}
        reasoning: list[int] = []  # thinking tokens despite reasoning_off
        unavailable: list[str] = []
        total = max(1, len(frames))

        async def locate(frame: tuple[str, float, str]) -> None:
            if ctx.cancel.cancelled or unavailable:
                return
            try:
                found, from_cache, size = await self._locate(
                    ctx, frame[2], model, instance, setup, reasoning=reasoning
                )
            except LmStudioUnavailableError as exc:
                unavailable.append(exc.detail)
                return
            except VfeError as exc:
                counts["failed"] += 1
                ctx.log.warning("grounding failed", t_s=frame[1], error=exc.detail)
                return
            except (OSError, ValueError) as exc:  # keyframe file missing or unreadable
                counts["failed"] += 1
                ctx.log.warning("grounding failed", t_s=frame[1], error=str(exc))
                return
            counts["cached" if from_cache else "located"] += 1
            beings = dedupe(to_detections(found, convention, *size))
            scans.append(
                SubjectScan(keyframe_id=frame[0], source=Source.VLM.value, video_id=ctx.video.id,
                            model=model.key, found=len(beings))
            )  # fmt: skip
            for idx, d in enumerate(beings):
                rows.append(
                    DetectionRow(
                        keyframe_id=frame[0], video_id=ctx.video.id, t_s=frame[1],
                        source=Source.VLM.value, idx=idx, label=d.label,
                        category=d.category.value, box=d.box.rounded(), score=None,
                        main=d.main, model=model.key,
                    )
                )  # fmt: skip
            done = sum(counts.values())
            ctx.progress(done / total, f"Sujets repérés sur {done}/{total} images")

        # Concurrency is bounded by the token budget and the loaded slots.
        await run_requests(
            ctx, [partial(locate, frame) for frame in frames], parallel=instance.parallel or 1
        )
        ctx.cancel.raise_if_cancelled()
        summary: dict[str, Any] = {
            "model": model.key,
            "frames": len(frames),
            "boxes": len(rows),
            **counts,
        }
        if sum(reasoning):
            summary["reasoning_tokens"] = sum(reasoning)
        if unavailable:
            return StageOutcome.skipped(unavailable[0], retryable=True, **summary)
        if frames and counts["failed"] == len(frames):
            raise VfeError(f"Aucune image n'a pu être analysée par {model.key}")
        rows.sort(key=lambda r: (r.t_s, r.idx))
        await anyio.to_thread.run_sync(replace_detections, ctx, {Source.VLM}, rows, scans)
        return StageOutcome.ok(**summary)

    async def _locate(
        self,
        ctx: StageContext,
        rel: str,
        model: ModelInfo,
        instance: LoadedInstance,
        setup: GroundingSetup,
        *,
        reasoning: list[int],
    ) -> tuple[Grounding | GroundingYFirst, bool, tuple[int, int]]:
        """The model's answer for one frame, whether it came from the cache, and the size of
        the image it was sent (pixel conventions are relative to it)."""
        output = grounding_schema(setup.box_field)
        image = await anyio.to_thread.run_sync(read_image, ctx.tools.artifacts.resolve(rel))
        image = resize_long_side(
            image, ctx.prefs.vision_image_long_side, multiple=GROUNDING_MULTIPLE
        )
        jpeg = encode_jpeg(image, quality=90)
        height, width = image.shape[:2]
        rendered = subjects_prompt(ctx.prefs.language, setup.box_field)
        key = hashlib.sha256(
            jpeg
            + json.dumps(
                [model.key, rendered.version, GROUNDING_SCHEMA_VERSION, rendered.system,
                 rendered.user],
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()  # fmt: skip
        cached = await anyio.to_thread.run_sync(cached_response, ctx, key)
        if cached is not None:
            return output.model_validate(cached), True, (width, height)
        result = await ctx.tools.lmstudio.chat_structured(
            model=instance.id,
            system=rendered.system,
            user_text=rendered.user,
            output=output,
            images=[ChatImage(jpeg, width, height)],
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            budget=ctx.tools.lm_budget,
            purpose=PROMPT_NAME,
            reasoning_off=bool(model.reasoning_options),
        )
        reasoning.append(result.reasoning_tokens or 0)
        await anyio.to_thread.run_sync(
            lambda: store_call(
                ctx, key, purpose=PROMPT_NAME, model_key=model.key,
                prompt_version=rendered.version, schema_version=GROUNDING_SCHEMA_VERSION,
                result=result,
            )
        )  # fmt: skip
        return result.data, False, (width, height)
