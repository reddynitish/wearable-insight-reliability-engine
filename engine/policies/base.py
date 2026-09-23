"""The ClaimPolicy record: a machine-checkable evidence contract for one claim type.

Every number in a policy is a product decision with a rationale in
docs/claim-contracts.md. Policies are frozen dataclasses because a mutated threshold
would invalidate the `policy_version` travelling with every decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from engine.reasons import ReasonCode
from engine.schemas import Decision
from engine.signals import Signal

Mode = Literal["deviation", "insufficiency", "anomaly"]
Aggregation = Literal["mean", "sum", "max", "min", "last"]


@dataclass(frozen=True)
class DecisionThresholds:
    """Support-score cut points. See docs/claim-contracts.md section 4."""

    reject_below: float = 0.20
    warn_above: float = 0.55
    show_above: float = 0.75

    def __post_init__(self) -> None:
        if not 0 < self.reject_below < self.warn_above < self.show_above < 1:
            raise ValueError("thresholds must satisfy 0 < reject < warn < show < 1")


@dataclass(frozen=True)
class ClaimPolicy:
    claim_type: str
    mode: Mode
    description: str

    # --- required evidence ---
    required_signals: tuple[Signal, ...] = ()
    supporting_signals: tuple[Signal, ...] = ()
    target_signal: Signal | None = None
    target_aggregation: Aggregation = "mean"
    # +1 when the claim asserts the signal is HIGH, -1 when it asserts LOW.
    direction: int = 1

    # --- window and freshness ---
    requires_closed_window: bool = True
    requires_sync_after_window_end: bool = True
    requires_timezone: bool = True
    max_measurement_age_hours: float = 36.0
    good_measurement_age_hours: float = 6.0
    max_sync_age_hours: float = 24.0

    # --- coverage ---
    min_wear_coverage: float = 0.60
    good_wear_coverage: float = 0.90
    min_overnight_coverage: float | None = None
    good_overnight_coverage: float = 0.85
    max_gap_minutes: float = 240.0
    good_gap_minutes: float = 30.0
    # Below this many worn minutes the device was not on the body; the claim is about a
    # nightstand, not a person.
    not_worn_minutes: float = 120.0

    # --- baseline ---
    min_baseline_days: int = 14
    warn_baseline_days: int = 7
    good_baseline_days: int = 28
    max_baseline_sd: float | None = None
    max_baseline_shift: float | None = None

    # --- effect size, as directional magnitudes in baseline SDs ---
    contradict_z: float = 0.5
    warn_z: float = 1.0
    show_z: float = 1.5
    min_absolute_delta: float = 0.0

    # --- quality ---
    implausible_reject_fraction: float = 0.20
    flagged_wait_fraction: float = 0.30
    min_signal_quality: float = 0.50

    # --- consistency rules to run, by name (see engine/consistency.py) ---
    consistency_rules: tuple[str, ...] = ()
    min_consistency: float = 0.50

    # --- scope ---
    thresholds: DecisionThresholds = field(default_factory=DecisionThresholds)
    # Disclosures that always apply to this claim type, regardless of the evidence.
    permanent_limitations: tuple[ReasonCode, ...] = ()
    # Hard ceiling on the decision this claim may ever reach.
    max_decision: Decision = Decision.SHOW
    # Free-text confounders, surfaced in documentation and in the trace, never silently
    # adjusted for.
    known_confounders: tuple[str, ...] = ()
    # For the insufficiency claim: the inputs whose adequacy is being asserted about.
    insufficiency_inputs: tuple[Signal, ...] = ()

    def __post_init__(self) -> None:
        if self.direction not in (1, -1):
            raise ValueError("direction must be +1 or -1")
        if not 0 <= self.contradict_z < self.warn_z < self.show_z:
            raise ValueError("require 0 <= contradict_z < warn_z < show_z")
        if not 0 <= self.min_wear_coverage <= self.good_wear_coverage <= 1:
            raise ValueError("require 0 <= min_wear_coverage <= good_wear_coverage <= 1")
        if self.warn_baseline_days > self.min_baseline_days:
            raise ValueError("warn_baseline_days must not exceed min_baseline_days")
        if self.good_baseline_days < self.min_baseline_days:
            raise ValueError("good_baseline_days must be at least min_baseline_days")
        if self.mode == "deviation" and self.target_signal is None:
            raise ValueError("deviation policies need a target_signal")
        if self.mode == "insufficiency" and not self.insufficiency_inputs:
            raise ValueError("insufficiency policies need insufficiency_inputs")
