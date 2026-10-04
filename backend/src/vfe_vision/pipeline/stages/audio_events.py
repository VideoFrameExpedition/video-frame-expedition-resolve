"""Stage ``audio_events``: what is heard (YAMNet, CED-small): families, instruments, sounds.

The 521 AudioSet classes are summed up by the ontology: share of the duration for
each family (speech, music, nature…), instruments heard long enough, a dozen notable sounds with
their times, the room or outdoor setting, recording issues (wind in the microphone, hum), and
the three main labels of each shot. The « sounds heard » list names the specific
sounds with their times; CED-small, when installed, gives a second opinion on them. CPU only:
the GPU belongs to the vision model.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from vfe_vision.adapters.audio_tagging import ced
from vfe_vision.adapters.audio_tagging.yamnet import (
    MODEL_FILE,
    ONTOLOGY_FILE,
    SAMPLE_RATE,
    YamnetTagger,
    decode_pcm16k,
    load_rollup,
    rms_dbfs,
)
from vfe_vision.adapters.models.catalog import DEFAULTS, spec
from vfe_vision.core.errors import CancelledError
from vfe_vision.db.models import AudioScene, AudioSegment, Shot, Video
from vfe_vision.domain.audio_events import AUDIO_EVENTS_VERSION, Rollup, analyze, shot_labels
from vfe_vision.domain.audio_events import AudioScene as Scene
from vfe_vision.domain.heard import (
    HEARD_VERSION,
    CedWindows,
    HeardSound,
    heard_dict,
    heard_sounds,
    shot_heard,
)
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage

AUDIO_SCENE_SCHEMA_VERSION = 2
MODEL_ID = DEFAULTS["yamnet"]
CED_ID = DEFAULTS["ced"]
TAGGER_THREADS = 2

_lock = threading.Lock()
_loaded: dict[Path, tuple[YamnetTagger, Rollup]] = {}
_ced_loaded: dict[Path, tuple[ced.CedTagger, Rollup]] = {}


def _tagger(model_dir: Path) -> tuple[YamnetTagger, Rollup]:
    """One ONNX session and category table per model folder, shared by the worker's threads."""
    with _lock:
        if model_dir not in _loaded:
            _loaded.clear()  # another revision was installed: drop the old session
            _loaded[model_dir] = (
                YamnetTagger(model_dir / MODEL_FILE, threads=TAGGER_THREADS),
                load_rollup(model_dir),
            )
        return _loaded[model_dir]


def _ced_tagger(ced_dir: Path, yamnet_dir: Path) -> tuple[ced.CedTagger, Rollup]:
    """CED's session and category table (the AudioSet ontology comes with YAMNet)."""
    with _lock:
        if ced_dir not in _ced_loaded:
            _ced_loaded.clear()
            _ced_loaded[ced_dir] = (
                ced.CedTagger(ced_dir / ced.MODEL_FILE, threads=TAGGER_THREADS),
                ced.load_rollup(ced_dir, yamnet_dir / ONTOLOGY_FILE),
            )
        return _ced_loaded[ced_dir]


def model_identity(ctx: StageContext, model_id: str = MODEL_ID) -> str | None:
    store = ctx.tools.models
    return store.identity(spec(model_id)) if store is not None else None


class AudioEventsStage(SyncStage):
    name = "audio_events"
    version = 2  # « sounds heard » and the Snake fix: shown as outdated
    family = StageFamily.SOUND
    requires = ("probe",)
    after = ("analysis_pass",)  # the shots get their own labels
    optional = True

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        # A model installed later changes the key: a "not installed" skip is redone then, and
        # « Update » adds CED's opinion to files analysed without it.
        return {
            "model": model_identity(ctx),
            "ced": model_identity(ctx, CED_ID),
            "rules": AUDIO_EVENTS_VERSION,
            "heard": HEARD_VERSION,
        }

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        with ctx.tools.db.read() as session:
            has_audio = bool(session.get_one(Video, ctx.video.id).has_audio)
            spans = _shot_spans(session, ctx.video.id)
        digest = hashlib.sha1(repr(spans).encode(), usedforsecurity=False).hexdigest()
        return {"has_audio": has_audio, "shots": digest}

    def run(self, ctx: StageContext) -> StageOutcome:
        with ctx.tools.db.read() as session:
            has_audio = bool(session.get_one(Video, ctx.video.id).has_audio)
            spans = _shot_spans(session, ctx.video.id)
        if not has_audio:
            _clear(ctx)
            return StageOutcome.skipped("Pas de piste audio", permanent=True)
        store = ctx.tools.models
        model_dir = store.installed(spec(MODEL_ID)) if store is not None else None
        if model_dir is None:
            return StageOutcome.skipped(
                "Modèle YAMNet non installé : lancez « vfe models yamnet » puis « Compléter »"
            )
        tagger, rollup = _tagger(model_dir)
        ced_dir = store.installed(spec(CED_ID)) if store is not None else None
        share = 0.35 if ced_dir is not None else 0.7  # of the progress bar, for YAMNet
        ctx.progress(0.02, "Décodage du son (16 kHz)")
        pcm = decode_pcm16k(ctx.tools.ffmpeg.ffmpeg_path, ctx.video.path, cancel=ctx.cancel)
        ctx.progress(0.2, "Reconnaissance des sons")
        scores = tagger.scores(
            pcm, cancel=ctx.cancel, progress=lambda f: ctx.progress(0.2 + share * f, None)
        )
        windows, ced_note = None, None
        if ced_dir is not None:
            ctx.progress(0.55, "Second avis sur les sons (CED)")
            try:
                windows = _ced_windows(ctx, ced_dir, model_dir, pcm)
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the second opinion is optional
                ced_note = f"Second avis CED indisponible : {getattr(exc, 'detail', exc)}"
                ctx.log.warning("ced failed", error=str(exc))
        duration = len(pcm) / SAMPLE_RATE
        scene = analyze(scores, rollup, duration_s=duration, rms_db=rms_dbfs(pcm))
        heard = heard_sounds(scores, rollup, duration_s=duration, ced=windows)
        shot_spans = [(a, b) for _, a, b in spans]
        per_shot = shot_labels(scores, rollup, shot_spans) if spans else []
        taggers = ["yamnet"] + (["ced-small"] if windows is not None else [])
        data = scene_data(
            scene, spans, per_shot, heard=heard, shot_heard=shot_heard(heard, shot_spans),
            taggers=taggers,
        )  # fmt: skip
        identity = model_identity(ctx) or MODEL_ID
        with ctx.tools.db.write() as session:
            session.execute(sa.delete(AudioSegment).where(AudioSegment.video_id == ctx.video.id))
            row = session.get(AudioScene, ctx.video.id) or AudioScene(video_id=ctx.video.id)
            main = scene.main_category
            row.model = identity
            row.speech_s = scene.speech_s
            row.music_s = scene.music_s
            row.dominant = main.value if main else None
            row.data = data
            session.add(row)
            session.add_all(_segments(ctx.video.id, scene, heard))
        summary: dict[str, Any] = {
            "dominant": row.dominant,
            "speech_s": scene.speech_s,
            "music_s": scene.music_s,
            "instruments": [label for label, _, _ in scene.instruments],
            "events": len(scene.events),
            "heard": [sound.label for sound in heard],
            "taggers": taggers,
        }
        if ced_note is not None:  # YAMNet's result is kept; a later analysis tries CED again
            return StageOutcome.degraded(ced_note, **summary)
        return StageOutcome.ok(**summary)


