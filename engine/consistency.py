"""Named cross-signal consistency rules.

A consistency rule looks for evidence that disagrees with itself. Disagreement is not
the same as a claim being false: it means the evidence cannot be trusted to settle the
question either way, so the honest outcome is abstention rather than a confident call in
either direction.

Each rule returns a Finding or None. Rules must be pure and must never mutate the
evidence. A rule that cannot be evaluated (because the signals it compares are absent)
returns None rather than guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from engine.normalize import NormalizedEvidence
from engine.reasons import ReasonCode
from engine.schemas import EvaluationRequest
from engine.scoring import mean, percentile, sample_sd
from engine.signals import Signal


@dataclass(frozen=True)
class Finding:
    code: ReasonCode
    detail: str
    penalty: float
    measurements: dict[str, float] = field(default_factory=dict)


RuleFn = Callable[[NormalizedEvidence, EvaluationRequest], "Finding | None"]
_RULES: dict[str, RuleFn] = {}


def rule(name: str) -> Callable[[RuleFn], RuleFn]:
    def register(fn: RuleFn) -> RuleFn:
        _RULES[name] = fn
        return fn

    return register


def get_rule(name: str) -> RuleFn:
    if name not in _RULES:
        raise KeyError(f"unknown consistency rule {name!r}; known: {sorted(_RULES)}")
    return _RULES[name]


def rule_names() -> list[str]:
    return sorted(_RULES)


# --------------------------------------------------------------------------- rules


@rule("rhr_vs_heart_rate_detail")
def _rhr_vs_heart_rate_detail(
    norm: NormalizedEvidence, request: EvaluationRequest
) -> Finding | None:
    """A daily resting-heart-rate summary should sit near the bottom of the day's heart rate.

    When the summary and the interval data disagree it usually means the two came from
    different syncs, or the summary was computed over a different day than the detail.
    Either way the pair cannot both be describing this window.
    """
    rhr = norm.window_values(Signal.RESTING_HEART_RATE)
    hr = norm.window_values(Signal.HEART_RATE)
    if not rhr or len(hr) < 10:
        return None
    summary = mean(rhr)
    low_end = percentile(hr, 10)
    floor = min(hr)
    if summary > low_end + 20:
        return Finding(
            ReasonCode.SUMMARY_DETAIL_MISMATCH,
            f"the reported resting heart rate ({summary:.0f} bpm) is {summary - low_end:.0f} bpm "
            f"above the 10th percentile of the day's measured heart rate ({low_end:.0f} bpm)",
            0.35,
            {"summary_rhr": round(summary, 1), "hr_p10": round(low_end, 1)},
        )
    if summary < floor - 10:
        return Finding(
            ReasonCode.SUMMARY_DETAIL_MISMATCH,
            f"the reported resting heart rate ({summary:.0f} bpm) is below the lowest "
            f"measured heart rate of the window ({floor:.0f} bpm)",
            0.35,
            {"summary_rhr": round(summary, 1), "hr_min": round(floor, 1)},
        )
    return None


@rule("rhr_vs_hrv")
def _rhr_vs_hrv(norm: NormalizedEvidence, request: EvaluationRequest) -> Finding | None:
    """Resting heart rate up and heart-rate variability also up is an incoherent pair.

    Under the usual autonomic interpretation these move in opposite directions. Both
    rising together points at a measurement problem rather than a physiological story,
    so it weakens the evidence instead of corroborating it.
    """
    rhr_now = norm.window_values(Signal.RESTING_HEART_RATE)
    hrv_now = norm.window_values(Signal.HRV_RMSSD)
    rhr_base = norm.baseline_values(Signal.RESTING_HEART_RATE)
    hrv_base = norm.baseline_values(Signal.HRV_RMSSD)
    if not (rhr_now and hrv_now and len(rhr_base) >= 3 and len(hrv_base) >= 3):
        return None
    rhr_sd = max(sample_sd(rhr_base), 1.5)
    hrv_sd = max(sample_sd(hrv_base), 4.0)
    rhr_z = (mean(rhr_now) - mean(rhr_base)) / rhr_sd
    hrv_z = (mean(hrv_now) - mean(hrv_base)) / hrv_sd
    if rhr_z >= 1.5 and hrv_z >= 1.0:
        return Finding(
            ReasonCode.CONFLICTING_SIGNALS,
            f"resting heart rate is {rhr_z:.1f} SD above baseline while heart rate "
            f"variability is also {hrv_z:.1f} SD above it, which is not a coherent pair",
            0.40,
            {"rhr_z": round(rhr_z, 2), "hrv_z": round(hrv_z, 2)},
        )
    return None


@rule("sleep_duration_vs_efficiency")
def _sleep_duration_vs_efficiency(
    norm: NormalizedEvidence, request: EvaluationRequest
) -> Finding | None:
    """Very short sleep reported at near-perfect efficiency is a wear-detection artifact."""
    duration = norm.window_values(Signal.SLEEP_DURATION)
    efficiency = norm.window_values(Signal.SLEEP_EFFICIENCY)
    if not duration or not efficiency:
        return None
    total = sum(duration)
    eff = mean(efficiency)
    if total < 240 and eff >= 97:
        return Finding(
            ReasonCode.CONTEXT_CONFLICT,
            f"only {total:.0f} minutes of sleep were recorded at {eff:.0f}% efficiency, "
            "a combination that usually means the device stopped detecting wear rather "
            "than that sleep ended",
            0.45,
            {"sleep_minutes": round(total, 1), "efficiency": round(eff, 1)},
        )
    return None


@rule("sleep_efficiency_vs_duration")
def _sleep_efficiency_vs_duration(
    norm: NormalizedEvidence, request: EvaluationRequest
) -> Finding | None:
    """Efficiency falling while duration rises by a lot needs stage data to make sense."""
    duration = norm.window_values(Signal.SLEEP_DURATION)
    efficiency = norm.window_values(Signal.SLEEP_EFFICIENCY)
    dur_base = norm.baseline_values(Signal.SLEEP_DURATION)
    eff_base = norm.baseline_values(Signal.SLEEP_EFFICIENCY)
    if not (duration and efficiency and len(dur_base) >= 3 and len(eff_base) >= 3):
        return None
    dur_sd = max(sample_sd(dur_base), 10.0)
    eff_sd = max(sample_sd(eff_base), 1.5)
    dur_z = (sum(duration) - mean(dur_base)) / dur_sd
    eff_z = (mean(efficiency) - mean(eff_base)) / eff_sd
    if eff_z <= -1.0 and dur_z >= 1.0:
        return Finding(
            ReasonCode.SUMMARY_DETAIL_MISMATCH,
            f"sleep efficiency is {abs(eff_z):.1f} SD below baseline while total sleep "
            f"time is {dur_z:.1f} SD above it; without sleep-stage detail these cannot "
            "both be characterised",
            0.35,
            {"efficiency_z": round(eff_z, 2), "duration_z": round(dur_z, 2)},
        )
    return None


@rule("activity_vs_heart_rate")
def _activity_vs_heart_rate(
    norm: NormalizedEvidence, request: EvaluationRequest
) -> Finding | None:
    """High step counts with a flat heart rate and no logged exercise are vibration."""
    steps = norm.window_values(Signal.STEPS)
    hr = norm.window_values(Signal.HEART_RATE)
    if not steps or len(hr) < 10:
        return None
    total_steps = sum(steps)
    hr_spread = max(hr) - min(hr)
    exercise = sum(norm.window_values(Signal.EXERCISE_MINUTES))
    logged = exercise > 0 or (request.context.exercise_minutes_last_24h or 0) > 0
    if total_steps >= 12000 and hr_spread < 15 and not logged:
        return Finding(
            ReasonCode.CONTEXT_CONFLICT,
            f"{total_steps:.0f} steps were recorded but heart rate varied by only "
            f"{hr_spread:.0f} bpm across the window and no exercise was logged, which "
            "is the signature of vehicle or handlebar vibration rather than movement",
            0.45,
            {"steps": round(total_steps), "hr_spread": round(hr_spread, 1)},
        )
    return None


@rule("wear_vs_reported_gaps")
def _wear_vs_reported_gaps(
    norm: NormalizedEvidence, request: EvaluationRequest
) -> Finding | None:
    """Declared wear time must not exceed the window minus its own reported gaps."""
    worn = request.context.device_worn_minutes
    gaps = request.context.reported_gaps_minutes
    if worn is None or not gaps:
        return None
    window_minutes = norm.target_window.duration_minutes
    implied_max = window_minutes - sum(gaps)
    if worn > implied_max + 1.0:
        return Finding(
            ReasonCode.CONTEXT_CONFLICT,
            f"{worn:.0f} minutes of wear were declared, but the reported gaps totalling "
            f"{sum(gaps):.0f} minutes leave room for at most {implied_max:.0f}",
            0.50,
            {"declared_wear_minutes": round(worn, 1), "implied_max_minutes": round(implied_max, 1)},
        )
    return None
