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
    Fetch current + hourly forecast weather from Open-Meteo.
    Returns a dict with current conditions and hourly forecast list.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": "true",
        "hourly": ",".join([
            "cloudcover",
            "precipitation_probability",
            "precipitation",
            "visibility",
            "relativehumidity_2m",
            "temperature_2m",
            "windspeed_10m",
            "windgusts_10m",
            "weathercode",
        ]),
        "wind_speed_unit": "mph",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "forecast_days": 2,
    }
    url = OPEN_METEO_URL + "?" + urllib.parse.urlencode(params)

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return _parse_weather(data)
    except Exception:
        return None


def _parse_weather(raw: dict) -> dict:
    cw = raw.get("current_weather", {})
    hourly = raw.get("hourly", {})
    times = hourly.get("time", [])

    # If current_weather is not populated, attempt to infer from hourly at now
    now = datetime.now(timezone.utc)
    current_entry = None
    if cw:
        current_entry = {
            "temp_f": cw.get("temperature"),
            "humidity": hourly.get("relativehumidity_2m", [None])[0] if hourly.get("relativehumidity_2m") else None,
            "precip_mm": hourly.get("precipitation", [None])[0] if hourly.get("precipitation") else None,
            "weather_code": cw.get("weathercode"),
            "cloud_cover": hourly.get("cloudcover", [None])[0] if hourly.get("cloudcover") else None,
            "wind_mph": cw.get("windspeed"),
            "gusts_mph": hourly.get("windgusts_10m", [None])[0] if hourly.get("windgusts_10m") else None,
            "visibility_m": hourly.get("visibility", [None])[0] if hourly.get("visibility") else None,
        }
    else:
        # Fallback to nearest hourly values
        target = now.replace(minute=0, second=0, microsecond=0)
        target_str = target.strftime("%Y-%m-%dT%H:00")
        idx = None
        for i, t in enumerate(times):
            if t.startswith(target_str):
                idx = i
                break
        if idx is None and times:
            idx = 0
        if idx is not None:
            current_entry = {
                "temp_f": hourly.get("temperature_2m", [None])[idx] if hourly.get("temperature_2m") else None,
                "humidity": hourly.get("relativehumidity_2m", [None])[idx] if hourly.get("relativehumidity_2m") else None,
                "precip_mm": hourly.get("precipitation", [None])[idx] if hourly.get("precipitation") else None,
                "weather_code": hourly.get("weathercode", [None])[idx] if hourly.get("weathercode") else None,
                "cloud_cover": hourly.get("cloudcover", [None])[idx] if hourly.get("cloudcover") else None,
                "wind_mph": hourly.get("windspeed_10m", [None])[idx] if hourly.get("windspeed_10m") else None,
                "gusts_mph": hourly.get("windgusts_10m", [None])[idx] if hourly.get("windgusts_10m") else None,
                "visibility_m": hourly.get("visibility", [None])[idx] if hourly.get("visibility") else None,
            }

    forecast = []
    for i, t in enumerate(times):
        forecast.append({
            "time": t,
            "cloud_cover": hourly.get("cloudcover", [None])[i],
            "precip_prob": hourly.get("precipitation_probability", [None])[i],
            "precip_mm": hourly.get("precipitation", [None])[i],
            "visibility_m": hourly.get("visibility", [None])[i],
            "humidity": hourly.get("relativehumidity_2m", [None])[i],
        })

    return {
        "current": current_entry or {},
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
