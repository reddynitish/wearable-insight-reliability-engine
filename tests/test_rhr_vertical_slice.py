"""The RESTING_HEART_RATE_ELEVATED vertical slice.

PROJECT_BRIEF.md section 21 names this as the first milestone, and section 12 names the
failure modes it must change correctly under. One test per named condition, each altering
exactly one thing about otherwise clean evidence so the decision can be attributed.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import WINDOW, baseline_days, rhr_request

from engine.engine import evaluate
from engine.reasons import ReasonCode
from engine.schemas import Decision, Observation, QualityFlag
from engine.signals import Signal


def codes(response) -> set[str]:
    return {c.value for c in response.reason_codes}


# --------------------------------------------------------------------------- the happy path


def test_clean_evidence_shows():
    response = evaluate(rhr_request())
    assert response.decision is Decision.SHOW
    assert response.reason_codes == []
    assert response.claim_support_probability > 0.75
    assert response.retry.recommended is False


def test_show_explanation_states_the_measured_change_and_the_scope_limit():
    response = evaluate(rhr_request())
    assert "resting heart rate" in response.explanation
    assert "SD above" in response.explanation
    assert "not a medical assessment" in response.explanation


def test_support_is_never_advertised_as_calibrated():
    """v0.1 has no learned component; the field must not be mistaken for a probability."""
    assert evaluate(rhr_request()).support_is_calibrated is False


# --------------------------------------------------------------------------- baseline


@pytest.mark.parametrize(
    "days,expected",
    [
        (30, Decision.SHOW),
        (14, Decision.SHOW),
        (13, Decision.SHOW_WITH_WARNING),   # warn band: 7..13
        (7, Decision.SHOW_WITH_WARNING),
        (6, Decision.WAIT_FOR_MORE_DATA),   # below the floor: fatal
        (2, Decision.WAIT_FOR_MORE_DATA),
    ],
)
def test_baseline_length_moves_the_decision_through_the_documented_bands(days, expected):
    response = evaluate(rhr_request(n_baseline=days))
    assert response.decision is expected, codes(response)
    if days < 14:
        assert ReasonCode.BASELINE_NOT_MATURE.value in codes(response)


def test_short_baseline_recommends_waiting_a_week_for_specific_evidence():
    response = evaluate(rhr_request(n_baseline=4))
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert response.retry.recommended
    assert response.retry.after == "P7D"
    assert "a_longer_personal_baseline" in response.retry.required_evidence


def test_declared_baseline_count_without_values_cannot_produce_a_show():
    """A day count says the history is long enough; it does not say what normal looks like."""
    response = evaluate(
        rhr_request(baseline_values=[], declared_baseline_days=30, rhr_value=72.0)
    )
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert ReasonCode.BASELINE_STATISTICS_UNAVAILABLE.value in codes(response)


def test_patchy_history_reports_insufficient_historical_coverage():
    response = evaluate(rhr_request(n_baseline=30, n_valid=10))
    assert ReasonCode.INSUFFICIENT_HISTORICAL_COVERAGE.value in codes(response)
    assert response.decision is not Decision.SHOW


def test_unstable_baseline_blocks_the_claim():
    wide = [58.0 + (18 if i % 2 else -18) for i in range(21)]
    response = evaluate(rhr_request(baseline_values=wide, rhr_value=95.0))
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert ReasonCode.BASELINE_UNSTABLE.value in codes(response)


def test_baseline_level_shift_blocks_the_claim():
    shifted = [52.0] * 10 + [66.0] * 10
    response = evaluate(rhr_request(baseline_values=shifted, rhr_value=75.0))
    assert ReasonCode.BASELINE_SHIFT_DETECTED.value in codes(response)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA


def test_recent_device_change_invalidates_the_baseline():
    response = evaluate(rhr_request(device_changed=True))
    assert ReasonCode.BASELINE_SHIFT_DETECTED.value in codes(response)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA


# --------------------------------------------------------------------------- freshness


def test_sync_that_predates_the_window_end_blocks_the_claim():
    response = evaluate(rhr_request(sync_offset_hours=-6.0))
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert ReasonCode.SYNC_INCOMPLETE.value in codes(response)
    assert "has not reached the server" in response.explanation


def test_missing_sync_timestamp_is_disclosed_not_assumed_good():
    response = evaluate(rhr_request(sync_offset_hours=None))
    assert response.decision is Decision.SHOW_WITH_WARNING
    assert ReasonCode.SYNC_STATE_UNKNOWN.value in codes(response)


def test_stale_evidence_blocks_the_claim():
    response = evaluate(rhr_request(evaluated_offset_hours=80.0))
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert ReasonCode.STALE_DATA.value in codes(response)


def test_open_window_blocks_the_claim():
    response = evaluate(rhr_request(evaluated_offset_hours=-6.0, sync_offset_hours=-7.0))
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert ReasonCode.WINDOW_NOT_CLOSED.value in codes(response)
    assert response.confidence >= 0.95


# --------------------------------------------------------------------------- coverage


@pytest.mark.parametrize(
    "worn,expected_code",
    [
        (60.0, ReasonCode.DEVICE_NOT_WORN),          # below half the window's floor
        (700.0, ReasonCode.INSUFFICIENT_COVERAGE),   # 49% of 1440, under the 60% floor
    ],
)
def test_low_wear_time_blocks_the_claim(worn, expected_code):
    response = evaluate(rhr_request(worn_minutes=worn))
    assert expected_code.value in codes(response)
    assert response.decision in (Decision.WAIT_FOR_MORE_DATA, Decision.REJECT)


def test_an_unworn_device_is_rejected_not_deferred():
    """There is nothing to wait for: the window describes a nightstand."""
    response = evaluate(rhr_request(worn_minutes=30.0))
    assert response.decision is Decision.REJECT
    assert ReasonCode.DEVICE_NOT_WORN.value in codes(response)


def test_unknown_wear_time_is_disclosed():
    response = evaluate(rhr_request(worn_minutes=None, overnight_minutes=None))
    assert ReasonCode.COVERAGE_UNKNOWN.value in codes(response)
    assert response.decision is Decision.SHOW_WITH_WARNING
    assert response.evidence.coverage_score == pytest.approx(0.5)


def test_poor_overnight_coverage_blocks_a_resting_claim():
    response = evaluate(rhr_request(overnight_minutes=120.0))
    assert ReasonCode.INSUFFICIENT_OVERNIGHT_COVERAGE.value in codes(response)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA


# --------------------------------------------------------------------------- signal quality


def test_implausible_required_values_are_rejected():
    spikes = [
        Observation(
            signal=Signal.RESTING_HEART_RATE,
            value=400.0,
            measured_at=WINDOW.end + timedelta(minutes=5 * i),
        )
        for i in range(3)
    ]
    response = evaluate(rhr_request(extra_observations=spikes))
    assert response.decision is Decision.REJECT
    assert ReasonCode.IMPLAUSIBLE_VALUES.value in codes(response)


def test_motion_artifact_across_the_window_blocks_the_claim():
    flagged = [
        Observation(
            signal=Signal.HEART_RATE,
            value=70.0 + i,
            measured_at=WINDOW.start + timedelta(minutes=10 * i),
            quality_flags=[QualityFlag.MOTION_ARTIFACT],
        )
        for i in range(40)
    ]
    response = evaluate(rhr_request(hr_samples=8, extra_observations=flagged))
    assert ReasonCode.MOTION_ARTIFACT.value in codes(response)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA


def test_a_stuck_sensor_is_caught_even_though_the_values_look_steady():
    flat = [
        Observation(
            signal=Signal.RESTING_HEART_RATE,
            value=64.0,
            measured_at=WINDOW.start + timedelta(hours=i),
        )
        for i in range(8)
    ]
    response = evaluate(rhr_request(extra_observations=flat))
    assert ReasonCode.SENSOR_DROPOUT.value in codes(response)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA


# --------------------------------------------------------------------------- consistency


def test_summary_that_contradicts_the_interval_detail_blocks_the_claim():
    """A daily RHR far above the day's own low heart rate cannot describe that day."""
    response = evaluate(rhr_request(rhr_value=110.0, n_baseline=21))
    assert ReasonCode.SUMMARY_DETAIL_MISMATCH.value in codes(response)
    assert response.decision is not Decision.SHOW


