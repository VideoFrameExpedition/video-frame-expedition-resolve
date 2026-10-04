"""The stage ``synthesis`` and what is read from it: texts by the model, structure by code."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
import structlog

from tests.conftest import FakeLmStudio
from vfe_vision.adapters.ffmpeg.tools import Ffmpeg
from vfe_vision.adapters.lmstudio.budget import TokenBudget
from vfe_vision.api.schemas import SynthesisOut
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings
from vfe_vision.db.models import (
    AudioScene,
    ContextPlace,
    ContextWeather,
    FrameAnalysis,
    Keyframe,
    LibraryRoot,
    Shot,
    Transcript,
    TranscriptSegment,
    Video,
    VideoSynthesis,
)
from vfe_vision.db.session import Database
from vfe_vision.db.translations import TranslationCache
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.mcp.formatting import synthesis_header, synthesis_sections
from vfe_vision.mcp.server import _synthesis
from vfe_vision.pipeline.stage import StageContext, Toolbox, VideoRef
from vfe_vision.pipeline.stages import synthesis as stage_module
from vfe_vision.pipeline.stages.synthesis import SynthesisStage
from vfe_vision.services import synthesis
from vfe_vision.storage.artifacts import ArtifactStore

pytestmark = pytest.mark.anyio

TOPICS = [
    ("Un chat noir dort sur un canapé gris.", "close_up", ["hero"], "indoor", ["chat"]),
    ("Une femme verse du riz dans une casserole.", "medium", ["action"], "indoor", ["riz"]),
    ("Un bateau glisse sur un lac bleu.", "wide", ["establishing"], "outdoor", ["bateau"]),
]


@pytest.fixture
def video(db: Database, tmp_path: Path) -> VideoRef:
    """Two minutes, six 20 s shots on three subjects, speech over the cooking, a place and the
    model weather."""
    path = tmp_path / "vacances.mp4"
    with db.write() as session:
        root = LibraryRoot(path=str(tmp_path), path_key=str(tmp_path).lower(), label="t")
        session.add(root)
        session.flush()
        row = Video(
            root_id=root.id, path=str(path), path_key=str(path).lower(), rel_path=path.name,
            filename=path.name, size_bytes=1, mtime=0.0, fingerprint="f" * 16, duration_s=120.0,
            has_audio=True, width=1920, height=1080, fps=30.0,
        )  # fmt: skip
        session.add(row)
        session.flush()
        for idx in range(6):
            shot = Shot(
                video_id=row.id, idx=idx, start_s=20.0 * idx, end_s=20.0 * idx + 20,
                boundary="cut", motion="static", motion_score=0.1, stability=0.95,
                metrics={"luma": 0.5, "black_ratio": 0.0, "frozen_ratio": 0.0},
            )  # fmt: skip
            session.add(shot)
            session.flush()
            caption, framing, uses, setting, tags = TOPICS[idx // 2]
            for k in range(2):
                kf = Keyframe(
                    video_id=row.id, idx=2 * idx + k, t_s=20.0 * idx + 5 + 10 * k,
                    image_path=f"k{idx}{k}.jpg", thumb_path=f"t{idx}{k}.jpg", width=1920,
                    height=1080, selection_reason="scene", sharpness=100.0 + k, shot_id=shot.id,
                    metrics={},
                )  # fmt: skip
                session.add(kf)
                session.flush()
                session.add(
                    FrameAnalysis(
                        keyframe_id=kf.id, model="m", prompt_version="frame_analysis.v2",
                        schema_version=1, language="fr",
                        data={
                            "caption": caption, "shot_type": framing, "editing_value": uses,
                            "setting": setting, "subjects": [{"label": tags[0]}],
                            "quality_issues": [], "tags": tags, "actions": [],
                            "weather": "clear" if setting == "outdoor" else "sky_not_visible",
                        },
                    )
                )  # fmt: skip
        session.add(
            Transcript(
                video_id=row.id, source="asr", status="ok", language="fr", speech_s=12.0,
                text="On met le riz dans la casserole.",
            )
        )  # fmt: skip
        words = [[45.0, 45.4, " On"], [45.4, 45.8, " met"], [45.8, 46.0, " le"],
                 [46.0, 46.5, " riz"], [46.6, 47.5, " maintenant."]]  # fmt: skip
        session.add(
            TranscriptSegment(
                video_id=row.id, idx=0, start_s=45.0, end_s=47.5,
                text="On met le riz maintenant.", words=words,
            )
        )  # fmt: skip
        session.add(
            AudioScene(
                video_id=row.id, model="yamnet", dominant="speech",
                data={"presence": {"speech": 0.1, "nature": 0.6}, "heard": [], "shots": []},
            )
        )  # fmt: skip
        session.add(
            ContextPlace(
                video_id=row.id, source="offline", label="Hyères, Var, France",
                locality="Hyères", region="Var", country="France", country_code="FR", data={},
            )
        )  # fmt: skip
        session.add(
            ContextWeather(
                video_id=row.id, source="archive", at_utc=datetime(2026, 7, 1, tzinfo=UTC),
                weather_code=0, category="clear", temperature_c=27.0, cloud_cover_pct=5.0,
                data={"values": {"is_day": True, "precipitation_mm": 0.0}, "sun_fraction": 1.0},
                fetched_at=datetime(2026, 7, 2, tzinfo=UTC),
            )
        )  # fmt: skip
        return VideoRef(id=row.id, path=path, filename=row.filename, fingerprint="f" * 16)


def _ctx(settings: Settings, db: Database, video: VideoRef, lm: FakeLmStudio) -> StageContext:
    tools = cast(
        Toolbox,
        type(
            "T",
            (),
            {
                "db": db,
                "artifacts": ArtifactStore(settings.artifacts_dir),
                "ffmpeg": Ffmpeg(settings.ffmpeg_path, settings.ffprobe_path),
                "lmstudio": lm.client(),
                "lm_budget": TokenBudget(capacity=8192, max_concurrency=1),
            },
        )(),
    )
    return StageContext(
        video=video,
        prefs=AnalysisPreferences(),
        tools=tools,
        cancel=CancelToken(),
        progress=lambda _f, _m: None,
        log=structlog.get_logger("test"),
    )


def _stored(db: Database, video_id: str) -> VideoSynthesis | None:
    with db.read() as session:
        return session.get(VideoSynthesis, video_id)


def _text(message: dict[str, Any]) -> str:
    """A message's text, whether sent as a string or as content parts."""
    content = message["content"]
    if isinstance(content, str):
        return content
    return "".join(str(part.get("text", "")) for part in content)


