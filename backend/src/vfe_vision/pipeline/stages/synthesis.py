"""Stage ``synthesis``: the title, summary, chapters and highlight moments of a video.

The application decides the structure, the language model only writes the texts:
- code cuts the video into blocks (shots, ~20 s pieces of long shots), measures how usable each
  one is, cuts chapters (exact dynamic programming over the block contents), ranks the
  highlight blocks, and writes the lean input (descriptions, sounds that pass the gate, speech);
- the model writes a title, a logline, a summary, one title and sentence per chapter, one reason
  per chosen moment, and tags. It never sees or writes a time;
- a proofreading pass fixes its spelling (guarded), code puts titles in sentence case and merges
  the tags.

A video whose input is too long for one request is summarised chapter by chapter (map), then as
a whole (reduce). Everything goes through the loaded instance and the shared token budget.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import anyio
from pydantic import BaseModel

from vfe_vision.adapters.lmstudio import prompts
from vfe_vision.adapters.lmstudio.budget import estimate_text_tokens
from vfe_vision.adapters.lmstudio.catalog import LoadedInstance, ModelInfo
from vfe_vision.adapters.lmstudio.client import LmStudioUnavailableError
from vfe_vision.adapters.lmstudio.schema import field_guide
from vfe_vision.core.errors import VfeError
from vfe_vision.db.models import VideoSynthesis
from vfe_vision.domain.blocks import synthesis_blocks
from vfe_vision.domain.chapters import CHAPTER_RULES_VERSION, chapter_ranges
from vfe_vision.domain.editing import (
    EDITING_RULES_VERSION,
    allowed_blocks,
    highlight_count,
    pick_highlights,
)
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.sound_names import sound_name
from vfe_vision.domain.synthesis_input import Block
from vfe_vision.domain.synthesis_input import Video as VideoFacts
from vfe_vision.domain.synthesis_schema import (
    SYNTHESIS_SCHEMA_VERSION,
    ChapterPlan,
    draft_model,
    map_model,
    moment_field,
    proof_model,
    reduce_model,
)
from vfe_vision.domain.synthesis_text import (
    LANGUAGE_HINT,
    SOUND_GATE_VERSION,
    TEXT_RULES_VERSION,
    merge_tags,
    proper_nouns,
    render_input,
    sentence_case,
    sound_in_prompt,
    strip_absences,
    strip_block_reference,
)
from vfe_vision.domain.usability import (
    USABILITY_RULES_VERSION,
    block_usability,
    usability_by_shot,
)
from vfe_vision.domain.weather_consensus import (
    WEATHER_RULES_VERSION,
    prompt_weather,
    weather_consensus,
)
from vfe_vision.pipeline.stage import Resource, Stage, StageContext, StageFamily, StageOutcome
from vfe_vision.pipeline.stages.vision_frames import (
    cached_response,
    pick_vision_model,
    run_requests,
    store_call,
)
from vfe_vision.pipeline.synthesis_facts import load_facts

# v2: never about the analysis (« les descriptions confirment… »), never an absence (« aucune
# parole n'est entendue »: 14 summaries of 22 with v1), no duration.
PROMPT_VERSION = 2
RULES_VERSION = (
    f"u{USABILITY_RULES_VERSION}.w{WEATHER_RULES_VERSION}.c{CHAPTER_RULES_VERSION}"
    f".e{EDITING_RULES_VERSION}.s{SOUND_GATE_VERSION}.t{TEXT_RULES_VERSION}"
)
TEMPERATURE = 0.1
MAX_SINGLE_PROMPT = 9_000  # estimated tokens: the largest single pass verified, with qwen3-vl-4b
FRAME_RESERVATION = 2_100  # what a frame description holds in the shared budget meanwhile
MAP_MAX_TOKENS = 600
REDUCE_MAX_TOKENS = 700
PROOF_MAX_TOKENS = 1_200
PROOF_MIN_KEPT = 0.7  # a « corrected » text keeping fewer of its words is not a correction
PROOFREAD = True


@dataclass(slots=True)
class _Plan:
    """What code decided before any request."""

    facts: VideoFacts
    language: str
    blocks: list[Block]
    usable: dict[int, int]
    chapters: list[ChapterPlan]
    weather_line: str | None
    full: str  # V4: every distinct description and the full speech
    compact: str  # V4c: one line per block, speech clipped

    @property
    def moments(self) -> list[int]:
        return [n for c in self.chapters for n in c.moments]

    def key(self) -> str:
        payload = json.dumps(
            [self.full, [[c.first, c.last, list(c.moments)] for c in self.chapters]],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _Texts:
    title: str = ""
    logline: str = ""
    summary: str = ""
    chapters: list[tuple[str, str]] = field(default_factory=list)  # (title, sentence)
    moments: dict[int, str] = field(default_factory=dict)  # block → reason
    tags: list[str] = field(default_factory=list)  # as written by the model
    merged_tags: list[tuple[str, str]] = field(default_factory=list)  # (tag, source)


@dataclass(slots=True)
class _Usage:
    requests: int = 0
    cached: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


def build_plan(facts: VideoFacts, language: str) -> _Plan:
    """Blocks, usability, chapters, highlight moments and the model input, all by code."""
    blocks = synthesis_blocks(facts)
    by_shot = usability_by_shot(facts)
    usable = {b.no: block_usability(facts, b, by_shot=by_shot).score for b in blocks}
    ranges = chapter_ranges(facts, blocks)
    allowed = allowed_blocks(blocks, usable)
    count = highlight_count(facts.duration, sum(1 for n in allowed if usable.get(n, 0) >= 50))
    picks = pick_highlights(blocks, ranges, usable, count)
    chapters = [
        ChapterPlan(first=a, last=z, moments=tuple(picks.get(index, [])))
        for index, (a, z) in enumerate(ranges)
    ]
    weather_line = prompt_weather(weather_consensus(facts))
    render = partial(render_input, facts, blocks, ranges, usable, weather_line=weather_line,
                     language=language)  # fmt: skip
    return _Plan(
        facts=facts,
        language=language,
        blocks=blocks,
        usable=usable,
        chapters=chapters,
        weather_line=weather_line,
        full=render(),
        compact=render(compact=True),
    )


def input_key(facts: VideoFacts, language: str) -> str:
    """What the synthesis is written from: a new description, transcript, sound or place, or a
    new rule, changes it (and makes a stored synthesis « out of date »)."""
    return build_plan(facts, language).key()


class SynthesisStage(Stage):
    name = "synthesis"
    version = 1
    family = StageFamily.VISION
    requires = ("keyframes",)
    # Everything it summarises; the transcript is the longest of them to arrive.
    after = (
        "analysis_pass", "audio_levels", "audio_events", "place", "sun", "weather",
        "vision_frames", "grounding", "detections", "ocr", "transcript", "vision_shots",
    )  # fmt: skip
    resource = Resource.LMSTUDIO
    optional = True
    facts_read_texts = True  # the descriptions, stories and place it summarises

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        return {
            "prompt": f"synthesis.v{PROMPT_VERSION}",
            "schema": SYNTHESIS_SCHEMA_VERSION,
            "language": prefs.language,
            "rules": RULES_VERSION,
            "proofread": PROOFREAD,
            "single_max": MAX_SINGLE_PROMPT,
        }

    async def resolve_config(self, ctx: StageContext) -> dict[str, Any]:
        picked = await pick_vision_model(ctx)
        return {"model": picked[0].key if picked else None}

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        facts = load_facts(ctx.tools.db, ctx.video.id)
        return {"input": input_key(facts, ctx.prefs.language)}

    def precheck(self, ctx: StageContext) -> StageOutcome | None:
        """A video with nothing to summarise never waits for the language model."""
        return _nothing_to_summarise(load_facts(ctx.tools.db, ctx.video.id))

    async def execute(self, ctx: StageContext) -> StageOutcome:
        facts = await anyio.to_thread.run_sync(load_facts, ctx.tools.db, ctx.video.id)
        if (skip := _nothing_to_summarise(facts)) is not None:
            return skip
        picked = await pick_vision_model(ctx)
        if picked is None:
            return StageOutcome.waiting_for_lmstudio("LM Studio injoignable ou aucun modèle chargé")
        model, instance = picked
        language = ctx.prefs.language
        plan = await anyio.to_thread.run_sync(build_plan, facts, language)
        capacity = int((instance.context_length or 8192) * 0.9)
        await ctx.tools.lm_budget.resize(capacity, instance.parallel or 1)
        ask = _Asker(ctx, model, instance)
        try:
            texts, strategy, variant = await _write(ask, plan, capacity)
            if PROOFREAD:
                await _proofread(ask, texts, language)
        except LmStudioUnavailableError as exc:
            return StageOutcome.skipped(exc.detail, retryable=True)
        _tidy(texts, plan)
        ctx.cancel.raise_if_cancelled()
        usage = ask.usage
        row = _row(plan, texts, model, strategy=strategy, variant=variant, usage=usage)
        await anyio.to_thread.run_sync(_store, ctx, row)
        summary: dict[str, Any] = {
            "model": model.key, "strategy": strategy, "input": variant,
            "blocks": len(plan.blocks), "chapters": len(plan.chapters),
            "moments": len(plan.moments), "requests": usage.requests, "cached": usage.cached,
        }  # fmt: skip
        if usage.reasoning_tokens:
            summary["reasoning_tokens"] = usage.reasoning_tokens
        return StageOutcome.ok(**summary)


def _nothing_to_summarise(facts: VideoFacts) -> StageOutcome | None:
    if not facts.shots:
        return StageOutcome.skipped("Aucun plan détecté : rien à résumer")
    if not any(f.data for f in facts.frames) and not facts.segments:
        return StageOutcome.skipped("Ni image décrite ni parole : rien à résumer")
    return None


# ---------------------------------------------------------------- requests
@dataclass(slots=True)
class _Asker:
    """Structured requests to the loaded instance, answered from the cache when asked before,
    counted in ``usage``."""

    ctx: StageContext
    model: ModelInfo
    instance: LoadedInstance
    usage: _Usage = field(default_factory=_Usage)

    async def __call__(
        self,
        rendered: prompts.RenderedPrompt,
        output: type[BaseModel],
        *,
        max_tokens: int,
        temperature: float = TEMPERATURE,
    ) -> BaseModel:
        ctx, model, usage = self.ctx, self.model, self.usage
        schema = json.dumps(output.model_json_schema(), sort_keys=True)
        material = [model.key, rendered.version, SYNTHESIS_SCHEMA_VERSION, rendered.system,
                    rendered.user, schema, temperature]  # fmt: skip
        key = hashlib.sha256(json.dumps(material, ensure_ascii=False).encode("utf-8")).hexdigest()
        cached = await anyio.to_thread.run_sync(cached_response, ctx, key)
        if cached is not None:
            usage.cached += 1
            return output.model_validate(cached)
        purpose = rendered.version.split(".", 1)[0]  # synthesis, synthesis_map…
        result = await ctx.tools.lmstudio.chat_structured(
            model=self.instance.id,
            system=rendered.system,
            user_text=rendered.user,
            output=output,
            max_tokens=max_tokens,
            temperature=temperature,
            budget=ctx.tools.lm_budget,
            purpose=purpose,
            reasoning_off=bool(model.reasoning_options),
        )
        usage.requests += 1
        usage.prompt_tokens += result.prompt_tokens or 0
        usage.completion_tokens += result.completion_tokens or 0
        usage.reasoning_tokens += result.reasoning_tokens or 0
        await anyio.to_thread.run_sync(
            partial(
                store_call, ctx, key, purpose=purpose, model_key=model.key,
                prompt_version=rendered.version, schema_version=SYNTHESIS_SCHEMA_VERSION,
                result=result,
            )
        )  # fmt: skip
        return result.data


def _render(
    name: str, language: str, output: type[BaseModel], **context: Any
) -> prompts.RenderedPrompt:
    return prompts.render(
        name,
        PROMPT_VERSION,
        language=language,
        language_hint=LANGUAGE_HINT.get(language, ""),
        field_guide=field_guide(output),
        **context,
    )


def _estimate(rendered: prompts.RenderedPrompt) -> int:
    return estimate_text_tokens(rendered.system) + estimate_text_tokens(rendered.user)


async def _write(ask: _Asker, plan: _Plan, capacity: int) -> tuple[_Texts, str, str]:
    """One request when the input fits, else chapter by chapter then the whole (map-reduce)."""
    moments = len(plan.moments)
    max_tokens = min(1_800, round(1.5 * (250 + 60 * len(plan.chapters) + 50 * moments)))
    cap = min(MAX_SINGLE_PROMPT, capacity - FRAME_RESERVATION - max_tokens)
    output = draft_model(plan.chapters)
    for variant, data in (("V4", plan.full), ("V4c", plan.compact)):
        rendered = _render(
            "synthesis", plan.language, output, data=data, chapters=len(plan.chapters),
            moments=moments > 0,
        )  # fmt: skip
        if _estimate(rendered) <= cap:
            answer = await ask(rendered, output, max_tokens=max_tokens)
            return _from_draft(answer.model_dump(), plan), "single", variant
    return await _map_reduce(ask, plan, cap), "map_reduce", "V4"


def _from_draft(data: dict[str, Any], plan: _Plan) -> _Texts:
    texts = _Texts(
        title=str(data.get("title", "")),
        logline=str(data.get("logline", "")),
        summary=str(data.get("summary", "")),
        tags=[str(t) for t in data.get("tags") or []],
    )
    entries = data.get("chapters") or {}
    for index, chapter in enumerate(plan.chapters, 1):
        entry = entries.get(f"C{index}") or {}
        texts.chapters.append((str(entry.get("title", "")), str(entry.get("summary", ""))))
        for k, block in enumerate(chapter.moments, 1):
            texts.moments[block] = str(entry.get(moment_field(k), ""))
    return texts


@dataclass(frozen=True, slots=True)
class _MapJob:
    chapter: int  # index from 0
    part: ChapterPlan
    rendered: prompts.RenderedPrompt


def _map_jobs(plan: _Plan, cap: int) -> list[_MapJob]:
    """Each chapter as one request, split into consecutive block ranges while too long; a
    single block that still does not fit goes compact as it is."""
    jobs: list[_MapJob] = []
    for index, chapter in enumerate(plan.chapters):
        queue = [chapter]
        while queue:
            part = queue.pop(0)
            if (rendered := _map_input(plan, part, index, cap=cap)) is not None:
                jobs.append(_MapJob(index, part, rendered))
            elif part.first < part.last:
                middle = (part.first + part.last) // 2
                left = ChapterPlan(
                    part.first, middle, tuple(m for m in part.moments if m <= middle)
                )
                right = ChapterPlan(
                    middle + 1, part.last, tuple(m for m in part.moments if m > middle)
                )
                queue = [left, right, *queue]
            else:
                jobs.append(_MapJob(index, part, _map_prompt(plan, part, index, compact=True)))
    return jobs


async def _map_reduce(ask: _Asker, plan: _Plan, cap: int) -> _Texts:
    """Each chapter alone, then the whole video from the chapter digests (no highlights at
    that step)."""
    jobs = _map_jobs(plan, cap)
    digests: dict[tuple[int, int], dict[str, Any]] = {}
    unavailable: list[LmStudioUnavailableError] = []

    async def one(job: _MapJob) -> None:
        try:
            answer = await ask(job.rendered, map_model(job.part), max_tokens=MAP_MAX_TOKENS)
        except LmStudioUnavailableError as exc:
            unavailable.append(exc)
            return
        digests[(job.chapter, job.part.first)] = answer.model_dump()

    await run_requests(ask.ctx, [partial(one, job) for job in jobs], parallel=2)
    ask.ctx.cancel.raise_if_cancelled()
    if unavailable:
        raise unavailable[0]
    texts = _Texts()
    lines = [plan.full.split("\n\n", 1)[0], "", "CHAPTERS (written by a first pass)"]
    for index in range(len(plan.chapters)):
        own = [job for job in jobs if job.chapter == index]
        parts = [digests[(index, j.part.first)] for j in own if (index, j.part.first) in digests]
        if len(parts) < len(own) or not parts:
            raise VfeError(f"Chapitre {index + 1} non résumé")
        title = str(parts[0].get("title", ""))
        sentence = " ".join(str(p.get("summary", "")) for p in parts)
        texts.chapters.append((title, sentence))
        for job, digest in zip(own, parts, strict=True):
            for k, block in enumerate(job.part.moments, 1):
                texts.moments[block] = str(digest.get(moment_field(k), ""))
        tags = dict.fromkeys(str(t) for p in parts for t in p.get("tags") or [])
        lines += [f"Chapter {index + 1}: {title}", f"  {sentence}", "  tags: " + ", ".join(tags)]
    output = reduce_model()
    rendered = _render("synthesis_reduce", plan.language, output, data="\n".join(lines))
    whole = (await ask(rendered, output, max_tokens=REDUCE_MAX_TOKENS)).model_dump()
    texts.title = str(whole.get("title", ""))
    texts.logline = str(whole.get("logline", ""))
    texts.summary = str(whole.get("summary", ""))
    texts.tags = [str(t) for t in whole.get("tags") or []]
    return texts


def _map_input(
    plan: _Plan, part: ChapterPlan, index: int, *, cap: int
) -> prompts.RenderedPrompt | None:
    """The map request for a block range, lean first then compact; None when neither fits."""
    for compact in (False, True):
        rendered = _map_prompt(plan, part, index, compact=compact)
        if _estimate(rendered) <= cap:
            return rendered
    return None


def _map_prompt(
    plan: _Plan, part: ChapterPlan, index: int, *, compact: bool
) -> prompts.RenderedPrompt:
    blocks = [b for b in plan.blocks if part.first <= b.no <= part.last]
    body = render_input(
        plan.facts, blocks, [(part.first, part.last)], plan.usable,
        weather_line=plan.weather_line, language=plan.language, compact=compact,
    )  # fmt: skip
    where = f"Part {index + 1} of {len(plan.chapters)} of the video"
    data = f"{where} (blocks B{part.first}–B{part.last}).\n{body}"
    return _render(
        "synthesis_map", plan.language, map_model(part), data=data, moments=bool(part.moments)
    )


async def _proofread(ask: _Asker, texts: _Texts, language: str) -> None:
    """Fix the model's own spelling and agreement, keeping any text the pass damaged."""
    items: list[str] = [texts.title, texts.logline, texts.summary]
    for title, sentence in texts.chapters:
        items += [title, sentence]
    blocks = list(texts.moments)
    items += [texts.moments[b] for b in blocks]
    slots = [i for i, text in enumerate(items) if text.strip()]
    if not slots:
        return
    output = proof_model(len(slots))
    rendered = prompts.render(
        "synthesis_proof", PROMPT_VERSION, language=language, texts=[items[i] for i in slots]
    )
    try:
        answer = await ask(rendered, output, max_tokens=PROOF_MAX_TOKENS, temperature=0.0)
    except LmStudioUnavailableError:
        raise
    except VfeError:
        return  # an unusable answer: the texts stay as written
    fixed = [str(t) for t in answer.model_dump().get("texts") or []]
    if len(fixed) != len(slots):
        return
    for slot, new in zip(slots, fixed, strict=True):
        if _kept(items[slot], new) >= PROOF_MIN_KEPT:
            items[slot] = new
    texts.title, texts.logline, texts.summary = items[0], items[1], items[2]
    position = 3
    for index in range(len(texts.chapters)):
        texts.chapters[index] = (items[position], items[position + 1])
        position += 2
    for block in blocks:
        texts.moments[block] = items[position]
        position += 1


