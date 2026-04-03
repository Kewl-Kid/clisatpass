#!/usr/bin/env python3

import curses
import json
import os
import sys
import time
import threading
import math
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

import satellites as sat_mod
import weather as wx_mod
import confidence as conf_mod

CONFIG_FILE = "config.json"
VERSION = "1.0.0"

# ── Color pair IDs ────────────────────────────────────────────────────────────
C_NORMAL   = 0
C_HEADER   = 1
C_TITLE    = 2
C_GRADE_S  = 3   # Strong  - bright green
C_GRADE_G  = 4   # Good    - green
C_GRADE_F  = 5   # Fair    - yellow
C_GRADE_P  = 6   # Poor    - red
C_GRADE_X  = 7   # No-go   - bright red
C_DIM      = 8
C_CYAN     = 9
C_BAR_FILL = 10
C_BAR_EMPTY= 11
C_NEXT     = 12  # Highlighted next pass
C_STATUS   = 13
C_BORDER   = 14
C_ACTIVE   = 15

GRADE_COLOR = {
    "S": C_GRADE_S,
    "G": C_GRADE_G,
    "F": C_GRADE_F,
    "P": C_GRADE_P,
    "X": C_GRADE_X,
}

# ── App state ─────────────────────────────────────────────────────────────────
@dataclass
class AppState:
    passes: list = field(default_factory=list)
    weather: Optional[dict] = None
    status_msg: str = "Initializing..."
    last_update: Optional[float] = None
    loading: bool = True
    error: Optional[str] = None
    selected_idx: int = 0
    show_detail: bool = False
    view: str = "passes"   # "passes" | "weather" | "help"


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        print(f"Config file '{CONFIG_FILE}' not found. Creating default...")
        default = {
            "location": {"name": "My Location", "lat": 33.0, "lon": -97.0, "elevation_m": 200},
            "satellites": [
                {"name": "NOAA 19", "norad_id": 33591},
                {"name": "METEOR-M 2-3", "norad_id": 57166},
            ],
            "settings": {
                "min_elevation_deg": 10, "forecast_hours": 24,
                "tle_cache_hours": 6, "pass_lookahead_hours": 24,
                "refresh_interval_sec": 60
            }
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open(CONFIG_FILE) as f:
        return json.load(f)


def fetch_all(config: dict, state: AppState):
    """Background data fetch thread."""
    loc = config["location"]
    sats = config["satellites"]
    settings = config["settings"]

    state.status_msg = "Fetching TLEs..."
    all_passes = []
    for s in sats:
        state.status_msg = f"Fetching TLE: {s['name']}..."
        try:
            passes = sat_mod.get_passes(
                s, loc,
                lookahead_hours=settings["pass_lookahead_hours"],
                min_elevation=settings["min_elevation_deg"],
                cache_hours=settings["tle_cache_hours"]
            )
            all_passes.extend(passes)
        except Exception as e:
            state.status_msg = f"Error fetching {s['name']}: {e}"
            time.sleep(1)

    all_passes.sort(key=lambda p: p["aos"])

    state.status_msg = "Fetching weather data..."
    try:
        wx = wx_mod.fetch_weather(loc["lat"], loc["lon"])
        state.weather = wx
    except Exception as e:
        state.weather = None
        state.status_msg = f"Weather error: {e}"

    # Attach confidence scores to each pass
    state.status_msg = "Computing confidence scores..."
    for p in all_passes:
        wx_at_pass = None
        if state.weather:
            wx_at_pass = wx_mod.get_weather_for_time(
                state.weather["hourly"], p["aos"])

        if wx_at_pass:
            cr = conf_mod.compute_confidence(
                max_el=p["max_el"],
                cloud_pct=wx_at_pass.get("cloud_cover"),
                precip_mm=wx_at_pass.get("precip_mm"),
                precip_prob=wx_at_pass.get("precip_prob"),
                humidity=wx_at_pass.get("humidity"),
                duration_sec=p["duration_sec"],
                visibility_m=wx_at_pass.get("visibility_m"),
            )
        else:
            cr = conf_mod.compute_confidence(
                max_el=p["max_el"],
                cloud_pct=None, precip_mm=None, precip_prob=None,
                humidity=None, duration_sec=p["duration_sec"],
                visibility_m=None,
            )
        p["confidence"] = cr
        p["wx_at_pass"] = wx_at_pass

    state.passes = all_passes
    state.last_update = time.time()
    state.loading = False
    state.status_msg = f"Updated {datetime.now().strftime('%H:%M:%S')} | {len(all_passes)} passes in next {settings['pass_lookahead_hours']}h"


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_HEADER,   curses.COLOR_BLACK,  curses.COLOR_CYAN)
    curses.init_pair(C_TITLE,    curses.COLOR_CYAN,   -1)
    curses.init_pair(C_GRADE_S,  curses.COLOR_GREEN,  -1)
    curses.init_pair(C_GRADE_G,  curses.COLOR_GREEN,  -1)
    curses.init_pair(C_GRADE_F,  curses.COLOR_YELLOW, -1)
    curses.init_pair(C_GRADE_P,  curses.COLOR_RED,    -1)
    curses.init_pair(C_GRADE_X,  curses.COLOR_RED,    -1)
    curses.init_pair(C_DIM,      curses.COLOR_WHITE,  -1)
    curses.init_pair(C_CYAN,     curses.COLOR_CYAN,   -1)
    curses.init_pair(C_BAR_FILL, curses.COLOR_GREEN,  -1)
    curses.init_pair(C_BAR_EMPTY,curses.COLOR_WHITE,  -1)
    curses.init_pair(C_NEXT,     curses.COLOR_BLACK,  curses.COLOR_GREEN)
    curses.init_pair(C_STATUS,   curses.COLOR_BLACK,  curses.COLOR_BLUE)
    curses.init_pair(C_BORDER,   curses.COLOR_CYAN,   -1)
    curses.init_pair(C_ACTIVE,   curses.COLOR_BLACK,  curses.COLOR_YELLOW)


