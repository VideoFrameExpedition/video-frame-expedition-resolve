"""Read what the synthesis is built from, once, into plain data (``domain.synthesis_input``).

Used by the ``synthesis`` stage, and by the service that computes the read-time parts
(usability, suggestions, in/out points) from the same facts.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from datetime import timezone as fixed_zone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa

from vfe_vision.db.models import (
    AudioScene,
    AudioStats,
    ContextPlace,
    ContextSun,
    ContextWeather,
    Detection,
    FrameAnalysis,
    Keyframe,
    Shot,
    Transcript,
    TranscriptSegment,
    Video,
    VideoSignals,
)
from vfe_vision.db.session import Database
from vfe_vision.domain.shots import shown_motion
from vfe_vision.domain.subjects import ANIMALS, Category
from vfe_vision.domain.synthesis_input import (
    Box,
    Frame,
    HeardSound,
    Picture,
    Segment,
    WeatherFacts,
    Word,
)
from vfe_vision.domain.synthesis_input import Shot as ShotFacts
from vfe_vision.domain.synthesis_input import Video as VideoFacts

TRUSTED_CAPTURE = {"high", "medium"}  # a capture time good enough to be written down
# Detections that are whole beings (faces and hands are parts of a person already counted).
BEINGS = tuple(c.value for c in (Category.PERSON, *sorted(ANIMALS)))


def load_facts(db: Database, video_id: str) -> VideoFacts:
    with db.read() as session:
        video = session.get_one(Video, video_id)
        shots = list(
            session.execute(
                sa.select(Shot).where(Shot.video_id == video_id).order_by(Shot.idx)
            ).scalars()
        )
        shot_index = {s.id: s.idx for s in shots}
        rows = session.execute(
            sa.select(Keyframe, FrameAnalysis.data)
            .outerjoin(FrameAnalysis, FrameAnalysis.keyframe_id == Keyframe.id)
            .where(Keyframe.video_id == video_id, Keyframe.duplicate_of.is_(None))
            .order_by(Keyframe.idx)
        ).all()
        frames = tuple(
            Frame(
                idx=kf.idx, keyframe_id=kf.id, t=kf.t_s, shot=shot_index.get(kf.shot_id or ""),
                sharpness=kf.sharpness, metrics=dict(kf.metrics or {}),
                data=dict(data) if data else None,
            )
            for kf, data in rows
        )  # fmt: skip
        scene = session.get(AudioScene, video_id)
        audio: dict[str, Any] = dict(scene.data) if scene else {}
        heard_by_shot = {
            int(s["shot_idx"]): tuple(str(h) for h in s.get("heard") or [])
            for s in audio.get("shots") or []
            if isinstance(s, dict) and "shot_idx" in s
        }
        shot_facts = tuple(
            ShotFacts(
                idx=s.idx, start=s.start_s, end=s.end_s,
                motion=shown_motion(s.motion, s.motion_score), stability=s.stability,
                boundary=s.boundary, metrics=dict(s.metrics or {}),
                heard=heard_by_shot.get(s.idx, ()),
            )
            for s in shots
        )  # fmt: skip
        transcript = session.get(Transcript, video_id)
        segments: tuple[Segment, ...] = ()
        language = None
        if transcript is not None and transcript.status == "ok":
            language = transcript.language
            segments = tuple(
                Segment(
                    start=seg.start_s, end=seg.end_s, text=seg.text.strip(),
                    words=tuple(
                        Word(float(w[0]), float(w[1]), str(w[2])) for w in seg.words or []
                    ),
                )
                for seg in session.execute(
                    sa.select(TranscriptSegment)
                    .where(
                        TranscriptSegment.video_id == video_id,
                        TranscriptSegment.suspect.is_(False),
                    )
                    .order_by(TranscriptSegment.idx)
                ).scalars()
            )  # fmt: skip
        stats = session.get(AudioStats, video_id)
        place = session.get(ContextPlace, video_id)
        sun = session.get(ContextSun, video_id)
        weather = session.get(ContextWeather, video_id)
        feature = (place.data or {}).get("feature") if place else None
        return VideoFacts(
            id=video.id,
            filename=video.filename,
            duration=float(video.duration_s or (shots[-1].end_s if shots else 0.0)),
            orientation=video.orientation.value if video.orientation else None,
            capture_local=capture_local(video),
            place_label=place.label if place else None,
            place_feature=str(feature["name"])
            if isinstance(feature, dict) and feature.get("name")
            else None,
            light_phase=sun.light_phase if sun else None,
            day_part=sun.day_part if sun else None,
            weather=_weather(weather),
            presence={str(k): float(v) for k, v in (audio.get("presence") or {}).items()},
            heard=tuple(_heard(h) for h in audio.get("heard") or [] if isinstance(h, dict)),
            instruments=tuple(
                str(i["label"]) for i in audio.get("instruments") or [] if isinstance(i, dict)
            ),
            transcript_language=language if segments else None,
            segments=segments,
            silences=tuple(
                (float(s[0]), float(s[1])) for s in (stats.silences if stats else []) or []
            ),
            shots=shot_facts,
            frames=frames,
            fps=video.fps,
        )


def load_picture(db: Database, video_id: str) -> Picture:
    """The 2 Hz picture signals and the living beings' boxes (where to place a clip)."""
    with db.read() as session:
        signals = session.get(VideoSignals, video_id)
        visual: dict[str, Any] = dict(signals.visual or {}) if signals else {}
        boxes: dict[str, list[Box]] = {}
        for keyframe_id, box in session.execute(
            sa.select(Detection.keyframe_id, Detection.box).where(
                Detection.video_id == video_id, Detection.category.in_(BEINGS)
            )
        ):
            if isinstance(box, list) and len(box) == 4:
                x1, y1, x2, y2 = (float(v) for v in box)
                boxes.setdefault(keyframe_id, []).append((x1, y1, x2, y2))

    def series(key: str) -> tuple[float | None, ...]:
        return tuple(_number(v) for v in visual.get(key) or [])

    return Picture(
        hz=float(visual.get("hz") or 2.0),
        t=tuple(float(v) for v in visual.get("t") or []),
        motion=series("motion"),
        luma=series("luma"),
        beings={k: tuple(v) for k, v in boxes.items()},
    )


