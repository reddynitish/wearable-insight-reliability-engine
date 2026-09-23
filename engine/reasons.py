"""Reason codes: the machine-readable vocabulary for *why* a decision was made.

Every reason code returned by the engine must correspond to a gate that actually fired
on the evidence, recorded in the decision trace. Explanations are rendered from those
gates, so a reason code that is not in the trace is a bug, not a wording choice.

Codes marked "extension" are not listed in PROJECT_BRIEF.md section 8. They were added
because the brief's list is explicitly non-exhaustive ("possible reasons") and these
conditions are otherwise unreportable. They are documented here rather than silently
folded into a neighbouring code.
"""
from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class Dimension(str, Enum):
    """Evidence dimension a reason belongs to. Also fixes reporting order."""

    INTEGRITY = "integrity"
    SCOPE = "scope"
    COVERAGE = "coverage"
    FRESHNESS = "freshness"
    QUALITY = "signal_quality"
    CONSISTENCY = "consistency"
    BASELINE = "baseline_maturity"
    CLAIM_SUPPORT = "claim_support"


DIMENSION_ORDER = [
    Dimension.SCOPE,
    Dimension.INTEGRITY,
    Dimension.COVERAGE,
    Dimension.FRESHNESS,
    Dimension.QUALITY,
    Dimension.CONSISTENCY,
    Dimension.BASELINE,
    Dimension.CLAIM_SUPPORT,
]


class Outcome(str, Enum):
    """What a fired gate forces.

    REJECT and WAIT are fatal: they determine the decision regardless of any score or,
    later, any learned model output. WARN is a nonfatal limitation that caps the
    decision at SHOW_WITH_WARNING. NOTE is recorded in the trace and does not
    constrain the decision.
    """

    REJECT = "reject"
    WAIT = "wait"
    WARN = "warn"
    NOTE = "note"


class ReasonCode(str, Enum):
    # --- scope ---
    UNSUPPORTED_CLAIM_TYPE = "UNSUPPORTED_CLAIM_TYPE"
    CLAIM_OUT_OF_SCOPE = "CLAIM_OUT_OF_SCOPE"                      # extension
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"                # extension
    LOW_REFERENCE_AGREEMENT = "LOW_REFERENCE_AGREEMENT"            # extension

    # --- integrity of the submitted evidence ---
    NO_OBSERVATIONS = "NO_OBSERVATIONS"                            # extension
    DUPLICATE_OBSERVATIONS = "DUPLICATE_OBSERVATIONS"              # extension
    OUT_OF_ORDER_OBSERVATIONS = "OUT_OF_ORDER_OBSERVATIONS"        # extension
    TIMEZONE_UNKNOWN = "TIMEZONE_UNKNOWN"                          # extension
    OBSERVATIONS_OUTSIDE_WINDOW = "OBSERVATIONS_OUTSIDE_WINDOW"    # extension
    WINDOW_MISALIGNED = "WINDOW_MISALIGNED"                        # extension

    # --- coverage ---
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    LONG_DATA_GAP = "LONG_DATA_GAP"
    DEVICE_NOT_WORN = "DEVICE_NOT_WORN"
    INSUFFICIENT_OVERNIGHT_COVERAGE = "INSUFFICIENT_OVERNIGHT_COVERAGE"  # extension
    COVERAGE_UNKNOWN = "COVERAGE_UNKNOWN"                          # extension

    # --- freshness ---
    STALE_DATA = "STALE_DATA"
    SYNC_INCOMPLETE = "SYNC_INCOMPLETE"
    WINDOW_NOT_CLOSED = "WINDOW_NOT_CLOSED"
    SYNC_STATE_UNKNOWN = "SYNC_STATE_UNKNOWN"                      # extension

    # --- signal quality ---
    MOTION_ARTIFACT = "MOTION_ARTIFACT"
    SENSOR_DROPOUT = "SENSOR_DROPOUT"
    IMPLAUSIBLE_VALUES = "IMPLAUSIBLE_VALUES"
    LOW_SIGNAL_QUALITY = "LOW_SIGNAL_QUALITY"

    # --- consistency ---
    CONFLICTING_SIGNALS = "CONFLICTING_SIGNALS"
    SUMMARY_DETAIL_MISMATCH = "SUMMARY_DETAIL_MISMATCH"
    CONTEXT_CONFLICT = "CONTEXT_CONFLICT"

    # --- baseline maturity ---
    BASELINE_NOT_MATURE = "BASELINE_NOT_MATURE"
    BASELINE_UNSTABLE = "BASELINE_UNSTABLE"
    BASELINE_SHIFT_DETECTED = "BASELINE_SHIFT_DETECTED"
    INSUFFICIENT_HISTORICAL_COVERAGE = "INSUFFICIENT_HISTORICAL_COVERAGE"
    BASELINE_STATISTICS_UNAVAILABLE = "BASELINE_STATISTICS_UNAVAILABLE"  # extension

    # --- claim support ---
    MISSING_REQUIRED_SIGNAL = "MISSING_REQUIRED_SIGNAL"
    EFFECT_BELOW_RELIABLE_THRESHOLD = "EFFECT_BELOW_RELIABLE_THRESHOLD"
    CLAIM_CONTRADICTED = "CLAIM_CONTRADICTED"
    EVIDENCE_COMPLETE = "EVIDENCE_COMPLETE"                        # extension
    CONFOUNDER_UNRESOLVED = "CONFOUNDER_UNRESOLVED"                # extension
    SINGLE_SAMPLE_EXCURSION = "SINGLE_SAMPLE_EXCURSION"            # extension