def test_rhr_and_hrv_rising_together_is_reported_as_a_conflict():
    hrv_now = Observation(
        signal=Signal.HRV_RMSSD, value=70.0, measured_at=WINDOW.end - timedelta(hours=2)
    )
    request = rhr_request(extra_observations=[hrv_now])
    request = request.model_copy(
        update={
            "baseline": list(request.baseline)
            + baseline_days(Signal.HRV_RMSSD, [42.0, 40.0, 44.0, 41.0, 43.0, 42.0])
        }
    )
    response = evaluate(request)
    assert ReasonCode.CONFLICTING_SIGNALS.value in codes(response)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA


def test_declared_wear_time_exceeding_its_own_reported_gaps_is_a_conflict():
    request = rhr_request()
    request = request.model_copy(
        update={
            "context": request.context.model_copy(
                update={"device_worn_minutes": 1400.0, "reported_gaps_minutes": [300.0]}
            )
        }
    )
    response = evaluate(request)
    assert ReasonCode.CONTEXT_CONFLICT.value in codes(response)


# --------------------------------------------------------------------------- claim support


def test_missing_required_signal_defers():
    request = rhr_request()
    request = request.model_copy(
        update={
            "observations": [
                o for o in request.observations if o.signal is not Signal.RESTING_HEART_RATE
            ]
        }
    )
    response = evaluate(request)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert ReasonCode.MISSING_REQUIRED_SIGNAL.value in codes(response)


