"""The six v0.1 claim policies. Thresholds are justified in docs/claim-contracts.md."""
from __future__ import annotations

from engine.policies.base import ClaimPolicy, DecisionThresholds
from engine.reasons import ReasonCode
from engine.schemas import Decision
from engine.signals import Signal

S = Signal

RESTING_HEART_RATE_ELEVATED = ClaimPolicy(
    claim_type="RESTING_HEART_RATE_ELEVATED",
    mode="deviation",
    description="Resting heart rate is elevated relative to the subject's own baseline.",
    required_signals=(S.RESTING_HEART_RATE,),
    supporting_signals=(S.HEART_RATE, S.WEAR_MINUTES, S.STEPS, S.SLEEP_DURATION, S.HRV_RMSSD),
    target_signal=S.RESTING_HEART_RATE,
    target_aggregation="mean",
    direction=1,
    max_measurement_age_hours=36.0,
    max_sync_age_hours=24.0,
    min_wear_coverage=0.60,
    good_wear_coverage=0.90,
    min_overnight_coverage=0.50,
    min_baseline_days=14,
    warn_baseline_days=7,
    good_baseline_days=28,
    max_baseline_sd=8.0,
    max_baseline_shift=6.0,
    contradict_z=0.5,
    warn_z=1.0,
    show_z=1.5,
    # A 2 bpm move can clear z=1.5 against a 1 bpm baseline SD, and 2 bpm is inside
    # optical-sensor error. The absolute floor stops a tight baseline from manufacturing
    # a statistically large but physically meaningless effect.
    min_absolute_delta=3.0,
    consistency_rules=("rhr_vs_heart_rate_detail", "rhr_vs_hrv", "wear_vs_reported_gaps"),
    known_confounders=(
        "intense exercise in the preceding 24 hours",
        "alcohol",
        "acute illness",
        "altitude change",
        "device or wear-position change",
        "ambient heat",
    ),
)

SLEEP_DURATION_LOW = ClaimPolicy(
    claim_type="SLEEP_DURATION_LOW",
    mode="deviation",
    description="Total sleep duration is low relative to the subject's own baseline.",
    required_signals=(S.SLEEP_DURATION,),
    supporting_signals=(S.SLEEP_EFFICIENCY, S.WEAR_MINUTES, S.HEART_RATE),
    target_signal=S.SLEEP_DURATION,
    target_aggregation="sum",
    direction=-1,
    max_measurement_age_hours=36.0,
    max_sync_age_hours=24.0,
    min_wear_coverage=0.80,
    good_wear_coverage=0.95,
    max_gap_minutes=60.0,
    good_gap_minutes=10.0,
    # A "2 hour night" with daytime wear gaps is a device on a nightstand far more often
    # than it is a sleepless night, so this is a REJECT gate, not a low value to report.
    not_worn_minutes=120.0,
    min_baseline_days=7,
    warn_baseline_days=5,
    good_baseline_days=21,
    max_baseline_sd=120.0,
    max_baseline_shift=90.0,
    contradict_z=0.25,
    warn_z=0.7,
    show_z=1.0,
    min_absolute_delta=45.0,
    consistency_rules=("sleep_duration_vs_efficiency", "wear_vs_reported_gaps"),
    known_confounders=(
        "a nap recorded as the main sleep period",
        "travel or timezone change",
        "shift work",
        "an unrecorded second sleep period",
    ),
)

SLEEP_QUALITY_REDUCED = ClaimPolicy(
    claim_type="SLEEP_QUALITY_REDUCED",
    mode="deviation",
    description="Sleep efficiency is reduced relative to the subject's own baseline.",
    required_signals=(S.SLEEP_EFFICIENCY, S.SLEEP_DURATION),
    supporting_signals=(S.HRV_RMSSD, S.RESPIRATORY_RATE, S.HEART_RATE),
    target_signal=S.SLEEP_EFFICIENCY,
    target_aggregation="mean",
    direction=-1,
    max_measurement_age_hours=36.0,
    max_sync_age_hours=24.0,
    min_wear_coverage=0.85,
    good_wear_coverage=0.95,
    max_gap_minutes=45.0,
    good_gap_minutes=10.0,
    min_baseline_days=14,
    warn_baseline_days=10,
    good_baseline_days=28,
    max_baseline_sd=12.0,
    max_baseline_shift=8.0,
    contradict_z=0.25,
    warn_z=0.7,
    show_z=1.0,
    min_absolute_delta=5.0,
    consistency_rules=("sleep_efficiency_vs_duration", "wear_vs_reported_gaps"),
    # Consumer sleep staging and efficiency disagree materially with polysomnography.
    # Until Stage-2 validation against a PSG-labelled dataset exists, this claim cannot
    # be shown without that disclosure -- so its ceiling is SHOW_WITH_WARNING.
    permanent_limitations=(ReasonCode.LOW_REFERENCE_AGREEMENT,),
    max_decision=Decision.SHOW_WITH_WARNING,
    known_confounders=(
        "device sleep-staging error",
        "alcohol",
        "an unfamiliar sleep environment",
        "illness",
    ),
)