class ReasonSpec(NamedTuple):
    dimension: Dimension
    outcome: Outcome
    # How unambiguous this condition is when it fires, used as the decision confidence
    # for gate-driven decisions. 0.99 means "there is no reading of the evidence under
    # which this gate should not have fired".
    decisiveness: float
    # Suggested retry delay as an ISO-8601 duration, or None when retrying cannot help.
    retry_after: str | None
    # What evidence would have to exist for the gate to stop firing.
    required_evidence: tuple[str, ...] = ()


_R = ReasonCode
_D = Dimension
_O = Outcome

SPECS: dict[ReasonCode, ReasonSpec] = {
    _R.UNSUPPORTED_CLAIM_TYPE: ReasonSpec(_D.SCOPE, _O.REJECT, 0.99, None),
    _R.CLAIM_OUT_OF_SCOPE: ReasonSpec(_D.SCOPE, _O.REJECT, 0.99, None),
    _R.REQUIRES_CONFIRMATION: ReasonSpec(
        _D.SCOPE, _O.WARN, 0.90, "PT12H", ("a_second_independent_measurement",)
    ),
    _R.LOW_REFERENCE_AGREEMENT: ReasonSpec(_D.SCOPE, _O.WARN, 0.95, None),

    _R.NO_OBSERVATIONS: ReasonSpec(
        _D.INTEGRITY, _O.WAIT, 0.99, "PT6H", ("at_least_one_observation",)
    ),
    _R.DUPLICATE_OBSERVATIONS: ReasonSpec(_D.INTEGRITY, _O.NOTE, 0.60, None),
    _R.OUT_OF_ORDER_OBSERVATIONS: ReasonSpec(_D.INTEGRITY, _O.NOTE, 0.60, None),
    _R.TIMEZONE_UNKNOWN: ReasonSpec(
        _D.INTEGRITY, _O.WAIT, 0.95, "PT1H", ("subject_timezone",)
    ),
    _R.OBSERVATIONS_OUTSIDE_WINDOW: ReasonSpec(_D.INTEGRITY, _O.NOTE, 0.70, None),

    _R.WINDOW_MISALIGNED: ReasonSpec(
        _D.INTEGRITY, _O.WAIT, 0.92, "PT12H",
        ("an_aggregate_whose_interval_matches_the_target_window",),
    ),

    _R.INSUFFICIENT_COVERAGE: ReasonSpec(
        _D.COVERAGE, _O.WAIT, 0.92, "PT12H", ("additional_wear_time_in_target_window",)
    ),
    _R.LONG_DATA_GAP: ReasonSpec(
        _D.COVERAGE, _O.WAIT, 0.88, "PT12H", ("continuous_data_across_target_window",)
    ),
    _R.DEVICE_NOT_WORN: ReasonSpec(
        _D.COVERAGE, _O.REJECT, 0.94, "P1D", ("device_worn_during_target_window",)
    ),
    _R.INSUFFICIENT_OVERNIGHT_COVERAGE: ReasonSpec(
        _D.COVERAGE, _O.WAIT, 0.88, "P1D", ("overnight_wear_coverage",)
    ),

    _R.COVERAGE_UNKNOWN: ReasonSpec(
        _D.COVERAGE, _O.WARN, 0.75, "PT12H", ("wear_time_for_the_target_window",)
    ),

    _R.SYNC_STATE_UNKNOWN: ReasonSpec(
        _D.FRESHNESS, _O.WARN, 0.75, "PT6H", ("a_sync_completion_timestamp",)
    ),
    _R.STALE_DATA: ReasonSpec(
        _D.FRESHNESS, _O.WAIT, 0.96, "PT6H", ("a_recent_measurement",)
    ),
    _R.SYNC_INCOMPLETE: ReasonSpec(
        _D.FRESHNESS, _O.WAIT, 0.97, "PT6H", ("a_sync_covering_the_target_window",)
    ),
    _R.WINDOW_NOT_CLOSED: ReasonSpec(
        _D.FRESHNESS, _O.WAIT, 0.98, "PT6H", ("a_closed_target_window",)
    ),

    _R.MOTION_ARTIFACT: ReasonSpec(
        _D.QUALITY, _O.WAIT, 0.85, "PT12H", ("measurements_without_motion_artifact",)
    ),
    _R.SENSOR_DROPOUT: ReasonSpec(
        _D.QUALITY, _O.WAIT, 0.90, "PT12H", ("an_uninterrupted_measurement_series",)
    ),
    _R.IMPLAUSIBLE_VALUES: ReasonSpec(_D.QUALITY, _O.REJECT, 0.97, "P1D",
                                      ("measurements_within_physiological_range",)),
    _R.LOW_SIGNAL_QUALITY: ReasonSpec(
        _D.QUALITY, _O.WAIT, 0.82, "PT12H", ("higher_quality_measurements",)
    ),

    _R.CONFLICTING_SIGNALS: ReasonSpec(
        _D.CONSISTENCY, _O.WAIT, 0.84, "P1D", ("agreement_between_related_signals",)
    ),
    _R.SUMMARY_DETAIL_MISMATCH: ReasonSpec(
        _D.CONSISTENCY, _O.WAIT, 0.86, "PT12H", ("a_consistent_resync",)
    ),
    _R.CONTEXT_CONFLICT: ReasonSpec(
        _D.CONSISTENCY, _O.WAIT, 0.83, "P1D", ("context_consistent_with_the_measurements",)
    ),

    _R.BASELINE_NOT_MATURE: ReasonSpec(
        _D.BASELINE, _O.WAIT, 0.93, "P7D", ("a_longer_personal_baseline",)
    ),
    _R.BASELINE_UNSTABLE: ReasonSpec(
        _D.BASELINE, _O.WAIT, 0.87, "P14D", ("a_stable_personal_baseline",)
    ),
    _R.BASELINE_SHIFT_DETECTED: ReasonSpec(
        _D.BASELINE, _O.WAIT, 0.89, "P14D", ("a_baseline_rebuilt_after_the_shift",)
    ),
    _R.INSUFFICIENT_HISTORICAL_COVERAGE: ReasonSpec(
        _D.BASELINE, _O.WAIT, 0.90, "P7D", ("more_valid_baseline_days",)
    ),
    _R.BASELINE_STATISTICS_UNAVAILABLE: ReasonSpec(
        _D.BASELINE, _O.WAIT, 0.95, "P7D", ("per_day_baseline_values",)
    ),

    _R.MISSING_REQUIRED_SIGNAL: ReasonSpec(
        _D.CLAIM_SUPPORT, _O.WAIT, 0.98, "PT12H", ("the_required_signal",)
    ),
    _R.EFFECT_BELOW_RELIABLE_THRESHOLD: ReasonSpec(
        _D.CLAIM_SUPPORT, _O.WAIT, 0.88, "P1D", ("a_change_larger_than_measurement_error",)
    ),
    _R.CLAIM_CONTRADICTED: ReasonSpec(_D.CLAIM_SUPPORT, _O.REJECT, 0.93, None),
    _R.EVIDENCE_COMPLETE: ReasonSpec(_D.CLAIM_SUPPORT, _O.REJECT, 0.92, None),
    _R.SINGLE_SAMPLE_EXCURSION: ReasonSpec(
        _D.CLAIM_SUPPORT, _O.WAIT, 0.91, "PT2H", ("a_second_independent_measurement",)
    ),
    _R.CONFOUNDER_UNRESOLVED: ReasonSpec(
        _D.CLAIM_SUPPORT, _O.WARN, 0.80, "P1D", ("confounder_resolution",)
    ),
}

