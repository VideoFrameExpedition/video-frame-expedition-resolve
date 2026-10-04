"""Typed views over the JSON stored by pipeline stages."""

from __future__ import annotations

from vfe_vision.api.schemas import MetadataOut, SignalsOut
from vfe_vision.services.videos import SignalsView

VISUAL = {
    "schema_version": 1,
    "hz": 2.0,
    "t": [0.0, 0.5],
    "luma": [0.4, 0.5],
    "contrast": [0.2, 0.2],
    "saturation": [0.3, 0.31],
    "sharpness": [120.0, 118.0],
    "motion": [0.0, 1.2],
    "cct": {"t": [0.0], "k": [5600]},
}


def test_signals_are_typed_and_stale_payloads_hidden() -> None:
    out = SignalsOut.of(SignalsView(visual=VISUAL, audio={"hz": 2.0, "t": [0.0]}, audio_stats=None))
    assert out.visual is not None
    assert out.visual.cct.k == [5600]
    assert out.audio is None  # older/incomplete payload: hidden instead of a 500


def test_metadata_raw_tags_only_on_request() -> None:
    normalized = {
        "probe": {"duration_s": 6.0},
        "exif": {"make": "DJI", "dates": [], "capture": {"confidence": "low"}},
    }
    out = MetadataOut.of(normalized, None)
    assert out.exif is not None
    assert out.exif.make == "DJI"
    assert out.raw_tags is None
    assert MetadataOut.of(normalized, {"QuickTime:Make": "DJI"}).raw_tags == {
        "QuickTime:Make": "DJI"
    }
    assert MetadataOut.of(None, None).exif is None
