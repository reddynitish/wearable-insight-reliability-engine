"""Property-based invariants: the guarantees that must hold for any input.

These are the regression tests PROJECT_BRIEF.md section 19 asks for around unsafe SHOW
outcomes. Where a unit test pins one scenario, these search the input space for a
counterexample.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import WINDOW, rhr_request
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from engine.decide import decide
from engine.engine import evaluate
from engine.policies import all_policies, get_policy
from engine.reasons import NEVER_WARN_ONLY, SPECS, Outcome, ReasonCode
from engine.schemas import Decision
from engine.signals import Signal

RANK = {
    Decision.REJECT: 0,
    Decision.WAIT_FOR_MORE_DATA: 1,
    Decision.SHOW_WITH_WARNING: 2,
    Decision.SHOW: 3,
}
# No max_examples here on purpose: the profile registered in conftest.py governs how hard
# the search runs, so --hypothesis-profile=search actually widens it.
SETTINGS = settings(deadline=None,
                    suppress_health_check=[HealthCheck.function_scoped_fixture])


# --------------------------------------------------------------------------- fail closed


@given(
    z=st.floats(-4, 6, allow_nan=False),
    baseline=st.integers(0, 40),
    worn=st.floats(0, 1440),
    overnight=st.floats(0, 480),
    eval_offset=st.floats(-10, 200),
)
@SETTINGS
def test_a_fatal_reason_never_coexists_with_a_displayed_claim(
    z, baseline, worn, overnight, eval_offset
):
    """The core safety property: certain reasons can never be reduced to a footnote."""
    response = evaluate(
        rhr_request(
            z=z,
            n_baseline=baseline,
            worn_minutes=worn,
            overnight_minutes=overnight,
            evaluated_offset_hours=eval_offset,
        )
    )
    if response.decision in (Decision.SHOW, Decision.SHOW_WITH_WARNING):
        fired = {g.code for g in response.trace.gates}
        assert not (fired & NEVER_WARN_ONLY), (
            f"displayed a claim while {[c.value for c in fired & NEVER_WARN_ONLY]} had fired"
        )


@given(
    z=st.floats(-4, 6, allow_nan=False),
    baseline=st.integers(0, 40),
    worn=st.floats(0, 1440),
)
@SETTINGS
def test_a_wait_or_reject_gate_always_wins_over_the_score(z, baseline, worn):
    response = evaluate(rhr_request(z=z, n_baseline=baseline, worn_minutes=worn))
    outcomes = {g.outcome for g in response.trace.gates}
    if Outcome.REJECT in outcomes:
        assert response.decision is Decision.REJECT
    elif Outcome.WAIT in outcomes:
        assert response.decision is Decision.WAIT_FOR_MORE_DATA
    elif Outcome.WARN in outcomes:
        assert response.decision is not Decision.SHOW


@given(support=st.floats(0, 1, allow_nan=False))
@settings(deadline=None)
def test_no_support_score_can_reach_show_when_a_reject_gate_fired(support):
    """Stated at the aggregation layer, where a learned model would plug in."""
    from engine.gates import Gate

    policy = get_policy("RESTING_HEART_RATE_ELEVATED")
    assert policy is not None
    gate = Gate(ReasonCode.DEVICE_NOT_WORN, Outcome.REJECT, "synthetic gate", {})
    assert decide(support, [gate], policy).decision is Decision.REJECT


@given(support=st.floats(0, 1, allow_nan=False))
@settings(deadline=None)
def test_no_support_score_can_reach_show_when_a_wait_gate_fired(support):
    from engine.gates import Gate

    policy = get_policy("RESTING_HEART_RATE_ELEVATED")
    assert policy is not None
    gate = Gate(ReasonCode.STALE_DATA, Outcome.WAIT, "synthetic gate", {})
    assert decide(support, [gate], policy).decision is Decision.WAIT_FOR_MORE_DATA


# --------------------------------------------------------------------------- monotonicity


@given(
    z=st.floats(1.6, 5, allow_nan=False),
    worn_high=st.floats(1000, 1440),
    drop=st.floats(50, 900),
)
@SETTINGS
def test_removing_coverage_never_improves_the_decision(z, worn_high, drop):
    better = evaluate(rhr_request(z=z, worn_minutes=worn_high))
    worse = evaluate(rhr_request(z=z, worn_minutes=max(0.0, worn_high - drop)))
    assert RANK[worse.decision] <= RANK[better.decision]


@given(z=st.floats(1.6, 5, allow_nan=False), days=st.integers(1, 20), extra=st.integers(1, 20))
@SETTINGS
def test_lengthening_the_baseline_never_worsens_the_decision(z, days, extra):
    shorter = evaluate(rhr_request(z=z, n_baseline=days))
    longer = evaluate(rhr_request(z=z, n_baseline=days + extra))
    assert RANK[longer.decision] >= RANK[shorter.decision]


@given(z=st.floats(1.6, 5, allow_nan=False), age=st.floats(1, 40), extra=st.floats(1, 200))
@SETTINGS
def test_older_evidence_never_improves_the_decision(z, age, extra):
    fresher = evaluate(rhr_request(z=z, evaluated_offset_hours=age))
    staler = evaluate(rhr_request(z=z, evaluated_offset_hours=age + extra))
    assert RANK[staler.decision] <= RANK[fresher.decision]


@given(z=st.floats(0.6, 5, allow_nan=False))
@SETTINGS
def test_support_increases_with_effect_size_on_identical_evidence(z):
    lower = evaluate(rhr_request(z=z))
    higher = evaluate(rhr_request(z=z + 0.4))
    assert higher.claim_support_probability >= lower.claim_support_probability


# --------------------------------------------------------------------------- well-formedness


@given(
    z=st.floats(-4, 6, allow_nan=False),
    baseline=st.integers(0, 40),
    worn=st.floats(0, 1440),
    tz=st.sampled_from([None, "UTC", "America/New_York", "Mars/Olympus_Mons"]),
    sync=st.one_of(st.none(), st.floats(-30, 30, allow_nan=False)),
)
@SETTINGS
def test_every_response_is_internally_consistent(z, baseline, worn, tz, sync):
    response = evaluate(
        rhr_request(z=z, n_baseline=baseline, worn_minutes=worn, tz=tz, sync_offset_hours=sync)
    )
    assert 0.0 <= response.confidence <= 1.0
    assert 0.0 <= response.claim_support_probability <= 1.0
    for score in response.evidence.model_dump().values():
        assert 0.0 <= score <= 1.0
    assert response.decision in Decision
    # An abstention must always say what it is waiting for; a rejection need not.
    if response.decision is Decision.WAIT_FOR_MORE_DATA:
        assert response.retry.recommended
        assert response.retry.after
        assert response.retry.required_evidence
    if response.decision is Decision.SHOW:
        assert response.reason_codes == []
        assert response.limitations == []
    if response.decision is Decision.SHOW_WITH_WARNING:
        assert response.limitations
    assert response.model_version and response.policy_version
    assert response.support_is_calibrated is False


@given(
    z=st.floats(-3, 5, allow_nan=False),
    baseline=st.integers(0, 30),
    worn=st.floats(0, 1440),
)
@SETTINGS
def test_evaluation_is_deterministic(z, baseline, worn):
    first = evaluate(rhr_request(z=z, n_baseline=baseline, worn_minutes=worn))
    second = evaluate(rhr_request(z=z, n_baseline=baseline, worn_minutes=worn))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


@given(z=st.floats(-3, 5, allow_nan=False), baseline=st.integers(0, 30))
@SETTINGS
def test_reason_codes_are_reported_in_a_stable_order(z, baseline):
    from engine.reasons import sort_key

    response = evaluate(rhr_request(z=z, n_baseline=baseline))
    assert list(response.reason_codes) == sorted(response.reason_codes, key=sort_key)


def test_evaluation_never_raises_on_an_empty_request():
    from engine.schemas import Claim, EvaluationRequest

    request = EvaluationRequest(
        subject_id="empty-001",
        claim=Claim(type="RESTING_HEART_RATE_ELEVATED", target_window=WINDOW),
        evaluated_at=WINDOW.end + timedelta(hours=1),
    )
    response = evaluate(request)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert ReasonCode.NO_OBSERVATIONS in response.reason_codes


def test_every_reason_code_has_a_spec_and_a_sane_retry():
    for code in ReasonCode:
        spec = SPECS[code]
        assert 0.0 < spec.decisiveness <= 0.99
        if spec.outcome is Outcome.WAIT:
            assert spec.retry_after, f"{code.value} defers without saying what to wait for"


def test_every_policy_is_self_consistent():
    for policy in all_policies():
        assert policy.warn_baseline_days <= policy.min_baseline_days <= policy.good_baseline_days
        assert policy.contradict_z < policy.warn_z < policy.show_z
        assert policy.min_wear_coverage <= policy.good_wear_coverage
        assert policy.good_measurement_age_hours <= policy.max_measurement_age_hours
        for name in policy.consistency_rules:
            from engine.consistency import get_rule

            assert callable(get_rule(name))
        if policy.mode == "deviation":
            assert policy.target_signal in policy.required_signals


def test_confidence_in_an_unambiguous_abstention_is_high():
    """ADR 0004: confidence is in the decision, not in the claim.

    This case is the clearest illustration of why the two numbers are separate. The
    window has not closed, so the engine is nearly certain it must wait -- and the
    evidence it does have points strongly toward the claim. A single number could not
    express both, and collapsing them would either hide the certainty of the abstention
    or understate the evidence.
    """
    response = evaluate(rhr_request(evaluated_offset_hours=-8.0, sync_offset_hours=-9.0))
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert response.confidence >= 0.95
    assert response.claim_support_probability > 0.5
    assert ReasonCode.WINDOW_NOT_CLOSED in response.reason_codes


def test_confidence_is_high_when_there_is_nothing_to_go_on():
    """The other end: an abstention with no evidence at all is also high-confidence."""
    response = evaluate(rhr_request(hr_samples=0, baseline_values=[], rhr_value=None))
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert response.confidence >= 0.9
    assert response.claim_support_probability < 0.3


def test_confidence_is_lowest_near_a_decision_boundary():
    near = evaluate(
        rhr_request(z=1.5, n_baseline=30, worn_minutes=1440.0, overnight_minutes=480.0,
                    sync_offset_hours=2.0, evaluated_offset_hours=2.5)
    )
    far = evaluate(
        rhr_request(z=4.0, n_baseline=30, worn_minutes=1440.0, overnight_minutes=480.0,
                    sync_offset_hours=2.0, evaluated_offset_hours=2.5)
    )
    assert near.confidence < far.confidence
