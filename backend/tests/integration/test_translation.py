"""The stage ``translation``: every text of the analyses in French and in English,
kept apart from the analysis rows."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import sqlalchemy as sa
import structlog
from fastapi.testclient import TestClient

from tests.conftest import FRAME_ANSWER, FakeLmStudio
from vfe_vision.adapters.lmstudio.budget import TokenBudget
from vfe_vision.api.app import create_app
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings
from vfe_vision.db.models import (
    ContextPlace,
    Detection,
    FrameAnalysis,
    Job,
    Keyframe,
    LibraryRoot,
    LlmCall,
    Shot,
    ShotStory,
    Translation,
    Video,
    VideoSynthesis,
)
from vfe_vision.db.session import Database
from vfe_vision.db.translations import TranslationCache, dictionary_for, video_texts
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.pipeline.stage import StageContext, Toolbox, VideoRef
from vfe_vision.pipeline.stages.translation import TranslationStage

pytestmark = pytest.mark.anyio


@pytest.fixture
def video(db: Database, tmp_path: Path) -> VideoRef:
    """A video described in French, one caption of it written in English by the model."""
    path = tmp_path / "chats.mp4"
    with db.write() as session:
        root = LibraryRoot(path=str(tmp_path), path_key=str(tmp_path).lower(), label="t")
        session.add(root)
        session.flush()
        row = Video(
            root_id=root.id, path=str(path), path_key=str(path).lower(), rel_path=path.name,
            filename=path.name, size_bytes=1, mtime=0.0, fingerprint="f" * 16, duration_s=20.0,
        )  # fmt: skip
        session.add(row)
        session.flush()
        shot = Shot(
            video_id=row.id, idx=0, start_s=0.0, end_s=20.0, boundary="cut", motion="static",
            motion_score=0.1, stability=0.9, metrics={},
        )  # fmt: skip
        session.add(shot)
        session.flush()
        captions = ["Un chat dort.", "EN: A cat wakes up."]
        for idx, caption in enumerate(captions):
            kf = Keyframe(
                video_id=row.id, idx=idx, t_s=5.0 + 10 * idx, image_path=f"k{idx}.jpg",
                thumb_path=f"t{idx}.jpg", width=640, height=360, selection_reason="scene",
                sharpness=10.0, shot_id=shot.id, metrics={},
            )  # fmt: skip
            session.add(kf)
            session.flush()
            session.add(
                FrameAnalysis(
                    keyframe_id=kf.id, model="m", prompt_version="frame_analysis.v2",
                    schema_version=2, language="fr",
                    data={**FRAME_ANSWER, "caption": caption, "tags": ["chat"],
                          "visible_text": "STOP"},
                )
            )  # fmt: skip
            session.add(
                Detection(
                    keyframe_id=kf.id, video_id=row.id, t_s=kf.t_s, source="vlm", idx=0,
                    label="chat", category="mammal", box=[0.2, 0.2, 0.6, 0.7], model="m",
                )
            )  # fmt: skip
        session.add(
            ShotStory(
                video_id=row.id, shot_id=shot.id, part=1, parts=1, start_s=0.0, end_s=20.0,
                frame_times=[5.0], keyframe_ids=[None], frame_paths=["s.jpg"], model="m",
                prompt_version="shot_story.v1", schema_version=1, language="fr",
                answer={"summary": "Le chat se réveille.", "main_action": "se réveille",
                        "beats": []},
                story={"summary": "Le chat se réveille.", "main_action": "se réveille",
                       "notes": [], "possible_cut": False},
            )
        )  # fmt: skip
        session.add(
            VideoSynthesis(
                video_id=row.id, schema_version=1, rules_version="r", model="m",
                prompt_version="synthesis.v2", language="fr", strategy="single",
                input_variant="V4", proofread=True, input_key="k",
                data={"title": "Sieste du chat", "summary": "Un chat dort puis se réveille.",
                      "chapters": [], "moments": [], "tags": [{"label": "chat", "source": "llm"}],
                      "blocks": []},
            )
        )  # fmt: skip
        session.add(
            ContextPlace(
                video_id=row.id, source="nominatim", label="Bruxelles, Belgique",
                locality="Bruxelles", region=None, country="Belgique", country_code="BE",
                data={"language": "fr", "road": "Rue Neuve", "state": "Bruxelles-Capitale",
                      "attribution": "OSM"},
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


def _rows(db: Database) -> dict[tuple[str, str], str]:
    with db.read() as session:
        rows = session.execute(sa.select(Translation.source, Translation.target, Translation.text))
        return {(row.source, row.target): row.text for row in rows}


def _analysis(db: Database) -> list[Any]:
    with db.read() as session:
        return [
            *session.execute(sa.select(FrameAnalysis.data)).scalars(),
            *session.execute(sa.select(ShotStory.story)).scalars(),
            *session.execute(sa.select(VideoSynthesis.data)).scalars(),
            *session.execute(sa.select(ContextPlace.label)).scalars(),
        ]


async def test_every_text_is_translated_apart_from_the_analyses(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    lm = FakeLmStudio()
    before = _analysis(db)
    outcome = await TranslationStage().execute(_ctx(settings, db, video, lm))
    assert outcome.status == StageStatus.SUCCEEDED, outcome.skip_reason
    rows = _rows(db)
    assert rows[("Un chat dort.", "en")] == "Un chat dort. [en]"
    assert rows[("Sieste du chat", "en")] == "Sieste du chat [en]"
    assert rows[("Le chat se réveille.", "en")] == "Le chat se réveille. [en]"
    assert rows[("Bruxelles, Belgique", "en")] == "Bruxelles, Belgique [en]"
    assert rows[("Bruxelles-Capitale", "en")] == "Bruxelles-Capitale [en]"
    assert ("Rue Neuve", "en") not in rows  # roads keep their local names
    assert ("STOP", "en") not in rows  # text as seen
    # The caption the model wrote in English: given back unchanged, then asked in French.
    assert rows[("EN: A cat wakes up.", "en")] == "EN: A cat wakes up."
    assert rows[("EN: A cat wakes up.", "fr")] == "FR: A cat wakes up."
    # The vision model's subject label: language unknown, asked in both.
    assert rows[("chat", "fr")] == "chat [fr]"
    assert rows[("chat", "en")] == "chat [en]"
    assert _analysis(db) == before  # no analysis row changed
    assert outcome.summary["failed"] == 0
    assert outcome.summary["unchanged"] == 1
    # One request per language and kind: the requests hold only the texts, under their keys.
    request = lm.chat_requests[0]
    assert request["model"] == "qwen/qwen3-vl-8b"
    assert "into English" in request["messages"][1]["content"][0]["text"]
    assert set(request["response_format"]["json_schema"]["schema"]["required"]) >= {"t1"}
    with db.read() as session:
        purposes = set(session.execute(sa.select(LlmCall.purpose)).scalars())
    assert purposes == {"translation"}


async def test_known_texts_are_never_asked_again(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    lm = FakeLmStudio()
    stage = TranslationStage()
    await stage.execute(_ctx(settings, db, video, lm))
    asked = len(lm.chat_requests)
    again = await stage.execute(_ctx(settings, db, video, lm))
    assert again.status == StageStatus.SUCCEEDED
    assert again.summary == {"texts": again.summary["texts"], "asked": 0}
    assert len(lm.chat_requests) == asked


async def test_a_new_description_only_costs_its_own_texts(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    lm = FakeLmStudio()
    stage = TranslationStage()
    ctx = _ctx(settings, db, video, lm)
    facts = stage.input_facts(ctx)
    await stage.execute(ctx)
    with db.write() as session:
        row = session.execute(sa.select(FrameAnalysis).limit(1)).scalar_one()
        row.data = {**row.data, "caption": "Un chat noir dort."}
    assert stage.input_facts(ctx) != facts  # the stage runs again at the next analysis
    lm.chat_requests.clear()
    outcome = await stage.execute(ctx)
    assert outcome.summary["asked"] == 1
    assert _rows(db)[("Un chat noir dort.", "en")] == "Un chat noir dort. [en]"


class _Leaking(FakeLmStudio):
    """Slips a Chinese word into the first translation of « Un chat dort. », as Qwen did;
    ``stubborn``: into every one."""

    leaked = False
    stubborn = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        response = super().handler(request)
        if (self.leaked and not self.stubborn) or request.url.path != "/v1/chat/completions":
            return response
        body = response.json()
        answer = json.loads(body["choices"][0]["message"]["content"])
        if answer.get("t1", "").startswith("Un chat dort.") and (len(answer) > 1 or self.stubborn):
            self.leaked = True
            answer["t1"] = "A cat 猫 sleeps."
            body["choices"][0]["message"]["content"] = json.dumps(answer)
            return httpx.Response(200, json=body)
        return response


async def test_a_word_of_another_script_is_asked_again_alone(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    lm = _Leaking()
    outcome = await TranslationStage().execute(_ctx(settings, db, video, lm))
    assert outcome.status == StageStatus.SUCCEEDED, outcome.skip_reason
    assert outcome.summary["failed"] == 0
    assert _rows(db)[("Un chat dort.", "en")] == "Un chat dort. [en]"  # asked again, alone
    alone = [
        r for r in lm.chat_requests if "Un chat dort." in r["messages"][1]["content"][0]["text"]
    ]
    assert len(alone) == 2


async def test_a_text_that_keeps_slipping_stays_as_written(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    lm = _Leaking()
    lm.stubborn = True
    outcome = await TranslationStage().execute(_ctx(settings, db, video, lm))
    assert outcome.status == StageStatus.SUCCEEDED
    assert outcome.retryable  # asked again at the next analysis
    assert outcome.summary["failed"] == 1
    assert ("Un chat dort.", "en") not in _rows(db)
    temperatures = [
        r["temperature"]
        for r in lm.chat_requests
        if "Un chat dort." in r["messages"][1]["content"][0]["text"]
    ]
    assert temperatures == [0.0, 0.0, 0.4]  # in its batch, alone, alone and varied


async def test_lm_studio_down_is_worth_retrying(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    lm = FakeLmStudio()
    lm.down = True
    outcome = await TranslationStage().execute(_ctx(settings, db, video, lm))
    assert outcome.status == StageStatus.SKIPPED
    assert outcome.retryable


async def test_a_cut_answer_is_asked_again_in_halves(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    lm = FakeLmStudio()
    lm.finish_reason = "length"
    lm.answers = ["{"]  # the first request is cut; the halves are answered
    outcome = await TranslationStage().execute(_ctx(settings, db, video, lm))
    lm.finish_reason = None
    assert outcome.status == StageStatus.SUCCEEDED, outcome.skip_reason
    assert outcome.summary["requests"] > 1


async def test_nothing_to_translate(settings: Settings, db: Database, tmp_path: Path) -> None:
    lm = FakeLmStudio()
    ref = VideoRef(id="0" * 32, path=tmp_path / "x.mp4", filename="x.mp4", fingerprint="f")
    outcome = await TranslationStage().execute(_ctx(settings, db, ref, lm))
    assert outcome.status == StageStatus.SUCCEEDED
    assert outcome.summary == {"texts": 0}
    assert lm.chat_requests == []


async def test_the_dictionary_of_a_language(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    await TranslationStage().execute(_ctx(settings, db, video, FakeLmStudio()))
    cache = TranslationCache()
    english = cache.get(db, "en")
    assert english("Un chat dort.") == "Un chat dort. [en]"
    assert cache.get(db, "en") is english  # unchanged table: kept
    assert cache.loads == 1
    french = cache.get(db, "fr")
    assert french("EN: A cat wakes up.") == "FR: A cat wakes up."
    assert french("Un chat dort.") == "Un chat dort."  # already in French
    assert cache.get(db, None)("Un chat dort.") == "Un chat dort."
    with db.write() as session:
        session.add(Translation(source_sha="0" * 64, target="en", source="x", text="y"))
    assert cache.get(db, "en") is not english  # a new entry: read again
    with db.read() as session:
        texts = [s.text for s in video_texts(session, video.id)]
        assert dictionary_for(session, "en", texts)("Sieste du chat") == "Sieste du chat [en]"


# ---------------------------------------------------------------- read in the interface's language
@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


async def test_the_interface_reads_the_analyses_in_its_language(
    settings: Settings, client: TestClient, video: VideoRef
) -> None:
    db = client.app.state.container.db  # type: ignore[attr-defined]
    await TranslationStage().execute(_ctx(settings, db, video, FakeLmStudio()))

    def get(path: str, language: str | None = None, **params: str) -> Any:
        headers = {"X-VFE-Language": language} if language else {}
        response = client.get(f"/api/v1/videos/{video.id}{path}", headers=headers, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    english = get("/keyframes", "en")
    assert english[0]["analysis"]["data"]["caption"] == "Un chat dort. [en]"
    assert english[0]["analysis"]["data"]["tags"] == ["chat [en]"]
    assert english[0]["analysis"]["data"]["visible_text"] == "STOP"  # as seen
    french = get("/keyframes", "fr")
    assert french[0]["analysis"]["data"]["caption"] == "Un chat dort."
    assert french[1]["analysis"]["data"]["caption"] == "FR: A cat wakes up."  # the leak, fixed
    # No language asked: the analysis language of the application (French by default).
    assert get("/keyframes")[1]["analysis"]["data"]["caption"] == "FR: A cat wakes up."
    assert get("/keyframes", lang="en")[0]["analysis"]["data"]["caption"] == "Un chat dort. [en]"

    assert get("/synthesis", "en")["title"] == "Sieste du chat [en]"
    assert get("/synthesis", "en")["tags"][0]["label"] == "chat [en]"
    assert get("/synthesis", "fr")["title"] == "Sieste du chat"
    story = get("/shots", "en")[0]["stories"][0]
    assert story["summary"] == "Le chat se réveille. [en]"
    place = get("/context", "en")["place"]
    assert place["label"] == "Bruxelles, Belgique [en]"
    assert place["road"] == "Rue Neuve"
    assert place["state"] == "Bruxelles-Capitale [en]"
    assert get("/subjects", "en")["frames"][0]["subjects"][0]["label"] == "chat [en]"
    assert get("", "en")["place"] == "Bruxelles [en]"


def test_the_analyses_already_made_are_translated_on_request(
    client: TestClient, video: VideoRef
) -> None:
    db = client.app.state.container.db  # type: ignore[attr-defined]
    response = client.post("/api/v1/library/translate", headers={"X-VFE-Client": "tests"})
    assert response.status_code == 202, response.text
    assert response.json()["queued"] == 1
    with db.read() as session:
        job = session.execute(sa.select(Job)).scalar_one()
    assert job.video_id == video.id
    assert job.payload["stages"] == ["translation"]
    assert job.priority == 150  # after the analyses asked for
