"""Sun position, light phase, sun events and theoretical daylight colour.

Position: the NOAA solar calculator algorithm (Meeus; US government, public domain), within
~0.02° of NREL SPA from 1900 to 2100. Phases are classified on the *geometric* elevation of the
sun's centre (refraction is only added for display): mixing the two shifts sunset by 1.5 min.

The legacy classifier is not reused: two of its « Kelvin » values were colour channels (237, 73),
its morning test was always true (next transit), and two twilight branches were dead code. Event
times come from a bisection of the elevation within ± 24 h, so polar days and nights give
« no event » with a reason instead of an exception.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from enum import StrEnum

SUNRISE_ELEVATION = -0.833  # geometric: sun's upper limb on a level horizon (refraction + radius)
GOLDEN_TOP = 6.0
GOLDEN_BOTTOM = -4.0
CIVIL = -6.0
NAUTICAL = -12.0
ASTRONOMICAL = -18.0
MIN_YEAR, MAX_YEAR = 1900, 2100  # validity of the NOAA approximations
CULMINATION_HOUR_ANGLE = 7.5  # degrees (30 min) from solar noon or midnight
CULMINATION_RATE = 2.0  # degrees per hour
MEDIUM_WINDOW = timedelta(minutes=30)  # capture time only probable: phases over ± 30 min


class LightPhase(StrEnum):
    """Photographic phase (PhotoPills bands), mutually exclusive."""

    DAY = "day"
    GOLDEN_HOUR = "golden_hour"
    BLUE_HOUR = "blue_hour"
    NAUTICAL_TWILIGHT = "nautical_twilight"
    ASTRONOMICAL_TWILIGHT = "astronomical_twilight"
    NIGHT = "night"


class TwilightPhase(StrEnum):
    """Official classes (USNO): day, civil / nautical / astronomical twilight, night."""

    DAY = "day"
    CIVIL = "civil_twilight"
    NAUTICAL = "nautical_twilight"
    ASTRONOMICAL = "astronomical_twilight"
    NIGHT = "night"


def light_phase(elevation: float) -> LightPhase:
    if elevation >= GOLDEN_TOP:
        return LightPhase.DAY
    if elevation >= GOLDEN_BOTTOM:
        return LightPhase.GOLDEN_HOUR
    if elevation >= CIVIL:
        return LightPhase.BLUE_HOUR
    if elevation >= NAUTICAL:
        return LightPhase.NAUTICAL_TWILIGHT
    if elevation >= ASTRONOMICAL:
        return LightPhase.ASTRONOMICAL_TWILIGHT
    return LightPhase.NIGHT


def twilight_phase(elevation: float) -> TwilightPhase:
    if elevation > SUNRISE_ELEVATION:
        return TwilightPhase.DAY
    if elevation >= CIVIL:
        return TwilightPhase.CIVIL
    if elevation >= NAUTICAL:
        return TwilightPhase.NAUTICAL
    if elevation >= ASTRONOMICAL:
        return TwilightPhase.ASTRONOMICAL
    return TwilightPhase.NIGHT


# ---------------------------------------------------------------- NOAA solar position
@dataclass(frozen=True, slots=True)
class SolarPosition:
    elevation: float  # geometric, degrees (classification)
    apparent_elevation: float  # with atmospheric refraction (display)
    azimuth: float  # degrees clockwise from north
    hour_angle: float  # degrees, negative in the morning
    declination: float
    equation_of_time_min: float


def _julian_century(t: datetime) -> float:
    jd = t.timestamp() / 86400 + 2440587.5
    return (jd - 2451545.0) / 36525


def refraction(elevation: float) -> float:
    """NOAA's piecewise atmospheric refraction, degrees."""
    if elevation > 85:
        return 0.0
    tan_e = math.tan(math.radians(elevation))
    if elevation > 5:
        arcsec = 58.1 / tan_e - 0.07 / tan_e**3 + 0.000086 / tan_e**5
    elif elevation > -0.575:
        e = elevation
        arcsec = 1735 + e * (-518.2 + e * (103.4 + e * (-12.79 + e * 0.711)))
    else:
        arcsec = -20.772 / tan_e
    return arcsec / 3600