# Codes the engine may never emit as a nonfatal warning next to a displayed claim,
# because a warning is not an adequate substitute for withholding the claim.
NEVER_WARN_ONLY = frozenset(
    {
        _R.IMPLAUSIBLE_VALUES,
        _R.DEVICE_NOT_WORN,
        _R.CLAIM_CONTRADICTED,
        _R.UNSUPPORTED_CLAIM_TYPE,
        _R.MISSING_REQUIRED_SIGNAL,
        _R.SYNC_INCOMPLETE,
        _R.WINDOW_NOT_CLOSED,
        _R.SINGLE_SAMPLE_EXCURSION,
        _R.WINDOW_MISALIGNED,
    }
)


def spec(code: ReasonCode) -> ReasonSpec:
    return SPECS[code]


def sort_key(code: ReasonCode) -> tuple[int, str]:
    """Stable reporting order: by evidence dimension, then alphabetically.

    Deterministic ordering keeps decision traces diffable across engine versions,
    which is what makes regression tests on unsafe SHOW outcomes readable.
    """
    return (DIMENSION_ORDER.index(SPECS[code].dimension), code.value)


def assert_specs_complete() -> None:
    missing = [c for c in ReasonCode if c not in SPECS]
    if missing:
        raise AssertionError(f"ReasonCode without a spec: {missing}")


assert_specs_complete()
