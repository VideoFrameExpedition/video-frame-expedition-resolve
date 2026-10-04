"""LLM-facing text of the capture context: place, sun, model weather (sources and doubts stated).

Weather is a model estimate for a grid cell, not an observation: the text says so, and the frame
descriptions remain the judge of what the sky looked like. Place names come from OpenStreetMap
or GeoNames (third-party data): they are sanitised like any other metadata string.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from datetime import timezone as fixed_zone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from vfe_vision.db.models import ContextPlace, ContextSun, ContextWeather, Video
from vfe_vision.domain.weather_codes import weather_label
from vfe_vision.services.context import ContextView

PHASE_FR = {
    "day": "jour",
    "golden_hour": "heure dorée",
    "blue_hour": "heure bleue",
    "nautical_twilight": "crépuscule nautique",
    "astronomical_twilight": "crépuscule astronomique",
    "night": "nuit",
}
DAY_PART_FR = {
    "morning": "matin",
    "midday": "vers midi",
    "afternoon": "après-midi",
    "evening": "soir",
}
REGIME_FR = {
    "sunlit": "soleil selon le modèle météo",
    "overcast": "ciel couvert selon le modèle météo",
    "mixed": "ciel variable selon le modèle météo",
    "clear_assumed": "ciel dégagé supposé (pas de météo)",
    "twilight": "soleil couché : lumière du ciel seulement",
}
FEATURE_FR = {
    "beach": "plage", "peak": "sommet", "island": "île", "islet": "îlot",
    "archipelago": "archipel", "bay": "baie", "cape": "cap", "cliff": "falaise",
    "volcano": "volcan", "saddle": "col", "glacier": "glacier", "water": "plan d'eau",
    "wetland": "zone humide", "wood": "bois", "spring": "source", "cave_entrance": "grotte",
    "ridge": "crête", "valley": "vallée", "dune": "dune", "rock": "rocher",
}  # fmt: skip
COMPARISON_FR = {
    "consistent": "cohérent avec la lumière naturelle",
    "warmer": "plus chaud que la lumière naturelle (tungstène, LED chaude, étalonnage ?)",
    "cooler": "plus froid que la lumière naturelle (ombre, préréglage tungstène, écrans ?)",
}
STAGE_FR = {"place": "lieu", "weather": "météo", "sun": "soleil"}
_COMPASS_FR = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO")  # fmt: skip


def compass(degrees: float) -> str:
    return _COMPASS_FR[round(degrees / 22.5) % 16]


def _clean(value: Any, limit: int = 120) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).replace("`", "'")
    return text[:limit] or None


def _num(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _zone(video: Video) -> Any:
    if video.capture_timezone:
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(video.capture_timezone)
    if video.capture_utc_offset_min is not None:
        return fixed_zone(timedelta(minutes=video.capture_utc_offset_min))
    return UTC


def _clock(value: Any, zone: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(zone).strftime("%H:%M")
    except ValueError:
        return None


def place_line(place: ContextPlace) -> str:
    label = _clean(place.label) or "lieu inconnu"
    if place.source == "nominatim":
        origin = "OpenStreetMap"
    else:
        origin = "répertoire hors-ligne GeoNames, approximatif"
    if place.data.get("location_source") == "folder_default":
        origin += " ; position par défaut du dossier, pas celle du tournage"
    text = f"{label} ({origin})"
    feature = place.data.get("feature")
    if isinstance(feature, dict) and _clean(feature.get("name")):
        distance = feature.get("distance_m")
        near = f", {round(distance)} m" if isinstance(distance, int | float) else ""
        kind = _clean(feature.get("type"), 40) or "élément naturel"
        text += (
            f" · à proximité : {_clean(feature.get('name'))} ({FEATURE_FR.get(kind, kind)}{near})"
        )
    return text


def sun_line(sun: ContextSun, zone: Any) -> str:
    data = sun.data
    parts = [
        f"{_num(sun.elevation_deg)}° au-dessus de l'horizon"
        if sun.elevation_deg >= 0
        else f"{_num(-sun.elevation_deg)}° sous l'horizon",
        f"azimut {round(sun.azimuth_deg)}° ({compass(sun.azimuth_deg)})",
    ]
    if sun.light_phase:
        phase = PHASE_FR.get(sun.light_phase, sun.light_phase)
        part = DAY_PART_FR.get(sun.day_part or "", "")
        parts.append(f"{phase}{' du ' + part if part in {'matin', 'soir'} else ''}")
    else:
        window = data.get("window") or {}
        phases = " → ".join(PHASE_FR.get(p, p) for p in window.get("phases", []))
        parts.append(f"phase incertaine ({phases}) : heure de tournage seulement probable")
    direction = data.get("direction")
    if direction:
        parts.append("le soleil monte" if direction == "rising" else "le soleil descend")
    events = data.get("events") or {}
    rise = _clock((events.get("rising") or {}).get("sun"), zone)
    set_ = _clock((events.get("setting") or {}).get("sun"), zone)
    if rise or set_:
        parts.append(f"lever {rise or '—'}, coucher {set_ or '—'} (heure locale)")
    elif events.get("polar"):
        parts.append("soleil de minuit" if events["polar"] == "polar_day" else "nuit polaire")
    window = data.get("window")
    if data.get("time_confidence") == "medium" and isinstance(window, dict):
        parts.append(
            "heure de tournage seulement probable : élévation "
            f"{_num(window.get('elevation_min', 0))}–{_num(window.get('elevation_max', 0))}° "
            "sur ± 30 min"
        )
    take = data.get("take")
    if isinstance(take, dict) and len(take.get("phases", [])) > 1:
        crossed = " → ".join(PHASE_FR.get(p, p) for p in take["phases"])
        parts.append(f"pendant la prise : {crossed}")
    return " · ".join(parts)


def light_line(sun: ContextSun, view: ContextView) -> str | None:
    light = sun.data.get("light")
    if not isinstance(light, dict):
        return None
    ambient = light.get("ambient_k") or []
    direct = light.get("direct_k")
    ranges = []
    if direct:
        ranges.append(f"directe {direct[0]}–{direct[1]} K")
    if len(ambient) == 2:
        ranges.append(f"ambiante {ambient[0]}–{ambient[1]} K")
    text = ", ".join(ranges) + f" ({REGIME_FR.get(light.get('regime', ''), '')})"
    if light.get("off_locus"):
        text += " — lumière crépusculaire : Kelvin seulement indicatif"
    if view.measured_cct_k:
        text += f" · mesurée sur les images : {view.measured_cct_k} K"
        if view.light_comparison:
            text += f" → {COMPARISON_FR.get(view.light_comparison, view.light_comparison)}"
    return text


def weather_line(weather: ContextWeather) -> str:
    values = weather.data.get("values") or {}
    grid = weather.data.get("grid") or {}
    parts = [weather_label(weather.weather_code) or "conditions inconnues"]
    if values.get("temperature_c") is not None:
        parts.append(f"{_num(values['temperature_c'])} °C")
    if values.get("relative_humidity_pct") is not None:
        parts.append(f"humidité {round(values['relative_humidity_pct'])} %")
    if values.get("wind_speed_kmh") is not None:
        wind = f"vent {round(values['wind_speed_kmh'])} km/h"
        if values.get("wind_direction_deg") is not None:
            wind += f" de {compass(values['wind_direction_deg'])}"
        if values.get("wind_gusts_kmh") is not None:
            wind += f" (rafales {round(values['wind_gusts_kmh'])})"
        parts.append(wind)
    layers = [values.get(k) for k in ("cloud_low_pct", "cloud_mid_pct", "cloud_high_pct")]
    known = [round(v) for v in layers if isinstance(v, int | float)]
    if len(known) == len(layers):
        parts.append("nuages bas/moyens/hauts " + "/".join(map(str, known)) + " %")
    if weather.data.get("sun_fraction") is not None:
        parts.append(f"soleil {round(weather.data['sun_fraction'] * 100)} % de l'heure")
    rain = values.get("precipitation_mm")
    if rain is not None:
        parts.append("pas de précipitations" if rain == 0 else f"précipitations {_num(rain)} mm/h")
    if values.get("visibility_m") is not None:
        parts.append(f"visibilité {_num(values['visibility_m'] / 1000)} km")
    distance = grid.get("distance_km")
    cell = f", cellule à {_num(distance)} km" if isinstance(distance, int | float) else ""
    caveat = [f"modèle Open-Meteo {weather.source}{cell} — estimation, pas une observation"]
    if weather.data.get("time_confidence") == "medium":
        caveat.append("heure de tournage seulement probable")
    if weather.provisional:
        caveat.append("données provisoires")
    return " · ".join(parts) + f" ({'; '.join(caveat)}). {weather.data.get('attribution', '')}"


def context_summary(view: ContextView) -> str | None:
    """One line for the video manifest."""
    parts = []
    if view.place and view.place.label:
        folder = view.place.data.get("location_source") == "folder_default"
        suffix = " (position par défaut du dossier)" if folder else ""
        parts.append((_clean(view.place.label) or "") + suffix)
    if view.sun:
        if view.sun.light_phase:
            parts.append(
                f"{PHASE_FR.get(view.sun.light_phase, view.sun.light_phase)} "
                f"(soleil à {round(view.sun.elevation_deg)}°)"
            )
        else:
            parts.append("phase du soleil incertaine")
    if view.weather:
        temp = (view.weather.data.get("values") or {}).get("temperature_c")
        label = weather_label(view.weather.weather_code) or "météo"
        degrees = f" {_num(temp)} °C" if temp is not None else ""
        parts.append(f"{label}{degrees} (modèle)")
    return " · ".join(p for p in parts if p) or None


def context_text(view: ContextView) -> str:
    video = view.video
    zone = _zone(video)
    lines = [f"# Contexte de tournage — {_clean(video.title) or video.filename}"]
    if video.captured_at is not None:
        local = video.captured_at.astimezone(zone)
        lines.append(
            f"- heure de tournage : {local.isoformat(timespec='minutes')} "
            f"(confiance : {video.captured_at_confidence or 'inconnue'})"
        )
    if video.latitude is not None and video.longitude is not None:
        lines.append(
            f"- position : {video.latitude:.5f}, {video.longitude:.5f} "
            f"(source : {video.location_source})"
        )
    if view.place:
        lines.append(f"- lieu : {place_line(view.place)}")
    if view.sun:
        lines.append(f"- soleil (calculé) : {sun_line(view.sun, zone)}")
        light = light_line(view.sun, view)
        if light:
            lines.append(f"- lumière naturelle théorique : {light}")
    if view.weather:
        lines.append(f"- météo : {weather_line(view.weather)}")
    for stage, reason in view.notes.items():
        lines.append(f"- {STAGE_FR.get(stage, stage)} : {_clean(reason, 200)}")
    if not view.online_services:
        lines.append(
            "- services en ligne désactivés"
            + ("" if view.weather else " : pas de météo")
            + " (données déjà calculées conservées)"
        )
    if len(lines) == 1:
        lines.append("- aucun contexte : ni position ni heure de tournage exploitables")
    return "\n".join(lines)
