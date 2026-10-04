"""Parsing of the ffmpeg ebur128 + silencedetect log (stage ``audio_levels``)."""

from __future__ import annotations

from vfe_vision.adapters.ffmpeg.tools import SILENT_LUFS, parse_audio_levels

_LINE = (
    "[Parsed_ebur128_0 @ 000001a043ea2f00] t: {t} TARGET:-23 LUFS    M: {m} S: -16.4     "
    "I: -17.1 LUFS       LRA:   6.4 LU  FTPK:  -inf  -inf dBFS  TPK:  -1.0  -1.0 dBFS"
)
_SUMMARY = """[Parsed_ebur128_0 @ 000001a043ea2f00] Summary:

  Integrated loudness:
    I:         -16.9 LUFS
    Threshold: -27.4 LUFS

  Loudness range:
    LRA:         6.3 LU
    Threshold: -37.9 LUFS

  True peak:
    Peak:       {peak} dBFS
"""


def _log(values: list[str], peak: str = "-1.0") -> str:
    lines = [_LINE.format(t=f"{0.1 * (i + 1):.6f}", m=m) for i, m in enumerate(values)]
    return "\n".join([*lines, _SUMMARY.format(peak=peak)])


def test_digital_silence_nan_is_read_as_silence() -> None:
    """ffmpeg 9 prints ``M: nan`` over pure digital silence (AFM DEMO court.mp4)."""
    levels = parse_audio_levels(_log(["-18.2", "  nan", " -inf", "-70.5"]), 1.0)
    assert [v for _, v in levels.momentary] == [-18.2, SILENT_LUFS, SILENT_LUFS, -70.5]
    assert levels.momentary[1][0] == 0.2
    assert levels.integrated_lufs == -16.9
    assert levels.loudness_range_lu == 6.3
    assert levels.true_peak_dbfs == -1.0


def test_a_silent_track_has_no_true_peak() -> None:
    for peak in ("-inf", "nan"):
        assert parse_audio_levels(_log(["nan"], peak=peak), 1.0).true_peak_dbfs is None


def test_silences_are_paired_and_the_last_one_runs_to_the_end() -> None:
    log = """[silencedetect @ 0] silence_start: -0.02
[silencedetect @ 0] silence_end: 1.5 | silence_duration: 1.52
[silencedetect @ 0] silence_start: 8.25
"""
    assert parse_audio_levels(log, 10.0).silences == [(0.0, 1.5), (8.25, 10.0)]