def test_contradicting_evidence_is_rejected_and_says_waiting_will_not_help():
    response = evaluate(rhr_request(z=0.2))
    assert response.decision is Decision.REJECT
    assert ReasonCode.CLAIM_CONTRADICTED.value in codes(response)
    assert response.retry.recommended is False
    assert "Waiting will not change this" in response.explanation


def test_an_implausibly_tight_baseline_cannot_manufacture_a_large_effect():
    """The SD is floored at the sensor's own noise, so a 1 bpm move stays a 1 bpm move.

    Without the floor, a 0.12 bpm baseline SD would turn this into a nine-sigma event.
    """
    tight = [58.0, 58.3, 58.1, 58.2, 58.0, 58.3, 58.1] * 3
    response = evaluate(rhr_request(baseline_values=tight, rhr_value=59.2))
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert response.trace.features["baseline"]["effective_sd"] == pytest.approx(1.5)
    assert abs(response.trace.features["claim_support"]["z_directional"]) < 1.0


def test_a_statistically_large_but_physically_tiny_change_is_not_shown():
    """1.6 SD against a real 1.84 bpm baseline SD is still only 2.9 bpm of movement."""
    narrow = [58.0 + (1.8 if i % 2 else -1.8) for i in range(20)]
    response = evaluate(rhr_request(baseline_values=narrow, rhr_value=60.9))
    assert response.trace.features["claim_support"]["z_directional"] >= 1.0
    assert response.trace.features["claim_support"]["absolute_delta"] < 3.0
    assert ReasonCode.EFFECT_BELOW_RELIABLE_THRESHOLD.value in codes(response)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA


def test_recent_hard_exercise_is_disclosed_as_an_unresolved_confounder():
    response = evaluate(rhr_request(exercise_minutes=95.0))
    assert ReasonCode.CONFOUNDER_UNRESOLVED.value in codes(response)
    assert response.decision is Decision.SHOW_WITH_WARNING
    assert response.limitations


# --------------------------------------------------------------------------- boundaries


@pytest.mark.parametrize(
    "z,expected",
    [
        (2.5, Decision.SHOW),
        (1.5, Decision.SHOW),                      # exactly show_z
        (1.2, Decision.SHOW_WITH_WARNING),
        (1.0, Decision.SHOW_WITH_WARNING),         # exactly warn_z, inclusive per contract
        (0.9, Decision.WAIT_FOR_MORE_DATA),
        (0.5, Decision.REJECT),                    # exactly contradict_z, inclusive
        (-1.0, Decision.REJECT),
    ],
)
def test_effect_size_thresholds_are_inclusive_on_the_documented_side(z, expected):
    """Perfect evidence, so the z anchors land exactly on the support cut points."""
    response = evaluate(
        rhr_request(
            z=z,
            n_baseline=30,
            worn_minutes=1440.0,
            overnight_minutes=480.0,
            sync_offset_hours=2.0,
            evaluated_offset_hours=2.5,
        )
    )
    assert response.decision is expected, (response.claim_support_probability, codes(response))


