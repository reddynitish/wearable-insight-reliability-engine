"""Evidence feature extraction: five dimensions plus claim support.

Every feature is either measured from the submitted evidence or explicitly absent.
`None` means "not determinable from what was submitted" and is never replaced by a
default, because a plausible-looking default is how a reliability engine ends up
certifying evidence it never saw.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.consistency import Finding, get_rule
from engine.normalize import NormalizedEvidence
from engine.policies.base import ClaimPolicy
from engine.schemas import EvaluationRequest, QualityFlag
from engine.scoring import band_score, clamp, inverse_band_score, mean, sample_sd
from engine.signals import Signal, spec

# Flags that indicate the measurement itself is suspect, as opposed to merely annotated.
DEGRADING_FLAGS = frozenset(
    {
        QualityFlag.MOTION_ARTIFACT,
        QualityFlag.LOW_PERFUSION,
        QualityFlag.POOR_SKIN_CONTACT,
        QualityFlag.SENSOR_DROPOUT,
        QualityFlag.DEVICE_REMOVED,
        QualityFlag.INTERPOLATED,
        QualityFlag.UNVALIDATED,
    }
)
ARTIFACT_FLAGS = frozenset({QualityFlag.MOTION_ARTIFACT, QualityFlag.LOW_PERFUSION,
                            QualityFlag.POOR_SKIN_CONTACT})
DROPOUT_FLAGS = frozenset({QualityFlag.SENSOR_DROPOUT, QualityFlag.DEVICE_REMOVED})

MIN_FLATLINE_RUN = 5


@dataclass
class CoverageFeatures:
    wear_minutes: float | None = None
    expected_minutes: float = 0.0
    wear_ratio: float | None = None
    overnight_ratio: float | None = None
    max_gap_minutes: float | None = None
    gap_count: int = 0
    known: bool = False
    overnight_known: bool = False
    overnight_required: bool = False
    score: float = 0.5

    def as_dict(self) -> dict[str, Any]:
        return {
            "wear_minutes": _r(self.wear_minutes),
            "expected_minutes": _r(self.expected_minutes),
            "wear_ratio": _r(self.wear_ratio, 3),
            "overnight_ratio": _r(self.overnight_ratio, 3),
            "max_gap_minutes": _r(self.max_gap_minutes),
            "gap_count": self.gap_count,
            "known": self.known,
            "overnight_known": self.overnight_known,
            "overnight_required": self.overnight_required,
            "score": round(self.score, 3),
        }


@dataclass
class FreshnessFeatures:
    measurement_age_hours: float | None = None
    sync_age_hours: float | None = None
    window_closed: bool = True
    sync_covers_window: bool | None = None
    sync_known: bool = False
    score: float = 0.5

    def as_dict(self) -> dict[str, Any]:
        return {
            "measurement_age_hours": _r(self.measurement_age_hours, 2),
            "sync_age_hours": _r(self.sync_age_hours, 2),
            "window_closed": self.window_closed,
            "sync_covers_window": self.sync_covers_window,
            "sync_known": self.sync_known,
            "score": round(self.score, 3),
        }


@dataclass
class QualityFeatures:
    implausible_fraction: float = 0.0
    flagged_fraction: float = 0.0
    artifact_fraction: float = 0.0
    dropout_fraction: float = 0.0
    longest_flat_run: int = 0
    flatline_detected: bool = False
    raw_quality: float = 1.0
    score: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "implausible_fraction": round(self.implausible_fraction, 3),
            "flagged_fraction": round(self.flagged_fraction, 3),
            "artifact_fraction": round(self.artifact_fraction, 3),
            "dropout_fraction": round(self.dropout_fraction, 3),
            "longest_flat_run": self.longest_flat_run,
            "flatline_detected": self.flatline_detected,
            "raw_quality": round(self.raw_quality, 3),
            "score": round(self.score, 3),
        }


@dataclass
class ConsistencyFeatures:
    findings: list[Finding] = field(default_factory=list)
    rules_run: list[str] = field(default_factory=list)
    raw_consistency: float = 1.0
    score: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "rules_run": self.rules_run,
            "findings": [
                {"code": f.code.value, "detail": f.detail, "penalty": f.penalty}
                for f in self.findings
            ],
            "raw_consistency": round(self.raw_consistency, 3),
            "score": round(self.score, 3),
        }


@dataclass
class BaselineFeatures:
    signal: Signal | None = None
    valid_days: int = 0
    submitted_days: int = 0
    mean: float | None = None
    sd: float | None = None
    effective_sd: float | None = None
    half_shift: float | None = None
    # "samples" = per-day values supplied; "declared" = only context.baseline_days;
    # "none" = nothing to go on.
    source: str = "none"
    score: float = 0.0

    # Set by _baseline(): whether these statistics are fit to standardise a change
    # against. A baseline below the policy's floor, or one that is unstable or contains a
    # level shift, has a mean and an SD but no meaning.
    usable: bool = False

    @property
    def statistics_available(self) -> bool:
        return self.mean is not None and self.effective_sd is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal.value if self.signal else None,
            "valid_days": self.valid_days,
            "submitted_days": self.submitted_days,
            "mean": _r(self.mean, 2),
            "sd": _r(self.sd, 2),
            "effective_sd": _r(self.effective_sd, 2),
            "half_shift": _r(self.half_shift, 2),
            "source": self.source,
            "statistics_available": self.statistics_available,
            "usable": self.usable,
            "score": round(self.score, 3),
        }


@dataclass
class SupportFeatures:
    signal: Signal | None = None
    target_value: float | None = None
    sample_count: int = 0
    delta: float | None = None
    absolute_delta: float | None = None
    z: float | None = None
    # Effect expressed in the direction the claim asserts: positive means "toward the
    # claim". A claim that the value is LOW has direction -1, so a fall becomes +z here.
    z_directional: float | None = None
    available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal.value if self.signal else None,
            "target_value": _r(self.target_value, 2),
            "sample_count": self.sample_count,
            "delta": _r(self.delta, 2),
            "absolute_delta": _r(self.absolute_delta, 2),
            "z": _r(self.z, 3),
            "z_directional": _r(self.z_directional, 3),
            "available": self.available,
        }


@dataclass
class EvidenceFeatures:
    coverage: CoverageFeatures
    freshness: FreshnessFeatures
    quality: QualityFeatures
    consistency: ConsistencyFeatures
    baseline: BaselineFeatures
    support: SupportFeatures
    missing_required: list[Signal] = field(default_factory=list)
    present_required: list[Signal] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage.as_dict(),
            "freshness": self.freshness.as_dict(),
            "signal_quality": self.quality.as_dict(),
            "consistency": self.consistency.as_dict(),
            "baseline": self.baseline.as_dict(),
            "claim_support": self.support.as_dict(),
            "missing_required_signals": [s.value for s in self.missing_required],
            "present_required_signals": [s.value for s in self.present_required],
        }


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


# --------------------------------------------------------------------------- extraction


def extract(
    norm: NormalizedEvidence, request: EvaluationRequest, policy: ClaimPolicy
) -> EvidenceFeatures:
    target_signal = _resolve_target_signal(request, policy)
    return EvidenceFeatures(
        coverage=_coverage(norm, request, policy),
        freshness=_freshness(norm, request, policy, target_signal),
        quality=_quality(norm, policy, target_signal),
        consistency=_consistency(norm, request, policy),
        baseline=_baseline(norm, request, policy, target_signal),
        support=_support(norm, request, policy, target_signal),
        missing_required=[s for s in policy.required_signals if not norm.in_window.get(s)],
        present_required=[s for s in policy.required_signals if norm.in_window.get(s)],
    )


def _resolve_target_signal(request: EvaluationRequest, policy: ClaimPolicy) -> Signal | None:
    """The signal the effect is measured on. Anomaly claims name it in the request."""
    if policy.mode == "anomaly":
        return request.claim.signal
    return policy.target_signal


def _coverage(
    norm: NormalizedEvidence, request: EvaluationRequest, policy: ClaimPolicy
) -> CoverageFeatures:
    ctx = request.context
    window_minutes = norm.target_window.duration_minutes
    expected = ctx.expected_worn_minutes or window_minutes

    worn = ctx.device_worn_minutes
    if worn is None:
        wear_obs = norm.window_values(Signal.WEAR_MINUTES)
        worn = sum(wear_obs) if wear_obs else None

    feats = CoverageFeatures(wear_minutes=worn, expected_minutes=expected)
    feats.wear_ratio = None if worn is None or expected <= 0 else clamp(worn / expected, 0.0, 2.0)
    feats.known = feats.wear_ratio is not None

    feats.overnight_required = policy.min_overnight_coverage is not None
    if ctx.overnight_worn_minutes is not None:
        overnight_span = ctx.overnight_window_minutes or 480.0
        if overnight_span > 0:
            feats.overnight_ratio = clamp(
                ctx.overnight_worn_minutes / overnight_span, 0.0, 2.0
            )
            feats.overnight_known = True

    gaps = list(ctx.reported_gaps_minutes)
    if not gaps:
        gaps = _inferred_gaps(norm, policy)
    if gaps:
        feats.gap_count = len(gaps)
        feats.max_gap_minutes = max(gaps)

    # Unknown coverage scores exactly at the policy floor: not a pass, not a failure.
    # A gate reports the unknown separately; see gates.COVERAGE_UNKNOWN.
    parts = [
        0.5
        if feats.wear_ratio is None
        else band_score(feats.wear_ratio, policy.min_wear_coverage, policy.good_wear_coverage)
    ]
    if policy.min_overnight_coverage is not None:
        parts.append(
            0.5
            if feats.overnight_ratio is None
            else band_score(
                feats.overnight_ratio,
                policy.min_overnight_coverage,
                policy.good_overnight_coverage,
            )
        )
    if feats.max_gap_minutes is not None:
        parts.append(
            inverse_band_score(feats.max_gap_minutes, policy.max_gap_minutes, policy.good_gap_minutes)
        )
    # The weakest sub-dimension governs: a perfect wear ratio does not compensate for a
    # six-hour hole in the middle of the claim window.
    feats.score = min(parts)
    return feats


def _inferred_gaps(norm: NormalizedEvidence, policy: ClaimPolicy) -> list[float]:
    """Gaps inferred from interval spacing, only where spacing is meaningful.

    Requires at least four in-window samples of a required signal. With fewer, the
    spacing carries no information about wear -- one daily aggregate has no gaps -- and
    inventing a gap from it would be exactly the kind of fabricated evidence this engine
    is built to avoid.
    """
    for signal in policy.required_signals:
        obs = norm.in_window.get(signal, [])
        if len(obs) < 4:
            continue
        deltas = [
            (b.measured_at - a.measured_at).total_seconds() / 60.0
            for a, b in zip(obs, obs[1:])
        ]
        if not deltas:
            continue
        typical = sorted(deltas)[len(deltas) // 2]
        if typical <= 0:
            continue
        # A gap is an interval more than four times the typical cadence.
        return [d for d in deltas if d > max(4 * typical, typical + 5)]
    return []


def _freshness(
    norm: NormalizedEvidence,
    request: EvaluationRequest,
    policy: ClaimPolicy,
    target_signal: Signal | None,
) -> FreshnessFeatures:
    feats = FreshnessFeatures()
    now = norm.evaluated_at
    feats.window_closed = now >= norm.target_window.end

    relevant = [s for s in (policy.required_signals or ()) if s in norm.all_plausible]
    if target_signal and target_signal in norm.all_plausible:
        relevant.append(target_signal)
    newest = None
    for signal in relevant:
        candidate = norm.newest(signal)
        if candidate is None:
            continue
        if newest is None or candidate.measured_at > newest.measured_at:
            newest = candidate
    if newest is not None:
        feats.measurement_age_hours = max(
            0.0, (now - newest.measured_at).total_seconds() / 3600.0
        )

    sync = request.context.sync_completed_at
    feats.sync_known = sync is not None
    if sync is not None:
        feats.sync_age_hours = max(0.0, (now - sync).total_seconds() / 3600.0)
        feats.sync_covers_window = sync >= norm.target_window.end

    parts: list[float] = []
    if feats.measurement_age_hours is not None:
        parts.append(
            inverse_band_score(
                feats.measurement_age_hours,
                policy.max_measurement_age_hours,
                policy.good_measurement_age_hours,
            )
        )
    if feats.sync_age_hours is not None:
        parts.append(
            inverse_band_score(
                feats.sync_age_hours, policy.max_sync_age_hours, policy.good_measurement_age_hours
            )
        )
    else:
        parts.append(0.5)
    if not feats.window_closed:
        parts.append(0.0)
    feats.score = min(parts) if parts else 0.5
    return feats


def _quality(
    norm: NormalizedEvidence, policy: ClaimPolicy, target_signal: Signal | None
) -> QualityFeatures:
    feats = QualityFeatures()
    considered = set(policy.required_signals) | set(policy.supporting_signals)
    if target_signal:
        considered.add(target_signal)

    total = 0
    flagged = 0
    artifact = 0
    dropout = 0
    for signal in considered:
        obs = norm.in_window.get(signal, [])
        total += len(obs)
        for o in obs:
            flags = set(o.quality_flags)
            if flags & DEGRADING_FLAGS:
                flagged += 1
            if flags & ARTIFACT_FLAGS:
                artifact += 1
            if flags & DROPOUT_FLAGS:
                dropout += 1
    if total:
        feats.flagged_fraction = flagged / total
        feats.artifact_fraction = artifact / total
        feats.dropout_fraction = dropout / total

    # Implausibility is judged on the required signals only: a bad SpO2 reading should
    # not sink a claim about step count.
    judged = list(policy.required_signals) or ([target_signal] if target_signal else [])
    fractions = [norm.implausible_fraction.get(s, 0.0) for s in judged if s]
    feats.implausible_fraction = max(fractions) if fractions else 0.0

    if target_signal:
        feats.longest_flat_run = _longest_flat_run(norm.window_values(target_signal))
        count = len(norm.window_values(target_signal))
        feats.flatline_detected = (
            feats.longest_flat_run >= MIN_FLATLINE_RUN
            and count > 0
            and feats.longest_flat_run / count >= 0.30
        )

    feats.raw_quality = clamp(
        1.0
        - 0.80 * feats.implausible_fraction
        - 0.50 * feats.artifact_fraction
        - 0.60 * feats.dropout_fraction
        - 0.20 * max(0.0, feats.flagged_fraction - feats.artifact_fraction - feats.dropout_fraction)
        - (0.30 if feats.flatline_detected else 0.0)
    )
    feats.score = band_score(feats.raw_quality, policy.min_signal_quality, 1.0)
    return feats


def _longest_flat_run(values: list[float]) -> int:
    """Longest run of consecutive identical values. A stuck sensor reads perfectly steady."""
    if not values:
        return 0
    best = run = 1
    for a, b in zip(values, values[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    return best


def _consistency(
    norm: NormalizedEvidence, request: EvaluationRequest, policy: ClaimPolicy
) -> ConsistencyFeatures:
    feats = ConsistencyFeatures(rules_run=list(policy.consistency_rules))
    for name in policy.consistency_rules:
        finding = get_rule(name)(norm, request)
        if finding is not None:
            feats.findings.append(finding)
    feats.raw_consistency = clamp(1.0 - sum(f.penalty for f in feats.findings))
    feats.score = band_score(feats.raw_consistency, policy.min_consistency, 1.0)
    return feats


def _baseline(
    norm: NormalizedEvidence,
    request: EvaluationRequest,
    policy: ClaimPolicy,
    target_signal: Signal | None,
) -> BaselineFeatures:
    feats = BaselineFeatures(signal=target_signal)
    samples = norm.baseline.get(target_signal, []) if target_signal else []
    valid = [s for s in samples if s.valid]
    feats.submitted_days = len(samples)

    if valid:
        feats.source = "samples"
        feats.valid_days = len(valid)
        values = [s.value for s in valid]
        feats.mean = mean(values)
        feats.sd = sample_sd(values)
        noise = spec(target_signal).noise_sd if target_signal else 1.0
        # Floor the SD at the device's own measurement noise: a suspiciously tight
        # history must not turn a change smaller than the sensor can resolve into a
        # large standardised effect.
        feats.effective_sd = max(feats.sd, noise)
        if len(values) >= 6:
            half = len(values) // 2
            feats.half_shift = abs(mean(values[half:]) - mean(values[:half]))
    elif request.context.baseline_days is not None:
        # Only a declared count: maturity is assessable, but no mean or SD exists, so no
        # effect size can be computed. That is a gate, not something to approximate.
        feats.source = "declared"
        feats.valid_days = max(0, int(request.context.baseline_days))
        feats.submitted_days = feats.valid_days

    feats.score = band_score(
        feats.valid_days, policy.min_baseline_days, policy.good_baseline_days
    )
    unstable = (
        policy.max_baseline_sd is not None
        and feats.sd is not None
        and feats.sd > policy.max_baseline_sd
    )
    shifted = (
        policy.max_baseline_shift is not None
        and feats.half_shift is not None
        and feats.half_shift > policy.max_baseline_shift
    )
    feats.usable = (
        feats.statistics_available
        and feats.source == "samples"
        and feats.valid_days >= policy.warn_baseline_days
        and not unstable
        and not shifted
    )
    return feats


def _support(
    norm: NormalizedEvidence,
    request: EvaluationRequest,
    policy: ClaimPolicy,
    target_signal: Signal | None,
) -> SupportFeatures:
    feats = SupportFeatures(signal=target_signal)
    if target_signal is None:
        return feats
    values = norm.window_values(target_signal)
    feats.sample_count = len(values)
    if not values:
        return feats
    feats.target_value = _aggregate(values, policy.target_aggregation)

    base = _baseline(norm, request, policy, target_signal)
    if not base.usable:
        # Without a usable baseline there is no defensible effect size. Reporting one
        # anyway -- "4.7 SD above a one-day baseline" -- would dress an arbitrary number
        # up as a measurement, and the support score would inherit that fiction.
        return feats
    assert base.mean is not None and base.effective_sd is not None
    feats.delta = feats.target_value - base.mean
    feats.absolute_delta = abs(feats.delta)
    feats.z = feats.delta / base.effective_sd
    # Anomaly claims are two-sided: an excursion in either direction is an excursion.
    feats.z_directional = abs(feats.z) if policy.mode == "anomaly" else policy.direction * feats.z
    feats.available = True
    return feats


def _aggregate(values: list[float], how: str) -> float:
    if how == "sum":
        return sum(values)
    if how == "max":
        return max(values)
    if how == "min":
        return min(values)
    if how == "last":
        return values[-1]
    return mean(values)
