"""When was the footage shot? Rank the available timestamps and state how much to trust them.

Rules:
* GPS time is UTC and the most reliable.
* A tag carrying an explicit UTC offset (Apple ``Keys:CreationDate``) is reliable.
* QuickTime ``CreateDate`` is UTC by specification, but DJI, GoPro and many action cameras write
  local time there: for those makes it is interpreted in the local time zone.
* Files exported by an editing application carry the export date in their container atoms:
  those become low confidence (tags with an explicit offset or GPS time are original metadata
  that survived the export and stay trustworthy).
* A date in the file name (``VID_20240509_143012``, ``2024-05-09 14.30.12``, ``20240509``) is
  local wall-clock time. It is used when the container dates are an export date, or when the
  container date is more than a day *later* than the name: the file was rewritten (trimmed,
  re-encoded, copied by a tool that resets atoms) after it got its name.
* Android phones (AOSP ``MPEG4Writer``: Samsung, Pixel…, recognised by ``Keys:AndroidVersion``)
  write the container dates when the file is finalised, i.e. at the END of the recording, in UTC.
  Their camera apps name files with the local START time (``20260826_175837.mp4``). The start is
  the file name read with the device offset tag (Samsung ``Keys:AndroidTimeZone``; high confidence
  once the container minus the duration agrees), else with the place's zone, else with the offset
  implied by the two clocks (container − duration vs name, on the grid of real offsets), and
  failing all that the container date minus the duration.
* Implausible values (camera defaults 1904/1970/2000-01-01, dates in the future) are rejected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from datetime import timezone as fixed_zone
from enum import IntEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from vfe_vision.domain.enums import Confidence

# Makes whose cameras store local time in QuickTime date atoms (no time zone information).
LOCAL_TIME_MAKES = ("dji", "gopro", "insta360", "akaso", "sjcam", "yi ", "garmin", "osmo")
MIN_PLAUSIBLE_YEAR = 1995
# A container date this much later than the file-name date means the file was rewritten.
REWRITE_TOLERANCE = timedelta(hours=36)
# Two clocks of the same recording corroborate each other within STRONG_AGREEMENT (name
# truncated to the second, file finalised 1-2 s after the last frame; residuals measured on real
# Samsung files: 0.9-1.8 s) and are still compatible within CLOCK_AGREEMENT.
STRONG_AGREEMENT = timedelta(seconds=5)
CLOCK_AGREEMENT = timedelta(seconds=30)
# A recording paused for a while ends later than name + duration: the name stays the start.
MAX_PAUSE = timedelta(hours=2)
# Pauses tolerated when the offset itself is inferred (below half the 15-minute grid step).
MAX_INFERRED_PAUSE = timedelta(minutes=7)
# UTC offsets in use (minutes), DST variants included (Newfoundland -2:30, Chatham +13:45).
VALID_UTC_OFFSETS_MIN = frozenset(
    {-720, -660, -600, -570, -540, -480, -420, -360, -300, -240, -210, -180, -150, -120, -60, 0,
     60, 120, 180, 210, 240, 270, 300, 330, 345, 360, 390, 420, 480, 525, 540, 570, 600, 630,
     660, 720, 765, 780, 825, 840}
)  # fmt: skip

_OFFSET = re.compile(r"^\s*([+-])(\d{2}):?(\d{2})\s*$")
_NAME_DATE = re.compile(
    r"(?<!\d)(?P<y>(?:19|20)\d{2})(?P<sep>[-_.]?)(?P<m>0[1-9]|1[0-2])(?P=sep)"
    r"(?P<d>0[1-9]|[12]\d|3[01])"
    r"(?:[-_ T.]?(?P<H>[01]\d|2[0-3])[-_.:h]?(?P<M>[0-5]\d)[-_.:m]?(?P<S>[0-5]\d))?(?!\d)"
)


class Kind(IntEnum):
    """Candidate kinds, best first."""

    GPS_UTC = 0
    WITH_OFFSET = 1
    CONTAINER_UTC = 2  # QuickTime CreateDate for devices that follow the spec
    CAMERA_LOCAL = 3  # naive local time written by the camera clock
    FILE_NAME = 4  # naive local time (or date only) found in the file name
    FILE_SYSTEM = 5


@dataclass(frozen=True, slots=True)
class Candidate:
    value: datetime  # aware for GPS_UTC/WITH_OFFSET/CONTAINER_UTC, naive otherwise
    kind: Kind
    source: str  # tag name, for provenance


@dataclass(frozen=True, slots=True)
class CaptureTime:
    utc: datetime | None
    timezone: str | None  # IANA zone of the place, when known
    source: str
    confidence: Confidence
    utc_offset_min: int | None = None  # local offset at capture (zone, device tag or inferred)
    warnings: tuple[str, ...] = ()  # e.g. "possible_pause", "name_mismatch"

    @property
    def local(self) -> datetime | None:
        if self.utc is None:
            return None
        if self.timezone is not None:
            try:
                return self.utc.astimezone(ZoneInfo(self.timezone))
            except (ZoneInfoNotFoundError, ValueError):
                pass
        if self.utc_offset_min is not None:
            return self.utc.astimezone(fixed_zone(timedelta(minutes=self.utc_offset_min)))
        return None


NAME_DATE_ONLY = "filename:date"
NAME_DATETIME = "filename:datetime"
NAME_WITH_OFFSET_TAG = "filename:datetime+Keys:AndroidTimeZone"
NAME_WITH_ZONE = "filename:datetime+timezone"
NAME_WITH_INFERRED_OFFSET = "filename:datetime+inferred_offset"
STOP_MINUS_DURATION = "-duration"  # source suffix: container stop date minus the duration


def parse_filename_date(filename: str) -> Candidate | None:
    """Find a capture date in a file name; date-only names get noon to limit day shifts."""
    for match in _NAME_DATE.finditer(filename):
        try:
            day = date(int(match["y"]), int(match["m"]), int(match["d"]))
        except ValueError:  # 2024-02-31
            continue
        if match["H"] is None:
            return Candidate(datetime.combine(day, time(12)), Kind.FILE_NAME, NAME_DATE_ONLY)
        clock = time(int(match["H"]), int(match["M"]), int(match["S"]))
        return Candidate(datetime.combine(day, clock), Kind.FILE_NAME, NAME_DATETIME)
    return None


def parse_utc_offset(value: object) -> int | None:
    """``"+0200"`` / ``"-03:30"`` → minutes, only for offsets that exist somewhere."""
    match = _OFFSET.match(value) if isinstance(value, str) else None
    if match is None:
        return None
    minutes = int(match[2]) * 60 + int(match[3])
    signed = -minutes if match[1] == "-" else minutes
    return signed if signed in VALID_UTC_OFFSETS_MIN else None


def infer_utc_offset(
    name_local: datetime, start_utc: datetime, *, max_pause: timedelta = timedelta(0)
) -> int | None:
    """Offset implied by a local wall-clock time and the UTC instant of the same moment.

    ``None`` unless the difference is within :data:`CLOCK_AGREEMENT` of a real offset.
    ``max_pause`` additionally tolerates a name EARLIER than ``start_utc`` (paused recording);
    it must stay below half the 15-minute grid step.
    """
    delta = name_local.replace(tzinfo=None) - start_utc.astimezone(UTC).replace(tzinfo=None)
    seconds = delta.total_seconds()
    quarters = round(seconds / 900)
    residual = seconds - quarters * 900  # negative when the name is earlier than the start
    low = -(CLOCK_AGREEMENT + min(max_pause, MAX_INFERRED_PAUSE)).total_seconds()
    if not low <= residual <= CLOCK_AGREEMENT.total_seconds():
        return None
    minutes = quarters * 15
    return minutes if minutes in VALID_UTC_OFFSETS_MIN else None


def zone_offset_min(tz_name: str | None, at: datetime) -> int | None:
    """UTC offset (minutes) of an IANA zone at an instant."""
    if tz_name is None:
        return None
    try:
        delta = at.astimezone(ZoneInfo(tz_name)).utcoffset()
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return None if delta is None else round(delta.total_seconds() / 60)


def is_plausible(value: datetime, *, now: datetime) -> bool:
    naive = value.replace(tzinfo=None)
    if naive.year < MIN_PLAUSIBLE_YEAR or (
        naive.month == 1 and naive.day == 1 and naive.year == 2000
    ):
        return False
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware <= now + timedelta(days=1)


def records_local_time(make: str | None) -> bool:
    lowered = (make or "").lower()
    return any(marker in lowered for marker in LOCAL_TIME_MAKES)


def _localize(naive: datetime, tz_name: str | None) -> datetime | None:
    """Attach a time zone to a naive local time (first occurrence when DST makes it ambiguous)."""
    if tz_name is None:
        return None
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return naive.replace(tzinfo=zone, fold=0).astimezone(UTC)


def _closest_local(naive: datetime, tz_name: str, target: datetime) -> datetime | None:
    """UTC value of a local time, choosing the DST fold closest to ``target``."""
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    options = {naive.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)}
    return min(options, key=lambda utc: abs(utc - target))


def _from_offset(naive: datetime, minutes: int) -> datetime:
    return naive.replace(tzinfo=fixed_zone(timedelta(minutes=minutes))).astimezone(UTC)


def _corrected(candidate: Candidate, clock: timedelta) -> datetime:
    """Value after the folder clock correction, for dates written by the camera clock."""
    camera_clock = candidate.kind in {Kind.WITH_OFFSET, Kind.CONTAINER_UTC, Kind.CAMERA_LOCAL} or (
        candidate.kind == Kind.FILE_NAME and candidate.source == NAME_DATETIME
    )
    return candidate.value + clock if camera_clock else candidate.value


def _as_utc(candidate: Candidate, timezone: str | None) -> datetime:
    """Best-effort UTC value (naive local times without a zone are read as UTC)."""
    if candidate.value.tzinfo is not None:
        return candidate.value.astimezone(UTC)
    return _localize(candidate.value, timezone) or candidate.value.replace(tzinfo=UTC)


def _from_name(
    candidate: Candidate, timezone: str | None, clock: timedelta, utc_offset_min: int | None
) -> CaptureTime:
    utc = _localize(candidate.value, timezone)
    if utc is None and utc_offset_min is not None:
        utc = _from_offset(candidate.value, utc_offset_min)
    precise = candidate.source == NAME_DATETIME and utc is not None
    value = utc if utc is not None else candidate.value.replace(tzinfo=UTC)
    return CaptureTime(
        value + (clock if precise else timedelta()),
        timezone,
        candidate.source,
        Confidence.MEDIUM if precise else Confidence.LOW,
        utc_offset_min,
    )


def _corroboration(
    name_utc: datetime, start: datetime, max_pause: timedelta
) -> tuple[Confidence, str | None] | None:
    """How a start read from the file name relates to the container stop minus the duration."""
    gap = start - name_utc  # positive: the recording lasted longer than its frames (pause)
    if abs(gap) <= STRONG_AGREEMENT:
        return Confidence.HIGH, None
    if abs(gap) <= CLOCK_AGREEMENT:
        return Confidence.MEDIUM, None
    if timedelta(0) < gap <= max_pause:
        return Confidence.MEDIUM, "possible_pause"
    return None  # the name contradicts the container (renamed file, wrong clock)


def _android_start(
    container: Candidate,
    name: Candidate | None,
    *,
    timezone: str | None,
    duration_s: float | None,
    utc_offset_min: int | None,
    clock: timedelta,
) -> CaptureTime | None:
    """Recording start for AOSP files, whose container dates mark the stop (see module doc)."""
    end = container.value.astimezone(UTC)
    start = end - timedelta(seconds=duration_s) if duration_s and duration_s > 0 else None
    shifted = f"{container.source}{STOP_MINUS_DURATION}"
    named = name if name is not None and name.source == NAME_DATETIME else None
    if start is None:
        if named is not None and utc_offset_min is not None:  # uncorroborated
            from_name = _from_offset(named.value, utc_offset_min)
            return CaptureTime(
                from_name + clock, timezone, NAME_WITH_OFFSET_TAG, Confidence.MEDIUM,
                utc_offset_min,
            )  # fmt: skip
        return None
    if named is None:
        return CaptureTime(start + clock, timezone, shifted, Confidence.MEDIUM, utc_offset_min)

    # The file name read with, in order: the device offset tag (authoritative, long pauses
    # allowed), the place's zone (short pauses only: a gap of an hour means the phone clock was
    # on another zone, not a pause), then an offset inferred from the two clocks (no pause: a
    # tolerance would let arbitrary renamed files "infer" wrong offsets).
    readings: list[tuple[datetime, str, int | None, timedelta]] = []
    if utc_offset_min is not None:
        readings.append((_from_offset(named.value, utc_offset_min), NAME_WITH_OFFSET_TAG,
                         utc_offset_min, MAX_PAUSE))  # fmt: skip
    else:
        if timezone is not None:
            local = _closest_local(named.value, timezone, start)
            if local is not None:
                readings.append((local, NAME_WITH_ZONE, None, MAX_INFERRED_PAUSE))
        inferred = infer_utc_offset(named.value, start)
        if inferred is not None:
            readings.append((_from_offset(named.value, inferred), NAME_WITH_INFERRED_OFFSET,
                             inferred, timedelta(0)))  # fmt: skip
    for utc, source, offset, max_pause in readings:
        judged = _corroboration(utc, start, max_pause)
        if judged is None:
            continue
        confidence, warning = judged
        warnings = [warning] if warning else []
        if source == NAME_WITH_INFERRED_OFFSET:
            confidence = Confidence.MEDIUM  # the offset itself is an inference
            place = zone_offset_min(timezone, utc)
            if place is not None and place != offset:
                warnings.append("utc_offset_mismatch")  # phone clock on another zone
        return CaptureTime(
            utc + clock, timezone, source, confidence,
            offset if offset is not None else utc_offset_min, tuple(warnings),
        )  # fmt: skip
    return CaptureTime(
        start + clock, timezone, shifted, Confidence.MEDIUM, utc_offset_min, ("name_mismatch",)
    )


def _from_container(
    candidate: Candidate, timezone: str | None, clock: timedelta, utc_offset_min: int | None
) -> CaptureTime:
    if candidate.kind == Kind.CAMERA_LOCAL:
        utc = _localize(candidate.value, timezone)
        if utc is not None:
            return CaptureTime(utc + clock, timezone, candidate.source, Confidence.MEDIUM)
        # Unknown time zone: keep the wall-clock time, flagged as low confidence.
        return CaptureTime(
            candidate.value.replace(tzinfo=UTC) + clock, None, candidate.source, Confidence.LOW
        )
    return CaptureTime(
        candidate.value.astimezone(UTC) + clock,
        timezone,
        candidate.source,
        Confidence.MEDIUM,
        utc_offset_min,
    )


def _with_zone_offset(result: CaptureTime) -> CaptureTime:
    """The place's zone, when known, defines the local offset shown to the user."""
    if result.utc is None:
        return result
    from_zone = zone_offset_min(result.timezone, result.utc)
    if from_zone is None or from_zone == result.utc_offset_min:
        return result
    return replace(result, utc_offset_min=from_zone)


