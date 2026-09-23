"""Canonical signal vocabulary, units, and physiological plausibility ranges.

Ranges exist to reject impossible readings, i.e. sensor faults. They are deliberately
wide: they must not encode a judgement about whether a person is healthy, only about
whether a number could have come from a working sensor on a living wrist.

Boundaries are inclusive (see docs/claim-contracts.md section 2).
"""
from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class Signal(str, Enum):
    HEART_RATE = "heart_rate"
    RESTING_HEART_RATE = "resting_heart_rate"
    HRV_RMSSD = "hrv_rmssd"
    SPO2 = "spo2"
    RESPIRATORY_RATE = "respiratory_rate"
    SLEEP_DURATION = "sleep_duration"
    SLEEP_EFFICIENCY = "sleep_efficiency"
    STEPS = "steps"
    ACTIVE_MINUTES = "active_minutes"
    WEAR_MINUTES = "wear_minutes"
    ACTIVE_ENERGY = "active_energy"
    EXERCISE_MINUTES = "exercise_minutes"


class SignalSpec(NamedTuple):
    unit: str
    low: float
    high: float
    # Typical within-subject measurement noise, used as a floor on the baseline
    # standard deviation so that an implausibly tight baseline cannot manufacture a
    # large z-score out of a change smaller than the device can resolve.
    noise_sd: float
    # Human-readable name used in explanations.
    label: str


SPECS: dict[Signal, SignalSpec] = {
    Signal.HEART_RATE: SignalSpec("bpm", 25, 220, 2.0, "heart rate"),
    Signal.RESTING_HEART_RATE: SignalSpec("bpm", 30, 120, 1.5, "resting heart rate"),
    Signal.HRV_RMSSD: SignalSpec("ms", 1, 300, 4.0, "heart rate variability"),
    Signal.SPO2: SignalSpec("%", 50, 100, 1.0, "blood oxygen saturation"),
    Signal.RESPIRATORY_RATE: SignalSpec("breaths/min", 4, 40, 0.5, "respiratory rate"),
    Signal.SLEEP_DURATION: SignalSpec("minutes", 0, 1080, 10.0, "sleep duration"),
    Signal.SLEEP_EFFICIENCY: SignalSpec("%", 0, 100, 1.5, "sleep efficiency"),
    Signal.STEPS: SignalSpec("count", 0, 100000, 200.0, "step count"),
    Signal.ACTIVE_MINUTES: SignalSpec("minutes", 0, 1440, 5.0, "active minutes"),
    Signal.WEAR_MINUTES: SignalSpec("minutes", 0, 1440, 5.0, "wear time"),
    Signal.ACTIVE_ENERGY: SignalSpec("kcal", 0, 15000, 50.0, "active energy"),
    Signal.EXERCISE_MINUTES: SignalSpec("minutes", 0, 1440, 5.0, "exercise minutes"),
}


def spec(signal: Signal) -> SignalSpec:
    return SPECS[signal]


def is_plausible(signal: Signal, value: float) -> bool:
    """True when `value` could have come from a working sensor. Bounds inclusive."""
    s = SPECS[signal]
    return s.low <= value <= s.high


def canonical_unit(signal: Signal) -> str:
    return SPECS[signal].unit


def label(signal: Signal) -> str:
    return SPECS[signal].label
