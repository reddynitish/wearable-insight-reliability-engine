"""Deterministic, seeded corruption transforms.

Each transform takes a clean synthetic case and degrades its *evidence* in one specific,
documented way, then relabels the ground-truth evidence state. Corruptions are applied
to the request only: no transform touches a feature, a score, or a policy, so the
corruption's identity cannot leak into anything the engine reads. That property is
asserted by a test, because it is the assumption that keeps this harness usable once a
learned component exists (docs/evaluation-protocol.md section 6).

Two transforms are deliberately *benign* at mild severity -- duplicated and out-of-order
samples -- and keep the SUPPORTABLE label. An engine that abstains on those is
over-abstaining, and without benign cases in the suite that failure mode is invisible.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Literal

from engine.measure import measure
from engine.policies import get_policy
from engine.schemas import BaselineSample, EvaluationRequest, Observation, QualityFlag
from engine.signals import Signal, spec
from engine.synth import GroundTruth, SyntheticCase

Severity = Literal["mild", "moderate", "severe"]
SEVERITIES: tuple[Severity, ...] = ("mild", "moderate", "severe")


@dataclass(frozen=True)
class CorruptionSpec:
    name: str
    rationale: str
    dimension: str
    fn: Callable[[SyntheticCase, Severity, random.Random], EvaluationRequest]
    # False for transforms the engine is expected to absorb (duplication, reordering).
    degrades_evidence: bool = True


_REGISTRY: dict[str, CorruptionSpec] = {}

# Which contract clause a measured breach falls under. The distinction matters for the
# per-failure-mode breakdown; for the unsupported-show rate all UNSUPPORTABLE_* states
# are equivalent, because showing a claim is wrong in all of them.
_INSUFFICIENCY_BREACHES = frozenset({
    "min_wear_coverage", "not_worn", "max_gap_minutes",
    "requires_sync_after_window_end", "max_measurement_age_hours",
    "requires_closed_window", "requires_timezone", "missing_required_signal",
    "no_target_samples", "warn_baseline_days", "baseline_statistics_unavailable",
    "max_sync_age_hours", "window_misaligned",
})
# Breaches that stop the engine from placing or reading the window at all. For the
# inverted claim these are NOT support for insufficiency: being unable to locate the day
# is a different problem from the day's evidence being thin, and the engine should abstain.
# Staleness is deliberately NOT blocking: for the inverted claim, evidence that is too
# old to use is exactly the inadequacy the claim asserts, so a stale bundle supports it.
_BLOCKING_BREACHES = frozenset({
    "requires_timezone", "requires_closed_window", "window_misaligned",
})
_UNTRUSTWORTHY_BREACHES = frozenset({
    "flagged_wait_fraction", "dropout_fraction", "implausible_reject_fraction",
    "flatline", "max_baseline_sd", "max_baseline_shift", "device_changed_recently",
    "summary_detail_mismatch",
})


def corruption(
    name: str, rationale: str, dimension: str, degrades_evidence: bool = True
) -> Callable[..., Callable[..., EvaluationRequest]]:
    def register(fn):
        _REGISTRY[name] = CorruptionSpec(name, rationale, dimension, fn, degrades_evidence)
        return fn

    return register


def apply(case: SyntheticCase, name: str, severity: Severity, seed: int = 0) -> SyntheticCase:
    """Return a new case with `name` at `severity` applied. The input is not mutated.

    The ground-truth label is **measured, not assumed**. A corruption that was expected
    to breach a requirement but did not -- because the case had too few samples, or
    because that requirement does not apply to this claim type -- is labelled by what the
    evidence actually contains. Assuming "delayed sync therefore unsupportable" would be
    wrong for a claim whose policy does not require a sync after the window, and the
    resulting false failures would make the headline metric meaningless.
    """
    if name not in _REGISTRY:
        raise KeyError(f"unknown corruption {name!r}; have {sorted(_REGISTRY)}")
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}, got {severity!r}")
    spec_ = _REGISTRY[name]
    rng = random.Random(f"{seed}|{name}|{severity}|{case.generator}")
    request = spec_.fn(case, severity, rng)

    policy = get_policy(request.claim.type)
    if policy is None:
        raise KeyError(f"no policy for {request.claim.type!r}")
    m = measure(request, policy)
    truth, reason = _label(m, policy, case, spec_, severity)

    return SyntheticCase(
        request=request,
        truth=truth,
        truth_reason=reason,
        generator=case.generator,
        seed=case.seed,
        corruption=name,
        severity=severity,
        tags=sorted({*case.tags, "corrupted", f"dim:{spec_.dimension}"}),
        breaches=list(m.breaches),
        measurements=m.as_dict(),
    )


def _label(m, policy, case, spec_, severity) -> tuple[GroundTruth, str]:
    """Derive the ground-truth evidence state from measured breaches and effect size."""
    if policy.mode == "insufficiency":
        # Inverted claim: degraded evidence is what makes the insufficiency claim TRUE --
        # but only when the engine could still read the window at all.
        blocking = sorted(set(m.breaches) & _BLOCKING_BREACHES)
        if blocking:
            return (
                GroundTruth.UNSUPPORTABLE_INSUFFICIENT,
                f"{spec_.rationale} ({severity}) breached {', '.join(blocking)}, which "
                "prevents placing the window rather than showing thin evidence",
            )
        if "recovery_inputs_inadequate" in m.breaches:
            return (
                GroundTruth.SUPPORTABLE,
                f"{spec_.rationale} ({severity}) left {m.inputs_inadequate} of "
                f"{m.inputs_checked} recovery inputs inadequate, which is what this "
                "claim asserts",
            )
        return (
            GroundTruth.UNSUPPORTABLE_CONTRADICTED,
            f"{spec_.rationale} ({severity}) breached nothing, so the recovery inputs "
            "remain adequate and the insufficiency claim is contradicted",
        )

    insufficient = sorted(set(m.breaches) & _INSUFFICIENCY_BREACHES)
    untrustworthy = sorted(set(m.breaches) & _UNTRUSTWORTHY_BREACHES)
    if untrustworthy:
        return (
            GroundTruth.UNSUPPORTABLE_UNTRUSTWORTHY,
            f"{spec_.rationale} ({severity}) breached {', '.join(untrustworthy)}",
        )
    if insufficient:
        return (
            GroundTruth.UNSUPPORTABLE_INSUFFICIENT,
            f"{spec_.rationale} ({severity}) breached {', '.join(insufficient)}",
        )
    if m.z_directional is None:
        return (
            GroundTruth.UNSUPPORTABLE_INSUFFICIENT,
            f"{spec_.rationale} ({severity}) left no computable effect size",
        )
    if m.z_directional <= policy.contradict_z:
        return (
            GroundTruth.UNSUPPORTABLE_CONTRADICTED,
            f"{spec_.rationale} ({severity}) left the effect at "
            f"{m.z_directional:+.2f} SD, at or below the contradiction threshold",
        )
    below_floor = (
        policy.min_absolute_delta > 0
        and m.absolute_delta is not None
        and m.absolute_delta < policy.min_absolute_delta
    )
    if m.z_directional >= policy.show_z and not below_floor:
        return (
            GroundTruth.SUPPORTABLE,
            f"{spec_.rationale} ({severity}) breached no requirement and left the effect "
            f"at {m.z_directional:+.2f} SD, above the display threshold",
        )
    return (
        GroundTruth.BORDERLINE_EXCLUDED,
        f"{spec_.rationale} ({severity}) breached no requirement but left the effect at "
        f"{m.z_directional:+.2f} SD, between contradiction and display; excluded from "
        "the headline rates",
    )


def names() -> list[str]:
    return sorted(_REGISTRY)


def describe() -> list[CorruptionSpec]:
    return [_REGISTRY[n] for n in names()]


# --------------------------------------------------------------------------- helpers



def _req(case: SyntheticCase, **updates) -> EvaluationRequest:
    return case.request.model_copy(update=updates)


def _ctx(case: SyntheticCase, **updates) -> dict:
    return {"context": case.request.context.model_copy(update=updates)}


def _target_signal(case: SyntheticCase) -> Signal | None:
    """The signal the claim's effect is measured on, for targeted corruptions."""
    from engine.policies import get_policy

    policy = get_policy(case.request.claim.type)
    if policy is None:
        return None
    if policy.mode == "anomaly":
        return case.request.claim.signal
    return policy.target_signal


