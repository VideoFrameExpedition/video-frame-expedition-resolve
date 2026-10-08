"""Stage ``translation``: every text of the analyses in French and in English.

The texts written by the models (frame descriptions, shot stories, synthesis, names of the
living beings) and the place names are translated by the model already loaded in LM Studio, a few
dozen texts of one kind per request, and kept in the dictionary (``translations``), never in
the analysis rows: no stage reading them runs again. A text known already (another video, an
earlier run) is never asked for again, so a video analysed again only costs its new texts.

Two passes: each text in the other language of its row; then a text given back unchanged (the
model had written it in the wrong language, or it is the same word in both) in its row's
language, so that each language reads entirely in that language.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import cache, partial
from typing import Any

import anyio
from pydantic import BaseModel, ConfigDict, create_model

from vfe_vision.adapters.lmstudio import prompts
from vfe_vision.adapters.lmstudio.budget import estimate_text_tokens, slot_tokens
from vfe_vision.adapters.lmstudio.catalog import LoadedInstance, ModelInfo
from vfe_vision.adapters.lmstudio.client import (
    LmStudioResponseError,
    LmStudioTruncatedError,
    LmStudioUnavailableError,
)
from vfe_vision.core.errors import VfeError
from vfe_vision.db.models import ContextPlace, VideoSynthesis
from vfe_vision.db.translations import known_entries, store_entries, video_texts
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.translation import (
    LANGUAGES,
    Batch,
    SourceText,
    Wanted,
    batches,
    plausible,
    unchanged,
    wanted,
)
from vfe_vision.pipeline.stage import Resource, Stage, StageContext, StageFamily, StageOutcome
from vfe_vision.pipeline.stages.vision_frames import (
    pick_vision_model,
    run_requests,
    store_call,
)

PROMPT_NAME = "translation"
PROMPT_VERSION = 1
SCHEMA_VERSION = 1
SYSTEM_TOKENS = 350  # the instructions, about the same for every request
MIN_CHARS, MAX_CHARS = 800, 4_000  # text per request: a slot's share of the context allows it
BROKEN_AFTER = 10  # that many texts asked and none translated: the model cannot do it
RETRY_TEMPERATURE = 0.4  # a text still answered in another script is asked once more, varied


@cache
def answer_model(count: int) -> type[BaseModel]:
    """``{"t1": str, …, "tN": str}``: one translation per text sent, under its key."""
    fields: dict[str, Any] = {f"t{i}": (str, ...) for i in range(1, count + 1)}
    model: type[BaseModel] = create_model(
        f"Translations{count}", __config__=ConfigDict(extra="forbid"), **fields
    )
    return model


def batch_chars(instance: LoadedInstance) -> int:
    """How much text one request may hold: its prompt and its answer (about as long) fit a
    slot's share of the loaded context, so the parallel requests all fit together."""
    share = slot_tokens(instance.context_length, instance.parallel) - SYSTEM_TOKENS
    return max(MIN_CHARS, min(MAX_CHARS, share * 3 // 2 - 200))


def digest(sources: list[SourceText]) -> str:
    blob = json.dumps([[s.text, s.kind, s.language] for s in sources], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _Counters:
    asked: int = 0
    changed: int = 0
    unchanged: int = 0
    failed: int = 0
    requests: int = 0
    reasoning_tokens: int = 0
    unavailable: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Run:
    """What every request of a run shares: the loaded model and what the texts are about."""

    model: ModelInfo
    instance: LoadedInstance
    context: dict[str, str | None]
    counters: _Counters = field(default_factory=_Counters)


class TranslationStage(Stage):
    name = "translation"
    version = 1
    family = StageFamily.LANGUAGE
    # Every stage whose texts it translates; none is needed: it translates what there is.
    after = ("place", "vision_frames", "grounding", "vision_shots", "synthesis")
    resource = Resource.LMSTUDIO
    optional = True
    # The dictionary is not in the analysis file: the two files of a video (one per language)
    # give it back when imported, and the stage then finds every text known.
    rebuilt_after_import = True

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        return {"prompt": f"{PROMPT_NAME}.v{PROMPT_VERSION}", "languages": list(LANGUAGES)}

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        with ctx.tools.db.read() as session:
            sources = video_texts(session, ctx.video.id)
        return {"texts": digest(sources), "count": len(sources)}

    async def resolve_config(self, ctx: StageContext) -> dict[str, Any]:
        picked = await pick_vision_model(ctx)
        return {"model": picked[0].key if picked else None}

    async def execute(self, ctx: StageContext) -> StageOutcome:
        sources = await anyio.to_thread.run_sync(self._sources, ctx)
        if not sources:
            return StageOutcome.ok(texts=0)
        texts = [source.text for source in sources]
        known = await anyio.to_thread.run_sync(self._known, ctx, texts)
        first, second = wanted(sources, known)
        if not first and not second:
            return StageOutcome.ok(texts=len(set(texts)), asked=0)
        picked = await pick_vision_model(ctx)
        if picked is None:
            return StageOutcome.waiting_for_lmstudio("LM Studio injoignable ou aucun modèle chargé")
        model, instance = picked
        await ctx.tools.lm_budget.resize(
            int((instance.context_length or 8192) * 0.9), instance.parallel or 1
        )
        run = _Run(model, instance, await anyio.to_thread.run_sync(self._context, ctx))
        counters = run.counters
        await self._run_pass(ctx, first, run)
        ctx.cancel.raise_if_cancelled()
        if not counters.unavailable:  # then what the first pass gave back unchanged
            known = await anyio.to_thread.run_sync(self._known, ctx, texts)
            second = wanted(sources, known)[1]
            await self._run_pass(ctx, second, run)
            ctx.cancel.raise_if_cancelled()
        summary: dict[str, Any] = {
            "model": model.key,
            "texts": len(set(texts)),
            "asked": counters.asked,
            "translated": counters.changed,
            "unchanged": counters.unchanged,
            "failed": counters.failed,
            "requests": counters.requests,
        }
        if counters.reasoning_tokens:
            summary["reasoning_tokens"] = counters.reasoning_tokens
        if counters.unavailable:
            return StageOutcome.skipped(counters.unavailable[0], retryable=True, **summary)
        if counters.asked >= BROKEN_AFTER and counters.failed == counters.asked:
            raise VfeError(f"Aucun texte n'a pu être traduit par {model.key}")
        if counters.failed:  # shown as written meanwhile; asked again at the next analysis
            return StageOutcome.degraded(
                f"{counters.failed} texte(s) resté(s) non traduit(s)", **summary
            )
        return StageOutcome.ok(**summary)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _sources(ctx: StageContext) -> list[SourceText]:
        with ctx.tools.db.read() as session:
            return video_texts(session, ctx.video.id)

    @staticmethod
    def _known(ctx: StageContext, texts: list[str]) -> dict[tuple[str, str], str]:
        with ctx.tools.db.read() as session:
            return known_entries(session, texts)

    @staticmethod
    def _context(ctx: StageContext) -> dict[str, str | None]:
        """What the texts are about, for the model: the video's name, title and place."""
        with ctx.tools.db.read() as session:
            synthesis = session.get(VideoSynthesis, ctx.video.id)
            place = session.get(ContextPlace, ctx.video.id)
            title = (synthesis.data or {}).get("title") if synthesis is not None else None
            return {
                "filename": ctx.video.filename,
                "title": title if isinstance(title, str) and title.strip() else None,
                "place": place.label if place is not None else None,
            }

    async def _run_pass(self, ctx: StageContext, items: list[Wanted], run: _Run) -> None:
        if not items:
            return
        counters = run.counters
        counters.asked += len(items)
        todo = batches(items, max_chars=batch_chars(run.instance))
        total = len(items)
        done = [0]

        async def translate(batch: Batch) -> None:
            if ctx.cancel.cancelled or counters.unavailable:
                return
            try:
                await self._translate(ctx, batch, run)
            except LmStudioUnavailableError as exc:
                counters.unavailable.append(exc.detail)
                return
            done[0] += len(batch.items)
            ctx.progress(min(1.0, done[0] / total), f"Textes traduits : {done[0]}/{total}")

        await run_requests(
            ctx, [partial(translate, batch) for batch in todo], parallel=run.instance.parallel or 1
        )

    async def _translate(
        self, ctx: StageContext, batch: Batch, run: _Run, *, temperature: float = 0.0
    ) -> None:
        """One request; a cut or unreadable answer is asked again in two halves, down to one
        text (which then stays untranslated). A text answered with a word of another script is
        asked again on its own, then once more with some variety, then left untranslated."""
        counters = run.counters
        try:
            answers = await self._ask(ctx, batch, run, temperature=temperature)
        except (LmStudioTruncatedError, LmStudioResponseError) as exc:
            if len(batch.items) == 1:
                counters.failed += 1
                ctx.log.warning("text not translated", error=exc.detail, target=batch.target)
                return
            half = len(batch.items) // 2
            for part in (batch.items[:half], batch.items[half:]):
                await self._translate(ctx, Batch(batch.target, batch.kind, part), run)
            return
        entries: list[tuple[str, str, str]] = []
        again: list[Wanted] = []
        for item, answer in zip(batch.items, answers, strict=True):
            if not plausible(item.text, answer):
                again.append(item)
                continue
            same = unchanged(item.text, answer)
            counters.unchanged += int(same)
            counters.changed += int(not same)
            entries.append((item.text, batch.target, item.text if same else answer.strip()))
        await anyio.to_thread.run_sync(self._store, ctx, entries, run.model.key)
        if len(batch.items) == 1:
            if again and temperature == 0.0:
                await self._translate(ctx, batch, run, temperature=RETRY_TEMPERATURE)
            elif again:
                counters.failed += 1
                ctx.log.warning("text not translated", target=batch.target, kind=batch.kind)
            return
        for item in again:  # asked again on its own, away from the texts that misled it
            await self._translate(ctx, Batch(batch.target, batch.kind, (item,)), run)

    async def _ask(
        self, ctx: StageContext, batch: Batch, run: _Run, *, temperature: float
    ) -> list[str]:
        model, counters = run.model, run.counters
        payload = {f"t{i}": item.text.strip() for i, item in enumerate(batch.items, 1)}
        rendered = prompts.render(
            PROMPT_NAME, PROMPT_VERSION, language=batch.target, kind=batch.kind.value,
            texts=json.dumps(payload, ensure_ascii=False, indent=0), **run.context,
        )  # fmt: skip
        joined = "".join(payload.values())
        max_tokens = int(estimate_text_tokens(joined) * 1.5) + 12 * len(payload) + 64
        output = answer_model(len(payload))
        result = await ctx.tools.lmstudio.chat_structured(
            model=run.instance.id,
            system=rendered.system,
            user_text=rendered.user,
            output=output,
            max_tokens=max_tokens,
            temperature=temperature,
            budget=ctx.tools.lm_budget,
            purpose="translation",
            reasoning_off=bool(model.reasoning_options),
        )
        counters.requests += 1
        counters.reasoning_tokens += result.reasoning_tokens or 0
        key = hashlib.sha256(
            json.dumps(
                [
                    model.key,
                    rendered.version,
                    SCHEMA_VERSION,
                    rendered.system,
                    rendered.user,
                    temperature,
                ],
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        await anyio.to_thread.run_sync(
            lambda: store_call(
                ctx, key, purpose="translation", model_key=model.key,
                prompt_version=rendered.version, schema_version=SCHEMA_VERSION, result=result,
            )
        )  # fmt: skip
        data = result.data.model_dump()
        return [str(data[f"t{i}"]) for i in range(1, len(payload) + 1)]

    @staticmethod
    def _store(ctx: StageContext, entries: list[tuple[str, str, str]], model: str) -> None:
        if entries:
            with ctx.tools.db.write() as session:
                store_entries(session, entries, model=model)