def safe_addstr(win, y, x, text, attr=0):
    """Write text clipped to window bounds."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    max_len = w - x - 1
    if max_len <= 0:
        return
    try:
        win.addstr(y, x, text[:max_len], attr)
    except curses.error:
        pass


def draw_hline(win, y, x, length, char="-", attr=0):
    safe_addstr(win, y, x, char * length, attr)


def format_local(dt: datetime) -> str:
    """Convert UTC dt to local time string."""
    local = dt.astimezone()
    return local.strftime("%m/%d %H:%M")


def time_until(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = (dt - now).total_seconds()
    if delta < 0:
        return "NOW"
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        m, s = divmod(int(delta), 60)
        return f"{m}m{s:02d}s"
    h, rem = divmod(int(delta), 3600)
    m = rem // 60
    return f"{h}h{m:02d}m"


def is_active(p: dict) -> bool:
    now = datetime.now(timezone.utc)
    return p["aos"] <= now <= p["los"]


def is_past(p: dict) -> bool:
    now = datetime.now(timezone.utc)
    return p["los"] < now


def draw_header(win, config, state):
    h, w = win.getmaxyx()
    title = "  [*] SATPASS - Satellite Pass Tracker & LRPT Signal Analyzer  "
    loc = config["location"]["name"]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    right = f"{loc}  |  {now_str}  "
    header = title.ljust(w - len(right) - 1) + right
    safe_addstr(win, 0, 0, header[:w-1], curses.color_pair(C_HEADER) | curses.A_BOLD)

    # Nav bar
    views = [("F1", "PASSES"), ("F2", "WEATHER"), ("F3", "HELP"), ("R", "REFRESH"), ("Q", "QUIT")]
    nav_y = 1
    x = 1
    for key, label in views:
        safe_addstr(win, nav_y, x, f" {key}:", curses.color_pair(C_DIM))
        x += len(key) + 2
        attr = curses.color_pair(C_ACTIVE) | curses.A_BOLD if label.lower() == state.view else curses.color_pair(C_CYAN) | curses.A_BOLD
        safe_addstr(win, nav_y, x, label, attr)
        x += len(label) + 3

    draw_hline(win, 2, 0, w - 1, "-", curses.color_pair(C_BORDER))


def draw_status_bar(win, state):
    h, w = win.getmaxyx()
    msg = f"  {state.status_msg}"
    if state.loading:
        spinner = "|/-\\"[int(time.time() * 4) % 4]
        msg = f"  [{spinner}] {state.status_msg}"
    safe_addstr(win, h - 1, 0, msg.ljust(w - 1), curses.color_pair(C_STATUS))


def draw_pass_row(win, y, p, is_sel, is_next, w):
    now = datetime.now(timezone.utc)
    cr = p.get("confidence")

    active = is_active(p)
    past   = is_past(p)

    # Build columns
    sat_name = p["sat_name"][:14].ljust(14)
    aos_str  = format_local(p["aos"])
    los_str  = format_local(p["los"])
    dur_str  = sat_mod.format_duration(p["duration_sec"])
    el_str   = f"{p['max_el']:5.1f}d"
    az_str   = f"{sat_mod.az_to_cardinal(p['max_el_az']):3s}"
    until    = time_until(p["aos"]) if not active else ">>ACTIVE<<"

    if cr:
        score_str = f"{cr.score:5.1f}"
        grade_str = f" {cr.grade} {conf_mod.grade_label(cr.grade)}"
        bar       = cr.bar
    else:
        score_str = "  ?.?"
        grade_str = " ? UNKNOWN "
        bar       = "[--------------------]"

    line = (f" {sat_name}  AOS:{aos_str}  LOS:{los_str}  "
            f"DUR:{dur_str}  EL:{el_str}  AZ:{az_str}  "
            f"IN:{until:>10s}  SCORE:{score_str}  {grade_str}")

    if past:
        attr = curses.color_pair(C_DIM) | curses.A_DIM
    elif active:
        attr = curses.color_pair(C_GRADE_S) | curses.A_BOLD | curses.A_BLINK
    elif is_sel:
        attr = curses.color_pair(C_NEXT) | curses.A_BOLD
    elif is_next:
        attr = curses.color_pair(C_NEXT)
    else:
        attr = curses.color_pair(C_NORMAL)

    safe_addstr(win, y, 0, line.ljust(w - 1), attr)

    # Grade color inline if not highlighted
    if cr and not is_sel and not active:
        grade_x = line.index("SCORE:") + 6 + 6 + 2
        if grade_x < w - 1:
            safe_addstr(win, y, grade_x, f"{cr.grade}",
                        curses.color_pair(GRADE_COLOR.get(cr.grade, C_DIM)) | curses.A_BOLD)


def draw_passes_view(win, state, start_row):
    h, w = win.getmaxyx()
    passes = state.passes

    # Column header
    header = (" SATELLITE      AOS             LOS             DUR     "
              "ELEV   AZ    IN             SCORE  GRADE")
    safe_addstr(win, start_row, 0, header.ljust(w - 1),
                curses.color_pair(C_TITLE) | curses.A_UNDERLINE | curses.A_BOLD)
    draw_hline(win, start_row + 1, 0, w - 1, "-", curses.color_pair(C_BORDER))

    max_rows = h - start_row - 3  # leave room for status bar

    if state.loading:
        safe_addstr(win, start_row + 2, 2, "Loading satellite data...",
                    curses.color_pair(C_CYAN) | curses.A_BOLD)
        return

    if not passes:
        safe_addstr(win, start_row + 2, 2,
                    "No passes found. Check your config.json and internet connection.",
                    curses.color_pair(C_GRADE_P))
        return

    now = datetime.now(timezone.utc)
    next_idx = next((i for i, p in enumerate(passes) if not is_past(p)), -1)

    # Scroll offset
    sel = state.selected_idx
    scroll = max(0, sel - max_rows // 2)

    for i, p in enumerate(passes[scroll:scroll + max_rows]):
        idx = i + scroll
        row_y = start_row + 2 + i
        is_sel = idx == sel
        is_next = idx == next_idx and not is_sel
        draw_pass_row(win, row_y, p, is_sel, is_next, w)


def draw_detail_panel(win, state):
    """Draw detail pane for selected pass at bottom."""
    h, w = win.getmaxyx()
    if not state.passes or state.selected_idx >= len(state.passes):
        return

    p = state.passes[state.selected_idx]
    cr = p.get("confidence")
    wx = p.get("wx_at_pass")

    panel_h = 10
    panel_y = h - panel_h - 1

    # Draw box
    draw_hline(win, panel_y, 0, w - 1, "=", curses.color_pair(C_BORDER) | curses.A_BOLD)
    safe_addstr(win, panel_y, 2, f"[ Pass Detail: {p['sat_name']} ]",
                curses.color_pair(C_TITLE) | curses.A_BOLD)

    y = panel_y + 1
    col1_x, col2_x, col3_x = 2, 28, 54

    safe_addstr(win, y, col1_x, f"AOS:      {format_local(p['aos'])}", curses.color_pair(C_CYAN))
    safe_addstr(win, y, col2_x, f"LOS:      {format_local(p['los'])}", curses.color_pair(C_CYAN))
    safe_addstr(win, y, col3_x, f"Duration: {sat_mod.format_duration(p['duration_sec'])}", curses.color_pair(C_CYAN))
    y += 1
    safe_addstr(win, y, col1_x, f"Max Elev: {p['max_el']:.1f} deg", curses.color_pair(C_CYAN))
    safe_addstr(win, y, col2_x, f"Az@Max:   {p['max_el_az']:.1f}d ({sat_mod.az_to_cardinal(p['max_el_az'])})", curses.color_pair(C_CYAN))
    safe_addstr(win, y, col3_x, f"AOS Az:   {p['aos_az']:.1f}d ({sat_mod.az_to_cardinal(p['aos_az'])})", curses.color_pair(C_CYAN))
    y += 1

    if wx:
        safe_addstr(win, y, col1_x, f"Cloud:    {wx.get('cloud_cover','?')}%", curses.color_pair(C_DIM))
        safe_addstr(win, y, col2_x, f"Precip:   {wx.get('precip_mm','?')}mm ({wx.get('precip_prob','?')}%)", curses.color_pair(C_DIM))
        safe_addstr(win, y, col3_x, f"Humidity: {wx.get('humidity','?')}%", curses.color_pair(C_DIM))
        y += 1
        vis = wx.get('visibility_m')
        vis_str = f"{vis/1000:.1f}km" if vis else "?"
        safe_addstr(win, y, col1_x, f"Visibility: {vis_str}", curses.color_pair(C_DIM))
    y += 1

    if cr:
        score_attr = curses.color_pair(GRADE_COLOR.get(cr.grade, C_DIM)) | curses.A_BOLD
        safe_addstr(win, y, col1_x, f"LRPT Confidence: {cr.score:.1f}/100  Grade: {cr.grade} ({conf_mod.grade_label(cr.grade)})", score_attr)
        y += 1
        safe_addstr(win, y, col1_x, f"Score bar: {cr.bar}", curses.color_pair(C_CYAN))
        y += 1
        # Factor breakdown
        factor_line = "  ".join(
            f"{k[:3].upper()}:{v[0]:.0f}/{v[1]}"
            for k, v in cr.factors.items()
        )
        safe_addstr(win, y, col1_x, factor_line, curses.color_pair(C_DIM))


def draw_weather_view(win, state, start_row):
    h, w = win.getmaxyx()
    wx = state.weather

    safe_addstr(win, start_row, 2,
                "== CURRENT WEATHER CONDITIONS ==",
                curses.color_pair(C_TITLE) | curses.A_BOLD)

    if state.loading or not wx:
        safe_addstr(win, start_row + 2, 2, "Weather data not yet loaded...",
                    curses.color_pair(C_DIM))
        return

    cur = wx["current"]
    y = start_row + 2

    def row(label, val, color=C_CYAN):
        nonlocal y
        safe_addstr(win, y, 4, f"{label:<22} {val}", curses.color_pair(color))
        y += 1

    row("Temperature:",     f"{cur.get('temp_f','?')} F")
    row("Humidity:",        f"{cur.get('humidity','?')}%")
    row("Cloud Cover:",     f"{cur.get('cloud_cover','?')}%")
    row("Precipitation:",   f"{cur.get('precip_mm','?')} mm")
    row("Wind Speed:",      f"{cur.get('wind_mph','?')} mph")
    row("Wind Gusts:",      f"{cur.get('gusts_mph','?')} mph")
    vis = cur.get('visibility_m')
    row("Visibility:",      f"{vis/1000:.1f} km" if vis else "?")
    row("Conditions:",      wx_mod.describe_weather(cur.get('weather_code')))

    y += 1
    safe_addstr(win, y, 2, "== HOURLY FORECAST (next 12h) ==",
                curses.color_pair(C_TITLE) | curses.A_BOLD)
    y += 1

    header = f"  {'TIME':<17} {'CLOUD':>6} {'PRECIP%':>8} {'PRECIP mm':>10} {'HUMIDITY':>9} {'VIS km':>7}"
    safe_addstr(win, y, 0, header.ljust(w - 1), curses.color_pair(C_DIM) | curses.A_UNDERLINE)
    y += 1

    now = datetime.now()
    shown = 0
    for entry in wx["hourly"]:
        if shown >= 12 or y >= h - 2:
            break
        try:
            t = datetime.strptime(entry["time"], "%Y-%m-%dT%H:%M")
        except Exception:
            continue
        if t < now - timedelta(hours=1):
            continue
        vis_km = f"{entry['visibility_m']/1000:.1f}" if entry.get('visibility_m') else "?"
        line = (f"  {t.strftime('%a %m/%d %H:%M'):<17} "
                f"{str(entry.get('cloud_cover','?')):>6}% "
                f"{str(entry.get('precip_prob','?')):>7}% "
                f"{str(entry.get('precip_mm','?')):>9} mm "
                f"{str(entry.get('humidity','?')):>8}% "
                f"{vis_km:>6} km")
        safe_addstr(win, y, 0, line.ljust(w - 1), curses.color_pair(C_NORMAL))
        y += 1
        shown += 1


def draw_help_view(win, start_row):
    h, w = win.getmaxyx()
    y = start_row
    lines = [
        ("SATPASS - Satellite Pass Tracker & LRPT Signal Analyzer", C_TITLE, curses.A_BOLD),
        ("", C_NORMAL, 0),
        ("NAVIGATION", C_CYAN, curses.A_BOLD),
        ("  F1           Passes view (satellite pass schedule)", C_NORMAL, 0),
        ("  F2           Weather view (current + hourly forecast)", C_NORMAL, 0),
        ("  F3           This help screen", C_NORMAL, 0),
        ("  UP/DOWN      Navigate pass list", C_NORMAL, 0),
        ("  ENTER        Toggle detailed pass breakdown panel", C_NORMAL, 0),
        ("  R            Force refresh all data", C_NORMAL, 0),
        ("  Q            Quit", C_NORMAL, 0),
        ("", C_NORMAL, 0),
        ("PASS LIST COLUMNS", C_CYAN, curses.A_BOLD),
        ("  SATELLITE    Satellite name", C_NORMAL, 0),
        ("  AOS          Acquisition of Signal (local time)", C_NORMAL, 0),
        ("  LOS          Loss of Signal (local time)", C_NORMAL, 0),
        ("  DUR          Pass duration (minutes:seconds)", C_NORMAL, 0),
        ("  ELEV         Maximum elevation angle above horizon", C_NORMAL, 0),
        ("  AZ           Azimuth cardinal direction at max elevation", C_NORMAL, 0),
        ("  IN           Time until AOS", C_NORMAL, 0),
        ("  SCORE        LRPT confidence score (0-100)", C_NORMAL, 0),
        ("  GRADE        S=Strong G=Good F=Fair P=Poor X=No-go", C_NORMAL, 0),
        ("", C_NORMAL, 0),
        ("CONFIDENCE SCORING (LRPT/APT ~137 MHz)", C_CYAN, curses.A_BOLD),
        ("  Elevation    35pts  Primary factor - higher = longer decode window", C_NORMAL, 0),
        ("  Clouds       20pts  Overcast sky raises sky noise temperature", C_NORMAL, 0),
        ("  Precipitation 20pts Rain/snow elevates sky noise, saturates atmosphere", C_NORMAL, 0),
        ("  Humidity     10pts  High vapor increases absorption path loss", C_NORMAL, 0),
        ("  Duration     10pts  Short passes may not allow LRPT frame sync", C_NORMAL, 0),
        ("  Visibility    5pts  Fog/dense precip near antenna degrades performance", C_NORMAL, 0),
        ("", C_NORMAL, 0),
        ("CONFIG (config.json)", C_CYAN, curses.A_BOLD),
        ("  Edit config.json to set your location, tracked satellites,", C_NORMAL, 0),
        ("  minimum elevation filter, and refresh intervals.", C_NORMAL, 0),
        ("  TLEs are cached locally to avoid excessive CelesTrak requests.", C_NORMAL, 0),
    ]
    for text, color, attr in lines:
        if y >= h - 2:
            break
        safe_addstr(win, y, 0, text, curses.color_pair(color) | attr)
        y += 1


def run(stdscr, config, state):
    init_colors()
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)

    settings = config["settings"]
    refresh_interval = settings.get("refresh_interval_sec", 60)
    last_auto_refresh = 0

    def do_refresh():
        nonlocal last_auto_refresh
        state.loading = True
        state.status_msg = "Refreshing..."
        t = threading.Thread(target=fetch_all, args=(config, state), daemon=True)
        t.start()
        last_auto_refresh = time.time()

    do_refresh()

    while True:
        now = time.time()
        if now - last_auto_refresh > refresh_interval and not state.loading:
            do_refresh()

        stdscr.erase()
        h, w = stdscr.getmaxyx()

        draw_header(stdscr, config, state)
        start_row = 3

        if state.view == "passes":
            detail_offset = 10 if state.show_detail else 0
            draw_passes_view(stdscr, state, start_row)
            if state.show_detail and state.passes:
                draw_detail_panel(stdscr, state)
        elif state.view == "weather":
            draw_weather_view(stdscr, state, start_row)
        elif state.view == "help":
            draw_help_view(stdscr, start_row)

        draw_status_bar(stdscr, state)
        stdscr.refresh()

        # Input handling
        try:
            key = stdscr.getch()
        except Exception:
            key = -1

        if key in (ord('q'), ord('Q')):
            break
        elif key in (ord('r'), ord('R')):
            if not state.loading:
                do_refresh()
        elif key == curses.KEY_F1:
            state.view = "passes"
        elif key == curses.KEY_F2:
            state.view = "weather"
        elif key == curses.KEY_F3:
            state.view = "help"
        elif key == curses.KEY_UP and state.view == "passes":
            state.selected_idx = max(0, state.selected_idx - 1)
        elif key == curses.KEY_DOWN and state.view == "passes":
            state.selected_idx = min(len(state.passes) - 1, state.selected_idx + 1)
        elif key in (curses.KEY_ENTER, 10, 13) and state.view == "passes":
            state.show_detail = not state.show_detail

        time.sleep(0.1)


def main():
    config = load_config()
    state = AppState()

    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")

    try:
        curses.wrapper(run, config, state)
    except KeyboardInterrupt:
        pass
    print("satpass exited.")


if __name__ == "__main__":
    main()
