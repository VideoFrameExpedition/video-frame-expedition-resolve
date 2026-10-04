"""Time formatting and timecode arithmetic."""

from __future__ import annotations

import math
import re

_TC_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[:;](\d{2})$")


def format_clock(seconds: float, *, millis: bool = False) -> str:
    """``83.5`` → ``"01:23"`` (or ``"01:23.500"``); hours are shown only when needed."""
    if seconds < 0 or not math.isfinite(seconds):
        raise ValueError(f"durée invalide : {seconds}")
    total_ms = round(seconds * 1000) if millis else int(seconds) * 1000
    whole, ms = divmod(total_ms, 1000)
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    base = f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
    return f"{base}.{ms:03d}" if millis else base


def parse_timecode(tc: str, fps: float) -> float:
    """SMPTE ``HH:MM:SS:FF`` (``;`` for drop-frame) → seconds, using the nominal frame rate."""
    match = _TC_RE.match(tc.strip())
    if match is None:
        raise ValueError(f"timecode invalide : {tc!r}")
    hours, minutes, secs, frames = (int(g) for g in match.groups())
    nominal = round(fps)
    if frames >= nominal:
        raise ValueError(f"numéro d'image {frames} hors limite pour {fps} i/s")
    return hours * 3600 + minutes * 60 + secs + frames / nominal


def seconds_to_frame(seconds: float, fps: float) -> int:
    """Index of the frame displayed at ``seconds`` (floor, never negative)."""
    return max(0, math.floor(seconds * fps + 1e-6))


def timecode_at(start: str | None, seconds: float, fps: float | None) -> str | None:
    """The source timecode of the frame shown at ``seconds``: the file's start timecode (none:
    ``00:00:00:00``, as editors number a file without one) plus that frame's index.

    A drop-frame start (``;``, at 29.97 or 59.94 i/s) gives a drop-frame timecode; None for
    an unreadable start or no frame rate.
    """
    if not fps or fps <= 0 or not math.isfinite(seconds) or seconds < 0:
        return None
    base = start or "00:00:00:00"
    try:
        first = timecode_frames(base, fps)
    except ValueError:
        return None
    return frames_timecode(
        first + seconds_to_frame(seconds, fps), fps, drop_frame=is_drop_frame(base, fps)
    )


# ---------------------------------------------------------------- frame counts
def drop_frame_rate(fps: float) -> bool:
    """29.97 and 59.94 i/s: the rates whose timecode may skip frame numbers (SMPTE drop-frame)."""
    return any(abs(fps - rate) < 0.01 for rate in (30000 / 1001, 60000 / 1001))


def is_drop_frame(timecode: str | None, fps: float) -> bool:
    """A timecode written with ``;`` at a drop-frame rate."""
    return bool(timecode) and ";" in str(timecode) and drop_frame_rate(fps)


def _dropped(fps: float) -> int:
    """Frame numbers skipped each minute (but every tenth) in drop-frame: 2 at 29.97, 4 at 59.94."""
    return 2 * round(fps / 30)


def timecode_frames(tc: str, fps: float) -> int:
    """SMPTE timecode → frame count from 00:00:00:00, drop-frame when written with ``;`` at 29.97
    or 59.94 i/s (a ``;`` at another rate reads as non-drop-frame)."""
    match = _TC_RE.match(tc.strip())
    if match is None:
        raise ValueError(f"timecode invalide : {tc!r}")
    hours, minutes, secs, frames = (int(g) for g in match.groups())
    nominal = round(fps)
    if nominal <= 0 or frames >= nominal:
        raise ValueError(f"numéro d'image {frames} hors limite pour {fps} i/s")
    count = (hours * 3600 + minutes * 60 + secs) * nominal + frames
    if is_drop_frame(tc, fps):
        total_minutes = hours * 60 + minutes
        count -= _dropped(fps) * (total_minutes - total_minutes // 10)
    return count


def frames_timecode(frames: int, fps: float, *, drop_frame: bool = False) -> str:
    """Frame count from 00:00:00:00 → ``HH:MM:SS:FF`` (``HH:MM:SS;FF`` in drop-frame, which only
    applies at 29.97 or 59.94 i/s), wrapping at 24 h."""
    nominal = round(fps)
    if nominal <= 0:
        raise ValueError(f"fréquence d'images invalide : {fps}")
    drop = drop_frame and drop_frame_rate(fps)
    if drop:
        skipped = _dropped(fps)
        per_ten = nominal * 600 - skipped * 9  # 17 982 frames every ten minutes at 29.97
        per_minute = nominal * 60 - skipped
        frames %= per_ten * 6 * 24
        tens, rest = divmod(frames, per_ten)
        extra = skipped * 9 * tens
        if rest > skipped:
            extra += skipped * ((rest - skipped) // per_minute)
        frames += extra
    total = frames % (24 * 3600 * nominal)
    hours, rest = divmod(total, 3600 * nominal)
    minutes, rest = divmod(rest, 60 * nominal)
    secs, count = divmod(rest, nominal)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{';' if drop else ':'}{count:02d}"
