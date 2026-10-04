"""A small analysed library for the search tests: two videos, every kind of analysis.

- « vacances.mp4 » (2 min, Hyères, golden hour, clear sky, Galaxy S26 Ultra, horizontal, rated
  4 and a favourite): a cat asleep indoors, rice poured in a pan with speech over it, a boat on
  a blue lake (outdoor, wide); on-screen text, located beings, sounds, shot stories, synthesis;
- « montagne.mp4 » (40 s, Chamonix, snowing, iPhone, vertical, in a sub-folder): a snowy peak,
  then a dog running in the snow; no speech.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import structlog

from tests.conftest import FRAME_ANSWER
from vfe_vision.core.cancel import CancelToken
from vfe_vision.db.models import (
    AudioScene,
    ContextPlace,
    ContextSun,
    ContextWeather,
    Detection,
    FrameAnalysis,
    Keyframe,
    LibraryRoot,
    OcrText,
    Shot,
    ShotStory,
    SubjectScan,
    Transcript,
    TranscriptSegment,
    Video,
    VideoSynthesis,
)
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import Orientation, VideoStatus
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.pipeline.stage import StageContext, Toolbox, VideoRef
from vfe_vision.ports.embeddings import EmbedderSource, no_embedder


@dataclass(frozen=True, slots=True)
class Library:
    root_id: str
    holiday: str  # vacances.mp4
    mountain: str  # Sommets/montagne.mp4


# (caption, description, framing, editing uses, setting, subject, weather, tags)
HOLIDAY = [
    ("Un chat noir dort sur un canapé gris.", "Le chat roulé en boule dort paisiblement.",
     "close_up", ["hero"], "indoor", "chat", "sky_not_visible", ["chat", "sieste"]),
    ("Une femme verse du riz dans une casserole.", "La cuisine est lumineuse, la vapeur monte.",
     "medium", ["action"], "indoor", "femme", "sky_not_visible", ["cuisine", "riz"]),
    ("Un bateau glisse sur un lac bleu.", "Une barque à voile traverse un lac sous le soleil.",
     "wide", ["establishing"], "outdoor", "bateau", "clear", ["lac", "bateau"]),
]  # fmt: skip
MOUNTAIN = [
    ("Un sommet enneigé sous la neige qui tombe.", "Des flocons tombent devant un pic blanc.",
     "extreme_wide", ["establishing"], "outdoor", "montagne", "snow", ["neige", "montagne"]),
    ("Un chien court dans la neige.", "Un chien noir bondit dans la poudreuse.",
     "medium", ["action"], "outdoor", "chien", "snow", ["chien", "neige"]),
]  # fmt: skip
WORDS = [[45.0, 45.4, " On"], [45.4, 45.8, " met"], [45.8, 46.0, " le"], [46.0, 46.5, " riz"],
         [46.6, 47.2, " dans"], [47.2, 47.5, " la"], [47.5, 48.4, " casserole."]]  # fmt: skip


def _video(session: Any, root_id: str, base: Path, rel: str, **values: Any) -> Video:
    path = base / rel
    video = Video(
        root_id=root_id, path=str(path), path_key=str(path).lower(), rel_path=rel,
        filename=path.name, size_bytes=1, mtime=0.0, fingerprint=f"fp-{path.stem}",
        status=VideoStatus.READY, has_audio=True, width=1920, height=1080, fps=25.0, **values,
    )  # fmt: skip
    session.add(video)
    session.flush()
    return video


def _shots_and_frames(
    session: Any, video: Video, topics: list[tuple[Any, ...]], shot_s: float, per_topic: int
) -> tuple[list[Shot], list[Keyframe]]:
    shots: list[Shot] = []
    frames: list[Keyframe] = []
    for idx in range(len(topics) * per_topic):
        shot = Shot(
            video_id=video.id, idx=idx, start_s=shot_s * idx, end_s=shot_s * (idx + 1),
            boundary="start" if idx == 0 else "cut", motion="static", motion_score=0.1,
            stability=0.95, metrics={"luma": 0.5, "black_ratio": 0.0, "frozen_ratio": 0.0},
        )  # fmt: skip
        session.add(shot)
        session.flush()
        shots.append(shot)
        caption, description, framing, uses, setting, subject, weather, tags = topics[
            idx // per_topic
        ]
        frame = Keyframe(
            video_id=video.id, idx=idx, t_s=shot_s * idx + shot_s / 4,
            image_path=f"{video.id}/k{idx}.jpg", thumb_path=f"{video.id}/t{idx}.jpg",
            width=1920, height=1080, selection_reason="scene", sharpness=100.0 + idx,
            shot_id=shot.id, metrics={},
        )  # fmt: skip
        session.add(frame)
        session.flush()
        frames.append(frame)
        session.add(
            FrameAnalysis(
                keyframe_id=frame.id, model="m", prompt_version="frame_analysis.v2",
                schema_version=2, language="fr",
                data={
                    **FRAME_ANSWER,  # a complete answer, as the vision stage stores it
                    "caption": caption, "description": description, "shot_type": framing,
                    "editing_value": uses, "setting": setting, "weather": weather,
                    "subjects": [{"label": subject, "description": "", "is_main": True}],
                    "quality_issues": [], "tags": tags, "actions": [], "visible_text": "",
                },
            )
        )  # fmt: skip
    return shots, frames


def build_library(db: Database, base: Path) -> Library:
    with db.write() as session:
        root = LibraryRoot(path=str(base), path_key=str(base).lower(), label="Rushs")
        session.add(root)
        session.flush()
        holiday = _video(
            session, root.id, base, "vacances.mp4", duration_s=120.0,
            orientation=Orientation.HORIZONTAL, camera_make="samsung",
            camera_model="Galaxy S26 Ultra", captured_at=datetime(2026, 7, 1, 17, 30, tzinfo=UTC),
            captured_at_source="QuickTime:CreateDate", captured_at_confidence="high",
            capture_timezone="Europe/Paris", rating=4, favorite=True,
            start_timecode="10:00:00:00",
        )  # fmt: skip
        shots, frames = _shots_and_frames(session, holiday, HOLIDAY, 20.0, 2)
        _holiday_analyses(session, holiday, shots, frames)
        mountain = _video(
            session, root.id, base, "Sommets/montagne.mp4", duration_s=40.0,
            orientation=Orientation.VERTICAL, camera_make="Apple", camera_model="iPhone 15 Pro",
            captured_at=datetime(2025, 12, 24, 9, 0, tzinfo=UTC),
            captured_at_source="QuickTime:CreateDate", captured_at_confidence="high",
            capture_timezone="Europe/Paris",
        )  # fmt: skip
        _shots_and_frames(session, mountain, MOUNTAIN, 20.0, 1)
        session.add_all(
            [
                ContextPlace(
                    video_id=mountain.id, source="offline", label="Chamonix, Haute-Savoie, France",
                    locality="Chamonix", region="Haute-Savoie", country="France",
                    country_code="FR", data={},
                ),
                _weather(mountain.id, "snow", datetime(2025, 12, 24, 9, tzinfo=UTC)),
                _sun(mountain.id, "day", datetime(2025, 12, 24, 9, tzinfo=UTC)),
            ]
        )  # fmt: skip
        return Library(root.id, holiday.id, mountain.id)


def _weather(video_id: str, category: str, at: datetime) -> ContextWeather:
    return ContextWeather(
        video_id=video_id, source="archive", at_utc=at, weather_code=0, category=category,
        temperature_c=20.0, cloud_cover_pct=5.0,
        data={"values": {"is_day": True, "precipitation_mm": 0.0}, "sun_fraction": 1.0},
        fetched_at=at,
    )  # fmt: skip


def _sun(video_id: str, phase: str, at: datetime) -> ContextSun:
    return ContextSun(
        video_id=video_id, at_utc=at, elevation_deg=5.0, azimuth_deg=280.0, light_phase=phase,
        twilight_phase="day", day_part="evening", data={},
    )  # fmt: skip


def _holiday_analyses(
    session: Any, video: Video, shots: list[Shot], frames: list[Keyframe]
) -> None:
    session.add_all(
        [
            Transcript(
                video_id=video.id, source="asr", status="ok", language="fr", speech_s=3.4,
                text="On met le riz dans la casserole.",
            ),
            TranscriptSegment(
                video_id=video.id, idx=0, start_s=45.0, end_s=48.4,
                text="On met le riz dans la casserole.", words=WORDS,
            ),
            ContextPlace(
                video_id=video.id, source="offline", label="Hyères, Var, France",
                locality="Hyères", region="Var", country="France", country_code="FR", data={},
            ),
            _weather(video.id, "clear", datetime(2026, 7, 1, 17, tzinfo=UTC)),
            _sun(video.id, "golden_hour", datetime(2026, 7, 1, 17, 30, tzinfo=UTC)),
            AudioScene(
                video_id=video.id, model="yamnet", dominant="nature",
                data={
                    "presence": {"speech": 0.05, "nature": 0.6},
                    "heard": [{"label": "Boat, Water vehicle", "category": "vehicles",
                               "seconds": 30.0, "score": 0.7, "sources": ["yamnet", "ced"]}],
                    "shots": [{"shot_idx": 4, "heard": ["Boat, Water vehicle"]}],
                },
            ),
            OcrText(
                keyframe_id=frames[2].id, video_id=video.id, t_s=frames[2].t_s, idx=0,
                text="RIZ BASMATI", score=0.95, box=[[0, 0], [1, 0], [1, 1], [0, 1]],
                engine="ppocr",
            ),
            Detection(
                keyframe_id=frames[0].id, video_id=video.id, t_s=frames[0].t_s, source="vlm",
                idx=0, label="chat", category="mammal", box=[0.2, 0.3, 0.7, 0.9], score=None,
                main=True, model="m",
            ),
            SubjectScan(keyframe_id=frames[0].id, source="vlm", video_id=video.id, model="m",
                        found=1),
            ShotStory(
                video_id=video.id, shot_id=shots[4].id, part=1, parts=1, start_s=80.0,
                end_s=100.0, frame_times=[85.0], keyframe_ids=[frames[4].id],
                frame_paths=[frames[4].thumb_path], model="m", prompt_version="shot_story.v1",
                schema_version=1, language="fr", answer={},
                story={"summary": "Une barque traverse le lac puis s'éloigne vers la rive.",
                       "main_action": "la barque avance", "possible_cut": False, "notes": []},
            ),
            VideoSynthesis(
                video_id=video.id, schema_version=1, rules_version="r", model="m",
                prompt_version="synthesis.v2", language="fr", strategy="single",
                input_variant="V4", proofread=True, input_key="k",
                data={
                    "title": "Vacances à Hyères", "logline": "Un chat, du riz et un lac.",
                    "summary": "Une journée d'été entre la maison et le lac.",
                    "chapters": [
                        {"first": 1, "last": 2, "title": "La sieste", "summary": "Le chat dort."},
                        {"first": 3, "last": 4, "title": "La cuisine", "summary": "Le riz."},
                        {"first": 5, "last": 6, "title": "Le lac", "summary": "Une barque."},
                    ],
                    "moments": [],
                    "tags": [{"label": "vacances", "source": "llm"}],
                    "blocks": [
                        {"no": i + 1, "start": 20.0 * i, "end": 20.0 * (i + 1), "shots": [i],
                         "keyframe_ids": [frames[i].id]}
                        for i in range(6)
                    ],
                },
            ),
        ]
    )  # fmt: skip


def stage_context(
    db: Database, video_id: str, embedder: EmbedderSource = no_embedder, *, language: str = "fr"
) -> StageContext:
    with db.read() as session:
        video = session.get_one(Video, video_id)
        ref = VideoRef(
            id=video.id, path=Path(video.path), filename=video.filename,
            fingerprint=video.fingerprint, duration_s=video.duration_s,
        )  # fmt: skip
    tools = cast(Toolbox, SimpleNamespace(db=db, embedder=embedder))
    return StageContext(
        video=ref,
        prefs=AnalysisPreferences(language=language),
        tools=tools,
        cancel=CancelToken(),
        progress=lambda _f, _m: None,
        log=structlog.get_logger("test"),
    )
