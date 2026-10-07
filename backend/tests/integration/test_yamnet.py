"""YAMNet adapter: 16 kHz decode, hop-aligned chunking, frame levels, and the real model.

Decoding and chunking run everywhere (ffmpeg and a fake ONNX session). The tests marked
``models`` need yamnet.onnx, yamnet_class_map.csv and ontology.json in VFE_YAMNET_MODEL_DIR
(default <data folder>/models/yamnet); they are skipped otherwise.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from vfe_vision.adapters.audio_tagging.yamnet import (
    CHUNK_FRAMES,
    HOP,
    INPUT_NAME,
    MODEL_FILES,
    NUM_CLASSES,
    SAMPLE_RATE,
    SPAN,
    TAIL_MIN,
    WINDOW,
    YamnetTagger,
    decode_pcm16k,
    frame_count,
    load_rollup,
    missing_files,
    rms_dbfs,
    self_test,
)
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import default_data_dir
from vfe_vision.core.errors import CancelledError, ExternalToolError, ServiceUnavailableError
from vfe_vision.domain.audio_events import Category, analyze, shot_labels


def _model_dir() -> Path:
    configured = os.environ.get("VFE_YAMNET_MODEL_DIR")
    if configured:
        return Path(configured)
    return default_data_dir() / "models" / "yamnet"


MODEL_DIR = _model_dir()
needs_model = pytest.mark.skipif(
    bool(missing_files(MODEL_DIR)), reason=f"YAMNet absent de {MODEL_DIR} (vfe models yamnet)"
)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


# ------------------------------------------------------------------ decoding
def test_decode_averages_channels_and_pads_a_late_audio_start(tmp_path: Path) -> None:
    clip = tmp_path / "late stereo é.mov"
    sine = "0.9*sin(2*PI*440*t)"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=25:duration=2",
        "-itsoffset", "0.04", "-f", "lavfi", "-i", f"aevalsrc={sine}|{sine}:s=48000:d=2",
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "pcm_s16le", str(clip),
    )  # fmt: skip
    pcm = decode_pcm16k("ffmpeg", clip)
    assert pcm.dtype == np.float32
    assert pcm.flags.writeable
    assert len(pcm) == pytest.approx(2.04 * SAMPLE_RATE, abs=32)
    assert np.abs(pcm[:600]).max() < 1e-3  # 40 ms of padding: sample 0 is container time 0
    assert np.abs(pcm[1000:]).max() == pytest.approx(0.9, abs=0.01)  # average, not +3 dB


def test_decode_without_audio_fails(tmp_path: Path) -> None:
    clip = tmp_path / "silent.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=64x64:rate=25:duration=1", "-c:v", "libx264",
            str(clip))  # fmt: skip
    with pytest.raises(ExternalToolError):
        decode_pcm16k("ffmpeg", clip)


def test_decode_sample_video(sample_video: Path) -> None:
    pcm = decode_pcm16k("ffmpeg", sample_video)
    assert len(pcm) == pytest.approx(6 * SAMPLE_RATE, rel=0.02)
    levels = rms_dbfs(pcm)
    assert len(levels) == frame_count(len(pcm))
    assert float(np.median(levels)) > -30  # the sine tone is loud


# ------------------------------------------------------------------ framing and chunking
def test_frame_count() -> None:
    assert [frame_count(n) for n in (0, 1, WINDOW, WINDOW + 1, 3 * SAMPLE_RATE, SPAN)] == [
        0, 1, 1, 2, 6, CHUNK_FRAMES,
    ]  # fmt: skip


class FakeSession:
    """Behaves like the YAMNet graph for framing: row j holds the patch's first sample value,
    with the official extra padded row when len - 15600 is a multiple of the hop."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def run(self, output_names: list[str], input_feed: dict[str, Any]) -> list[Any]:
        wave = input_feed[INPUT_NAME]
        self.calls.append(len(wave))
        quirk = len(wave) >= WINDOW and (len(wave) - WINDOW) % HOP == 0
        rows = max(1, frame_count(len(wave))) + int(quirk)
        out = np.full((rows, NUM_CLASSES), -1.0, dtype=np.float32)
        for j in range(rows):
            if j * HOP < len(wave):
                out[j] = wave[j * HOP]
        return [out]

    def get_providers(self) -> list[str]:
        return ["CPUExecutionProvider"]