def solar_position(t: datetime, latitude: float, longitude: float) -> SolarPosition:
    """Sun position for a timezone-aware instant."""
    if t.tzinfo is None:
        raise ValueError("instant sans fuseau horaire")
    t = t.astimezone(UTC)
    jc = _julian_century(t)
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    m_rad = math.radians(m)
    center = (
        math.sin(m_rad) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + math.sin(2 * m_rad) * (0.019993 - 0.000101 * jc)
        + math.sin(3 * m_rad) * 0.000289
    )
    omega = math.radians(125.04 - 1934.136 * jc)
    apparent_long = math.radians(l0 + center - 0.00569 - 0.00478 * math.sin(omega))
    mean_obliquity = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    obliquity = math.radians(mean_obliquity + 0.00256 * math.cos(omega))
    declination = math.asin(math.sin(obliquity) * math.sin(apparent_long))
    y = math.tan(obliquity / 2) ** 2
    l0_rad = math.radians(l0)
    eot = 4 * math.degrees(
        y * math.sin(2 * l0_rad)
        - 2 * e * math.sin(m_rad)
        + 4 * e * y * math.sin(m_rad) * math.cos(2 * l0_rad)
        - 0.5 * y * y * math.sin(4 * l0_rad)
        - 1.25 * e * e * math.sin(2 * m_rad)
    )
    minutes = t.hour * 60 + t.minute + (t.second + t.microsecond / 1e6) / 60
    true_solar = (minutes + eot + 4 * longitude) % 1440
    hour_angle = true_solar / 4 - 180
    lat = math.radians(latitude)
    ha = math.radians(hour_angle)
    cos_zenith = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(
        declination
    ) * math.cos(ha)
    zenith = math.acos(max(-1.0, min(1.0, cos_zenith)))
    elevation = 90 - math.degrees(zenith)
    denominator = math.cos(lat) * math.sin(zenith)
    if abs(denominator) < 1e-12:  # sun at the zenith, or observer at a pole
        azimuth = 180.0 if latitude > 0 else 0.0
    else:
        cos_az = (math.sin(lat) * math.cos(zenith) - math.sin(declination)) / denominator
        az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
        azimuth = (az + 180) % 360 if hour_angle > 0 else (540 - az) % 360
    return SolarPosition(
        elevation=elevation,
        apparent_elevation=elevation + refraction(elevation),
        azimuth=azimuth,
        hour_angle=hour_angle,
        declination=math.degrees(declination),
        equation_of_time_min=eot,
    )


def elevation_rate(t: datetime, latitude: float, longitude: float) -> float:
    """Degrees per hour (positive while the sun rises)."""
    step = timedelta(minutes=2)
    before = solar_position(t - step, latitude, longitude).elevation
    after = solar_position(t + step, latitude, longitude).elevation
    return (after - before) / (4 / 60)


# ---------------------------------------------------------------- sun events, found by bisection
EVENT_TARGETS: dict[str, float] = {
    "astronomical": ASTRONOMICAL,
    "nautical": NAUTICAL,
    "civil": CIVIL,
    "blue_golden": GOLDEN_BOTTOM,  # blue hour ↔ golden hour
    "sun": SUNRISE_ELEVATION,  # sunrise / sunset
    "golden_day": GOLDEN_TOP,  # golden hour ↔ day
}


@dataclass(frozen=True, slots=True)
class SunEvents:
    """Crossings on the capture's local date: ``rising[name]`` / ``setting[name]`` (UTC)."""

    local_date: date
    rising: dict[str, datetime | None]
    setting: dict[str, datetime | None]
    solar_noon: datetime | None
    polar: str | None  # "polar_day" / "polar_night" when the sun never crosses the horizon


def _bisect(
    f_lo: float, lo: datetime, hi: datetime, *, target: float, latitude: float, longitude: float
) -> datetime:
    while hi - lo > timedelta(seconds=1):
        mid = lo + (hi - lo) / 2
        f_mid = solar_position(mid, latitude, longitude).elevation - target
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return lo + (hi - lo) / 2


