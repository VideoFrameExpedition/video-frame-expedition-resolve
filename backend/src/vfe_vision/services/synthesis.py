"""The synthesis of a video as shown: the stored texts, and what code computes again when
it is read, from the current facts: usability per shot, editing suggestions, weather
consensus, highlight in/out points. A rule change or a new transcript needs no new request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from vfe_vision.core.errors import NotFoundError
from vfe_vision.db.models import Keyframe, Video, VideoSynthesis
from vfe_vision.domain.editing import Clip, best_frame, clip_for, roles
from vfe_vision.domain.synthesis_input import Block, speech_seconds
from vfe_vision.domain.synthesis_input import Video as VideoFacts
from vfe_vision.domain.translation import SYNTHESIS_TEXTS, Dictionary, translated
from vfe_vision.domain.usability import Usability, block_usability, usability_by_shot
from vfe_vision.domain.weather_consensus import WeatherConsensus, weather_consensus
from vfe_vision.pipeline.stages.synthesis import input_key
from vfe_vision.pipeline.synthesis_facts import load_facts, load_picture
from vfe_vision.services.audio_text import StageState, stage_state
from vfe_vision.services.container import AppContainer
from vfe_vision.services.reading import language_of, texts_in


@dataclass(frozen=True, slots=True)
class ChapterView:
    index: int  # 1-based
    start_s: float
    end_s: float
    title: str
    summary: str
    first_block: int
    last_block: int


@dataclass(frozen=True, slots=True)
class HighlightView:
    rank: int
    block: int
    chapter: int  # 1-based
    clip: Clip
    keyframe_id: str | None
    reason: str
    criteria: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SuggestionView:
    role: str  # establishing | b_roll | avoid
    block: int
    shots: tuple[int, ...]
    clip: Clip
    usability: Usability


@dataclass(frozen=True, slots=True)
class SynthesisView:
    state: StageState
    row: VideoSynthesis | None
    stale: bool  # written from facts that changed since (new descriptions, transcript…)
    chapters: list[ChapterView] = field(default_factory=list)
    highlights: list[HighlightView] = field(default_factory=list)
    suggestions: list[SuggestionView] = field(default_factory=list)
    usability: dict[int, Usability] = field(default_factory=dict)  # by shot index
    weather: WeatherConsensus | None = None
    tags: list[dict[str, Any]] = field(default_factory=list)
    thumbs: dict[str, str] = field(default_factory=dict)  # keyframe id → thumbnail path
    data: dict[str, Any] = field(default_factory=dict)  # the stored data, texts as asked


def get_synthesis(c: AppContainer, video_id: str, *, tr: Dictionary | None = None) -> SynthesisView:
    """``tr``: the language of the texts (default: the analysis language)."""
    tr = texts_in(c) if tr is None else tr
    with c.db.read() as session:
        if session.get(Video, video_id) is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
        stored = session.get(VideoSynthesis, video_id)
        state = stage_state(session, video_id, "synthesis")
        thumbs = {
            row.id: row.thumb_path
            for row in session.execute(
                sa.select(Keyframe.id, Keyframe.thumb_path).where(Keyframe.video_id == video_id)
            )
        }
    facts = load_facts(c.db, video_id)
    picture = load_picture(c.db, video_id)
    usability = usability_by_shot(facts)
    weather = weather_consensus(facts, language_of(c, tr))
    if stored is None:
        return SynthesisView(
            state, None, stale=False, usability=usability, weather=weather, thumbs=thumbs
        )
    data = translated(stored.data, SYNTHESIS_TEXTS, tr)
    blocks = [_block(facts, item) for item in data.get("blocks") or []]
    by_no = {b.no: b for b in blocks}
    usable = {b.no: block_usability(facts, b, by_shot=usability) for b in blocks}
    chapters = stored_chapters(data)
    highlights = []
    for moment in data.get("moments") or []:
        block = by_no.get(int(moment["block"]))
        if block is None:
            continue
        clip = clip_for(facts, block, picture=picture)
        frame = best_frame(block, picture, (clip.picture_in, clip.picture_out))
        highlights.append(
            HighlightView(
                rank=int(moment["rank"]), block=block.no, chapter=int(moment["chapter"]) + 1,
                clip=clip, keyframe_id=frame.keyframe_id if frame else None,
                reason=str(moment.get("reason", "")),
                criteria=_criteria(block, usable[block.no]),
            )
        )  # fmt: skip
    suggested = roles(blocks, {n: u.score for n, u in usable.items()})
    suggestions = [
        SuggestionView(role, n, by_no[n].shots,
                       clip_for(facts, by_no[n], broll=role == "b_roll", picture=picture),
                       usable[n])
        for role, numbers in (("establishing", suggested.establishing),
                              ("b_roll", suggested.b_roll), ("avoid", suggested.avoid))
        for n in numbers
        if n in by_no
    ]  # fmt: skip
    return SynthesisView(
        state=state,
        row=stored,
        stale=stored.input_key != input_key(facts, stored.language),
        chapters=chapters,
        highlights=highlights,
        suggestions=suggestions,
        usability=usability,
        weather=weather,
        tags=list(data.get("tags") or []),
        thumbs=thumbs,
        data=data,
    )


def stored_chapters(data: dict[str, Any]) -> list[ChapterView]:
    """The chapters of a stored synthesis, with the times of their blocks; none when there is
    only one (the whole video: the model gave it no title of its own)."""
    spans = {
        int(block["no"]): (float(block["start"]), float(block["end"]))
        for block in data.get("blocks") or []
    }
    chapters = [
        ChapterView(
            index=i, start_s=spans[ch["first"]][0], end_s=spans[ch["last"]][1],
            title=str(ch.get("title", "")), summary=str(ch.get("summary", "")),
            first_block=int(ch["first"]), last_block=int(ch["last"]),
        )
        for i, ch in enumerate(data.get("chapters") or [], 1)
        if ch.get("first") in spans and ch.get("last") in spans
    ]  # fmt: skip
    return chapters if len(chapters) >= 2 else []


def _block(facts: VideoFacts, stored: dict[str, Any]) -> Block:
    """A stored block, with today's facts (a new transcript moves its sound range)."""
    start, end = float(stored["start"]), float(stored["end"])
    ids = set(stored.get("keyframe_ids") or [])
    shots = tuple(int(s) for s in stored.get("shots") or [])
    heard: list[str] = []
    for shot in facts.shots:
        if shot.idx in shots:
            heard += [h for h in shot.heard if h not in heard]
    return Block(
        no=int(stored["no"]),
        start=start,
        end=end,
        shots=shots,
        frames=tuple(f for f in facts.frames if f.keyframe_id in ids),
        speech_s=speech_seconds(facts.segments, start, end),
        heard=tuple(heard),
    )


def _criteria(block: Block, usable: Usability) -> tuple[str, ...]:
    """Why code ranked this block: shown next to the suggestion (no ground truth exists)."""
    described = [f.data for f in block.frames if f.data]
    uses = [set(d.get("editing_value") or []) for d in described]
    out = [f"utilisable {usable.score}"]
    if described and sum("hero" in u for u in uses) * 2 >= len(uses):
        out.append("plan fort")
    if described and any(
        "action" in u or d.get("actions") for u, d in zip(uses, described, strict=True)
    ):
        out.append("action")
    if block.speech_s / max(1.0, block.duration) > 0.3:
        out.append("parole")
    return tuple(out)
