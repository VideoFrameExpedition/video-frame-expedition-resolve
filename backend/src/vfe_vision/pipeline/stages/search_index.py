"""Stage ``index``: the search index of a video.

Passages (the whole video, chapters, shots, keyframes, ~30 s of speech) are written from every
analysis above, with the facets the filters read; SQLite indexes their words (FTS5), and
EmbeddingGemma on the CPU their meaning (256-dimension vectors), when installed.

Without the model, the words are indexed all the same and the result is provisional: the next
« Complete » after ``vfe models search`` adds the vectors (the model's identity is a setting of
the stage). The index is derived data: it is not in the analysis file, and is built again after
an import (``rebuilt_after_import``).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import numpy.typing as npt

from vfe_vision.core.errors import ExternalToolError, ServiceUnavailableError
from vfe_vision.db.translations import dictionary_for, video_texts
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.search_chunks import (
    CHUNK_RULES_VERSION,
    Chunk,
    build_chunks,
    digest,
    video_chunk,
)
from vfe_vision.domain.translation import LANGUAGES, other_language
from vfe_vision.pipeline.search_facts import load_index_facts
from vfe_vision.pipeline.search_store import replace_chunks
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage
from vfe_vision.ports.embeddings import TextEmbedder

EMBED_GROUP = 16  # passages between two progress reports and cancellation checks
WORDS_ONLY = (
    "Recherche par mots seulement : modèle de recherche par le sens non installé "
    "(lancez « vfe models search » puis « Compléter »)"
)


class IndexStage(SyncStage):
    name = "index"
    version = 1
    family = StageFamily.SEARCH
    # Everything its passages are written from; the synthesis is the last of them to arrive.
    after = (
        "probe", "metadata", "place", "weather", "sun", "analysis_pass", "keyframes",
        "technical", "audio_levels", "audio_events", "ocr", "detections", "vision_frames",
        "grounding", "transcript", "vision_shots", "synthesis", "translation",
    )  # fmt: skip
    optional = True
    rebuilt_after_import = True  # derived data: never in the analysis file

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        embedder = ctx.tools.embedder()
        return {
            "rules": CHUNK_RULES_VERSION,
            "embedder": embedder.model_id if embedder is not None else None,
            "dim": embedder.dim if embedder is not None else None,
        }

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        # The passages themselves: any new description, word, sound, place or user title
        # changes one of them, and only then is the video indexed again.
        return {"passages": digest(_chunks(ctx))}

    def run(self, ctx: StageContext) -> StageOutcome:
        chunks = _chunks(ctx)
        embedder = ctx.tools.embedder()
        vectors: npt.NDArray[np.float32] | None = None
        failure: str | None = None
        if embedder is not None:
            try:
                vectors = _embed(ctx, embedder, chunks)
            except (ExternalToolError, ServiceUnavailableError) as exc:
                failure = f"Recherche par mots seulement : vecteurs non calculés ({exc.detail})"
                ctx.log.warning("passages not embedded", error=exc.detail)
        ctx.cancel.raise_if_cancelled()
        model = embedder.model_id if embedder is not None and vectors is not None else None
        replace_chunks(ctx.tools.db, ctx.video.id, chunks, vectors, model)
        kinds = Counter(chunk.kind.value for chunk in chunks)
        summary: dict[str, Any] = {
            "passages": len(chunks),
            "kinds": dict(sorted(kinds.items())),
            "vectors": 0 if vectors is None else len(chunks),
        }
        if embedder is None:
            return StageOutcome.provisional(WORDS_ONLY, **summary)
        if failure is not None:
            return StageOutcome.degraded(failure, **summary)
        return StageOutcome.ok(model=embedder.model_id, **summary)


def _chunks(ctx: StageContext) -> list[Chunk]:
    """The passages in the analysis language, and those of the other language without the
    speech: each text through the dictionary of its language."""
    return passages(ctx.tools.db, ctx.video.id, ctx.prefs.language)


def passages(db: Any, video_id: str, language: str, *, video_only: bool = False) -> list[Chunk]:
    """Every passage of a video in ``language`` and in the other one (``video_only``: the
    passage about the whole video only, in each)."""
    languages = [language]
    if language in LANGUAGES:
        languages.append(other_language(language))
    with db.read() as session:
        texts = [source.text for source in video_texts(session, video_id)]
        dictionaries = [dictionary_for(session, lang, texts) for lang in languages]
    chunks: list[Chunk] = []
    for position, tr in enumerate(dictionaries):
        facts = load_index_facts(db, video_id, tr.language or language, tr)
        if video_only:
            chunks.append(video_chunk(facts))
        else:
            chunks += build_chunks(facts, speech=position == 0)
    return chunks


def _embed(
    ctx: StageContext, embedder: TextEmbedder, chunks: list[Chunk]
) -> npt.NDArray[np.float32]:
    """One vector per passage, by groups: progress reported, cancellation checked."""
    out = np.zeros((len(chunks), embedder.dim), dtype=np.float32)
    for first in range(0, len(chunks), EMBED_GROUP):
        ctx.cancel.raise_if_cancelled()
        group = chunks[first : first + EMBED_GROUP]
        out[first : first + len(group)] = embedder.embed_documents([c.text for c in group])
        ctx.progress((first + len(group)) / max(1, len(chunks)), None)
    return out