def _ced_windows(ctx: StageContext, ced_dir: Path, yamnet_dir: Path, pcm: Any) -> CedWindows:
    ced_tagger, ced_rollup = _ced_tagger(ced_dir, yamnet_dir)
    result = ced_tagger.scores(
        pcm, cancel=ctx.cancel, progress=lambda f: ctx.progress(0.55 + 0.35 * f, None)
    )
    return CedWindows(result.start_s, result.end_s, result.probs, ced_rollup)


def scene_data(
    scene: Scene,
    spans: list[tuple[int, float, float]],
    per_shot: list[list[tuple[str, float]]],
    *,
    heard: list[HeardSound] | None = None,
    shot_heard: list[list[str]] | None = None,
    taggers: list[str] | None = None,
) -> dict[str, Any]:
    """The stored JSON read by the API (``AudioOut``)."""
    heard_by_shot = shot_heard or [[] for _ in spans]
    return {
        "schema_version": AUDIO_SCENE_SCHEMA_VERSION,
        "rules": AUDIO_EVENTS_VERSION,
        "heard_rules": HEARD_VERSION,
        "taggers": taggers or ["yamnet"],
        "heard": [heard_dict(sound) for sound in heard or []],
        "duration_s": scene.duration_s,
        "presence": {c.value: round(v, 4) for c, v in scene.presence.items()},
        "dominant_share": {c.value: round(v, 4) for c, v in scene.dominant.items()},
        "instruments": [
            {"label": label, "seconds": round(seconds, 2), "max_score": round(score, 3)}
            for label, seconds, score in scene.instruments
        ],
        "top_labels": [
            {"label": label, "score": round(score, 3)} for label, score in scene.top_labels
        ],
        "environment": scene.environment.value if scene.environment else None,
        # Most present first: the UI lists them in this order.
        "issues": [tag for tag, _ in sorted(scene.issues.items(), key=lambda kv: -kv[1])],
        "curves": {
            "hz": 1.0,
            "series": {c.value: values for c, values in scene.curves.items()},
        },
        "shots": [
            {
                "shot_idx": idx,
                "start_s": round(start, 3),
                "end_s": round(end, 3),
                "labels": [{"label": label, "score": round(score, 3)} for label, score in labels],
                "heard": sounds,
            }
            for (idx, start, end), labels, sounds in zip(
                spans, per_shot, heard_by_shot, strict=True
            )
        ],
    }


def _segments(video_id: str, scene: Scene, heard: list[HeardSound]) -> list[AudioSegment]:
    rows = [
        AudioSegment(
            video_id=video_id, kind="segment", category=category.value, label=None,
            start_s=start, end_s=end, score=None,
        )
        for start, end, category in scene.segments
    ]  # fmt: skip
    rows += [
        AudioSegment(
            video_id=video_id, kind="event", category=event.category.value, label=event.label,
            start_s=round(event.start_s, 2), end_s=round(event.end_s, 2),
            score=round(event.score, 3),
        )
        for event in scene.events
    ]  # fmt: skip
    rows += [
        AudioSegment(
            video_id=video_id, kind="heard", category=sound.category.value, label=sound.label,
            start_s=start, end_s=end, score=sound.score,
        )
        for sound in heard
        for start, end in sound.spans
    ]  # fmt: skip
    return rows


def _shot_spans(session: sa.orm.Session, video_id: str) -> list[tuple[int, float, float]]:
    return [
        (row.idx, round(row.start_s, 3), round(row.end_s, 3))
        for row in session.execute(
            sa.select(Shot.idx, Shot.start_s, Shot.end_s)
            .where(Shot.video_id == video_id)
            .order_by(Shot.idx)
        )
    ]


def _clear(ctx: StageContext) -> None:
    with ctx.tools.db.write() as session:
        session.execute(sa.delete(AudioSegment).where(AudioSegment.video_id == ctx.video.id))
        session.execute(sa.delete(AudioScene).where(AudioScene.video_id == ctx.video.id))