def _users(lm: FakeLmStudio) -> list[str]:
    return [_text(r["messages"][1]) for r in lm.chat_requests]


async def test_one_request_writes_the_texts_code_chose_the_structure(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    lm = FakeLmStudio()
    outcome = await SynthesisStage().execute(_ctx(settings, db, video, lm))
    assert outcome.status == StageStatus.SUCCEEDED, outcome.skip_reason
    assert outcome.summary["strategy"] == "single"
    assert outcome.summary["chapters"] >= 2
    draft, proof = lm.chat_requests  # the synthesis, then its proofreading
    assert draft["model"] == "qwen/qwen3-vl-8b"  # the loaded instance, never another
    user = _text(draft["messages"][1])
    assert user.startswith("Analysis data of the video:")
    assert "CHAPTER C1" in user
    assert not re.search(r"\b\d+:\d{2}\b", user.split("BLOCKS", 1)[1])  # no time in the blocks
    assert "<untrusted>" in user  # the speech is fenced
    system = _text(draft["messages"][0])
    assert "exactly" in system
    assert "chapter entries" in system
    schema = draft["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) >= {"title", "logline", "summary", "chapters", "tags"}
    assert "texts" in proof["response_format"]["json_schema"]["schema"]["properties"]

    row = _stored(db, video.id)
    assert row is not None
    assert row.strategy == "single"
    data = row.data
    assert data["title"]
    assert len(data["chapters"]) == outcome.summary["chapters"]
    assert data["chapters"][0]["first"] == 1
    assert data["chapters"][-1]["last"] == len(data["blocks"])
    assert all(m["reason"] for m in data["moments"])
    assert {t["source"] for t in data["tags"]} <= {"llm", "frames", "sounds"}

    # Same facts: every answer comes back from the cache.
    again = await SynthesisStage().execute(_ctx(settings, db, video, lm))
    assert (again.summary["requests"], again.summary["cached"]) == (0, 2)


async def test_a_long_input_is_written_chapter_by_chapter(
    settings: Settings, db: Database, video: VideoRef, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stage_module, "MAX_SINGLE_PROMPT", 10)  # nothing fits in one request
    lm = FakeLmStudio()
    outcome = await SynthesisStage().execute(_ctx(settings, db, video, lm))
    assert outcome.status == StageStatus.SUCCEEDED
    assert outcome.summary["strategy"] == "map_reduce"
    users = _users(lm)
    maps = [u for u in users if "Part " in u]
    assert len(maps) >= outcome.summary["chapters"]
    assert any("CHAPTERS (written by a first pass)" in u for u in users)  # the reduce
    row = _stored(db, video.id)
    assert row is not None
    assert all(c["title"] for c in row.data["chapters"])


async def test_lm_studio_down_or_nothing_to_summarise(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    lm = FakeLmStudio()
    lm.down = True
    down = await SynthesisStage().execute(_ctx(settings, db, video, lm))
    assert down.status == StageStatus.SKIPPED
    assert down.retryable
    with db.write() as session:
        session.execute(sa.delete(FrameAnalysis))
        session.execute(sa.delete(TranscriptSegment))
    empty = await SynthesisStage().execute(_ctx(settings, db, video, FakeLmStudio()))
    assert empty.status == StageStatus.SKIPPED
    assert not empty.retryable


async def test_read_time_parts_and_surfaces(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    await SynthesisStage().execute(_ctx(settings, db, video, FakeLmStudio()))
    container = cast(Any, type("C", (), {"db": db, "translations": TranslationCache()})())
    view = synthesis.get_synthesis(container, video.id)
    assert not view.stale
    assert view.chapters[0].start_s == 0.0
    assert view.chapters[-1].end_s == 120.0
    shots = {idx: (20.0 * idx, 20.0 * idx + 20) for idx in range(6)}
    for moment in view.highlights:  # the picture never leaves its shot
        start, end = shots[int(moment.clip.picture_in // 20)]
        assert start <= moment.clip.picture_in < moment.clip.picture_out <= end
    assert set(view.usability) == set(range(6))
    assert view.weather is not None

    out = SynthesisOut.of(view)
    assert out.status == "ready"
    assert out.title
    assert json.loads(out.model_dump_json())["chapters"]

    header = synthesis_header(view)
    assert header[0].startswith("- synthèse : ")
    sections = synthesis_sections(view)
    assert any(line.startswith("## Chapitres") for line in sections)
    with db.read() as session:
        row = session.get_one(Video, video.id)
    result = _synthesis(row, view, min_usability=0, max_items=20)
    assert result.title
    assert result.chapters[0].chapter == 1
    assert all(h.cut.in_frame is not None for h in result.highlights)

    # A new description: the stored synthesis is to be regenerated.
    with db.write() as session:
        analysis = session.execute(sa.select(FrameAnalysis)).scalars().first()
        assert analysis is not None
        analysis.data = {**analysis.data, "caption": "Un chien court sur la plage."}
    assert synthesis.get_synthesis(container, video.id).stale
