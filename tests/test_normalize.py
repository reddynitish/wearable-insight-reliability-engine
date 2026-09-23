"""Canonicalisation: nothing silently discarded, nothing silently invented."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import WINDOW, rhr_request

from engine.normalize import WINDOW_ALIGNMENT_MIN, normalize
from engine.schemas import Claim, EvaluationRequest, Observation, TimeWindow
from engine.signals import Signal

U = timezone.utc


def _request(observations, window=WINDOW, **ctx):
    return EvaluationRequest(
        subject_id="norm-001",
        claim=Claim(type="RESTING_HEART_RATE_ELEVATED", target_window=window),
        observations=observations,
        evaluated_at=window.end + timedelta(hours=1),
    )


def test_duplicates_are_removed_and_counted():
    obs = Observation(
        signal=Signal.HEART_RATE, value=60, measured_at=WINDOW.start + timedelta(hours=1)
    )
    norm = normalize(_request([obs, obs.model_copy(), obs.model_copy()]))
    assert norm.duplicates_removed == 2
    assert len(norm.in_window[Signal.HEART_RATE]) == 1
    assert any("duplicate" in n for n in norm.notes)


def test_out_of_order_is_sorted_and_reported():
    late = Observation(
        signal=Signal.HEART_RATE, value=61, measured_at=WINDOW.start + timedelta(hours=5)
    )
    early = Observation(
        signal=Signal.HEART_RATE, value=59, measured_at=WINDOW.start + timedelta(hours=1)
    )
    norm = normalize(_request([late, early]))
    assert norm.out_of_order_detected
    values = [o.measured_at for o in norm.in_window[Signal.HEART_RATE]]
    assert values == sorted(values)


def test_implausible_values_are_dropped_and_counted():
    good = Observation(
        signal=Signal.HEART_RATE, value=60, measured_at=WINDOW.start + timedelta(hours=1)
    )
    bad = Observation(
        signal=Signal.HEART_RATE, value=900, measured_at=WINDOW.start + timedelta(hours=2)
    )
    norm = normalize(_request([good, bad]))
    assert norm.implausible_dropped[Signal.HEART_RATE] == 1
    assert norm.implausible_fraction[Signal.HEART_RATE] == 0.5
    assert [o.value for o in norm.in_window[Signal.HEART_RATE]] == [60.0]


def test_range_boundaries_are_inclusive():
    low = Observation(signal=Signal.RESTING_HEART_RATE, value=30, measured_at=WINDOW.start)
    high = Observation(
        signal=Signal.RESTING_HEART_RATE, value=120, measured_at=WINDOW.start + timedelta(hours=1)
    )
    norm = normalize(_request([low, high]))
    assert norm.implausible_dropped == {}


def test_daily_aggregate_stamped_after_the_window_is_attributed_to_it():
    """The common reporting convention: the summary is computed at sync time."""
    obs = Observation(
        signal=Signal.RESTING_HEART_RATE, value=60, measured_at=WINDOW.end + timedelta(hours=11)
    )
    norm = normalize(_request([obs]))
    assert norm.attributed_after_window == 1
    assert norm.in_window[Signal.RESTING_HEART_RATE]
    assert any("allowance" in n for n in norm.notes)


def test_the_reporting_allowance_is_capped_at_half_the_window():
    """A sample two hours after a two-hour interval belongs to the next one, not this one."""
    short = TimeWindow(start=datetime(2026, 9, 23, 4, tzinfo=U), end=datetime(2026, 9, 23, 6, tzinfo=U))
    obs = Observation(
        signal=Signal.RESTING_HEART_RATE, value=60, measured_at=short.end + timedelta(hours=1, minutes=30)
    )
    norm = normalize(_request([obs], window=short))
    assert Signal.RESTING_HEART_RATE not in norm.in_window
    assert norm.outside_window == 1


def test_misaligned_aggregate_is_excluded_not_accepted():
    """A full-day summary shifted three hours overlaps 87.5% -- not enough to stand in."""
    shifted = TimeWindow(start=WINDOW.start - timedelta(hours=3), end=WINDOW.end - timedelta(hours=3))
    obs = Observation(
        signal=Signal.RESTING_HEART_RATE,
        value=60,
        measured_at=WINDOW.end,
        window_start=WINDOW.start,
        window_end=WINDOW.end,
    )
    norm = normalize(_request([obs], window=shifted))
    assert norm.misaligned_aggregates == 1
    assert Signal.RESTING_HEART_RATE not in norm.in_window
    assert norm.worst_alignment is not None and norm.worst_alignment < WINDOW_ALIGNMENT_MIN


def test_aligned_aggregate_is_accepted():
    obs = Observation(
        signal=Signal.RESTING_HEART_RATE,
        value=60,
        measured_at=WINDOW.end,
        window_start=WINDOW.start,
        window_end=WINDOW.end,
    )
    norm = normalize(_request([obs]))
    assert norm.misaligned_aggregates == 0
    assert norm.in_window[Signal.RESTING_HEART_RATE]


def test_invalid_timezone_is_ignored_and_flagged():
    request = rhr_request(tz="Mars/Olympus_Mons")
    norm = normalize(request)
    assert norm.timezone_invalid
    assert norm.subject_tz is None
    assert any("IANA" in n for n in norm.notes)


def test_valid_timezone_is_resolved():
    norm = normalize(rhr_request(tz="Australia/Sydney"))
    assert not norm.timezone_invalid
    assert norm.subject_tz is not None


def test_invalid_baseline_days_are_excluded_from_statistics():
    request = rhr_request(n_baseline=20, n_valid=5)
    norm = normalize(request)
    assert len(norm.baseline_values(Signal.RESTING_HEART_RATE)) == 5
    assert len(norm.baseline_values(Signal.RESTING_HEART_RATE, valid_only=False)) == 20


def test_normalisation_never_invents_observations():
    request = rhr_request(hr_samples=0)
    norm = normalize(request)
    assert Signal.HEART_RATE not in norm.in_window
    assert Signal.STEPS not in norm.in_window
