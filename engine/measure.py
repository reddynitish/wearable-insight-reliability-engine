"""Independent measurement of a request against a claim policy.

This module exists to label synthetic cases, and it is deliberately a **second,
separate implementation** of the arithmetic in engine/features.py. Labels must not be
produced by the code under test: if the same function decided both what the evidence is
and whether the engine was right about it, the evaluation would be tautological.

So this module is plain arithmetic over an EvaluationRequest plus the thresholds written
in a ClaimPolicy. It imports no feature, gate, support, or decision logic. The only
engine constants it borrows are the documented contract values (plausibility ranges, the
window-alignment floor), which are specification, not inference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from engine.normalize import WINDOW_ALIGNMENT_MIN
from engine.policies.base import ClaimPolicy
from engine.schemas import EvaluationRequest, QualityFlag
from engine.signals import Signal, is_plausible

ARTIFACT = {QualityFlag.MOTION_ARTIFACT, QualityFlag.LOW_PERFUSION,
            QualityFlag.POOR_SKIN_CONTACT}
DROPOUT = {QualityFlag.SENSOR_DROPOUT, QualityFlag.DEVICE_REMOVED}


@dataclass
class Measurement:
    """What the evidence objectively contains, and which contract clauses it breaks."""

    wear_ratio: float | None = None
    max_gap_minutes: float | None = None
    sync_covers_window: bool | None = None
    measurement_age_hours: float | None = None
    timezone_present: bool = False
    baseline_valid_days: int = 0
    baseline_sd: float | None = None
    baseline_half_shift: float | None = None
    artifact_fraction: float = 0.0
    dropout_fraction: float = 0.0
    implausible_fraction: float = 0.0
    longest_flat_run: int = 0
    flat_fraction: float = 0.0
    target_samples_in_window: int = 0
    inputs_inadequate: int = 0
    inputs_checked: int = 0
    summary_detail_gap: float | None = None
    sync_age_hours: float | None = None
    duplicates_removed: int = 0
    worst_alignment: float | None = None
    z_directional: float | None = None
    absolute_delta: float | None = None
    breaches: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {k: v for k, v in self.__dict__.items() if k != "breaches"}
        out = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}
        out["breaches"] = list(self.breaches)
        return out


def _target_signal(request: EvaluationRequest, policy: ClaimPolicy) -> Signal | None:
    if policy.mode == "anomaly":
        return request.claim.signal
    return policy.target_signal


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _sd(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def measure(request: EvaluationRequest, policy: ClaimPolicy, now: datetime | None = None) -> Measurement:
    m = Measurement()
    window = request.claim.target_window
    evaluated_at = request.evaluated_at or now
    target = _target_signal(request, policy)
    window_seconds = (window.end - window.start).total_seconds()

    # --- deduplicate exactly as the contract says to: a retried sync must not be read
    # as repeated physiology. Without this, duplicated samples would be measured as a
    # flatline and as a doubled sum, and the labels would be wrong. ---
    seen: set = set()
    submitted = []
    for o in sorted(request.observations, key=lambda o: (o.signal.value, o.measured_at, o.value)):
        key = (o.signal, o.measured_at, o.value)
        if key in seen:
            m.duplicates_removed += 1
            continue
        seen.add(key)
        submitted.append(o)

    # --- which observations describe this window ---
    in_window: dict[Signal, list] = {}
    for o in submitted:
        if not is_plausible(o.signal, o.value):
            continue
        if o.window_start is not None and o.window_end is not None:
            overlap = (min(o.window_end, window.end) - max(o.window_start, window.start)).total_seconds()
            if overlap <= 0:
                continue
            own = max(1.0, (o.window_end - o.window_start).total_seconds())
            alignment = overlap / own
            if m.worst_alignment is None or alignment < m.worst_alignment:
                m.worst_alignment = alignment
            if alignment < WINDOW_ALIGNMENT_MIN:
                # Only a misaligned aggregate of a signal the claim needs is a breach;
                # see the matching rule in engine/gates.py.
                material = o.signal in set(policy.required_signals) | (
                    {target} if target else set()
                )
                if material and "window_misaligned" not in m.breaches:
                    m.breaches.append("window_misaligned")
                continue
        else:
            if not (window.start <= o.measured_at < window.end):
                lag = (o.measured_at - window.end).total_seconds() / 3600.0
                if not 0 <= lag <= min(12.0, 0.5 * window_seconds / 3600.0):
                    continue
        in_window.setdefault(o.signal, []).append(o)

    # --- coverage ---
    expected = request.context.expected_worn_minutes or window_seconds / 60.0
    worn = request.context.device_worn_minutes
    if worn is None:
        wear_obs = in_window.get(Signal.WEAR_MINUTES, [])
        worn = sum(o.value for o in wear_obs) if wear_obs else None
    if worn is not None and expected > 0:
        m.wear_ratio = worn / expected
        if m.wear_ratio < policy.min_wear_coverage:
            m.breaches.append("min_wear_coverage")
        if worn < min(policy.not_worn_minutes, 0.5 * expected):
            m.breaches.append("not_worn")
    gaps = list(request.context.reported_gaps_minutes)
    if gaps:
        m.max_gap_minutes = max(gaps)
        if m.max_gap_minutes > policy.max_gap_minutes:
            m.breaches.append("max_gap_minutes")

    # --- freshness ---
    if request.context.sync_completed_at is not None:
        m.sync_covers_window = request.context.sync_completed_at >= window.end
        if policy.requires_sync_after_window_end and not m.sync_covers_window:
            m.breaches.append("requires_sync_after_window_end")
        if evaluated_at is not None:
            m.sync_age_hours = (
                evaluated_at - request.context.sync_completed_at
            ).total_seconds() / 3600.0
            if m.sync_age_hours > policy.max_sync_age_hours:
                m.breaches.append("max_sync_age_hours")
    if evaluated_at is not None:
        relevant = [o for s in set(policy.required_signals) | ({target} if target else set())
                    for o in request.observations if o.signal == s]
        if relevant:
            newest = max(o.measured_at for o in relevant)
            m.measurement_age_hours = (evaluated_at - newest).total_seconds() / 3600.0
            if m.measurement_age_hours > policy.max_measurement_age_hours:
                m.breaches.append("max_measurement_age_hours")
        if policy.requires_closed_window and evaluated_at < window.end:
            m.breaches.append("requires_closed_window")
    # A timezone that is not a real IANA zone is no timezone at all.
    m.timezone_present = False
    if request.context.timezone:
        try:
            ZoneInfo(request.context.timezone)
            m.timezone_present = True
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            m.timezone_present = False
    if policy.requires_timezone and not m.timezone_present:
        m.breaches.append("requires_timezone")

    # --- signal quality ---
    considered = [o for s in set(policy.required_signals) | set(policy.supporting_signals)
                  | ({target} if target else set())
                  for o in in_window.get(s, [])]
    if considered:
        m.artifact_fraction = sum(
            1 for o in considered if set(o.quality_flags) & ARTIFACT
        ) / len(considered)
        m.dropout_fraction = sum(
            1 for o in considered if set(o.quality_flags) & DROPOUT
        ) / len(considered)
    if m.artifact_fraction > policy.flagged_wait_fraction:
        m.breaches.append("flagged_wait_fraction")
    if m.dropout_fraction > 0.20:
        m.breaches.append("dropout_fraction")

    judged = list(policy.required_signals) or ([target] if target else [])
    worst = 0.0
    for s in judged:
        if s is None:
            continue
        submitted = [o for o in request.observations if o.signal == s]
        if not submitted:
            continue
        bad = sum(1 for o in submitted if not is_plausible(s, o.value))
        worst = max(worst, bad / len(submitted))
    m.implausible_fraction = worst
    if m.implausible_fraction > policy.implausible_reject_fraction:
        m.breaches.append("implausible_reject_fraction")

    if target:
        values = [o.value for o in sorted(in_window.get(target, []), key=lambda o: o.measured_at)]
        m.target_samples_in_window = len(values)
        if not values:
            if policy.required_signals and target in policy.required_signals:
                m.breaches.append("missing_required_signal")
            else:
                m.breaches.append("no_target_samples")
        run = best = 1 if values else 0
        for a, b in zip(values, values[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        m.longest_flat_run = best
        m.flat_fraction = best / len(values) if values else 0.0
        if best >= 5 and m.flat_fraction >= 0.30:
            m.breaches.append("flatline")
    for s in policy.required_signals:
        if not in_window.get(s) and "missing_required_signal" not in m.breaches:
            m.breaches.append("missing_required_signal")

    # --- baseline ---
    if target:
        samples = [b for b in request.baseline if b.signal == target and b.valid
                   and is_plausible(target, b.value)]
        m.baseline_valid_days = len(samples)
        if not samples and request.context.baseline_days is not None:
            m.baseline_valid_days = max(0, int(request.context.baseline_days))
        if m.baseline_valid_days < policy.warn_baseline_days:
            m.breaches.append("warn_baseline_days")
        values = [b.value for b in sorted(samples, key=lambda b: b.day)]
        if len(values) >= 2:
            m.baseline_sd = _sd(values)
            if policy.max_baseline_sd is not None and m.baseline_sd > policy.max_baseline_sd:
                m.breaches.append("max_baseline_sd")
        if len(values) >= 6:
            half = len(values) // 2
            m.baseline_half_shift = abs(_mean(values[half:]) - _mean(values[:half]))
            if (policy.max_baseline_shift is not None
                    and m.baseline_half_shift > policy.max_baseline_shift):
                m.breaches.append("max_baseline_shift")
        if not samples:
            m.breaches.append("baseline_statistics_unavailable")

    # --- achieved effect ---
    if target and policy.mode != "insufficiency":
        values = [o.value for o in in_window.get(target, [])]
        samples = [b.value for b in request.baseline if b.signal == target and b.valid
                   and is_plausible(target, b.value)]
        if values and len(samples) >= 2:
            agg = {
                "sum": sum, "max": max, "min": min,
                "last": lambda v: v[-1], "mean": _mean,
            }[policy.target_aggregation](values)
            base_mean = _mean(samples)
            from engine.signals import spec as _spec

            sd = max(_sd(samples), _spec(target).noise_sd)
            m.absolute_delta = abs(agg - base_mean)
            z = (agg - base_mean) / sd
            m.z_directional = abs(z) if policy.mode == "anomaly" else policy.direction * z
    # --- summary vs detail, for the one pair the policies define arithmetically ---
    rhr = [o.value for o in in_window.get(Signal.RESTING_HEART_RATE, [])]
    hr = sorted(o.value for o in in_window.get(Signal.HEART_RATE, []))
    if rhr and len(hr) >= 10:
        summary = _mean(rhr)
        pos = (len(hr) - 1) * 0.10
        low, high = int(pos), min(int(pos) + 1, len(hr) - 1)
        p10 = hr[low] + (hr[high] - hr[low]) * (pos - low)
        m.summary_detail_gap = summary - p10
        if summary > p10 + 20 or summary < hr[0] - 10:
            m.breaches.append("summary_detail_mismatch")

    # --- the inverted claim: adequacy of the recovery inputs is the measured quantity ---
    if policy.mode == "insufficiency":
        m.inputs_checked = len(policy.insufficiency_inputs)
        # Per the claim contract, an input is inadequate when it is missing, stale, below
        # coverage, or below baseline maturity. Coverage and quality are properties of the
        # whole bundle, so a breach there makes every input inadequate.
        bundle_degraded = bool(
            set(m.breaches)
            & {
                "min_wear_coverage", "not_worn", "max_gap_minutes",
                "flagged_wait_fraction", "dropout_fraction",
                "implausible_reject_fraction", "flatline",
            }
        )
        for s_in in policy.insufficiency_inputs:
            obs = in_window.get(s_in, [])
            present = bool(obs)
            fresh = False
            if obs and evaluated_at is not None:
                age = (evaluated_at - max(o.measured_at for o in obs)).total_seconds() / 3600.0
                fresh = age <= policy.max_measurement_age_hours
            days = len([b for b in request.baseline if b.signal == s_in and b.valid])
            if days == 0 and request.context.baseline_days is not None:
                days = max(0, int(request.context.baseline_days))
            if bundle_degraded or not (present and fresh and days >= policy.min_baseline_days):
                m.inputs_inadequate += 1
        if m.inputs_inadequate:
            m.breaches.append("recovery_inputs_inadequate")

    if request.context.device_changed_recently:
        m.breaches.append("device_changed_recently")
    return m