def _kept(original: str, corrected: str) -> float:
    """Share of the original's words still in the corrected text."""
    before = original.lower().split()
    after = set(corrected.lower().split())
    return sum(w in after for w in before) / len(before) if before else 1.0


# ---------------------------------------------------------------- texts and storage
def _tidy(texts: _Texts, plan: _Plan) -> None:
    """Sentence case for titles (proper nouns of the inputs kept), no « le bloc B3 » in the
    moments, no sentence about what is missing, tags merged with the frames' and the sounds'."""
    proper = proper_nouns(plan.full, plan.facts.filename, plan.facts.place_label)
    texts.title = sentence_case(texts.title.strip(), proper)
    texts.logline = strip_absences(texts.logline)
    texts.summary = strip_absences(texts.summary)
    texts.chapters = [
        (sentence_case(title.strip(), proper), strip_absences(sentence))
        for title, sentence in texts.chapters
    ]
    texts.moments = {
        b: strip_absences(strip_block_reference(r.strip())) for b, r in texts.moments.items()
    }
    sounds = [
        sound_name(h.label, plan.language)
        for h in plan.facts.heard
        if sound_in_prompt(plan.facts, h)
    ]
    texts.merged_tags = merge_tags(texts.tags, plan.facts.frames, sounds)


def _row(
    plan: _Plan, texts: _Texts, model: ModelInfo, *, strategy: str, variant: str, usage: _Usage
) -> VideoSynthesis:
    # Ranked as chosen: the best block of each chapter, then the second bests…
    moments = sorted(
        (
            {"block": block, "chapter": index, "round": rank}
            for index, chapter in enumerate(plan.chapters)
            for rank, block in enumerate(chapter.moments)
        ),
        key=lambda m: (m["round"], m["chapter"]),
    )
    data = {
        "title": texts.title.strip(),
        "logline": texts.logline.strip(),
        "summary": texts.summary.strip(),
        "chapters": [
            {"first": c.first, "last": c.last, "title": t, "summary": s}
            for c, (t, s) in zip(plan.chapters, texts.chapters, strict=True)
        ],
        "moments": [
            {
                "rank": rank,
                "block": m["block"],
                "chapter": m["chapter"],
                "reason": texts.moments.get(m["block"], ""),
            }
            for rank, m in enumerate(moments, 1)
        ],
        "tags": [{"label": label, "source": source} for label, source in texts.merged_tags],
        "blocks": [
            {
                "no": b.no,
                "start": round(b.start, 3),
                "end": round(b.end, 3),
                "shots": list(b.shots),
                "keyframe_ids": [f.keyframe_id for f in b.frames],
            }
            for b in plan.blocks
        ],
        "stats": {
            "requests": usage.requests,
            "cached": usage.cached,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
        },
    }
    return VideoSynthesis(
        video_id=plan.facts.id,
        schema_version=SYNTHESIS_SCHEMA_VERSION,
        rules_version=RULES_VERSION,
        model=model.key,
        prompt_version=f"synthesis.v{PROMPT_VERSION}",
        language=plan.language,
        strategy=strategy,
        input_variant=variant,
        proofread=PROOFREAD,
        input_key=plan.key(),
        data=data,
    )


def _store(ctx: StageContext, row: VideoSynthesis) -> None:
    with ctx.tools.db.write() as session:
        existing = session.get(VideoSynthesis, row.video_id)
        if existing is not None:
            session.delete(existing)
            session.flush()
        session.add(row)