def _densest_signal(case: SyntheticCase) -> Signal | None:
    """The signal with the most samples: the only one where per-sample damage shows up."""
    counts: dict[Signal, int] = {}
    for obs in case.request.observations:
        counts[obs.signal] = counts.get(obs.signal, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], kv[0].value))[0]


# --------------------------------------------------------------------------- transforms


@corruption(
    "drop_contiguous",
    "a contiguous block of the window is missing, as happens when a device is charged "
    "mid-window; wear time is reduced consistently with the hole",
    "coverage",
)
def _drop_contiguous(case, severity, rng) -> EvaluationRequest:
    fraction = {"mild": 0.25, "moderate": 0.45, "severe": 0.70}[severity]
    window = case.request.claim.target_window
    span = window.end - window.start
    gap_start = window.start + span * 0.3
    gap_end = gap_start + span * fraction
    kept = [
        o for o in case.request.observations
        if not (gap_start <= o.measured_at < gap_end)
    ]
    worn = case.request.context.device_worn_minutes
    updates = _ctx(
        case,
        device_worn_minutes=None if worn is None else max(0.0, worn * (1 - fraction)),
        reported_gaps_minutes=[span.total_seconds() / 60.0 * fraction],
    )
    return _req(case, observations=kept, **updates)


@corruption(
    "drop_random",
    "samples are missing at random across the window, as happens with an unreliable "
    "sync; the summary aggregates survive while the detail thins out",
    "coverage",
)
def _drop_random(case, severity, rng) -> EvaluationRequest:
    fraction = {"mild": 0.10, "moderate": 0.50, "severe": 0.85}[severity]
    target = _target_signal(case)
    kept = [
        o for o in case.request.observations
        if o.signal == target or rng.random() > fraction
    ]
    worn = case.request.context.device_worn_minutes
    updates = {}
    if severity != "mild" and worn is not None:
        updates = _ctx(case, device_worn_minutes=worn * (1 - fraction * 0.8))
    return _req(case, observations=kept, **updates)