def sun_events(t: datetime, latitude: float, longitude: float, zone: tzinfo) -> SunEvents:
    """Sunrise, sunset, twilights and golden/blue-hour limits on the local date of ``t``."""
    local_date = t.astimezone(zone).date()
    step = timedelta(minutes=10)
    start = t.astimezone(UTC) - timedelta(hours=24)
    times = [start + k * step for k in range(int(timedelta(hours=48) / step) + 1)]
    elevations = [solar_position(x, latitude, longitude).elevation for x in times]
    rising: dict[str, datetime | None] = {}
    setting: dict[str, datetime | None] = {}
    for name, target in EVENT_TARGETS.items():
        rising[name] = setting[name] = None
        for i in range(len(times) - 1):
            f0, f1 = elevations[i] - target, elevations[i + 1] - target
            if (f0 > 0) == (f1 > 0):
                continue
            when = _bisect(
                f0, times[i], times[i + 1], target=target, latitude=latitude, longitude=longitude
            )
            if when.astimezone(zone).date() != local_date:
                continue
            if f1 > f0:
                rising[name] = when
            else:
                setting[name] = when
    polar = None
    if rising["sun"] is None and setting["sun"] is None:
        day_elevations = [
            e
            for x, e in zip(times, elevations, strict=True)
            if x.astimezone(zone).date() == local_date
        ]
        if day_elevations and min(day_elevations) > SUNRISE_ELEVATION:
            polar = "polar_day"
        elif day_elevations and max(day_elevations) < SUNRISE_ELEVATION:
            polar = "polar_night"
    return SunEvents(local_date, rising, setting, _solar_noon(t, latitude, longitude, zone), polar)


def _solar_noon(t: datetime, latitude: float, longitude: float, zone: tzinfo) -> datetime | None:
    """Hour angle zero on the local date (two Newton steps from local clock noon)."""
    local = t.astimezone(zone)
    noon = local.replace(hour=12, minute=0, second=0, microsecond=0).astimezone(UTC)
    for _ in range(3):
        noon -= timedelta(minutes=solar_position(noon, latitude, longitude).hour_angle * 4)
    return noon if noon.astimezone(zone).date() == local.date() else None


# ---------------------------------------------------------------- situation at the capture
@dataclass(frozen=True, slots=True)
class SunAt:
    at_utc: datetime
    position: SolarPosition
    light_phase: LightPhase
    twilight_phase: TwilightPhase
    rate_deg_per_h: float
    near_culmination: bool

    @property
    def period(self) -> str:
        """Morning before solar noon, afternoon after (hour angle, valid at any latitude)."""
        return "morning" if self.position.hour_angle < 0 else "afternoon"

    @property
    def direction(self) -> str | None:
        if self.near_culmination:
            return None
        return "rising" if self.rate_deg_per_h > 0 else "setting"


def sun_at(t: datetime, latitude: float, longitude: float) -> SunAt:
    position = solar_position(t, latitude, longitude)
    rate = elevation_rate(t, latitude, longitude)
    ha = abs(position.hour_angle)
    near = (
        ha < CULMINATION_HOUR_ANGLE
        or ha > 180 - CULMINATION_HOUR_ANGLE
        or abs(rate) < CULMINATION_RATE
    )
    return SunAt(
        at_utc=t.astimezone(UTC),
        position=position,
        light_phase=light_phase(position.elevation),
        twilight_phase=twilight_phase(position.elevation),
        rate_deg_per_h=rate,
        near_culmination=near,
    )


@dataclass(frozen=True, slots=True)
class PhaseWindow:
    """Phases met while the capture time is uncertain (or during a long take)."""

    start: datetime
    end: datetime
    elevation_min: float
    elevation_max: float
    phases: tuple[LightPhase, ...]  # in order of appearance

    @property
    def single_phase(self) -> LightPhase | None:
        return self.phases[0] if len(self.phases) == 1 else None


def phase_window(start: datetime, end: datetime, latitude: float, longitude: float) -> PhaseWindow:
    step = timedelta(minutes=1)
    count = max(1, int((end - start) / step))
    elevations: list[float] = []
    phases: list[LightPhase] = []
    for k in range(count + 1):
        elevation = solar_position(start + k * step, latitude, longitude).elevation
        elevations.append(elevation)
        phase = light_phase(elevation)
        if phase not in phases:
            phases.append(phase)
    return PhaseWindow(start, end, min(elevations), max(elevations), tuple(phases))


# ---------------------------------------------------------------- theoretical colour temperature
class SkyRegime(StrEnum):
    SUNLIT = "sunlit"
    OVERCAST = "overcast"
    MIXED = "mixed"
    CLEAR_ASSUMED = "clear_assumed"  # no weather: clear sky and level horizon assumed
    TWILIGHT = "twilight"  # sun below the horizon: sky light only, sunshine means nothing


