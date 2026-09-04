"""Weather access.

Real path: Open-Meteo (free, no API key). Offline path: a deterministic stub so
local pipeline runs and tests need no network.
"""
from __future__ import annotations

from datetime import date as _date

from .. import config


class WeatherUnavailable(Exception):
    """Raised when live weather cannot be retrieved (network/server error)."""


def get_weather_live(date: str, lat: float, lon: float) -> dict:
    import requests  # local import: not needed in offline mode

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "weather_code,temperature_2m_max,precipitation_sum",
        "start_date": date,
        "end_date": date,
        "timezone": config.TIMEZONE,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        daily = resp.json()["daily"]
        return {
            "weather_code": int(daily["weather_code"][0]),
            "temperature_c": float(daily["temperature_2m_max"][0]),
            "precipitation_mm": float(daily["precipitation_sum"][0]),
        }
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed unavailability
        raise WeatherUnavailable(str(exc)) from exc


def get_weather_offline(date: str, lat: float, lon: float) -> dict:
    """Deterministic pseudo-weather derived from the date (no randomness source)."""
    d = _date.fromisoformat(date)
    doy = d.timetuple().tm_yday
    # Mild seasonal + deterministic variation; rainy every ~3rd day.
    temp = 18.0 + 6.0 * ((doy % 10) / 10.0)
    precip = 4.0 if doy % 3 == 0 else 0.0
    code = 61 if precip > 0 else 1
    return {"weather_code": code, "temperature_c": round(temp, 1), "precipitation_mm": precip}


def get_weather(date: str, lat: float | None = None, lon: float | None = None) -> dict:
    lat = config.RESTAURANT_LAT if lat is None else lat
    lon = config.RESTAURANT_LON if lon is None else lon
    if not config.gemini_enabled():
        # Offline mode (no API key) also avoids external weather calls.
        return get_weather_offline(date, lat, lon)
    try:
        return get_weather_live(date, lat, lon)
    except WeatherUnavailable:
        return get_weather_offline(date, lat, lon)
