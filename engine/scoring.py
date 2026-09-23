"""Numeric helpers shared by feature extraction and support scoring.

All evidence-dimension scores use one convention, defined once here:

    score == 0.5  <=>  the value sits exactly on this claim policy's minimum
    score == 1.0  <=>  the value is at or beyond the policy's "comfortable" level
    score  < 0.5  <=>  the value is below the policy floor (a gate will also have fired)

Anchoring 0.5 to the policy floor is what makes the five scores comparable to each
other and readable without knowing each claim's thresholds. It also means the scores
move when a policy version changes thresholds, which is why `policy_version` travels
with every decision.
"""
from __future__ import annotations

from typing import Sequence


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


def band_score(value: float, minimum: float, good: float) -> float:
    """Score a value where higher is better.

    `minimum` maps to 0.5, `good` maps to 1.0, and the scale below `minimum` continues
    linearly at the same slope down to 0.0.
    """
    span = good - minimum
    if span <= 0:
        return 1.0 if value >= minimum else 0.0
    return clamp(0.5 + 0.5 * (value - minimum) / span)


def inverse_band_score(value: float, maximum: float, good: float) -> float:
    """Score a value where lower is better (ages, gaps, variability).

    `maximum` (the policy ceiling) maps to 0.5, `good` or lower maps to 1.0.
    """
    span = maximum - good
    if span <= 0:
        return 1.0 if value <= maximum else 0.0
    return clamp(0.5 + 0.5 * (maximum - value) / span)


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of an empty sequence")
    return sum(values) / len(values)


def sample_sd(values: Sequence[float]) -> float:
    """Sample standard deviation (n-1). Returns 0.0 for fewer than two values."""
    n = len(values)
    if n < 2:
        return 0.0
    m = mean(values)
    return (sum((v - m) ** 2 for v in values) / (n - 1)) ** 0.5


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, `q` in [0, 100]. Requires a non-empty sequence."""
    if not values:
        raise ValueError("percentile of an empty sequence")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * clamp(q / 100.0)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def interpolate(x: float, anchors: Sequence[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation through `anchors`, sorted by x, clamped at the ends."""
    pts = sorted(anchors)
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def iso_duration(hours: float) -> str:
    """Render whole hours as an ISO-8601 duration, preferring days where exact."""
    if hours <= 0:
        return "PT0S"
    if hours % 24 == 0:
        return f"P{int(hours // 24)}D"
    if hours == int(hours):
        return f"PT{int(hours)}H"
    return f"PT{int(hours * 60)}M"


def duration_hours(iso: str) -> float:
    """Parse the small ISO-8601 duration subset this engine emits back into hours."""
    if not iso.startswith("P"):
        raise ValueError(f"not an ISO-8601 duration: {iso!r}")
    body = iso[1:]
    days = 0.0
    if "T" in body:
        date_part, time_part = body.split("T", 1)
    else:
        date_part, time_part = body, ""
    if date_part:
        if not date_part.endswith("D"):
            raise ValueError(f"unsupported duration date part: {iso!r}")
        days = float(date_part[:-1])
    hours = days * 24
    if time_part:
        number = ""
        for ch in time_part:
            if ch.isdigit() or ch == ".":
                number += ch
            elif ch == "H":
                hours += float(number)
                number = ""
            elif ch == "M":
                hours += float(number) / 60
                number = ""
            elif ch == "S":
                hours += float(number) / 3600
                number = ""
            else:
                raise ValueError(f"unsupported duration unit {ch!r} in {iso!r}")
    return hours


def longest_duration(isos: Sequence[str]) -> str | None:
    """The longest of several ISO-8601 durations, or None when there are none."""
    parsed = [(duration_hours(i), i) for i in isos if i]
    if not parsed:
        return None
    return max(parsed)[1]