# Clear-sky SPECTRL2 model, aerosol optical depth 0.08–0.30 at 500 nm, CCT after Ohno 2013:
# elevation → direct beam (lo, hi) and global horizontal (lo, hi) illuminant ranges in kelvin.
_CLEAR_SKY: tuple[tuple[float, int, int, int, int], ...] = (
    (2, 1570, 2120, 8560, 9540),
    (4, 2170, 2800, 6880, 8160),
    (6, 2650, 3280, 6070, 6810),
    (10, 3320, 3910, 5620, 5710),
    (15, 3850, 4370, 5440, 5570),
    (20, 4190, 4650, 5440, 5610),
    (30, 4600, 4990, 5550, 5710),
    (45, 4920, 5230, 5680, 5790),
    (60, 5080, 5350, 5750, 5840),
    (75, 5160, 5410, 5780, 5860),
)
OVERCAST_RANGE = (6500, 8000)
SHADE_RANGE = (7200, 12500)
SET_GOLDEN_RANGE = (3000, 15000)  # sun below the horizon, still « golden » sky: no real claim
BLUE_HOUR_RANGE = (9000, 20000)  # far from the Planckian locus: kelvin is only indicative
SUNLIT_MIN_FRACTION = 0.5
OVERCAST_MAX_FRACTION = 0.1
OVERCAST_DIFFUSE = 0.8
DIFFUSE_TRUSTED_FROM = 10.0  # degrees: lower, even a clear sky is mostly diffuse light
MIN_SUN_UP_S = 15 * 60  # less sun-up time in the covering hour: sunshine share is meaningless
CLOUDY_COVER_PCT = 85.0
CLEAR_COVER_PCT = 25.0


def _clear_sky(elevation: float) -> tuple[tuple[int, int], tuple[int, int]]:
    """Direct-beam and global ranges, interpolated linearly in elevation (clamped)."""
    rows = _CLEAR_SKY
    e = min(max(elevation, rows[0][0]), rows[-1][0])
    for r0, r1 in itertools.pairwise(rows):
        if r0[0] <= e <= r1[0]:
            f = (e - r0[0]) / (r1[0] - r0[0])
            v = [round(a + (b - a) * f) for a, b in zip(r0[1:], r1[1:], strict=True)]
            return (v[0], v[1]), (v[2], v[3])
    raise ValueError(elevation)  # unreachable: e is clamped to the table


