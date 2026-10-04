"""Questions answered by the real LM Studio (``-m lmstudio``).

Only the instance already loaded is used: the test asks the catalogue which one it is and skips
when LM Studio is off or nothing is loaded. Nothing is ever loaded or unloaded. Two requests:
one streamed answer over the small test library, one visual check with synthetic images.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa

from tests.fakes.embeddings import FakeEmbedder
from tests.fakes.library import build_library
from tests.integration.test_ask import index
from vfe_vision.adapters.imaging import write_jpeg
from vfe_vision.adapters.lmstudio.catalog import pick_text_instance
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import VfeError
from vfe_vision.db.models import Keyframe
from vfe_vision.domain.ask import AnswerStatus
from vfe_vision.services import ask
from vfe_vision.services.container import AppContainer

pytestmark = [pytest.mark.lmstudio, pytest.mark.anyio, pytest.mark.timeout(300)]


async def test_a_real_answer_from_the_loaded_instance(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", worker_enabled=False, log_level="WARNING")
    settings.ensure_dirs()
    from vfe_vision.db.migrate import upgrade_database

    upgrade_database(settings.db_path, settings.backups_dir)
    c = AppContainer.create(settings)
    try:
        try:
            loaded = pick_text_instance(await c.lmstudio.list_models())
        except VfeError as exc:
            pytest.skip(f"LM Studio injoignable : {exc.detail}")
        if loaded is None:
            pytest.skip("Aucun modèle chargé dans LM Studio")
        model, instance = loaded
        embedder = FakeEmbedder()
        c.embedder = lambda: embedder
        library = build_library(c.db, tmp_path / "Rushs")
        index(c.db, library, embedder)
        with c.db.read() as session:
            paths = list(session.execute(sa.select(Keyframe.image_path)).scalars())
        for rel in paths:  # grey images: the check can only say what it sees
            target = c.artifacts.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            write_jpeg(target, np.full((720, 1280, 3), 128, dtype=np.uint8))

        events = [
            event
            async for event in ask.ask(
                c, "Où verse-t-on du riz, et dans quelle vidéo ?", visual_check=model.vision
            )
        ]
    finally:
        await c.aclose()
    started = events[0]
    assert isinstance(started, ask.AskStarted)
    assert started.model == model.key
    assert started.context_length == instance.context_length
    pieces = [e.text for e in events if isinstance(e, ask.AnswerText)]
    assert len(pieces) > 1  # streamed
    view = events[-1]
    assert isinstance(view, ask.QuestionView)
    assert view.status in {AnswerStatus.ANSWERED, AnswerStatus.NO_ANSWER}
    assert "NO_ANSWER" not in view.answer
    assert all(1 <= citation.n <= view.passages for citation in view.citations)
    if view.status is AnswerStatus.ANSWERED:
        assert library.holiday in {citation.video_id for citation in view.citations}
    assert view.prompt_tokens
    assert view.prompt_tokens + (view.completion_tokens or 0) <= started.budget.slot
    if model.vision and view.status is AnswerStatus.ANSWERED:
        assert view.visual_check is not None
        assert view.visual_check["status"] in {"done", "skipped"}