@corruption(
    "shorten_wear",
    "the device was worn for much less of the window than the claim requires",
    "coverage",
)
def _shorten_wear(case, severity, rng) -> EvaluationRequest:
    ratio = {"mild": 0.55, "moderate": 0.30, "severe": 0.05}[severity]
    expected = (
        case.request.context.expected_worn_minutes
        or case.request.claim.target_window.duration_minutes
    )
    overnight = case.request.context.overnight_worn_minutes
    updates = _ctx(
        case,
        device_worn_minutes=expected * ratio,
        overnight_worn_minutes=None if overnight is None else overnight * ratio,
    )
    return _req(case, **updates)


@corruption(
    "delay_sync",
    "the last successful sync finished before the window ended, so part of the window "
    "has not reached the server yet",
    "freshness",
)
def _delay_sync(case, severity, rng) -> EvaluationRequest:
    hours_before_end = {"mild": 1.0, "moderate": 6.0, "severe": 20.0}[severity]
    window = case.request.claim.target_window
    return _req(
        case,
        **_ctx(case, sync_completed_at=window.end - timedelta(hours=hours_before_end)),
    )


@corruption(
    "stale_data",
    "the whole evidence bundle is old: evaluation happens long after the newest "
    "measurement and the last sync",
    "freshness",
)
def _stale_data(case, severity, rng) -> EvaluationRequest:
    extra_hours = {"mild": 30.0, "moderate": 60.0, "severe": 200.0}[severity]
    evaluated = (case.request.evaluated_at or case.request.claim.target_window.end)
    return _req(case, evaluated_at=evaluated + timedelta(hours=extra_hours))


