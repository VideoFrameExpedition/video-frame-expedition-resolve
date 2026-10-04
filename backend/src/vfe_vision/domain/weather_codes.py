"""WMO weather interpretation codes (WW) as returned by Open-Meteo, with FR/EN labels.

The API only returns numbers. English labels follow Open-Meteo's documentation; French labels are
ours. Thunderstorm codes differ between the documentation (95/96/97/99) and the source (95/96/99):
all are accepted, and an unknown code falls back to « Code WMO n ».
"""

from __future__ import annotations

from enum import StrEnum


class WeatherCategory(StrEnum):
    """Coarse families used for search filters."""

    CLEAR = "clear"
    PARTLY_CLOUDY = "partly_cloudy"
    OVERCAST = "overcast"
    FOG = "fog"
    DRIZZLE = "drizzle"
    RAIN = "rain"
    SNOW = "snow"
    THUNDERSTORM = "thunderstorm"


_CODES: dict[int, tuple[WeatherCategory, str, str]] = {
    0: (WeatherCategory.CLEAR, "Ciel dégagé", "Clear sky"),
    1: (WeatherCategory.CLEAR, "Plutôt dégagé", "Mainly clear"),
    2: (WeatherCategory.PARTLY_CLOUDY, "Partiellement nuageux", "Partly cloudy"),
    3: (WeatherCategory.OVERCAST, "Couvert", "Overcast"),
    45: (WeatherCategory.FOG, "Brouillard", "Fog"),
    48: (WeatherCategory.FOG, "Brouillard givrant", "Depositing rime fog"),
    51: (WeatherCategory.DRIZZLE, "Bruine légère", "Light drizzle"),
    53: (WeatherCategory.DRIZZLE, "Bruine modérée", "Moderate drizzle"),
    55: (WeatherCategory.DRIZZLE, "Bruine dense", "Dense drizzle"),
    56: (WeatherCategory.DRIZZLE, "Bruine verglaçante légère", "Light freezing drizzle"),
    57: (WeatherCategory.DRIZZLE, "Bruine verglaçante dense", "Dense freezing drizzle"),
    61: (WeatherCategory.RAIN, "Pluie faible", "Slight rain"),
    63: (WeatherCategory.RAIN, "Pluie modérée", "Moderate rain"),
    65: (WeatherCategory.RAIN, "Pluie forte", "Heavy rain"),
    66: (WeatherCategory.RAIN, "Pluie verglaçante faible", "Light freezing rain"),
    67: (WeatherCategory.RAIN, "Pluie verglaçante forte", "Heavy freezing rain"),
    71: (WeatherCategory.SNOW, "Neige faible", "Slight snowfall"),
    73: (WeatherCategory.SNOW, "Neige modérée", "Moderate snowfall"),
    75: (WeatherCategory.SNOW, "Neige forte", "Heavy snowfall"),
    77: (WeatherCategory.SNOW, "Neige en grains", "Snow grains"),
    80: (WeatherCategory.RAIN, "Averses faibles", "Slight rain showers"),
    81: (WeatherCategory.RAIN, "Averses modérées", "Moderate rain showers"),
    82: (WeatherCategory.RAIN, "Averses violentes", "Violent rain showers"),
    85: (WeatherCategory.SNOW, "Averses de neige faibles", "Slight snow showers"),
    86: (WeatherCategory.SNOW, "Averses de neige fortes", "Heavy snow showers"),
    95: (WeatherCategory.THUNDERSTORM, "Orage", "Thunderstorm"),
    96: (WeatherCategory.THUNDERSTORM, "Orage avec grêle faible", "Thunderstorm with slight hail"),
    97: (WeatherCategory.THUNDERSTORM, "Orage violent", "Heavy thunderstorm"),
    99: (WeatherCategory.THUNDERSTORM, "Orage avec forte grêle", "Thunderstorm with heavy hail"),
}

KNOWN_CODES = frozenset(_CODES)


def weather_category(code: int | None) -> WeatherCategory | None:
    if code is None or code not in _CODES:
        return None
    return _CODES[code][0]


def weather_label(code: int | None, language: str = "fr") -> str | None:
    if code is None:
        return None
    entry = _CODES.get(code)
    if entry is None:
        return f"Code WMO {code}" if language == "fr" else f"WMO code {code}"
    return entry[1] if language == "fr" else entry[2]
