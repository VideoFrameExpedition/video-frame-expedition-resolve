"""The weather of a video: Open-Meteo's model weather at the capture hour, checked against what
the keyframes show (pure functions).

Neither source is enough alone. The model misses a local shower and counts mid and high veils
in its cloud cover, which a camera barely sees; a frame shows little sky in a close-up, none
indoors, and a bokeh can pass for rain. Code arbitrates so that the language model never has
to. On the 26 videos of the library: agree 6, close 4, api_only 4, disagree 1, visual_only 6,
unknown 5 (screen recordings, a kitchen, an archive without sky); three « overcast 51–100 % »
hours with 100 % sunshine and clear frames came out clear, « voile nuageux possible » (possible
cloud veil).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from vfe_vision.domain.synthesis_input import Frame, Video, WeatherFacts
from vfe_vision.domain.vision import Setting, ShotType, WeatherVisual
from vfe_vision.domain.weather_codes import WeatherCategory

WEATHER_RULES_VERSION = 1
MIN_VISUAL_WEIGHT = 1.0  # the frames have an opinion from one wide frame (or two medium ones)
SUNNY_HOUR = 0.5  # sunshine share above which an « overcast » model hour was a high veil
OUTVOTE_WEIGHT = 3.0  # frames overrule the model on opposite ends only when many…
OUTVOTE_SHARE = 0.7  # …and nearly unanimous
CONFIDENT_VIEWS = 2  # wide or medium frames that must agree for a pictures-only medium
TRACE_MM = 0.1  # less precipitation than this does not confirm rain seen on a frame

Agreement = Literal["agree", "close", "disagree", "api_only", "visual_only", "unknown"]
Confidence = Literal["high", "medium", "low"]

# What a frame's weather is worth, by framing: a wide shot shows the sky, a macro rarely does.
_SKY_WEIGHT: dict[str, float] = {
    ShotType.EXTREME_WIDE: 1.0,
    ShotType.WIDE: 1.0,
    ShotType.MEDIUM: 0.5,
    ShotType.CLOSE_UP: 0.25,
    ShotType.EXTREME_CLOSE_UP: 0.1,
    ShotType.MACRO: 0.1,
    ShotType.UNKNOWN: 0.25,
}
_DEFAULT_WEIGHT = 0.25
_OPEN_VIEWS = frozenset({ShotType.EXTREME_WIDE, ShotType.WIDE, ShotType.MEDIUM})
_CLOUDINESS: dict[str, int] = {
    WeatherCategory.CLEAR: 0,
    WeatherCategory.PARTLY_CLOUDY: 1,
    WeatherCategory.OVERCAST: 2,
}
_PRECIPITATION = frozenset({WeatherVisual.RAIN, WeatherVisual.SNOW})
# Model (WeatherCategory) and visual (WeatherVisual) categories share their names.
_NAMES: dict[str, str] = {
    WeatherCategory.CLEAR: "ciel dégagé",
    WeatherCategory.PARTLY_CLOUDY: "partiellement nuageux",
    WeatherCategory.OVERCAST: "couvert",
    WeatherCategory.FOG: "brouillard",
    WeatherCategory.DRIZZLE: "bruine",
    WeatherCategory.RAIN: "pluie",
    WeatherCategory.SNOW: "neige",
    WeatherCategory.THUNDERSTORM: "orage",
}
_NAMES_EN: dict[str, str] = {
    WeatherCategory.CLEAR: "clear sky",
    WeatherCategory.PARTLY_CLOUDY: "partly cloudy",
    WeatherCategory.OVERCAST: "overcast",
    WeatherCategory.FOG: "fog",
    WeatherCategory.DRIZZLE: "drizzle",
    WeatherCategory.RAIN: "rain",
    WeatherCategory.SNOW: "snow",
    WeatherCategory.THUNDERSTORM: "thunderstorm",
}
# The line's words, in the language it is read in; ``{seen}``: a category's name.
_WORDS: dict[str, dict[str, str]] = {
    "fr": {
        "unknown": "météo inconnue (ni données Open-Meteo, ni ciel visible)",
        "pictures_only": " d'après les images seules",
        "model_only": " d'après Open-Meteo, {hidden}",
        "sky_barely": "ciel peu visible",
        "sky_hidden": "ciel non visible",
        "agree": ", Open-Meteo et images d'accord",
        "veil": ", voile nuageux possible",
        "disagree": ", sources en désaccord",
        "unconfirmed": " ; {seen} vue sur l'image non confirmée",
        "clouds": "nuages {value} %",
        "sun": "soleil {value} de l'heure",
        "precipitation": "pluie {value} mm",
        "model": "Open-Meteo : {parts}",
        "pictures": "images : {seen} ({share} des votes, poids {weight})",
    },
    "en": {
        "unknown": "weather unknown (no Open-Meteo data, no sky visible)",
        "pictures_only": " from the pictures alone",
        "model_only": " from Open-Meteo, {hidden}",
        "sky_barely": "sky barely visible",
        "sky_hidden": "sky not visible",
        "agree": ", Open-Meteo and pictures agree",
        "veil": ", possible high veil",
        "disagree": ", sources disagree",
        "unconfirmed": "; {seen} seen in the picture not confirmed",
        "clouds": "clouds {value} %",
        "sun": "sun {value} of the hour",
        "precipitation": "rain {value} mm",
        "model": "Open-Meteo: {parts}",
        "pictures": "pictures: {seen} ({share} of the votes, weight {weight})",
    },
}
# Where the category comes from, for the language model's input (English, like the prompt).
_SOURCES: dict[str, str] = {
    "agree": "Open-Meteo and pictures agree",
    "close": "Open-Meteo and pictures close",
    "disagree": "sources disagree",
    "api_only": "Open-Meteo only",
    "visual_only": "pictures only",
}


@dataclass(frozen=True, slots=True)
class WeatherConsensus:
    category: str | None
    agreement: Agreement
    confidence: Confidence
    line: str  # for the UI and the MCP, in the language asked for (French by default)
    visual_votes: dict[str, float]
    outdoor_view: bool  # at least one described frame is OUTDOOR and wide or medium


@dataclass(frozen=True, slots=True)
class _Sky:
    """What the keyframes say about the sky (indoor frames left out), and whether any shows an
    outdoor scene wide enough for the weather to matter."""

    votes: dict[str, float]  # weather → summed framing weight, in frame order
    open_views: Counter[str]  # weather → wide or medium frames showing it
    outdoor_view: bool

    @property
    def total(self) -> float:
        return sum(self.votes.values())

    @property
    def opinion(self) -> str | None:
        if self.total < MIN_VISUAL_WEIGHT:
            return None
        return max(self.votes, key=self.votes.__getitem__)  # a tie: the first one seen

    @property
    def share(self) -> float:
        opinion = self.opinion
        return self.votes[opinion] / self.total if opinion is not None else 0.0


@dataclass(frozen=True, slots=True)
class _Verdict:
    category: str | None
    agreement: Agreement
    confidence: Confidence
    remark: str = ""  # a key of the line's words, written right after the category's name
    remark_values: tuple[tuple[str, str], ...] = ()  # what the remark names (``{hidden}``…)


def weather_consensus(video: Video, language: str = "fr") -> WeatherConsensus:
    """The weather of the video, how sure it is, and a line that shows both sources, in
    ``language`` (French or English)."""
    words = _WORDS.get(language, _WORDS["fr"])
    sky = _sky(video.frames)
    verdict = _decide(video.weather, sky)
    votes = {weather: round(weight, 2) for weather, weight in sky.votes.items()}
    if verdict.category is None:
        line = words["unknown"]
    else:
        details = " ; " if language != "en" else "; "
        details = details.join(
            d for d in (_model_detail(video.weather, language), _visual_detail(sky, language)) if d
        )
        values = {key: _name(value, language) if key == "seen" else words.get(value, value)
                  for key, value in verdict.remark_values}  # fmt: skip
        remark = words[verdict.remark].format(**values) if verdict.remark else ""
        line = f"{_name(verdict.category, language)}{remark} ({details})"
    return WeatherConsensus(
        category=verdict.category,
        agreement=verdict.agreement,
        confidence=verdict.confidence,
        line=line,
        visual_votes=votes,
        outdoor_view=sky.outdoor_view,
    )


def prompt_weather(consensus: WeatherConsensus) -> str | None:
    """The weather line of the language model's input: a category and its source, no numbers
    (they leak into the prose). Only with an outdoor wide or medium frame: two indoor gallery
    frames were enough for the model to write about daylight and clouds."""
    if consensus.category is None or not consensus.outdoor_view:
        return None
    source = _SOURCES.get(consensus.agreement, consensus.agreement)
    return f"{_name(consensus.category)} ({source})"


# ---------------------------------------------------------------- rules
def _sky(frames: Sequence[Frame]) -> _Sky:
    votes: defaultdict[str, float] = defaultdict(float)
    open_views: Counter[str] = Counter()
    outdoor_view = False
    for frame in frames:
        data = frame.data
        # An indoor frame says nothing of the sky, whatever its weather field claims.
        if not data or data.get("setting") == Setting.INDOOR:
            continue
        shot_type = str(data.get("shot_type") or ShotType.UNKNOWN)
        # Outdoors in a wide or medium view, the weather is part of the scene even when the sky
        # is out of frame (a street, a night stage): Open-Meteo alone may then be written.
        if shot_type in _OPEN_VIEWS and data.get("setting") == Setting.OUTDOOR:
            outdoor_view = True
        weather = data.get("weather")
        if not isinstance(weather, str) or not weather or weather == WeatherVisual.SKY_NOT_VISIBLE:
            continue
        votes[weather] += _SKY_WEIGHT.get(shot_type, _DEFAULT_WEIGHT)
        if shot_type in _OPEN_VIEWS:
            open_views[weather] += 1
    return _Sky(dict(votes), open_views, outdoor_view)


def _decide(facts: WeatherFacts | None, sky: _Sky) -> _Verdict:
    model = (facts.category if facts else None) or None
    seen = sky.opinion
    if model is None:
        if seen is None:
            return _Verdict(None, "unknown", "low")
        # Pictures alone: medium only when several wide or medium frames show the same sky.
        sure = sky.open_views[seen] >= CONFIDENT_VIEWS and sky.share >= OUTVOTE_SHARE
        confidence: Confidence = "medium" if sure else "low"
        return _Verdict(seen, "visual_only", confidence, "pictures_only")
    if seen is None:
        hidden = "sky_barely" if sky.votes else "sky_hidden"
        return _Verdict(model, "api_only", "medium", "model_only", (("hidden", hidden),))
    if model == seen:
        return _Verdict(model, "agree", "high", "agree")
    return _arbitrate(model, seen, sky, facts)


def _arbitrate(model: str, seen: str, sky: _Sky, facts: WeatherFacts | None) -> _Verdict:
    """Open-Meteo and the frames disagree: cloudiness by steps, precipitation by the model."""
    sunshine = (facts.sun_fraction if facts else None) or 0.0
    if model in _CLOUDINESS and seen in _CLOUDINESS:
        # A sunny hour the model calls overcast is a high veil: the frames win.
        if model == WeatherCategory.OVERCAST and sunshine >= SUNNY_HOUR:
            return _Verdict(seen, "close", "medium", "veil")
        if abs(_CLOUDINESS[model] - _CLOUDINESS[seen]) == 1:
            return _Verdict(WeatherCategory.PARTLY_CLOUDY.value, "close", "medium")
        # Opposite ends: many nearly unanimous frames win, else the model, unsure either way.
        many = sky.total >= OUTVOTE_WEIGHT and sky.share >= OUTVOTE_SHARE
        return _Verdict(seen if many else model, "disagree", "low", "disagree")
    precipitation = (facts.precipitation_mm if facts else None) or 0.0
    if seen in _PRECIPITATION and precipitation < TRACE_MM:
        # Rain seen on a frame without any in the model hour: bokeh or drops on the lens.
        return _Verdict(model, "disagree", "low", "unconfirmed", (("seen", seen),))
    return _Verdict(model, "disagree", "low")


# ---------------------------------------------------------------- the line
def _model_detail(facts: WeatherFacts | None, language: str = "fr") -> str:
    if facts is None:
        return ""
    words = _WORDS.get(language, _WORDS["fr"])
    parts: list[str] = []
    if facts.category:
        parts.append(_name(facts.category, language))
    if facts.cloud_cover_pct is not None:
        parts.append(words["clouds"].format(value=round(facts.cloud_cover_pct)))
    if facts.sun_fraction is not None and facts.is_day:
        parts.append(words["sun"].format(value=_percent(facts.sun_fraction)))
    if facts.temperature_c is not None:
        parts.append(f"{round(facts.temperature_c)} °C")  # round(): never « -0 »
    if facts.precipitation_mm:
        parts.append(
            words["precipitation"].format(value=_decimal(facts.precipitation_mm, language))
        )
    return words["model"].format(parts=", ".join(parts)) if parts else ""


def _visual_detail(sky: _Sky, language: str = "fr") -> str:
    seen = sky.opinion
    if seen is None:
        return ""
    return _WORDS.get(language, _WORDS["fr"])["pictures"].format(
        seen=_name(seen, language),
        share=_percent(sky.share),
        weight=_decimal(sky.total, language),
    )


def _name(category: str, language: str = "fr") -> str:
    return (_NAMES_EN if language == "en" else _NAMES).get(category, category)


def _percent(share: float) -> str:
    return f"{round(share * 100)} %"


def _decimal(value: float, language: str = "fr") -> str:
    text = f"{value:.1f}"
    return text if language == "en" else text.replace(".", ",")