@corruption(
    "motion_artifact",
    "optical measurements are contaminated by movement, the dominant failure mode for "
    "wrist PPG; values survive but are not trustworthy",
    "signal_quality",
)
def _motion_artifact(case, severity, rng) -> EvaluationRequest:
    fraction = {"mild": 0.05, "moderate": 0.45, "severe": 0.90}[severity]
    dense = _densest_signal(case)
    obs = []
    for o in case.request.observations:
        if o.signal == dense and rng.random() < fraction:
            jitter = rng.gauss(0, 8.0)
            obs.append(
                o.model_copy(
                    update={
                        "quality_flags": [*o.quality_flags, QualityFlag.MOTION_ARTIFACT],
                        "value": o.value + jitter,
                    }
                )
            )
        else:
            obs.append(o)
    return _req(case, observations=obs)


@corruption(
    "flatline",
    "the sensor sticks and repeats one value; a stuck reading looks like admirably "
    "steady physiology unless it is checked for",
    "signal_quality",
)
def _flatline(case, severity, rng) -> EvaluationRequest:
    fraction = {"mild": 0.15, "moderate": 0.45, "severe": 0.85}[severity]
    dense = _densest_signal(case)
    same = [o for o in case.request.observations if o.signal == dense]
    if not same:
        return case.request
    stuck_value = same[len(same) // 2].value
    count = int(len(same) * fraction)
    frozen_ids = {id(o) for o in same[: max(count, 0)]}
    obs = [
        o.model_copy(update={"value": stuck_value}) if id(o) in frozen_ids else o
        for o in case.request.observations
    ]
    return _req(case, observations=obs)


@corruption(
    "dropout_flags",
    "the pipeline reports sensor dropout or device removal across part of the window",
    "signal_quality",
)
def _dropout_flags(case, severity, rng) -> EvaluationRequest:
    fraction = {"mild": 0.10, "moderate": 0.35, "severe": 0.80}[severity]
    dense = _densest_signal(case)
    obs = []
    for o in case.request.observations:
        if o.signal == dense and rng.random() < fraction:
            obs.append(
                o.model_copy(
                    update={"quality_flags": [*o.quality_flags, QualityFlag.SENSOR_DROPOUT]}
                )
            )
        else:
            obs.append(o)
    return _req(case, observations=obs)


@corruption(
    "implausible_spike",
    "impossible values appear in the required signal, which indicates a sensor or "
    "pipeline fault rather than a physiological event",
    "signal_quality",
)
def _implausible_spike(case, severity, rng) -> EvaluationRequest:
    fraction = {"mild": 0.10, "moderate": 0.30, "severe": 0.60}[severity]
    target = _target_signal(case)
    if target is None:
        return case.request
    high = spec(target).high
    obs = list(case.request.observations)
    existing = [o for o in obs if o.signal == target]
    # Guarantee at least one spike even when the target signal has a single sample.
    n_spikes = max(1, round(len(existing) * fraction / max(1e-9, 1 - fraction)))
    last = existing[-1] if existing else obs[-1]
    for i in range(n_spikes):
        obs.append(
            last.model_copy(
                update={
                    "signal": target,
                    "value": high * 3 + i,
                    "measured_at": last.measured_at - timedelta(seconds=30 * (i + 1)),
                    "window_start": None,
                    "window_end": None,
                }
            )
        )
    return _req(case, observations=obs)


@corruption(
    "conflict_summary_detail",
    "the daily summary and the interval detail disagree, the signature of two different "
    "syncs or two different days being combined",
    "consistency",
)
def _conflict_summary_detail(case, severity, rng) -> EvaluationRequest:
    offset = {"mild": 25.0, "moderate": 45.0, "severe": 80.0}[severity]
    target = _target_signal(case)
    if target is None:
        return case.request
    obs = [
        o.model_copy(update={"value": o.value + offset}) if o.signal == target else o
        for o in case.request.observations
    ]
    return _req(case, observations=obs)


@corruption(
    "truncate_baseline",
    "the personal baseline is too short to say what is normal for this person, the "
    "situation every new user is in",
    "baseline",
)
def _truncate_baseline(case, severity, rng) -> EvaluationRequest:
    keep = {"mild": 6, "moderate": 3, "severe": 1}[severity]
    by_signal: dict[Signal, list[BaselineSample]] = {}
    for sample in case.request.baseline:
        by_signal.setdefault(sample.signal, []).append(sample)
    kept: list[BaselineSample] = []
    for samples in by_signal.values():
        kept.extend(sorted(samples, key=lambda s: s.day)[-keep:])
    return _req(case, baseline=kept, **_ctx(case, baseline_days=keep))


@corruption(
    "destabilize_baseline",
    "the baseline swings so widely that no change can be distinguished from ordinary "
    "day-to-day variation",
    "baseline",
)
def _destabilize_baseline(case, severity, rng) -> EvaluationRequest:
    scale = {"mild": 1.5, "moderate": 5.0, "severe": 12.0}[severity]
    target = _target_signal(case)
    baseline = []
    for s in case.request.baseline:
        if s.signal == target:
            spread = rng.gauss(0, scale * max(1.0, abs(s.value) * 0.02))
            baseline.append(s.model_copy(update={"value": s.value + spread}))
        else:
            baseline.append(s)
    return _req(case, baseline=baseline)


@corruption(
    "shift_baseline",
    "the baseline contains a level shift, so its mean describes neither the earlier nor "
    "the later period",
    "baseline",
)
def _shift_baseline(case, severity, rng) -> EvaluationRequest:
    step = {"mild": 2.0, "moderate": 9.0, "severe": 20.0}[severity]
    target = _target_signal(case)
    same = sorted(
        [s for s in case.request.baseline if s.signal == target], key=lambda s: s.day
    )
    half = len(same) // 2
    shifted_ids = {id(s) for s in same[:half]}
    baseline = [
        s.model_copy(update={"value": s.value - step}) if id(s) in shifted_ids else s
        for s in case.request.baseline
    ]
    return _req(case, baseline=baseline)


@corruption(
    "drop_timezone",
    "the subject's timezone is missing or invalid, so a calendar-day window cannot be "
    "placed; this is the quiet source of off-by-one-day insights",
    "integrity",
)
def _drop_timezone(case, severity, rng) -> EvaluationRequest:
    value = {"mild": None, "moderate": "Mars/Olympus_Mons", "severe": None}[severity]
    return _req(case, **_ctx(case, timezone=value))


@corruption(
    "shift_day_boundary",
    "the window is placed a few hours off, as happens after travel or a DST change, so "
    "the evidence describes a different day than the claim",
    "integrity",
)
def _shift_day_boundary(case, severity, rng) -> EvaluationRequest:
    hours = {"mild": 3.0, "moderate": 8.0, "severe": 14.0}[severity]
    window = case.request.claim.target_window
    shifted = window.model_copy(
        update={
            "start": window.start - timedelta(hours=hours),
            "end": window.end - timedelta(hours=hours),
        }
    )
    claim = case.request.claim.model_copy(update={"target_window": shifted})
    return _req(case, claim=claim)


@corruption(
    "duplicate_samples",
    "samples are duplicated by a retried sync. The engine must deduplicate and still "
    "decide; abstaining here would be over-abstention, so this stays SUPPORTABLE",
    "integrity",
    degrades_evidence=False,
)
def _duplicate_samples(case, severity, rng) -> EvaluationRequest:
    copies = {"mild": 1, "moderate": 2, "severe": 4}[severity]
    obs = list(case.request.observations)
    for _ in range(copies):
        obs.extend(o.model_copy() for o in case.request.observations)
    rng.shuffle(obs)
    return _req(case, observations=obs)


@corruption(
    "shuffle_order",
    "samples arrive out of chronological order. The engine must sort and still decide; "
    "this stays SUPPORTABLE for the same reason as duplication",
    "integrity",
    degrades_evidence=False,
)
def _shuffle_order(case, severity, rng) -> EvaluationRequest:
    obs = list(case.request.observations)
    rng.shuffle(obs)
    return _req(case, observations=obs)
