"""
satellites.py - TLE fetching, caching, and pass prediction using skyfield
"""

import json
import os
import time
import math
from datetime import datetime, timezone, timedelta
from skyfield.api import load, wgs84, EarthSatellite
from skyfield.timelib import Time

TLE_CACHE_FILE = "tle_cache.json"
CELESTRAK_URL = "https://celestrak.org/SOCRATES/query.php"

# CelesTrak catalog URL per NORAD ID
def tle_url(norad_id: int) -> str:
    return f"https://celestrak.org/satcat/tle.php?CATNR={norad_id}"

def fetch_tle(norad_id: int, name: str) -> tuple[str, str] | None:
    """Fetch TLE lines from CelesTrak for a given NORAD ID."""
    import urllib.request
    url = tle_url(norad_id)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            text = resp.read().decode("utf-8").strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        # lines[0] = name, lines[1] = TLE line 1, lines[2] = TLE line 2
        if len(lines) >= 3:
            return lines[1], lines[2]
        elif len(lines) == 2:
            return lines[0], lines[1]
    except Exception as e:
        return None

def load_tle_cache(cache_file: str) -> dict:
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_tle_cache(cache: dict, cache_file: str):
    with open(cache_file, "w") as f:
        json.dump(cache, f, indent=2)

def get_tle(norad_id: int, name: str, cache_hours: float = 6) -> tuple[str, str] | None:
    """Get TLE from cache or fetch fresh."""
    cache = load_tle_cache(TLE_CACHE_FILE)
    key = str(norad_id)
    now = time.time()

    if key in cache:
        entry = cache[key]
        age_hours = (now - entry["fetched_at"]) / 3600
        if age_hours < cache_hours:
            return entry["line1"], entry["line2"]

    result = fetch_tle(norad_id, name)
    if result:
        cache[key] = {
            "name": name,
            "line1": result[0],
            "line2": result[1],
            "fetched_at": now
        }
        save_tle_cache(cache, TLE_CACHE_FILE)
        return result[0], result[1]
    elif key in cache:
        # Return stale cache as fallback
        return cache[key]["line1"], cache[key]["line2"]
    return None

def get_passes(sat_config: dict, location: dict, lookahead_hours: float,
               min_elevation: float, cache_hours: float) -> list[dict]:
    """
    Predict passes for a satellite over the next lookahead_hours.
    Returns list of pass dicts with AOS, LOS, max_el, azimuth at max, duration.
    """
    norad_id = sat_config["norad_id"]
    name = sat_config["name"]

    tle = get_tle(norad_id, name, cache_hours)
    if not tle:
        return []

    line1, line2 = tle
    ts = load.timescale()
    satellite = EarthSatellite(line1, line2, name, ts)
    observer = wgs84.latlon(location["lat"], location["lon"],
                            elevation_m=location.get("elevation_m", 0))

    now_utc = datetime.now(timezone.utc)
    t0 = ts.from_datetime(now_utc)
    t1 = ts.from_datetime(now_utc + timedelta(hours=lookahead_hours))

    try:
        times, events = satellite.find_events(observer, t0, t1,
                                              altitude_degrees=min_elevation)
    except Exception:
        return []

    passes = []
    current_pass = {}

    for ti, event in zip(times, events):
        dt = ti.utc_datetime()
        if event == 0:  # AOS
            current_pass = {"aos": dt, "max_el": 0.0, "max_el_az": 0.0,
                            "sat_name": name, "norad_id": norad_id}
        elif event == 1:  # Max elevation
            diff = satellite - observer
            topocentric = diff.at(ti)
            alt, az, _ = topocentric.altaz()
            current_pass["max_el"] = round(alt.degrees, 1)
            current_pass["max_el_az"] = round(az.degrees, 1)
        elif event == 2:  # LOS
            if "aos" in current_pass:
                current_pass["los"] = dt
                duration = (dt - current_pass["aos"]).total_seconds()
                current_pass["duration_sec"] = int(duration)
                # Sample azimuth at AOS
                t_aos = ts.from_datetime(current_pass["aos"])
                diff = satellite - observer
                topo_aos = diff.at(t_aos)
                alt_aos, az_aos, _ = topo_aos.altaz()
                current_pass["aos_az"] = round(az_aos.degrees, 1)
                if current_pass["max_el"] >= min_elevation:
                    passes.append(dict(current_pass))
                current_pass = {}

    return passes

def az_to_cardinal(degrees: float) -> str:
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    idx = round(degrees / 22.5) % 16
    return dirs[idx]

def format_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"

def tle_age_hours(norad_id: int) -> float | None:
    cache = load_tle_cache(TLE_CACHE_FILE)
    key = str(norad_id)
    if key in cache:
        return (time.time() - cache[key]["fetched_at"]) / 3600
    return None
