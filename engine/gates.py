"""Deterministic gates.

A gate is a condition that, when it fires, constrains the decision no matter what any
score or (later) any learned model says. Gates are the "fail closed" mechanism: a model
may lower the engine's confidence, and it may never talk a gate into a SHOW.

Gates are evaluated in a fixed dimension order so that traces stay diffable. Each fired
gate carries the numbers that made it fire, which is what makes the explanation
traceable rather than decorative.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.features import EvidenceFeatures
from engine.normalize import NormalizedEvidence
from engine.policies.base import ClaimPolicy
from engine.reasons import Outcome, ReasonCode, spec
from engine.schemas import EvaluationRequest
from engine.signals import Signal, label

# A consistency finding at or above this penalty is treated as fatal (abstain) rather
# than as a nonfatal limitation. Below it, the conflict is disclosed but the claim can
# still be shown with a warning.
FATAL_CONFLICT_PENALTY = 0.30
# Exercise load in the preceding day that makes a resting-physiology claim confounded.
CONFOUNDING_EXERCISE_MINUTES = 60.0


@dataclass(frozen=True)
class Gate:
    code: ReasonCode
    outcome: Outcome
    detail: str
    measurements: dict[str, Any] = field(default_factory=dict)

    @property
    def fatal(self) -> bool:
        return self.outcome in (Outcome.REJECT, Outcome.WAIT)


def _gate(code: ReasonCode, detail: str, measurements: dict[str, Any] | None = None,
          outcome: Outcome | None = None) -> Gate:
    return Gate(code, outcome or spec(code).outcome, detail, measurements or {})


def evaluate(
    norm: NormalizedEvidence,
    request: EvaluationRequest,
    policy: ClaimPolicy,
    features: EvidenceFeatures,
) -> list[Gate]:
    """All gates that fire, in dimension order."""
    gates: list[Gate] = []
    gates += _scope_gates(request, policy)
    gates += _integrity_gates(norm, policy)
    if policy.mode == "insufficiency":
        gates += _insufficiency_gates(norm, request, policy, features)
        return gates
    gates += _coverage_gates(policy, features)
    gates += _freshness_gates(policy, features)
    gates += _quality_gates(policy, features)
    gates += _consistency_gates(features)
    gates += _baseline_gates(request, policy, features)
    gates += _support_gates(request, policy, features)
    return gates


# --------------------------------------------------------------------------- scope


def _scope_gates(request: EvaluationRequest, policy: ClaimPolicy) -> list[Gate]:
    gates: list[Gate] = []
    if policy.mode == "anomaly" and request.claim.signal is None:
        gates.append(
            _gate(
                ReasonCode.CLAIM_OUT_OF_SCOPE,
                "this claim type must name the signal the anomaly was observed on, and "
                "the request did not",
            )
        )
    for code in policy.permanent_limitations:
        gates.append(_gate(code, _PERMANENT_DETAIL[code]))
    return gates


_PERMANENT_DETAIL = {
    ReasonCode.LOW_REFERENCE_AGREEMENT: (
        "consumer sleep staging disagrees materially with clinical sleep measurement, so "
        "this claim is always reported with that limitation attached"
    ),
    ReasonCode.REQUIRES_CONFIRMATION: (
        "this claim can only ever recommend re-measuring; it never identifies a cause"
    ),
}


# --------------------------------------------------------------------------- integrity


def _integrity_gates(norm: NormalizedEvidence, policy: ClaimPolicy) -> list[Gate]:
    gates: list[Gate] = []
    if not norm.all_plausible:
        gates.append(
            _gate(
                ReasonCode.NO_OBSERVATIONS,
                "no usable observations were submitted for this window",
                {"submitted": sum(norm.submitted_counts.values())},
            )
        )
    if norm.duplicates_removed:
        gates.append(
            _gate(
                ReasonCode.DUPLICATE_OBSERVATIONS,
                f"{norm.duplicates_removed} duplicate observation(s) were removed before "
                "any feature was computed",
                {"duplicates_removed": norm.duplicates_removed},
            )
        )
    if norm.out_of_order_detected:
        gates.append(
            _gate(
                ReasonCode.OUT_OF_ORDER_OBSERVATIONS,
                "observations arrived out of chronological order and were sorted",
            )
        )
    if norm.outside_window:
        gates.append(
            _gate(
                ReasonCode.OBSERVATIONS_OUTSIDE_WINDOW,
                f"{norm.outside_window} observation(s) fell outside the target window and "
                "were not used",
                {"outside_window": norm.outside_window},
            )
        )
    if policy.requires_timezone and (norm.subject_tz is None or norm.timezone_invalid):
        gates.append(
            _gate(
                ReasonCode.TIMEZONE_UNKNOWN,
                "this claim is about a calendar day, and without the subject's timezone "
                "the day boundaries cannot be placed",
                {"submitted_timezone": norm.timezone_name},
            )
        )
    return gates


# --------------------------------------------------------------------------- coverage


def _coverage_gates(policy: ClaimPolicy, features: EvidenceFeatures) -> list[Gate]:
    gates: list[Gate] = []
    cov = features.coverage
    # The not-worn threshold is an absolute minute count, but it must never exceed half
    # the window: on a two-hour anomaly interval a flat 120-minute floor would declare
    # every request an unworn device.
    not_worn_floor = min(policy.not_worn_minutes, 0.5 * cov.expected_minutes)
    if cov.wear_minutes is not None and cov.wear_minutes < not_worn_floor:
        gates.append(
            _gate(
                ReasonCode.DEVICE_NOT_WORN,
                f"the device recorded only {cov.wear_minutes:.0f} minutes of wear in a "
                f"{cov.expected_minutes:.0f}-minute window, which describes an unworn "
                "device rather than the subject",
                {"wear_minutes": round(cov.wear_minutes, 1),
                 "not_worn_threshold": round(not_worn_floor, 1)},
            )
        )
    elif cov.wear_ratio is not None and cov.wear_ratio < policy.min_wear_coverage:
        gates.append(
            _gate(
                ReasonCode.INSUFFICIENT_COVERAGE,
                f"wear coverage was {cov.wear_ratio:.0%} of the window, below the "
                f"{policy.min_wear_coverage:.0%} this claim requires",
                {"wear_ratio": round(cov.wear_ratio, 3),
                 "required": policy.min_wear_coverage},
            )
        )
    unverified: list[str] = []
    if not cov.known:
        unverified.append("no wear time was supplied for the window")
    if cov.overnight_required and not cov.overnight_known:
        unverified.append("no overnight wear time was supplied, and this claim depends on it")
    if unverified:
        # An unverifiable dimension scores at the policy floor, which is a real penalty.
        # It must therefore be reported: a coverage score silently dragged to 0.5 with no
        # accompanying reason is exactly the kind of unexplained number this engine is
        # meant to eliminate.
        gates.append(
            _gate(
                ReasonCode.COVERAGE_UNKNOWN,
                " and ".join(unverified) + ", so coverage could not be fully verified",
                {"wear_known": cov.known, "overnight_known": cov.overnight_known},
            )
        )
    if policy.min_overnight_coverage is not None and cov.overnight_ratio is not None:
        if cov.overnight_ratio < policy.min_overnight_coverage:
            gates.append(
                _gate(
                    ReasonCode.INSUFFICIENT_OVERNIGHT_COVERAGE,
                    f"overnight coverage was {cov.overnight_ratio:.0%}, below the "
                    f"{policy.min_overnight_coverage:.0%} this claim requires",
                    {"overnight_ratio": round(cov.overnight_ratio, 3),
                     "required": policy.min_overnight_coverage},
                )
            )
    if cov.max_gap_minutes is not None and cov.max_gap_minutes > policy.max_gap_minutes:
        gates.append(
            _gate(
                ReasonCode.LONG_DATA_GAP,
                f"the longest gap in the window was {cov.max_gap_minutes:.0f} minutes, "
                f"longer than the {policy.max_gap_minutes:.0f}-minute maximum",
                {"max_gap_minutes": round(cov.max_gap_minutes, 1),
                 "allowed": policy.max_gap_minutes},
            )
        )
    return gates


# --------------------------------------------------------------------------- freshness


def _freshness_gates(policy: ClaimPolicy, features: EvidenceFeatures) -> list[Gate]:
    gates: list[Gate] = []
    fresh = features.freshness
    if policy.requires_closed_window and not fresh.window_closed:
        gates.append(
            _gate(
                ReasonCode.WINDOW_NOT_CLOSED,
                "the target window has not ended yet, so the evidence for it is still "
                "incomplete by definition",
            )
        )
    if policy.requires_sync_after_window_end and fresh.sync_covers_window is False:
        gates.append(
            _gate(
                ReasonCode.SYNC_INCOMPLETE,
                "the last successful sync finished before the target window ended, so "
                "part of the window has not reached the server",
                {"sync_age_hours": fresh.sync_age_hours},
            )
        )
    if not fresh.sync_known:
        gates.append(
            _gate(
                ReasonCode.SYNC_STATE_UNKNOWN,
                "no sync-completion time was supplied, so whether the window has fully "
                "transferred could not be verified",
            )
        )
    if (
        fresh.measurement_age_hours is not None
        and fresh.measurement_age_hours > policy.max_measurement_age_hours
    ):
        gates.append(
            _gate(
                ReasonCode.STALE_DATA,
                f"the newest relevant measurement is {fresh.measurement_age_hours:.0f} "
                f"hours old, past the {policy.max_measurement_age_hours:.0f}-hour limit "
                "for this claim",
                {"measurement_age_hours": round(fresh.measurement_age_hours, 2),
                 "limit_hours": policy.max_measurement_age_hours},
            )
        )
    if fresh.sync_age_hours is not None and fresh.sync_age_hours > policy.max_sync_age_hours:
        gates.append(
            _gate(
                ReasonCode.STALE_DATA,
                f"the last successful sync was {fresh.sync_age_hours:.0f} hours ago, past "
                f"the {policy.max_sync_age_hours:.0f}-hour limit for this claim",
                {"sync_age_hours": round(fresh.sync_age_hours, 2),
                 "limit_hours": policy.max_sync_age_hours},
            )
        )
    return gates


# --------------------------------------------------------------------------- quality


def _quality_gates(policy: ClaimPolicy, features: EvidenceFeatures) -> list[Gate]:
    gates: list[Gate] = []
    q = features.quality
    if q.implausible_fraction > policy.implausible_reject_fraction:
        gates.append(
            _gate(
                ReasonCode.IMPLAUSIBLE_VALUES,
                f"{q.implausible_fraction:.0%} of the required measurements fall outside "
                "the physiological range, which indicates a sensor or pipeline fault "
                "rather than a physiological event",
                {"implausible_fraction": round(q.implausible_fraction, 3),
                 "allowed": policy.implausible_reject_fraction},
            )
        )
    if q.artifact_fraction > policy.flagged_wait_fraction:
        gates.append(
            _gate(
                ReasonCode.MOTION_ARTIFACT,
                f"{q.artifact_fraction:.0%} of the measurements in the window are flagged "
                "for motion or contact artifact",
                {"artifact_fraction": round(q.artifact_fraction, 3),
                 "allowed": policy.flagged_wait_fraction},
            )
        )
    if q.flatline_detected or q.dropout_fraction > 0.20:
        detail = (
            f"the sensor reported {q.longest_flat_run} identical consecutive values, "
            "which reads as a stuck sensor rather than a steady physiology"
            if q.flatline_detected
            else f"{q.dropout_fraction:.0%} of the measurements are flagged as dropout or "
                 "device removal"
        )
        gates.append(
            _gate(
                ReasonCode.SENSOR_DROPOUT,
                detail,
                {"longest_flat_run": q.longest_flat_run,
                 "dropout_fraction": round(q.dropout_fraction, 3)},
            )
        )
    if q.raw_quality < policy.min_signal_quality:
        gates.append(
            _gate(
                ReasonCode.LOW_SIGNAL_QUALITY,
                f"combined signal quality scored {q.raw_quality:.2f}, below the "
                f"{policy.min_signal_quality:.2f} floor for this claim",
                {"raw_quality": round(q.raw_quality, 3),
                 "floor": policy.min_signal_quality},
            )
        )
    return gates


# --------------------------------------------------------------------------- consistency


def _consistency_gates(features: EvidenceFeatures) -> list[Gate]:
    gates: list[Gate] = []
    for finding in features.consistency.findings:
        outcome = None if finding.penalty >= FATAL_CONFLICT_PENALTY else Outcome.WARN
        gates.append(_gate(finding.code, finding.detail, dict(finding.measurements), outcome))
    return gates


# --------------------------------------------------------------------------- baseline


def _baseline_gates(
    request: EvaluationRequest, policy: ClaimPolicy, features: EvidenceFeatures
) -> list[Gate]:
    gates: list[Gate] = []
    base = features.baseline
    days = base.valid_days

    if days < policy.min_baseline_days:
        # Below the warn floor the shortfall is fatal; inside the warn band it is a
        # disclosable limitation.
        outcome = Outcome.WAIT if days < policy.warn_baseline_days else Outcome.WARN
        gates.append(
            _gate(
                ReasonCode.BASELINE_NOT_MATURE,
                f"{days} valid baseline day(s) are available; this claim needs "
                f"{policy.min_baseline_days} to establish what is normal for this person",
                {"valid_days": days, "required_days": policy.min_baseline_days},
                outcome,
            )
        )
    if days < policy.warn_baseline_days:
        gates.append(
            _gate(
                ReasonCode.INSUFFICIENT_HISTORICAL_COVERAGE,
                f"the personal history covers {days} valid day(s), below the "
                f"{policy.warn_baseline_days}-day floor at which a comparison becomes "
                "meaningful at all",
                {"valid_days": days, "floor_days": policy.warn_baseline_days},
            )
        )
    elif base.submitted_days and days / base.submitted_days < 0.70:
        gates.append(
            _gate(
                ReasonCode.INSUFFICIENT_HISTORICAL_COVERAGE,
                f"only {days} of {base.submitted_days} submitted baseline days are valid, "
                "so the history is too patchy to describe a normal range",
                {"valid_days": days, "submitted_days": base.submitted_days},
            )
        )

    if base.source != "samples":
        gates.append(
            _gate(
                ReasonCode.BASELINE_STATISTICS_UNAVAILABLE,
                "no per-day baseline values were supplied, so the subject's normal range "
                "and the size of today's change cannot be computed"
                + (" (only a baseline day count was declared)" if base.source == "declared" else ""),
                {"baseline_source": base.source},
            )
        )
    if (
        policy.max_baseline_sd is not None
        and base.sd is not None
        and base.sd > policy.max_baseline_sd
    ):
        unit = ""
        gates.append(
            _gate(
                ReasonCode.BASELINE_UNSTABLE,
                f"the baseline varies by {base.sd:.1f}{unit} day to day, above the "
                f"{policy.max_baseline_sd:.1f} this claim allows; a change cannot be "
                "distinguished from ordinary swing",
                {"baseline_sd": round(base.sd, 2), "allowed_sd": policy.max_baseline_sd},
            )
        )
    if (
        policy.max_baseline_shift is not None
        and base.half_shift is not None
        and base.half_shift > policy.max_baseline_shift
    ):
        gates.append(
            _gate(
                ReasonCode.BASELINE_SHIFT_DETECTED,
                f"the first and second halves of the baseline differ by "
                f"{base.half_shift:.1f}, so the history contains a level shift rather "
                "than one stable normal",
                {"half_shift": round(base.half_shift, 2),
                 "allowed_shift": policy.max_baseline_shift},
            )
        )
    if request.context.device_changed_recently:
        gates.append(
            _gate(
                ReasonCode.BASELINE_SHIFT_DETECTED,
                "the device changed recently, so measurements before and after it are "
                "not directly comparable and the existing baseline does not apply",
                {"device_changed_recently": True},
            )
        )
    return gates


# --------------------------------------------------------------------------- claim support


def _support_gates(
    request: EvaluationRequest, policy: ClaimPolicy, features: EvidenceFeatures
) -> list[Gate]:
    gates: list[Gate] = []
    for signal in features.missing_required:
        gates.append(
            _gate(
                ReasonCode.MISSING_REQUIRED_SIGNAL,
                f"this claim requires {label(signal)} in the target window and none was "
                "available",
                {"signal": signal.value},
            )
        )

    sup = features.support
    if policy.mode == "anomaly" and sup.sample_count == 1:
        gates.append(
            _gate(
                ReasonCode.SINGLE_SAMPLE_EXCURSION,
                "the excursion rests on a single sample; this claim requires at least two "
                "independent measurements before it is worth raising",
                {"sample_count": sup.sample_count},
            )
        )

    if sup.available and sup.z_directional is not None:
        if sup.z_directional <= policy.contradict_z:
            gates.append(
                _gate(
                    ReasonCode.CLAIM_CONTRADICTED,
                    f"the measurement sits {sup.z_directional:+.1f} SD from baseline in "
                    f"the direction this claim asserts, at or below the "
                    f"{policy.contradict_z:.1f} SD point where the evidence argues "
                    "against the claim rather than for it",
                    {"z_directional": round(sup.z_directional, 3),
                     "contradict_z": policy.contradict_z},
                )
            )
        elif (
            policy.min_absolute_delta > 0
            and sup.absolute_delta is not None
            and sup.absolute_delta < policy.min_absolute_delta
            and sup.z_directional >= policy.warn_z
        ):
            gates.append(
                _gate(
                    ReasonCode.EFFECT_BELOW_RELIABLE_THRESHOLD,
                    f"the change is {sup.absolute_delta:.1f} in absolute terms, below the "
                    f"{policy.min_absolute_delta:.1f} the device can reliably resolve, "
                    f"even though it is {sup.z_directional:.1f} SD against a very tight "
                    "baseline",
                    {"absolute_delta": round(sup.absolute_delta, 2),
                     "min_absolute_delta": policy.min_absolute_delta,
                     "z_directional": round(sup.z_directional, 3)},
                )
            )

    exercise = request.context.exercise_minutes_last_24h
    if (
        exercise is not None
        and exercise >= CONFOUNDING_EXERCISE_MINUTES
        and features.support.signal in (Signal.RESTING_HEART_RATE, Signal.HRV_RMSSD)
    ):
        gates.append(
            _gate(
                ReasonCode.CONFOUNDER_UNRESOLVED,
                f"{exercise:.0f} minutes of exercise in the preceding 24 hours is a known "
                "cause of this change and has not been ruled out as the explanation",
                {"exercise_minutes_last_24h": round(exercise, 1)},
            )
        )
    return gates


# --------------------------------------------------------------------------- insufficiency


@dataclass(frozen=True)
class InputAdequacy:
    signal: Signal
    present: bool
    fresh: bool
    baseline_mature: bool
    reasons: tuple[str, ...]

    @property
    def adequate(self) -> bool:
        return self.present and self.fresh and self.baseline_mature


def assess_inputs(
    norm: NormalizedEvidence, request: EvaluationRequest, policy: ClaimPolicy
) -> list[InputAdequacy]:
    """Per-input adequacy for the insufficiency claim.

    This claim asserts that evidence is inadequate, so adequacy is the thing being
    measured rather than a precondition for measuring something else.
    """
    out: list[InputAdequacy] = []
    now = norm.evaluated_at
    for signal in policy.insufficiency_inputs:
        obs = norm.in_window.get(signal, [])
        present = bool(obs)
        reasons: list[str] = []
        if not present:
            reasons.append(f"no {label(signal)} in the window")
        age = None
        if obs:
            age = (now - obs[-1].measured_at).total_seconds() / 3600.0
        fresh = age is not None and age <= policy.max_measurement_age_hours
        if present and not fresh:
            reasons.append(f"{label(signal)} is {age:.0f} hours old")
        valid_days = len([s for s in norm.baseline.get(signal, []) if s.valid])
        if valid_days == 0 and request.context.baseline_days is not None:
            valid_days = max(0, int(request.context.baseline_days))
        mature = valid_days >= policy.min_baseline_days
        if not mature:
            reasons.append(
                f"{label(signal)} has {valid_days} of {policy.min_baseline_days} baseline days"
            )
        out.append(
            InputAdequacy(signal, present, fresh, mature, tuple(reasons))
        )
    return out


def _insufficiency_gates(
    norm: NormalizedEvidence,
    request: EvaluationRequest,
    policy: ClaimPolicy,
    features: EvidenceFeatures,
) -> list[Gate]:
    gates: list[Gate] = []
    fresh = features.freshness
    if policy.requires_closed_window and not fresh.window_closed:
        gates.append(
            _gate(
                ReasonCode.WINDOW_NOT_CLOSED,
                "the target window has not ended yet; calling the evidence incomplete "
                "before the window closes would be true but uninformative",
            )
        )
    adequacy = assess_inputs(norm, request, policy)
    inadequate = [a for a in adequacy if not a.adequate]
    if not inadequate:
        gates.append(
            _gate(
                ReasonCode.EVIDENCE_COMPLETE,
                "all recovery inputs are present, fresh, and backed by a mature "
                "baseline, so the claim that the evidence is incomplete is contradicted",
                {"inputs_checked": len(adequacy)},
            )
        )
    return gates