ACTIVITY_LOAD_HIGH = ClaimPolicy(
    claim_type="ACTIVITY_LOAD_HIGH",
    mode="deviation",
    description="Activity load is high relative to the subject's own baseline.",
    required_signals=(S.ACTIVE_MINUTES,),
    supporting_signals=(S.STEPS, S.HEART_RATE, S.ACTIVE_ENERGY, S.EXERCISE_MINUTES),
    target_signal=S.ACTIVE_MINUTES,
    target_aggregation="sum",
    direction=1,
    max_measurement_age_hours=36.0,
    max_sync_age_hours=24.0,
    min_wear_coverage=0.70,
    good_wear_coverage=0.90,
    min_baseline_days=14,
    warn_baseline_days=7,
    good_baseline_days=28,
    max_baseline_sd=45.0,
    max_baseline_shift=30.0,
    contradict_z=0.5,
    warn_z=1.0,
    show_z=1.5,
    min_absolute_delta=15.0,
    consistency_rules=("activity_vs_heart_rate", "wear_vs_reported_gaps"),
    known_confounders=(
        "vehicle or handlebar vibration counted as steps",
        "a pushed stroller or cart",
        "an unlogged workout",
    ),
)

RECOVERY_EVIDENCE_INCOMPLETE = ClaimPolicy(
    claim_type="RECOVERY_EVIDENCE_INCOMPLETE",
    mode="insufficiency",
    description=(
        "There is not enough evidence to judge recovery. This claim asserts "
        "insufficiency, so it is supported precisely when the recovery inputs are "
        "inadequate, and rejected when they are all adequate."
    ),
    required_signals=(),
    supporting_signals=(S.RESTING_HEART_RATE, S.HRV_RMSSD, S.SLEEP_DURATION),
    insufficiency_inputs=(S.RESTING_HEART_RATE, S.HRV_RMSSD, S.SLEEP_DURATION),
    requires_closed_window=True,
    requires_sync_after_window_end=False,
    max_measurement_age_hours=36.0,
    max_sync_age_hours=48.0,
    min_wear_coverage=0.60,
    good_wear_coverage=0.90,
    min_baseline_days=14,
    warn_baseline_days=7,
    good_baseline_days=28,
    thresholds=DecisionThresholds(reject_below=0.20, warn_above=0.55, show_above=0.70),
    known_confounders=("a sync still in progress",),
)

PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION = ClaimPolicy(
    claim_type="PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION",
    mode="anomaly",
    description=(
        "One reading is far enough from the subject's baseline to be worth "
        "re-measuring. This claim never names a condition and never returns a plain "
        "SHOW."
    ),
    required_signals=(),
    supporting_signals=(S.HEART_RATE, S.RESTING_HEART_RATE, S.SPO2, S.RESPIRATORY_RATE, S.HRV_RMSSD),
    target_aggregation="mean",
    direction=1,
    requires_closed_window=False,
    requires_sync_after_window_end=False,
    requires_timezone=False,
    # A stale anomaly is not actionable: there is nothing useful to re-measure about
    # yesterday's transient.
    max_measurement_age_hours=12.0,
    good_measurement_age_hours=2.0,
    max_sync_age_hours=12.0,
    min_wear_coverage=0.50,
    good_wear_coverage=0.80,
    # Zero tolerance: for an anomaly claim, any artifact or dropout flag on the interval
    # is enough to prefer re-measurement over an assertion.
    flagged_wait_fraction=0.0,
    not_worn_minutes=20.0,
    min_baseline_days=14,
    warn_baseline_days=14,
    good_baseline_days=28,
    max_baseline_shift=None,
    contradict_z=1.0,
    warn_z=2.0,
    show_z=3.0,
    consistency_rules=("wear_vs_reported_gaps",),
    permanent_limitations=(ReasonCode.REQUIRES_CONFIRMATION,),
    max_decision=Decision.SHOW_WITH_WARNING,
    known_confounders=(
        "motion artifact",
        "poor skin contact",
        "a single-sample sensor glitch",
    ),
)

_POLICIES: dict[str, ClaimPolicy] = {
    p.claim_type: p
    for p in (
        RESTING_HEART_RATE_ELEVATED,
        SLEEP_DURATION_LOW,
        SLEEP_QUALITY_REDUCED,
        ACTIVITY_LOAD_HIGH,
        RECOVERY_EVIDENCE_INCOMPLETE,
        PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION,
    )
}


def get_policy(claim_type: str) -> ClaimPolicy | None:
    """The policy for `claim_type`, or None when the type is unsupported.

    Returns None rather than raising: an unknown claim type is a typed REJECT with
    UNSUPPORTED_CLAIM_TYPE, which is a decision the caller can branch on, not an error.
    """
    return _POLICIES.get(claim_type)


def supported_claim_types() -> list[str]:
    return sorted(_POLICIES)


def all_policies() -> list[ClaimPolicy]:
    return [_POLICIES[k] for k in supported_claim_types()]