class FakeTagger(YamnetTagger):
    def __init__(self) -> None:
        super().__init__(Path("unused.onnx"))
        self.session = FakeSession()

    def _open(self) -> FakeSession:
        return self.session


@pytest.mark.parametrize(
    ("samples", "calls"),
    [
        (2 * CHUNK_FRAMES * HOP + WINDOW + 5 * HOP, 3),  # two chunks and a tail
        (CHUNK_FRAMES * HOP + TAIL_MIN, 1),  # the leftover is covered by the overlap
        (CHUNK_FRAMES * HOP + TAIL_MIN + 1, 2),
        (SPAN, 1),  # exact multiple: the padded extra frame is dropped
        (40_000, 1),  # shorter than a chunk
        (100, 1),
    ],
)
def test_chunks_are_hop_aligned(samples: int, calls: int) -> None:
    tagger = FakeTagger()
    pcm = np.arange(samples, dtype=np.float32)  # exact in float32 up to 2**24
    scores = tagger.scores(pcm)
    assert len(tagger.session.calls) == calls
    assert scores.shape == (frame_count(samples), NUM_CLASSES)
    np.testing.assert_array_equal(scores[:, 0], HOP * np.arange(len(scores), dtype=np.float32))


def test_empty_input_gives_no_frames() -> None:
    tagger = FakeTagger()
    assert tagger.scores(np.zeros(0, dtype=np.float32)).shape == (0, NUM_CLASSES)
    assert tagger.session.calls == []
    assert len(rms_dbfs(np.zeros(0, dtype=np.float32))) == 0


def test_progress_and_cancellation_between_chunks() -> None:
    samples = 3 * CHUNK_FRAMES * HOP + WINDOW
    seen: list[float] = []
    FakeTagger().scores(np.zeros(samples, dtype=np.float32), progress=seen.append)
    assert seen == sorted(seen)
    assert seen[-1] == 1.0
    assert len(seen) == 4

    token = CancelToken()
    tagger = FakeTagger()
    with pytest.raises(CancelledError):
        tagger.scores(
            np.zeros(samples, dtype=np.float32), cancel=token, progress=lambda _: token.cancel()
        )
    assert len(tagger.session.calls) == 1


def test_non_finite_samples_are_cleaned_and_shapes_checked() -> None:
    tagger = FakeTagger()
    pcm = np.full(20_000, np.nan, dtype=np.float32)
    assert np.isfinite(tagger.scores(pcm)).all()
    with pytest.raises(ValueError, match="mono"):
        tagger.scores(np.zeros((2, 100), dtype=np.float32))
    with pytest.raises(ValueError, match="threads"):
        YamnetTagger(Path("x.onnx"), threads=0)


def test_frame_levels() -> None:
    t = np.arange(3 * SAMPLE_RATE) / SAMPLE_RATE
    sine = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    levels = rms_dbfs(sine)
    assert len(levels) == 6
    assert levels[:5] == pytest.approx(20 * np.log10(0.5 / np.sqrt(2)), abs=0.05)  # -9.03 dBFS
    assert rms_dbfs(np.zeros(3 * SAMPLE_RATE, dtype=np.float32)).max() < -190
    loud_then_quiet = np.concatenate([sine[: 2 * HOP], np.zeros(4 * HOP, dtype=np.float32)])
    levels = rms_dbfs(loud_then_quiet)
    assert levels[0] > -10
    assert levels[1] == pytest.approx(levels[0] - 3.01, abs=0.1)  # half of the patch is loud
    assert levels[-1] < -190


def test_missing_model_is_reported(tmp_path: Path) -> None:
    assert missing_files(tmp_path) == list(MODEL_FILES)
    with pytest.raises(ServiceUnavailableError, match="vfe models yamnet"):
        YamnetTagger(tmp_path / "yamnet.onnx").scores(np.zeros(20_000, dtype=np.float32))
    with pytest.raises(ServiceUnavailableError, match="vfe models yamnet"):
        load_rollup(tmp_path)


def test_unreadable_model_is_reported(tmp_path: Path) -> None:
    broken = tmp_path / "yamnet.onnx"
    broken.write_bytes(b"not an onnx file")
    with pytest.raises(ExternalToolError, match="illisible"):
        YamnetTagger(broken).scores(np.zeros(20_000, dtype=np.float32))


