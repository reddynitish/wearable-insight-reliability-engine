"""Shared fixtures. No personal health data: every request here is constructed inline."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import Verbosity, settings

from engine.schemas import (
    BaselineSample,
    Claim,
    EvaluationContext,
    EvaluationRequest,
    Observation,
    QualityFlag,
    TimeWindow,
)
from engine.scoring import sample_sd
from engine.signals import Signal, spec

# Property tests run derandomised by default so that a green suite stays green and a
# failure is reproducible from the test name alone -- a randomised search that finds a new
# counterexample on the third CI run looks like flakiness and gets ignored, which is worse
# than a narrower search that is trusted. To search harder, on purpose:
#
#     ./.venv/bin/python -m pytest -p no:randomly --hypothesis-profile=search
#
settings.register_profile("default", derandomize=True, deadline=None, max_examples=120)
settings.register_profile("search", derandomize=False, deadline=None, max_examples=2000,
                          verbosity=Verbosity.normal)
settings.load_profile("default")

U = timezone.utc
DAY_END = datetime(2026, 9, 23, tzinfo=U)
WINDOW = TimeWindow(start=DAY_END - timedelta(days=1), end=DAY_END)


def baseline_days(
    signal: Signal,
    values: list[float],
    end: datetime = DAY_END,
    valid: int | None = None,
) -> list[BaselineSample]:
    n_valid = len(values) if valid is None else valid
    return [
        BaselineSample(
            signal=signal,
            value=v,
            day=end - timedelta(days=len(values) - i),
            valid=i < n_valid,
        )
        for i, v in enumerate(values)
    ]


def rhr_request(
    *,
    z: float = 2.2,
    baseline_mean: float = 58.0,
    baseline_sd: float = 2.5,
    n_baseline: int = 21,
    n_valid: int | None = None,
    worn_minutes: float | None = 1362.0,
    overnight_minutes: float | None = 468.0,
    sync_offset_hours: float | None = 3.0,
    evaluated_offset_hours: float = 4.0,
    tz: str | None = "America/New_York",
    hr_samples: int = 48,
    rhr_value: float | None = None,
    extra_observations: list[Observation] | None = None,
    quality_flags: list[QualityFlag] | None = None,
    device_changed: bool = False,
    exercise_minutes: float | None = 0.0,
    window: TimeWindow = WINDOW,
    claim_type: str = "RESTING_HEART_RATE_ELEVATED",
    subject_id: str = "test-subject-001",
    baseline_values: list[float] | None = None,
    declared_baseline_days: int | None = None,
) -> EvaluationRequest:
    """A resting-heart-rate request with one knob per failure mode the contract names.

    Defaults produce clean, fully-evidenced, supportable evidence, so each test can
    change exactly one thing and attribute the result to it.
    """
    if baseline_values is None:
        # Alternating +/- one SD keeps the sample SD at baseline_sd exactly, which makes
        # z-based boundary tests arithmetic rather than approximate.
        baseline_values = [
            baseline_mean + (baseline_sd if i % 2 else -baseline_sd) for i in range(n_baseline)
        ]
    baseline = baseline_days(Signal.RESTING_HEART_RATE, baseline_values, window.end, n_valid)
    # Derive the target value from the baseline's *actual* mean and effective SD so that
    # the requested z is exact. Boundary tests depend on that: "z exactly at show_z"
    # cannot be tested with an approximation.
    valid_values = baseline_values[: n_valid if n_valid is not None else len(baseline_values)]
    if valid_values:
        mean = sum(valid_values) / len(valid_values)
        sd = sample_sd(valid_values)
        effective_sd = max(sd, spec(Signal.RESTING_HEART_RATE).noise_sd)
    else:
        mean, effective_sd = baseline_mean, baseline_sd
    value = rhr_value if rhr_value is not None else mean + z * effective_sd

    observations = [
        Observation(
            signal=Signal.RESTING_HEART_RATE,
            value=value,
            unit="bpm",
            measured_at=window.end + timedelta(hours=2),
            source="test",
            window_start=window.start,
            window_end=window.end,
            quality_flags=quality_flags or [],
        )
    ]
    step = (window.end - window.start) / max(hr_samples, 1)
    for i in range(hr_samples):
        phase = i / max(hr_samples, 1)
        base = 52 + 66 * (0.15 + 0.85 * max(0.0, min(1.0, (phase - 0.25) / 0.5)))
        observations.append(
            Observation(
                signal=Signal.HEART_RATE,
                value=round(base, 1),
                unit="bpm",
                measured_at=window.start + step * i,
                source="test",
            )
        )
    if extra_observations:
        observations.extend(extra_observations)

    return EvaluationRequest(
        subject_id=subject_id,
        claim=Claim(
            type=claim_type,
            statement="Your resting heart rate is elevated today.",
            target_window=window,
        ),
        observations=observations,
        baseline=baseline,
        context=EvaluationContext(
            sync_completed_at=None
            if sync_offset_hours is None
            else window.end + timedelta(hours=sync_offset_hours),
            device_worn_minutes=worn_minutes,
            overnight_worn_minutes=overnight_minutes,
            overnight_window_minutes=480.0,
            expected_worn_minutes=1440.0,
            timezone=tz,
            device="test-device",
            device_changed_recently=device_changed,
            exercise_minutes_last_24h=exercise_minutes,
            baseline_days=declared_baseline_days,
        ),
        evaluated_at=window.end + timedelta(hours=evaluated_offset_hours),
    )


@pytest.fixture
def clean_rhr() -> EvaluationRequest:
    return rhr_request()