def capture_local(video: Video) -> datetime | None:
    """The local capture time, only when trusted enough to be written into a text."""
    if video.captured_at is None or video.captured_at_confidence not in TRUSTED_CAPTURE:
        return None
    utc = video.captured_at
    utc = utc.replace(tzinfo=UTC) if utc.tzinfo is None else utc.astimezone(UTC)
    if video.capture_timezone:
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
            return utc.astimezone(ZoneInfo(video.capture_timezone))
    if video.capture_utc_offset_min is not None:
        return utc.astimezone(fixed_zone(timedelta(minutes=video.capture_utc_offset_min)))
    return None  # a UTC hour would mislead as a local one


def _weather(row: ContextWeather | None) -> WeatherFacts | None:
    if row is None:
        return None
    values: dict[str, Any] = (row.data or {}).get("values") or {}
    sun_fraction = (row.data or {}).get("sun_fraction")
    is_day = values.get("is_day")
    return WeatherFacts(
        category=row.category,
        cloud_cover_pct=row.cloud_cover_pct,
        sun_fraction=float(sun_fraction) if sun_fraction is not None else None,
        is_day=bool(is_day) if is_day is not None else None,
        temperature_c=row.temperature_c,
        precipitation_mm=_number(values.get("precipitation_mm")),
    )


def _heard(item: dict[str, Any]) -> HeardSound:
    return HeardSound(
        label=str(item.get("label", "")),
        category=str(item.get("category", "other")),
        seconds=float(item.get("seconds") or 0.0),
        score=float(item.get("score") or 0.0),
        sources=tuple(str(s) for s in item.get("sources") or []),
    )


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None
