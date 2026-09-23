"""Seeded synthetic evidence generator.

Purpose (ADR 0005): exercise every gate and every decision path with a **known
ground-truth evidence state**, which is what makes the unsupported-show rate computable
at all. Real wearable data does not come with a label saying "this evidence was too poor
to support a conclusion".

Limits, stated here so nobody has to infer them: baselines are Gaussian, days are
independent, and sensor error is not modelled. These generators test the *decision
logic*, not physiology. Nothing produced here supports any claim about real-world
performance. Stage 2 replaces this with public datasets.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from engine.schemas import (
    BaselineSample,
    Claim,
    EvaluationContext,
    EvaluationRequest,
    Observation,
    TimeWindow,
)
from engine.signals import Signal

U = timezone.utc
DAY = timedelta(days=1)


class GroundTruth(str, Enum):
    """The evidence state, per docs/evaluation-protocol.md section 2."""

    SUPPORTABLE = "SUPPORTABLE"
    UNSUPPORTABLE_INSUFFICIENT = "UNSUPPORTABLE_INSUFFICIENT"
    UNSUPPORTABLE_CONTRADICTED = "UNSUPPORTABLE_CONTRADICTED"
    UNSUPPORTABLE_UNTRUSTWORTHY = "UNSUPPORTABLE_UNTRUSTWORTHY"
    # Not one of the protocol's four states: a case whose evidence breaks no contract
    # clause but whose achieved effect lands between the policy's contradiction and
    # display thresholds. Neither SHOW nor abstain is the single right answer, so these
    # are reported separately and excluded from the headline rates rather than scored
    # against a label we cannot justify. See docs/evaluation-protocol.md section 9.
    BORDERLINE_EXCLUDED = "BORDERLINE_EXCLUDED"


@dataclass
class SyntheticCase:
    request: EvaluationRequest
    truth: GroundTruth
    truth_reason: str
    generator: str
    seed: int
    corruption: str | None = None
    severity: str | None = None
    tags: list[str] = field(default_factory=list)
    breaches: list[str] = field(default_factory=list)
    measurements: dict[str, Any] = field(default_factory=dict)

    @property
    def scored(self) -> bool:
        return self.truth is not GroundTruth.BORDERLINE_EXCLUDED

    def manifest_row(self) -> dict[str, Any]:
        return {
            "subject_id": self.request.subject_id,
            "claim_type": self.request.claim.type,
            "truth": self.truth.value,
            "truth_reason": self.truth_reason,
            "generator": self.generator,
            "seed": self.seed,
            "corruption": self.corruption,
            "severity": self.severity,
            "tags": list(self.tags),
            "breaches": list(self.breaches),
            "measurements": dict(self.measurements),
            "synthetic": True,
        }


# Fixed reference instant so that generated cases are reproducible across runs and
# machines. Nothing in the engine reads the wall clock when evaluated_at is supplied.
REFERENCE_NOW = datetime(2026, 9, 23, 9, 0, tzinfo=U)


def _day_window(end: datetime) -> TimeWindow:
    return TimeWindow(start=end - DAY, end=end)


def _baseline(
    signal: Signal, days: int, mean: float, sd: float, rng: random.Random,
    end: datetime, valid: int | None = None, drift: float = 0.0,
) -> list[BaselineSample]:
    """`days` trailing daily values. `valid` marks only the first N as valid days."""
    out: list[BaselineSample] = []
    n_valid = days if valid is None else valid
    for i in range(days):
        day = end - DAY * (days - i)
        value = rng.gauss(mean + drift * i, sd)
        out.append(BaselineSample(signal=signal, value=value, day=day, valid=i < n_valid))
    return out


def _hr_series(
    rng: random.Random, window: TimeWindow, count: int, low: float, high: float
) -> list[Observation]:
    step = (window.end - window.start) / count
    out: list[Observation] = []
    for i in range(count):
        # A daily heart-rate trace: low overnight, higher during the day.
        phase = i / count
        base = low + (high - low) * (0.15 + 0.85 * max(0.0, min(1.0, (phase - 0.25) / 0.5)))
        out.append(
            Observation(
                signal=Signal.HEART_RATE,
                value=round(rng.gauss(base, 3.0), 1),
                unit="bpm",
                measured_at=window.start + step * i,
                source="synthetic",
            )
        )
    return out


# --------------------------------------------------------------------------- generators


def rhr_elevated(seed: int = 0, *, supportable: bool = True) -> SyntheticCase:
    """A clean, fully-evidenced resting-heart-rate case."""
    rng = random.Random(seed)
    window = _day_window(datetime(2026, 9, 23, tzinfo=U))
    base_mean, base_sd, days = 58.0, 2.5, 21
    baseline = _baseline(Signal.RESTING_HEART_RATE, days, base_mean, base_sd, rng, window.end)
    # +2.2 SD clears show_z=1.5 and the 3 bpm absolute floor; -0.1 SD sits below
    # contradict_z=0.5, so the evidence argues against the claim.
    z = 2.2 if supportable else -0.1
    rhr = base_mean + z * base_sd
    obs = [
        Observation(
            signal=Signal.RESTING_HEART_RATE, value=round(rhr, 1), unit="bpm",
            measured_at=window.end + timedelta(hours=2), source="synthetic",
            window_start=window.start, window_end=window.end,
        )
    ]
    obs += _hr_series(rng, window, 48, low=52.0, high=118.0)
    obs.append(
        Observation(signal=Signal.HRV_RMSSD, value=round(rng.gauss(38, 2), 1), unit="ms",
                    measured_at=window.end - timedelta(hours=3), source="synthetic")
    )
    request = EvaluationRequest(
        subject_id=f"synth-rhr-{seed:04d}",
        claim=Claim(
            type="RESTING_HEART_RATE_ELEVATED",
            statement="Your resting heart rate is elevated today.",
            target_window=window,
        ),
        observations=obs,
        baseline=baseline + _baseline(Signal.HRV_RMSSD, days, 42.0, 4.0, rng, window.end),
        context=EvaluationContext(
            sync_completed_at=window.end + timedelta(hours=3),
            device_worn_minutes=1362.0,
            overnight_worn_minutes=468.0,
            overnight_window_minutes=480.0,
            expected_worn_minutes=1440.0,
            timezone="America/New_York",
            device="synthetic-tracker",
            exercise_minutes_last_24h=0.0,
        ),
        evaluated_at=window.end + timedelta(hours=4),
    )
    return SyntheticCase(
        request=request,
        truth=GroundTruth.SUPPORTABLE if supportable else GroundTruth.UNSUPPORTABLE_CONTRADICTED,
        truth_reason=(
            "clean evidence with a +2.2 SD resting-heart-rate elevation"
            if supportable
            else "clean evidence showing no elevation (-0.1 SD)"
        ),
        generator="rhr_elevated",
        seed=seed,
        tags=["clean"],
    )


def sleep_duration_low(seed: int = 0, *, supportable: bool = True) -> SyntheticCase:
    rng = random.Random(seed)
    night_end = datetime(2026, 9, 23, 7, 30, tzinfo=U)
    window = TimeWindow(start=night_end - timedelta(hours=8, minutes=30), end=night_end)
    base_mean, base_sd, days = 425.0, 32.0, 14
    baseline = _baseline(Signal.SLEEP_DURATION, days, base_mean, base_sd, rng, window.end)
    z = -1.9 if supportable else 0.1
    duration = base_mean + z * base_sd
    obs = [
        Observation(signal=Signal.SLEEP_DURATION, value=round(duration, 1), unit="minutes",
                    measured_at=window.end + timedelta(minutes=20), source="synthetic",
                    window_start=window.start, window_end=window.end),
        Observation(signal=Signal.SLEEP_EFFICIENCY, value=round(rng.gauss(89, 1.5), 1), unit="%",
                    measured_at=window.end + timedelta(minutes=20), source="synthetic",
                    window_start=window.start, window_end=window.end),
    ]
    request = EvaluationRequest(
        subject_id=f"synth-sleep-{seed:04d}",
        claim=Claim(type="SLEEP_DURATION_LOW", statement="You slept less than usual.",
                    target_window=window),
        observations=obs,
        baseline=baseline,
        context=EvaluationContext(
            sync_completed_at=window.end + timedelta(minutes=45),
            device_worn_minutes=498.0,
            expected_worn_minutes=window.duration_minutes,
            timezone="America/New_York",
            device="synthetic-tracker",
        ),
        evaluated_at=window.end + timedelta(hours=1),
    )
    return SyntheticCase(
        request=request,
        truth=GroundTruth.SUPPORTABLE if supportable else GroundTruth.UNSUPPORTABLE_CONTRADICTED,
        truth_reason=(
            "clean evidence with sleep 1.9 SD below baseline"
            if supportable
            else "clean evidence showing typical sleep duration"
        ),
        generator="sleep_duration_low",
        seed=seed,
        tags=["clean"],
    )


def sleep_quality_reduced(seed: int = 0, *, supportable: bool = True) -> SyntheticCase:
    rng = random.Random(seed)
    night_end = datetime(2026, 9, 23, 7, 0, tzinfo=U)
    window = TimeWindow(start=night_end - timedelta(hours=8), end=night_end)
    days = 20
    eff_mean, eff_sd = 90.0, 3.0
    baseline = _baseline(Signal.SLEEP_EFFICIENCY, days, eff_mean, eff_sd, rng, window.end)
    baseline += _baseline(Signal.SLEEP_DURATION, days, 430.0, 30.0, rng, window.end)
    z = -2.0 if supportable else 0.05
    efficiency = eff_mean + z * eff_sd
    obs = [
        Observation(signal=Signal.SLEEP_EFFICIENCY, value=round(efficiency, 1), unit="%",
                    measured_at=window.end + timedelta(minutes=15), source="synthetic",
                    window_start=window.start, window_end=window.end),
        Observation(signal=Signal.SLEEP_DURATION, value=round(rng.gauss(428, 12), 1),
                    unit="minutes", measured_at=window.end + timedelta(minutes=15),
                    source="synthetic", window_start=window.start, window_end=window.end),
    ]
    request = EvaluationRequest(
        subject_id=f"synth-sleepq-{seed:04d}",
        claim=Claim(type="SLEEP_QUALITY_REDUCED", statement="Your sleep quality was reduced.",
                    target_window=window),
        observations=obs,
        baseline=baseline,
        context=EvaluationContext(
            sync_completed_at=window.end + timedelta(minutes=30),
            device_worn_minutes=472.0,
            expected_worn_minutes=window.duration_minutes,
            timezone="America/New_York",
            device="synthetic-tracker",
        ),
        evaluated_at=window.end + timedelta(minutes=45),
    )
    return SyntheticCase(
        request=request,
        truth=GroundTruth.SUPPORTABLE if supportable else GroundTruth.UNSUPPORTABLE_CONTRADICTED,
        truth_reason=(
            "clean evidence with efficiency 2.0 SD below baseline; this claim type is "
            "capped at SHOW_WITH_WARNING by policy"
            if supportable
            else "clean evidence showing typical sleep efficiency"
        ),
        generator="sleep_quality_reduced",
        seed=seed,
        tags=["clean", "policy_ceiling"],
    )


def activity_load_high(seed: int = 0, *, supportable: bool = True) -> SyntheticCase:
    rng = random.Random(seed)
    window = _day_window(datetime(2026, 9, 23, tzinfo=U))
    base_mean, base_sd, days = 46.0, 11.0, 21
    baseline = _baseline(Signal.ACTIVE_MINUTES, days, base_mean, base_sd, rng, window.end)
    z = 2.1 if supportable else 0.1
    active = base_mean + z * base_sd
    obs = [
        Observation(signal=Signal.ACTIVE_MINUTES, value=round(active, 1), unit="minutes",
                    measured_at=window.end + timedelta(hours=1), source="synthetic",
                    window_start=window.start, window_end=window.end),
        Observation(signal=Signal.STEPS, value=float(round(rng.gauss(13500, 400))), unit="count",
                    measured_at=window.end + timedelta(hours=1), source="synthetic",
                    window_start=window.start, window_end=window.end),
        Observation(signal=Signal.EXERCISE_MINUTES, value=42.0, unit="minutes",
                    measured_at=window.end - timedelta(hours=6), source="synthetic",
                    window_start=window.start, window_end=window.end),
    ]
    obs += _hr_series(rng, window, 48, low=54.0, high=142.0)
    request = EvaluationRequest(
        subject_id=f"synth-activity-{seed:04d}",
        claim=Claim(type="ACTIVITY_LOAD_HIGH", statement="Your activity load was high today.",
                    target_window=window),
        observations=obs,
        baseline=baseline,
        context=EvaluationContext(
            sync_completed_at=window.end + timedelta(hours=2),
            device_worn_minutes=1340.0,
            expected_worn_minutes=1440.0,
            timezone="America/New_York",
            device="synthetic-tracker",
            exercise_minutes_last_24h=42.0,
        ),
        evaluated_at=window.end + timedelta(hours=3),
    )
    return SyntheticCase(
        request=request,
        truth=GroundTruth.SUPPORTABLE if supportable else GroundTruth.UNSUPPORTABLE_CONTRADICTED,
        truth_reason=(
            "clean evidence with activity 2.1 SD above baseline and a logged workout"
            if supportable
            else "clean evidence showing typical activity"
        ),
        generator="activity_load_high",
        seed=seed,
        tags=["clean"],
    )


def recovery_evidence_incomplete(seed: int = 0, *, supportable: bool = True) -> SyntheticCase:
    """Inverted claim: supportable means the recovery inputs really are inadequate."""
    rng = random.Random(seed)
    window = _day_window(datetime(2026, 9, 23, tzinfo=U))
    days = 21
    obs = [
        Observation(signal=Signal.RESTING_HEART_RATE, value=round(rng.gauss(58, 2), 1),
                    unit="bpm", measured_at=window.end - timedelta(minutes=30),
                    source="synthetic", window_start=window.start, window_end=window.end)
    ]
    baseline = _baseline(Signal.RESTING_HEART_RATE, days, 58.0, 2.5, rng, window.end)
    if not supportable:
        # All three inputs present, fresh, and backed by a mature baseline: the claim
        # that evidence is incomplete is then contradicted.
        obs.append(
            Observation(signal=Signal.HRV_RMSSD, value=round(rng.gauss(42, 4), 1), unit="ms",
                        measured_at=window.end - timedelta(hours=5), source="synthetic",
                        window_start=window.start, window_end=window.end)
        )
        obs.append(
            Observation(signal=Signal.SLEEP_DURATION, value=round(rng.gauss(425, 25), 1),
                        unit="minutes", measured_at=window.end - timedelta(hours=16),
                        source="synthetic", window_start=window.start, window_end=window.end)
        )
        baseline += _baseline(Signal.HRV_RMSSD, days, 42.0, 4.0, rng, window.end)
        baseline += _baseline(Signal.SLEEP_DURATION, days, 425.0, 30.0, rng, window.end)
    request = EvaluationRequest(
        subject_id=f"synth-recovery-{seed:04d}",
        claim=Claim(
            type="RECOVERY_EVIDENCE_INCOMPLETE",
            statement="There is not enough evidence to judge your recovery.",
            target_window=window,
        ),
        observations=obs,
        baseline=baseline,
        context=EvaluationContext(
            sync_completed_at=window.end + timedelta(hours=1),
            device_worn_minutes=1300.0,
            expected_worn_minutes=1440.0,
            timezone="America/New_York",
            device="synthetic-tracker",
        ),
        evaluated_at=window.end + timedelta(hours=2),
    )
    return SyntheticCase(
        request=request,
        truth=GroundTruth.SUPPORTABLE if supportable else GroundTruth.UNSUPPORTABLE_CONTRADICTED,
        truth_reason=(
            "two of the three recovery inputs are absent, so the insufficiency claim holds"
            if supportable
            else "all three recovery inputs are present, fresh, and baselined"
        ),
        generator="recovery_evidence_incomplete",
        seed=seed,
        tags=["clean", "inverted_claim"],
    )


def physiological_anomaly(seed: int = 0, *, supportable: bool = True) -> SyntheticCase:
    rng = random.Random(seed)
    end = datetime(2026, 9, 23, 6, 0, tzinfo=U)
    window = TimeWindow(start=end - timedelta(hours=2), end=end)
    days = 20
    base_mean, base_sd = 96.5, 1.1
    baseline = _baseline(Signal.SPO2, days, base_mean, base_sd, rng, window.end)
    z = -5.0 if supportable else -0.4
    obs = [
        Observation(signal=Signal.SPO2, value=round(base_mean + z * base_sd + d, 1), unit="%",
                    measured_at=window.start + timedelta(minutes=30 * (i + 1)),
                    source="synthetic")
        for i, d in enumerate((0.0, 0.3, -0.2))
    ]
    request = EvaluationRequest(
        subject_id=f"synth-anomaly-{seed:04d}",
        claim=Claim(
            type="PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION",
            statement="One of your blood-oxygen readings is unusual enough to re-measure.",
            target_window=window,
            signal=Signal.SPO2,
        ),
        observations=obs,
        baseline=baseline,
        context=EvaluationContext(
            sync_completed_at=window.end + timedelta(minutes=20),
            device_worn_minutes=115.0,
            expected_worn_minutes=window.duration_minutes,
            timezone="America/New_York",
            device="synthetic-tracker",
        ),
        evaluated_at=window.end + timedelta(minutes=30),
    )
    return SyntheticCase(
        request=request,
        truth=GroundTruth.SUPPORTABLE if supportable else GroundTruth.UNSUPPORTABLE_CONTRADICTED,
        truth_reason=(
            "three consecutive readings 5 SD below baseline, no artifact flags"
            if supportable
            else "readings within 0.4 SD of baseline"
        ),
        generator="physiological_anomaly",
        seed=seed,
        tags=["clean", "policy_ceiling"],
    )


GENERATORS: dict[str, Callable[..., SyntheticCase]] = {
    "RESTING_HEART_RATE_ELEVATED": rhr_elevated,
    "SLEEP_DURATION_LOW": sleep_duration_low,
    "SLEEP_QUALITY_REDUCED": sleep_quality_reduced,
    "ACTIVITY_LOAD_HIGH": activity_load_high,
    "RECOVERY_EVIDENCE_INCOMPLETE": recovery_evidence_incomplete,
    "PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION": physiological_anomaly,
}


def clean_case(claim_type: str, seed: int = 0, *, supportable: bool = True) -> SyntheticCase:
    if claim_type not in GENERATORS:
        raise KeyError(f"no generator for {claim_type!r}; have {sorted(GENERATORS)}")
    return GENERATORS[claim_type](seed, supportable=supportable)