def resolve_capture_time(
    candidates: Iterable[Candidate],
    *,
    timezone: str | None,
    exported_by_editor: bool,
    clock_offset_s: int | None = None,
    now: datetime | None = None,
    duration_s: float | None = None,
    container_at_stop: bool = False,
    utc_offset_min: int | None = None,
) -> CaptureTime:
    """Pick the most trustworthy capture time (the START of the recording) among ``candidates``.

    ``container_at_stop`` marks AOSP files (container dates written at the stop), ``duration_s``
    is the real recording duration and ``utc_offset_min`` an offset tag written by the device.
    """
    return _with_zone_offset(
        _resolve(
            candidates,
            timezone=timezone,
            exported_by_editor=exported_by_editor,
            clock=timedelta(seconds=clock_offset_s or 0),
            now=now or datetime.now(UTC),
            duration_s=duration_s,
            container_at_stop=container_at_stop,
            utc_offset_min=utc_offset_min,
        )
    )


def _resolve(
    candidates: Iterable[Candidate],
    *,
    timezone: str | None,
    exported_by_editor: bool,
    clock: timedelta,
    now: datetime,
    duration_s: float | None,
    container_at_stop: bool,
    utc_offset_min: int | None,
) -> CaptureTime:
    # Plausibility is judged after the folder clock correction, which can rescue a camera set a
    # year ahead or reset to its 2000-01-01 default.
    plausible = sorted(
        (c for c in candidates if is_plausible(_corrected(c, clock), now=now)),
        key=lambda c: c.kind,
    )
    name = next((c for c in plausible if c.kind == Kind.FILE_NAME), None)
    rewritten = exported_by_editor
    for candidate in plausible:
        if candidate.kind == Kind.GPS_UTC:
            return CaptureTime(
                candidate.value.astimezone(UTC), timezone, candidate.source, Confidence.HIGH
            )
        if candidate.kind == Kind.WITH_OFFSET:
            if rewritten and candidate.source.startswith("XMP"):
                continue  # XMP dates of an exported file are written by the editing software
            offset = candidate.value.utcoffset()
            return CaptureTime(
                candidate.value.astimezone(UTC) + clock,
                timezone,
                candidate.source,
                Confidence.HIGH,
                None if offset is None else round(offset.total_seconds() / 60),
            )
        if rewritten:
            break  # container dates are the export date: fall through to the name or low answer
        if candidate.kind not in {Kind.CAMERA_LOCAL, Kind.CONTAINER_UTC}:
            continue
        at_stop = container_at_stop and candidate.kind == Kind.CONTAINER_UTC
        reference = _as_utc(candidate, timezone)
        if at_stop and duration_s and duration_s > 0:
            reference -= timedelta(seconds=duration_s)
        if name is not None and reference - _as_utc(name, timezone) > REWRITE_TOLERANCE:
            rewritten = True  # e.g. "… 20240509 … - Trim.mp4" with atoms dated from the trim
            break
        if at_stop:
            android = _android_start(
                candidate,
                name,
                timezone=timezone,
                duration_s=duration_s,
                utc_offset_min=utc_offset_min,
                clock=clock,
            )
            if android is not None:
                return android
        return _from_container(candidate, timezone, clock, utc_offset_min)
    if name is not None:
        return _from_name(name, timezone, clock, utc_offset_min)
    if plausible:
        best = plausible[0]
        value = best.value if best.value.tzinfo else best.value.replace(tzinfo=UTC)
        source = "export_date" if rewritten else best.source
        return CaptureTime(value.astimezone(UTC), timezone, source, Confidence.LOW, utc_offset_min)
    return CaptureTime(None, timezone, "none", Confidence.LOW, utc_offset_min)
