"""EmbeddingGemma-300m q4 on the CPU.

The ``models`` tests need the model in VFE_EMBEDDINGS_MODEL_DIR (default
%LOCALAPPDATA%/vfe-vision/models/embeddings/embeddinggemma-300m-q4, ``vfe models search``);
they are skipped otherwise.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest

from tests.fakes.library import build_library, stage_context
from vfe_vision.adapters.embeddings.gemma import (
    DIM,
    EmbedderLoader,
    GemmaEmbedder,
    missing_files,
    unit_prefix,
)
from vfe_vision.adapters.models.catalog import DEFAULTS, spec
from vfe_vision.adapters.models.store import ModelStore
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import ServiceUnavailableError
from vfe_vision.db.session import Database
from vfe_vision.domain.search_chunks import ChunkKind
from vfe_vision.pipeline.stages.search_index import IndexStage
from vfe_vision.services import search
from vfe_vision.services.container import AppContainer
from vfe_vision.services.search import SearchFilters

_MODELS = Path(os.environ.get("LOCALAPPDATA", "")) / "vfe-vision" / "models"
MODEL_DIR = Path(
    os.environ.get("VFE_EMBEDDINGS_MODEL_DIR") or _MODELS / "embeddings" / "embeddinggemma-300m-q4"
)
needs_model = pytest.mark.skipif(
    bool(missing_files(MODEL_DIR)),
    reason=f"EmbeddingGemma absent de {MODEL_DIR} (vfe models search)",
)

# French descriptions, and queries in both languages.
DOCS = [
    "Un moro-sphinx butine en vol stationnaire des fleurs de lavande violettes.",
    "Des rondelles de pommes de terre dorent dans une poêle sur une plaque à induction.",
    "Plan large d'une plage au coucher du soleil, vagues calmes et ciel orangé.",
    "Un chien court sur la pelouse d'un parc en été.",
    "Feu d'artifice au-dessus d'une ville la nuit.",
]
QUERIES = [
    ("insecte qui vole près de la lavande", 0),
    ("frying potatoes", 1),
    ("coucher de soleil à la mer", 2),
    ("dog running in a park", 3),
    ("fireworks at night", 4),
]


def test_vectors_are_cut_to_256_then_unit_length() -> None:
    raw = np.arange(768, dtype=np.float32)[np.newaxis, :] + 1
    unit = unit_prefix(raw)
    assert unit.shape == (1, DIM)
    assert np.linalg.norm(unit[0]) == pytest.approx(1.0)
    assert unit_prefix(np.zeros((2, 768))).tolist() == np.zeros((2, DIM)).tolist()  # stays null


def test_nothing_is_loaded_without_the_model(tmp_path: Path) -> None:
    assert EmbedderLoader(None)() is None
    assert EmbedderLoader(ModelStore(tmp_path))() is None  # not installed
    assert missing_files(tmp_path) == ["model_q4.onnx", "model_q4.onnx_data", "tokenizer.json"]
    broken = GemmaEmbedder(tmp_path, "x")
    with pytest.raises(ServiceUnavailableError, match="vfe models search"):
        broken.embed_query("chat")


def test_the_catalogue_pins_the_model() -> None:
    item = spec(DEFAULTS["embeddings"])
    assert [f.name for f in item.files] == ["model_q4.onnx", "model_q4.onnx_data", "tokenizer.json"]
    assert all("/resolve/5090578d9565bb06545b4552f76e6bc2c93e4a66/" in f.url for f in item.files)
    assert item.size == 519_322 + 196_725_760 + 20_323_312
    assert "Gemma" in item.licence


@pytest.mark.models
@needs_model
def test_french_and_english_queries_find_french_descriptions() -> None:
    embedder = GemmaEmbedder(MODEL_DIR, "embeddings/embeddinggemma-300m-q4")
    docs = embedder.embed_documents(DOCS)
    assert docs.shape == (len(DOCS), DIM)
    assert np.allclose(np.linalg.norm(docs, axis=1), 1.0, atol=1e-5)
    for query, expected in QUERIES:
        similarity = docs @ embedder.embed_query(query)
        assert int(np.argmax(similarity)) == expected, query
        assert similarity[expected] >= embedder.min_similarity  # found by meaning
    # A passage is embedded the same way alone or in a batch (padding is masked).
    alone = embedder.embed_documents([DOCS[2]])[0]
    assert float(alone @ docs[2]) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.models
@needs_model
def test_the_library_is_searched_by_meaning(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    """The index stage and the search with the real model, on the test library: English and
    French queries whose words are not in the passages."""
    embedder = GemmaEmbedder(MODEL_DIR, "embeddings/embeddinggemma-300m-q4")
    c = AppContainer.create(settings)
    c.embedder = lambda: embedder
    library = build_library(c.db, tmp_path / "Rushs")
    started = time.perf_counter()
    for video_id in (library.holiday, library.mountain):
        outcome = IndexStage().run(stage_context(c.db, video_id, lambda: embedder))
        assert outcome.summary["vectors"] == outcome.summary["passages"]
    assert time.perf_counter() - started < 30  # seconds per video, not minutes
    shots = SearchFilters(kinds=(ChunkKind.SHOT,))
    for query, video_id, starts in (  # each subject fills two shots of the holiday
        ("a sleeping cat", library.holiday, {0.0, 20.0}),
        ("puppy playing in snow", library.mountain, {20.0}),
        ("préparer le repas", library.holiday, {40.0, 60.0}),
        ("sailing boat", library.holiday, {80.0, 100.0}),
    ):
        hits = search.search(c, query, shots).hits
        assert "meaning" in hits[0].retrievers, query
        assert hits[0].video_id == video_id, query
        assert hits[0].t_start in starts, query
    c.db.dispose()
