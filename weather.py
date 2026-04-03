"""
weather.py - Fetch current and forecast weather from Open-Meteo (free, no key needed)
"""

import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

def fetch_weather(lat: float, lon: float) -> dict | None:
    """
    Fetch current + hourly forecast weather.
    Returns a dict with current conditions and hourly forecast list.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "precipitation",
            "weather_code",
            "cloud_cover",
            "wind_speed_10m",
            "wind_gusts_10m",
            "visibility",
        ]),
        "hourly": ",".join([
            "cloud_cover",
            "precipitation_probability",
            "precipitation",
            "visibility",
            "relative_humidity_2m",
        ]),
        "wind_speed_unit": "mph",
        "temperature_unit": "fahrenheit",
        "timezone": "America/Chicago",
        "forecast_days": 2,
    }
    url = OPEN_METEO_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return _parse_weather(data)
    except Exception as e:
        return None

def _parse_weather(raw: dict) -> dict:
    cur = raw.get("current", {})
    hourly = raw.get("hourly", {})
    times = hourly.get("time", [])

    forecast = []
    for i, t in enumerate(times):
        forecast.append({
            "time": t,
            "cloud_cover": hourly.get("cloud_cover", [None])[i],
            "precip_prob": hourly.get("precipitation_probability", [None])[i],
            "precip_mm": hourly.get("precipitation", [None])[i],
            "visibility_m": hourly.get("visibility", [None])[i],
            "humidity": hourly.get("relative_humidity_2m", [None])[i],
        })

    return {
        "current": {
            "temp_f": cur.get("temperature_2m"),
            "humidity": cur.get("relative_humidity_2m"),
            "precip_mm": cur.get("precipitation"),
            "weather_code": cur.get("weather_code"),
            "cloud_cover": cur.get("cloud_cover"),
            "wind_mph": cur.get("wind_speed_10m"),
            "gusts_mph": cur.get("wind_gusts_10m"),
            "visibility_m": cur.get("visibility"),
        },
        "hourly": forecast,
    }

def get_weather_for_time(forecast_hourly: list, target_dt: datetime) -> dict | None:
    """Find closest hourly forecast entry for a given datetime."""
    target_str = target_dt.strftime("%Y-%m-%dT%H:00")
    for entry in forecast_hourly:
        if entry["time"] == target_str:
            return entry
    # fallback: nearest hour
    target_h = target_dt.strftime("%Y-%m-%dT%H")
    for entry in forecast_hourly:
        if entry["time"].startswith(target_h):
            return entry
    return None

WMO_DESCRIPTIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers", 81: "Moderate showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ heavy hail",
}

def describe_weather(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return WMO_DESCRIPTIONS.get(code, f"Code {code}")
