"""Canonicalisation of a submitted evidence bundle.

Three principles:

1. Nothing is silently discarded. Duplicates, out-of-order samples, out-of-window
   samples and implausible values are all removed from feature computation *and*
   counted, so the counts reach the decision trace.
2. No imputation. Missing evidence stays missing; the engine's job is to notice.
3. Deterministic ordering, so two identical bundles produce byte-identical traces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from engine.schemas import BaselineSample, EvaluationRequest, Observation, TimeWindow
from engine.signals import Signal, is_plausible


@dataclass
class NormalizedEvidence:
    """Cleaned evidence plus an audit of what cleaning did."""

    evaluated_at: datetime
    target_window: TimeWindow
    # In-window, plausible, deduplicated, sorted by (signal, measured_at).
    in_window: dict[Signal, list[Observation]] = field(default_factory=dict)
    # Everything plausible and deduplicated, regardless of window -- needed for
    # freshness (the newest measurement may post-date the window) and for confounders.
    all_plausible: dict[Signal, list[Observation]] = field(default_factory=dict)
    baseline: dict[Signal, list[BaselineSample]] = field(default_factory=dict)
    duplicates_removed: int = 0
    out_of_order_detected: bool = False
    outside_window: int = 0
    implausible_dropped: dict[Signal, int] = field(default_factory=dict)
    implausible_fraction: dict[Signal, float] = field(default_factory=dict)
    submitted_counts: dict[Signal, int] = field(default_factory=dict)
    attributed_after_window: int = 0
    misaligned_aggregates: int = 0
    worst_alignment: float | None = None
    subject_tz: ZoneInfo | None = None
    timezone_name: str | None = None
    timezone_invalid: bool = False
    notes: list[str] = field(default_factory=list)

    def window_values(self, signal: Signal) -> list[float]:
        return [o.value for o in self.in_window.get(signal, [])]

    def newest(self, signal: Signal) -> Observation | None:
        obs = self.all_plausible.get(signal)
        return obs[-1] if obs else None

    def baseline_values(self, signal: Signal, valid_only: bool = True) -> list[float]:
        samples = self.baseline.get(signal, [])
        return [s.value for s in samples if s.valid or not valid_only]


# A daily or nightly aggregate is normally computed and timestamped when the device
# syncs, which is after the day it describes has ended. Rejecting those outright would
# make every daily summary look like a missing signal. So an aggregate stamped up to
# this long after the window end is attributed to the window -- and the attribution is
# counted and reported, never silent, because the same leniency is how genuine
# day-boundary and timezone bugs hide.
REPORTING_ALLOWANCE_HOURS = 12.0

# An interval aggregate must describe substantially the same period as the claim window.
# Mere overlap is not enough: a full-day summary overlaps a window shifted three hours by
# 87.5%, and accepting that is how a claim about Tuesday gets answered with Monday's
# numbers after a timezone or DST error.
WINDOW_ALIGNMENT_MIN = 0.90


def normalize(request: EvaluationRequest, now: datetime | None = None) -> NormalizedEvidence:
    evaluated_at = request.evaluated_at or now or datetime.now(timezone.utc)
    window = request.claim.target_window

    tz: ZoneInfo | None = None
    tz_invalid = False
    if request.context.timezone:
        try:
            tz = ZoneInfo(request.context.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            tz_invalid = True

    norm = NormalizedEvidence(
        evaluated_at=evaluated_at,
        target_window=window,
        subject_tz=tz,
        timezone_name=request.context.timezone,
        timezone_invalid=tz_invalid,
    )
    if tz_invalid:
        norm.notes.append(
            f"context.timezone {request.context.timezone!r} is not a recognised IANA "
            "zone and was ignored"
        )

    # --- observations: count, dedupe, order, range-check ---
    submitted: dict[Signal, list[Observation]] = {}
    for obs in request.observations:
        submitted.setdefault(obs.signal, []).append(obs)
    norm.submitted_counts = {s: len(v) for s, v in submitted.items()}

    for signal, obs_list in submitted.items():
        # Out-of-order is judged on the order as submitted, before sorting.
        if any(
            b.measured_at < a.measured_at for a, b in zip(obs_list, obs_list[1:])
        ):
            norm.out_of_order_detected = True

        seen: set[tuple[datetime, float]] = set()
        deduped: list[Observation] = []
        for obs in sorted(obs_list, key=lambda o: (o.measured_at, o.value)):
            key = (obs.measured_at, obs.value)
            if key in seen:
                norm.duplicates_removed += 1
                continue
            seen.add(key)
            deduped.append(obs)

        plausible = [o for o in deduped if is_plausible(signal, o.value)]
        dropped = len(deduped) - len(plausible)
        if dropped:
            norm.implausible_dropped[signal] = dropped
        norm.implausible_fraction[signal] = (
            dropped / len(deduped) if deduped else 0.0
        )

        norm.all_plausible[signal] = plausible
        inside = []
        for obs in plausible:
            placement, alignment = _placement(obs, window)
            if alignment is not None:
                if norm.worst_alignment is None or alignment < norm.worst_alignment:
                    norm.worst_alignment = alignment
            if placement == "misaligned":
                norm.misaligned_aggregates += 1
                continue
            if placement == "outside":
                continue
            if placement == "attributed":
                norm.attributed_after_window += 1
            inside.append(obs)
        norm.outside_window += len(plausible) - len(inside)
        if inside:
            norm.in_window[signal] = inside

    if norm.duplicates_removed:
        norm.notes.append(
            f"{norm.duplicates_removed} duplicate observation(s) removed; the first "
            "occurrence of each (signal, timestamp, value) was kept"
        )
    if norm.out_of_order_detected:
        norm.notes.append("observations were not in chronological order and were sorted")
    if norm.misaligned_aggregates:
        norm.notes.append(
            f"{norm.misaligned_aggregates} interval aggregate(s) describe a period that "
            "does not line up with the target window and were excluded"
        )
    if norm.attributed_after_window:
        norm.notes.append(
            f"{norm.attributed_after_window} aggregate observation(s) timestamped after "
            "the target window were attributed to it under the "
            f"{REPORTING_ALLOWANCE_HOURS:.0f}h sync-reporting allowance"
        )
    if norm.outside_window:
        norm.notes.append(
            f"{norm.outside_window} observation(s) fell outside the target window and "
            "were excluded from window features"
        )
    for signal, count in sorted(norm.implausible_dropped.items(), key=lambda kv: kv[0].value):
        norm.notes.append(
            f"{count} {signal.value} value(s) outside the physiological range were "
            "dropped from feature computation"
        )

    # --- baseline: dedupe by day, keep the last submitted value for a repeated day ---
    per_signal_days: dict[Signal, dict[datetime, BaselineSample]] = {}
    for sample in request.baseline:
        day_key = _day_key(sample.day, tz)
        bucket = per_signal_days.setdefault(sample.signal, {})
        if day_key in bucket:
            norm.duplicates_removed += 1
        bucket[day_key] = sample
    for signal, bucket in per_signal_days.items():
        kept = [bucket[k] for k in sorted(bucket)]
        plausible = [s for s in kept if is_plausible(signal, s.value)]
        if len(plausible) != len(kept):
            norm.notes.append(
                f"{len(kept) - len(plausible)} baseline {signal.value} day(s) outside "
                "the physiological range were excluded from baseline statistics"
            )
        norm.baseline[signal] = plausible

    return norm


def _placement(obs: Observation, window: TimeWindow) -> tuple[str, float | None]:
    """Where an observation sits relative to the target window, and how well it aligns.

    Returns one of "inside", "attributed" (stamped after the window but attributed to it
    under the sync-reporting allowance), "misaligned" (an interval aggregate describing a
    materially different period), or "outside", plus the alignment ratio for aggregates.
    """
    if obs.window_start is not None and obs.window_end is not None:
        overlap = (
            min(obs.window_end, window.end) - max(obs.window_start, window.start)
        ).total_seconds()
        if overlap <= 0:
            return "outside", 0.0
        own = max(1.0, (obs.window_end - obs.window_start).total_seconds())
        target = max(1.0, (window.end - window.start).total_seconds())
        alignment = min(overlap / own, overlap / target)
        if alignment < WINDOW_ALIGNMENT_MIN:
            return "misaligned", alignment
        return "inside", alignment
    if window.contains(obs.measured_at):
        return "inside", None
    lag_hours = (obs.measured_at - window.end).total_seconds() / 3600.0
    # The allowance is also capped at half the window: a value stamped more than half a
    # window past the end is at least as likely to describe the *next* window. Without
    # this cap a flat 12-hour allowance would pull samples from two hours after a
    # two-hour interval into it.
    allowance = min(REPORTING_ALLOWANCE_HOURS, 0.5 * window.duration_minutes / 60.0)
    if 0 <= lag_hours <= allowance:
        return ("inside" if lag_hours == 0 else "attributed"), None
    return "outside", None


def _day_key(moment: datetime, tz: ZoneInfo | None) -> datetime:
    local = moment.astimezone(tz) if tz else moment
    return local.replace(hour=0, minute=0, second=0, microsecond=0)