def _union(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    return min(a[0], b[0]), max(a[1], b[1])


def sun_up_seconds(cover_end: datetime, latitude: float, longitude: float) -> int:
    """Seconds with the sun above the horizon in the hour ending at ``cover_end`` (the interval
    that Open-Meteo's preceding-hour sunshine describes), by 1-minute steps."""
    start = cover_end - timedelta(hours=1)
    up = sum(
        solar_position(start + timedelta(minutes=k, seconds=30), latitude, longitude).elevation
        > SUNRISE_ELEVATION
        for k in range(60)
    )
    return up * 60


def sky_regime(
    elevation: float,
    *,
    sunshine_s: float | None,
    sun_up_s: int,
    diffuse_fraction: float | None,
    cloud_cover_pct: float | None,
) -> SkyRegime:
    """Was the sun out? From the weather sample covering the capture.

    Sunshine is compared with the time the sun was actually up in that hour (a clear sunset hour
    holds only a few minutes of possible sunshine); when that time is too short, the cloud cover
    decides. Below the horizon there is no direct sun to speak of.
    """
    if elevation < SUNRISE_ELEVATION:
        return SkyRegime.TWILIGHT
    if sunshine_s is not None and sun_up_s >= MIN_SUN_UP_S:
        fraction = min(1.0, sunshine_s / sun_up_s)
        use_diffuse = diffuse_fraction is not None and elevation >= DIFFUSE_TRUSTED_FROM
        if fraction <= OVERCAST_MAX_FRACTION or (
            use_diffuse and diffuse_fraction is not None and diffuse_fraction >= OVERCAST_DIFFUSE
        ):
            return SkyRegime.OVERCAST
        if fraction >= SUNLIT_MIN_FRACTION and (
            not use_diffuse or (diffuse_fraction is not None and diffuse_fraction <= 0.5)
        ):
            return SkyRegime.SUNLIT
        return SkyRegime.MIXED
    if cloud_cover_pct is not None:
        if cloud_cover_pct >= CLOUDY_COVER_PCT:
            return SkyRegime.OVERCAST
        if cloud_cover_pct <= CLEAR_COVER_PCT:
            return SkyRegime.SUNLIT
        return SkyRegime.MIXED
    return SkyRegime.CLEAR_ASSUMED


@dataclass(frozen=True, slots=True)
class TheoreticalLight:
    """Expected natural-light colour temperature (a range, never a single truth)."""

    regime: SkyRegime
    direct_k: tuple[int, int] | None  # a subject facing the sun
    ambient_k: tuple[int, int]  # the scene as a whole (sky + sun on a horizontal plane)
    off_locus: bool = False  # twilight colours: « kelvin » is only indicative

    @property
    def nominal_k(self) -> int:
        """Middle of the most telling range, in mireds (perceptually even)."""
        lo, hi = self.direct_k or self.ambient_k
        return round(2e6 / (1e6 / lo + 1e6 / hi))


def theoretical_light(elevation: float, regime: SkyRegime) -> TheoreticalLight | None:
    """``None`` from nautical twilight on: artificial light is expected, no daylight claim."""
    if elevation < CIVIL:
        return None
    if elevation < GOLDEN_BOTTOM:
        return TheoreticalLight(regime, None, BLUE_HOUR_RANGE, off_locus=True)
    if elevation < SUNRISE_ELEVATION:
        return TheoreticalLight(regime, None, SET_GOLDEN_RANGE, off_locus=True)
    beam, global_ = _clear_sky(elevation)
    if regime == SkyRegime.OVERCAST:
        return TheoreticalLight(regime, None, OVERCAST_RANGE)
    if regime == SkyRegime.MIXED:
        return TheoreticalLight(regime, beam, _union(global_, OVERCAST_RANGE))
    return TheoreticalLight(regime, beam, global_)


SRGB_NEUTRAL_MIRED = 153.8  # D65 white: what a neutral surface reads after auto white balance
DAYLIGHT_PRESET_MIRED = 181.8  # 5500 K « daylight » white-balance preset
AWB_TOLERANCE_MIRED = 40.0


def compare_with_measured(measured_k: float, light: TheoreticalLight) -> str:
    """Soft hypothesis: is the measured image white point plausible under this natural light?

    Cameras adapt their white balance only partly, so the expected *image* white point lies
    between neutral and the illuminant, with a generous tolerance (mireds). Returns
    ``consistent``, ``warmer`` (tungsten, warm LED, a warm grade…) or ``cooler`` (shade, a
    tungsten preset used outdoors, screens…).
    """
    ranges = [light.ambient_k] + ([light.direct_k] if light.direct_k else [])
    low_mired = min(1e6 / hi for _, hi in ranges)
    high_mired = max(1e6 / lo for lo, _ in ranges)
    lower = SRGB_NEUTRAL_MIRED + min(0.0, low_mired - DAYLIGHT_PRESET_MIRED) - AWB_TOLERANCE_MIRED
    upper = SRGB_NEUTRAL_MIRED + max(0.0, high_mired - DAYLIGHT_PRESET_MIRED) + AWB_TOLERANCE_MIRED
    measured = 1e6 / measured_k
    if measured > upper:
        return "warmer"
    if measured < lower:
        return "cooler"
    return "consistent"


# ---------------------------------------------------------------- moon (low precision)
@dataclass(frozen=True, slots=True)
class MoonPhase:
    illuminated: float  # 0 … 1
    waxing: bool


def moon_phase(t: datetime) -> MoonPhase:
    """Illuminated fraction (Meeus ch. 48, low precision: about 1 %)."""
    jc = _julian_century(t.astimezone(UTC))
    d = math.radians((297.8501921 + 445267.1114034 * jc) % 360)  # mean elongation
    m = math.radians((357.5291092 + 35999.0502909 * jc) % 360)  # sun's mean anomaly
    mp = math.radians((134.9633964 + 477198.8675055 * jc) % 360)  # moon's mean anomaly
    i = math.radians(
        180
        - math.degrees(d)
        - 6.289 * math.sin(mp)
        + 2.100 * math.sin(m)
        - 1.274 * math.sin(2 * d - mp)
        - 0.658 * math.sin(2 * d)
        - 0.214 * math.sin(2 * mp)
        - 0.110 * math.sin(d)
    )
    return MoonPhase(illuminated=(1 + math.cos(i)) / 2, waxing=math.sin(d) > 0)


def elevation_curve(
    start: datetime, latitude: float, longitude: float, *, step_min: int = 10, hours: int = 24
) -> list[float]:
    """Apparent sun elevation (degrees, refraction included) every ``step_min`` minutes from
    ``start`` over ``hours``: the day chart of the capture place."""
    return [
        round(
            solar_position(start + timedelta(minutes=m), latitude, longitude).apparent_elevation, 2
        )
        for m in range(0, hours * 60 + 1, step_min)
    ]