# ------------------------------------------------------------------ real model
@pytest.fixture(scope="module")
def tagger() -> YamnetTagger:
    return YamnetTagger(MODEL_DIR / "yamnet.onnx", threads=2)


@pytest.mark.models
@needs_model
def test_official_golden_checks(tagger: YamnetTagger) -> None:
    rollup = load_rollup(MODEL_DIR)
    assert tagger.providers == ["CPUExecutionProvider"]  # never the GPU
    assert self_test(tagger, rollup.names) == []
    zeros = tagger.scores(np.zeros(3 * SAMPLE_RATE, dtype=np.float32))
    assert zeros.shape == (6, NUM_CLASSES)
    assert rollup.names[int(zeros.mean(axis=0).argmax())] == "Silence"
    assert float(zeros.mean(axis=0).max()) > 0.99


@pytest.mark.models
@needs_model
def test_chunked_scores_equal_one_call(tagger: YamnetTagger) -> None:
    import onnxruntime as ort

    rng = np.random.RandomState(7)
    samples = 2 * CHUNK_FRAMES * HOP + WINDOW + 37 * HOP + 1234  # two chunks and a tail
    t = np.arange(samples) / SAMPLE_RATE
    wave = 0.2 * np.sin(2 * np.pi * (220 + 40 * t) * t) + 0.05 * rng.uniform(-1, 1, samples)
    pcm = wave.astype(np.float32)
    chunked = tagger.scores(pcm)
    whole = ort.InferenceSession(
        str(MODEL_DIR / "yamnet.onnx"), providers=["CPUExecutionProvider"]
    ).run(["output_0"], {INPUT_NAME: pcm})[0]
    assert len(chunked) == frame_count(samples)
    assert np.abs(chunked - whole[: len(chunked)]).max() < 1e-4


@pytest.mark.models
@needs_model
def test_rollup_of_the_real_ontology() -> None:
    rollup = load_rollup(MODEL_DIR)
    assert len(rollup) == NUM_CLASSES
    assert sum(rollup.context) == 20
    counts = {c: len(members) for c, members in rollup.members.items()}
    assert counts == {
        Category.SPEECH: 26, Category.MUSIC: 148, Category.NATURE: 64, Category.WIND: 3,
        Category.WATER: 26, Category.VEHICLES: 52, Category.CROWD: 8, Category.TOOLS: 31,
        Category.SILENCE: 1, Category.OTHER: 142,
    }  # fmt: skip
    category = dict(zip(rollup.names, rollup.categories, strict=True))
    assert category["Hiss"] is Category.OTHER
    assert category["Snake"] is Category.OTHER  # cicadas and hiss on rushes, never a snake
    assert category["Bell"] is Category.OTHER
    assert category["Chainsaw"] is Category.TOOLS
    assert category["Singing"] is Category.MUSIC
    assert category["Wind noise (microphone)"] is Category.WIND
    assert category["Hubbub, speech noise, speech babble"] is Category.CROWD
    instruments = {rollup.names[i] for i in rollup.instruments}
    assert {"Flute", "Guitar", "Piano", "Violin, fiddle", "Drum"} <= instruments
    assert not instruments & {"Musical instrument", "Bell", "Church bell", "Bicycle bell"}


@pytest.mark.models
@needs_model
def test_sample_video_end_to_end(tagger: YamnetTagger, sample_video: Path) -> None:
    rollup = load_rollup(MODEL_DIR)
    pcm = decode_pcm16k("ffmpeg", sample_video)
    scores = tagger.scores(pcm)
    levels = rms_dbfs(pcm)
    assert len(scores) == len(levels) == frame_count(len(pcm))
    scene = analyze(scores, rollup, duration_s=len(pcm) / SAMPLE_RATE, rms_db=levels)
    assert sum(scene.dominant.values()) == pytest.approx(1.0, abs=1e-3)
    assert scene.speech_s == 0.0  # a 440 Hz tone is not speech
    assert Category.SILENCE not in scene.presence
    labels = shot_labels(scores, rollup, [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)])
    assert len(labels) == 3
    assert all(labels)
