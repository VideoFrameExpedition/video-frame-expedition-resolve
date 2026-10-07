"""CED-small with the real model: reference checks, true-length short input, the full rule.

Marked ``models``: they need model.onnx and class_labels_indices.csv in VFE_CED_MODEL_DIR
(default <data folder>/models/sounds/ced-small) and YAMNet's ontology.json next to
YAMNet (``vfe models sounds``); they are skipped otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from vfe_vision.adapters.audio_tagging.ced import (
    MODEL_FILE,
    NUM_CLASSES,
    SAMPLE_RATE,
    CedTagger,
    load_rollup,
    missing_files,
    self_test,
)
from vfe_vision.adapters.audio_tagging.yamnet import ONTOLOGY_FILE
from vfe_vision.core.config import default_data_dir
from vfe_vision.core.errors import ServiceUnavailableError

_MODELS = default_data_dir() / "models"
MODEL_DIR = Path(os.environ.get("VFE_CED_MODEL_DIR") or _MODELS / "sounds" / "ced-small")
ONTOLOGY = Path(os.environ.get("VFE_YAMNET_MODEL_DIR") or _MODELS / "yamnet") / ONTOLOGY_FILE
needs_model = pytest.mark.skipif(
    bool(missing_files(MODEL_DIR)) or not ONTOLOGY.is_file(),
    reason=f"CED-small absent de {MODEL_DIR} (vfe models sounds)",
)


def test_missing_files_are_reported(tmp_path: Path) -> None:
    assert missing_files(tmp_path) == [MODEL_FILE, "class_labels_indices.csv"]
    with pytest.raises(ServiceUnavailableError, match="vfe models sounds"):
        load_rollup(tmp_path, tmp_path / ONTOLOGY_FILE)
    with pytest.raises(ServiceUnavailableError, match="vfe models sounds"):
        CedTagger(tmp_path / MODEL_FILE).scores(np.zeros(SAMPLE_RATE, dtype=np.float32))


@pytest.mark.models
@needs_model
def test_real_model_passes_its_reference_checks() -> None:
    rollup = load_rollup(MODEL_DIR, ONTOLOGY)
    assert len(rollup) == NUM_CLASSES
    assert self_test(CedTagger(MODEL_DIR / MODEL_FILE), rollup.names) == []


@pytest.mark.models
@needs_model
def test_real_model_hears_a_short_clip_at_its_true_length() -> None:
    rollup = load_rollup(MODEL_DIR, ONTOLOGY)
    tagger = CedTagger(MODEL_DIR / MODEL_FILE)
    t = np.arange(int(1.5 * SAMPLE_RATE)) / SAMPLE_RATE
    result = tagger.scores((0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32))
    assert len(result) == 1
    assert result.end_s[0] == pytest.approx(1.5)
    assert rollup.names[int(np.argmax(result.probs[0]))] == "Sine wave"
    assert len(tagger.scores(np.zeros(SAMPLE_RATE // 4, dtype=np.float32))) == 0