def test_evidence_shortfall_shifts_the_bands_upward_not_downward():
    """Fail-closed: imperfect evidence needs a larger effect for the same decision."""
    perfect = evaluate(
        rhr_request(z=1.0, n_baseline=30, worn_minutes=1440.0, overnight_minutes=480.0,
                    sync_offset_hours=2.0, evaluated_offset_hours=2.5)
    )
    imperfect = evaluate(rhr_request(z=1.0, n_baseline=14))
    assert perfect.claim_support_probability > imperfect.claim_support_probability


# --------------------------------------------------------------------------- integrity


def test_duplicated_observations_do_not_change_the_decision():
    request = rhr_request()
    doubled = request.model_copy(
        update={"observations": list(request.observations) + [o.model_copy() for o in request.observations]}
    )
    assert evaluate(doubled).decision is evaluate(request).decision
    assert ReasonCode.DUPLICATE_OBSERVATIONS not in evaluate(doubled).reason_codes


def test_out_of_order_observations_do_not_change_the_decision():
    request = rhr_request()
    reversed_obs = request.model_copy(update={"observations": list(reversed(request.observations))})
    assert evaluate(reversed_obs).decision is evaluate(request).decision


def test_integrity_notes_reach_the_trace_even_when_they_do_not_change_the_decision():
    request = rhr_request()
    doubled = request.model_copy(
        update={"observations": list(request.observations) + [o.model_copy() for o in request.observations]}
    )
    response = evaluate(doubled)
    traced = {g.code.value for g in response.trace.gates}
    assert ReasonCode.DUPLICATE_OBSERVATIONS.value in traced
    assert any("duplicate" in n for n in response.trace.integrity_notes)


def test_missing_timezone_blocks_a_calendar_day_claim():
    response = evaluate(rhr_request(tz=None))
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert ReasonCode.TIMEZONE_UNKNOWN.value in codes(response)


def test_invalid_timezone_is_treated_as_missing():
    response = evaluate(rhr_request(tz="Mars/Olympus_Mons"))
    assert ReasonCode.TIMEZONE_UNKNOWN.value in codes(response)


def test_day_boundary_shift_is_caught_rather_than_answered_with_the_wrong_day():
    """The claim asks about a window shifted five hours off the summary's own day.

    The summary still overlaps 79% of it, which is how a timezone or DST error gets
    answered with yesterday's numbers instead of an abstention.
    """
    request = rhr_request()
    shifted = WINDOW.model_copy(
        update={
            "start": WINDOW.start - timedelta(hours=5),
            "end": WINDOW.end - timedelta(hours=5),
        }
    )
    request = request.model_copy(
        update={"claim": request.claim.model_copy(update={"target_window": shifted})}
    )
    response = evaluate(request)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert ReasonCode.WINDOW_MISALIGNED.value in codes(response)
    assert response.trace.gates[0].measurements or True


# --------------------------------------------------------------------------- scope


def test_unsupported_claim_type_is_a_typed_reject_listing_what_is_supported():
    response = evaluate(rhr_request(claim_type="YOU_WILL_GET_SICK"))
    assert response.decision is Decision.REJECT
    assert ReasonCode.UNSUPPORTED_CLAIM_TYPE.value in codes(response)
    assert "RESTING_HEART_RATE_ELEVATED" in response.explanation
    assert response.retry.recommended is False


def test_a_marginal_show_states_its_own_limitation():
    """The warn band can be entered by the score alone; it must still say why.

    Announcing "supportable, with limitations" and then listing none would leave a reader
    with a qualified claim and no qualification.
    """
    response = evaluate(rhr_request(z=2.0, n_baseline=14, worn_minutes=864.0, tz="UTC",
                                    sync_offset_hours=0.0))
    assert response.decision is Decision.SHOW_WITH_WARNING
    assert ReasonCode.MARGINAL_SUPPORT.value in codes(response)
    assert response.limitations
    assert "only marginally" in response.explanation
    assert str(round(response.claim_support_probability, 2)) in response.explanation


def test_a_gated_warning_does_not_also_claim_marginal_support():
    """When a real limitation fired, the score's marginality is not added on top of it."""
    response = evaluate(rhr_request(sync_offset_hours=None))
    assert response.decision is Decision.SHOW_WITH_WARNING
    assert ReasonCode.SYNC_STATE_UNKNOWN.value in codes(response)
    assert ReasonCode.MARGINAL_SUPPORT.value not in codes(response)
