"""
confidence.py - LRPT signal reception confidence scoring engine

LRPT (Low Rate Picture Transmission) is used by METEOR-M satellites on ~137 MHz.
NOAA satellites use APT (analog) on similar frequencies.
Both share similar RF propagation characteristics.

Scoring factors (weights sum to 100):
  - Max elevation angle        (35 pts) - primary factor
  - Cloud cover                (20 pts) - minimal impact on VHF but moisture matters
  - Precipitation              (20 pts) - rain fade, sky noise
  - Atmospheric humidity/RFI   (10 pts)
  - Pass duration              (10 pts)
  - Visibility / fog           ( 5 pts)
"""

import math
from dataclasses import dataclass

@dataclass
class ConfidenceResult:
    score: float          # 0-100
    grade: str            # S (Strong), G (Good), F (Fair), P (Poor), X (No go)
    bar: str              # ASCII progress bar
    factors: dict         # breakdown of each factor score
    notes: list[str]      # human-readable explanations


def score_elevation(max_el: float) -> tuple[float, str]:
    """
    Elevation is king for low-orbit VHF passes.
    <10 deg: horizon noise, multipath, terrain blockage
    10-20: marginal, short decode windows
    20-40: good
    40-60: very good
    >60: excellent
    """
    if max_el < 10:
        return 0.0, f"Very low pass ({max_el:.0f}deg) - likely blocked"
    elif max_el < 20:
        score = 35 * ((max_el - 10) / 10) * 0.5
        return round(score, 1), f"Low pass ({max_el:.0f}deg) - short decode window"
    elif max_el < 35:
        score = 35 * (0.5 + 0.3 * ((max_el - 20) / 15))
        return round(score, 1), f"Moderate elevation ({max_el:.0f}deg)"
    elif max_el < 60:
        score = 35 * (0.8 + 0.15 * ((max_el - 35) / 25))
        return round(score, 1), f"Good elevation ({max_el:.0f}deg)"
    else:
        return 35.0, f"Excellent elevation ({max_el:.0f}deg) - overhead pass"


def score_clouds(cloud_pct: float | None) -> tuple[float, str]:
    """
    VHF (~137 MHz) passes through clouds easily.
    However heavy cloud cover = likely precipitation + higher sky noise temp.
    Light impact but still relevant for overall confidence.
    """
    if cloud_pct is None:
        return 10.0, "Cloud data unavailable (assuming partial)"
    if cloud_pct <= 25:
        return 20.0, f"Clear skies ({cloud_pct:.0f}% cloud)"
    elif cloud_pct <= 50:
        score = 20.0 - (cloud_pct - 25) * 0.16
        return round(score, 1), f"Partly cloudy ({cloud_pct:.0f}%)"
    elif cloud_pct <= 80:
        score = 16.0 - (cloud_pct - 50) * 0.27
        return round(score, 1), f"Mostly cloudy ({cloud_pct:.0f}%)"
    else:
        score = 8.0 - (cloud_pct - 80) * 0.15
        return round(max(score, 2.0), 1), f"Overcast ({cloud_pct:.0f}%)"


def score_precipitation(precip_mm: float | None, precip_prob: float | None) -> tuple[float, str]:
    """
    Rain causes increased sky noise temperature and can cause SDR issues.
    At 137 MHz, rain fade is minimal but sky noise and atmospheric absorption
    can degrade weak LRPT signals. Heavy rain near the antenna is the real risk.
    """
    if precip_mm is None and precip_prob is None:
        return 15.0, "Precip data unavailable"

    prob = precip_prob or 0
    mm = precip_mm or 0

    if mm == 0 and prob < 20:
        return 20.0, "No precipitation"
    elif mm < 0.5 and prob < 40:
        score = 20.0 - prob * 0.1
        return round(score, 1), f"Trace precip possible ({prob:.0f}% chance)"
    elif mm < 2.0:
        score = 15.0 - mm * 2
        return round(max(score, 8.0), 1), f"Light rain ({mm:.1f}mm, {prob:.0f}% chance)"
    elif mm < 5.0:
        score = 10.0 - mm * 1.5
        return round(max(score, 3.0), 1), f"Moderate rain ({mm:.1f}mm) - sky noise elevated"
    else:
        return 2.0, f"Heavy rain ({mm:.1f}mm) - significant sky noise, avoid if possible"


def score_humidity(humidity: float | None) -> tuple[float, str]:
    """
    High humidity increases atmospheric water vapor content.
    At 137 MHz this has minor but measurable effect on path loss.
    More importantly, humidity near 100% means fog/precipitation risk.
    """
    if humidity is None:
        return 5.0, "Humidity data unavailable"
    if humidity < 50:
        return 10.0, f"Low humidity ({humidity:.0f}%) - minimal attenuation"
    elif humidity < 70:
        return 8.0, f"Moderate humidity ({humidity:.0f}%)"
    elif humidity < 85:
        return 6.0, f"High humidity ({humidity:.0f}%) - slight attenuation"
    else:
        return 3.0, f"Very high humidity ({humidity:.0f}%) - moisture absorption"


def score_duration(duration_sec: int) -> tuple[float, str]:
    """
    Longer passes allow more complete image frames.
    LRPT frame sync needs at least ~60s to lock. Full images need 5-10 min.
    """
    minutes = duration_sec / 60
    if minutes < 2:
        return 1.0, f"Very short pass ({minutes:.1f}min) - no lock time"
    elif minutes < 4:
        score = 10 * (minutes - 2) / 2
        return round(score, 1), f"Short pass ({minutes:.1f}min) - partial image"
    elif minutes < 7:
        score = 10 * (0.5 + 0.4 * (minutes - 4) / 3)
        return round(score, 1), f"Decent pass ({minutes:.1f}min)"
    else:
        return 10.0, f"Long pass ({minutes:.1f}min) - full image possible"


def score_visibility(visibility_m: float | None) -> tuple[float, str]:
    """
    Very low visibility = fog/dense precip near the antenna.
    Can cause feed/cable moisture ingress in outdoor setups.
    """
    if visibility_m is None:
        return 4.0, "Visibility data unavailable"
    km = visibility_m / 1000
    if km > 10:
        return 5.0, f"Excellent visibility ({km:.0f}km)"
    elif km > 5:
        return 4.0, f"Good visibility ({km:.1f}km)"
    elif km > 2:
        return 2.5, f"Reduced visibility ({km:.1f}km) - fog/haze"
    else:
        return 1.0, f"Very poor visibility ({km:.1f}km) - dense fog"


def make_bar(score: float, width: int = 20) -> str:
    filled = int(round((score / 100) * width))
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}]"


def grade(score: float) -> str:
    if score >= 85: return "S"   # Strong
    if score >= 70: return "G"   # Good
    if score >= 50: return "F"   # Fair
    if score >= 30: return "P"   # Poor
    return "X"                   # No-go


def grade_label(g: str) -> str:
    return {"S": "STRONG", "G": "GOOD", "F": "FAIR", "P": "POOR", "X": "NO-GO"}[g]


def compute_confidence(
    max_el: float,
    cloud_pct: float | None,
    precip_mm: float | None,
    precip_prob: float | None,
    humidity: float | None,
    duration_sec: int,
    visibility_m: float | None,
) -> ConfidenceResult:

    el_score, el_note       = score_elevation(max_el)
    cl_score, cl_note       = score_clouds(cloud_pct)
    pr_score, pr_note       = score_precipitation(precip_mm, precip_prob)
    hu_score, hu_note       = score_humidity(humidity)
    du_score, du_note       = score_duration(duration_sec)
    vi_score, vi_note       = score_visibility(visibility_m)

    total = el_score + cl_score + pr_score + hu_score + du_score + vi_score
    total = round(min(max(total, 0), 100), 1)

    g = grade(total)
    return ConfidenceResult(
        score=total,
        grade=g,
        bar=make_bar(total),
        factors={
            "elevation":    (el_score, 35, el_note),
            "clouds":       (cl_score, 20, cl_note),
            "precipitation":(pr_score, 20, pr_note),
            "humidity":     (hu_score, 10, hu_note),
            "duration":     (du_score, 10, du_note),
            "visibility":   (vi_score,  5, vi_note),
        },
        notes=[el_note, cl_note, pr_note, hu_note, du_note, vi_note]
    )
