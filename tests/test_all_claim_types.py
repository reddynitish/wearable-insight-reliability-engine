"""Every registered claim type must reach every decision its policy permits."""
from __future__ import annotations

import pytest

from engine import SCOPE_NOTE
from engine.corruptions import SEVERITIES, apply, names
from engine.engine import evaluate
from engine.policies import all_policies, get_policy, supported_claim_types
from engine.schemas import Decision
from engine.synth import GENERATORS, clean_case

CLAIM_TYPES = sorted(GENERATORS)
SHOWN = {Decision.SHOW, Decision.SHOW_WITH_WARNING}


def test_every_policy_has_a_generator_and_vice_versa():
    """A claim type with no generator is a claim type nothing exercises."""
    assert sorted(GENERATORS) == supported_claim_types()


@pytest.mark.parametrize("claim_type", CLAIM_TYPES)
def test_clean_supportable_evidence_is_displayed(claim_type):
    response = evaluate(clean_case(claim_type, 0, supportable=True).request)
    assert response.decision in SHOWN, [c.value for c in response.reason_codes]


@pytest.mark.parametrize("claim_type", CLAIM_TYPES)
def test_contradicting_evidence_is_rejected(claim_type):
    response = evaluate(clean_case(claim_type, 0, supportable=False).request)
    assert response.decision is Decision.REJECT


@pytest.mark.parametrize("claim_type", CLAIM_TYPES)
def test_degraded_evidence_defers(claim_type):
    """Every claim type must be able to abstain, not only to answer.

    Which corruption causes the abstention differs by claim: a delayed sync is fatal to a
    daily claim and irrelevant to a two-hour anomaly window, and for the inverted
    insufficiency claim degraded evidence is support rather than a reason to wait. So the
    property asserted is that *something* in the catalogue makes this claim abstain.
    """
    case = clean_case(claim_type, 0, supportable=True)
    decisions = {
        evaluate(apply(case, c, s, seed=0).request).decision
        for c in names()
        for s in SEVERITIES
    }
    assert Decision.WAIT_FOR_MORE_DATA in decisions


@pytest.mark.parametrize("claim_type", CLAIM_TYPES)
def test_each_claim_type_reaches_every_decision_its_policy_allows(claim_type):
    policy = get_policy(claim_type)
    assert policy is not None
    reached: set[Decision] = set()
    for supportable in (True, False):
        base = clean_case(claim_type, 0, supportable=supportable)
        reached.add(evaluate(base.request).decision)
        # Unverifiable context is a nonfatal limitation for every claim type, and it is
        # the ordinary route to SHOW_WITH_WARNING: no corruption in the catalogue produces
        # a warn-only outcome, because they are all designed to breach something.
        for update in ({"sync_completed_at": None}, {"device_worn_minutes": None}):
            unverifiable = base.request.model_copy(
                update={"context": base.request.context.model_copy(update=update)}
            )
            reached.add(evaluate(unverifiable).decision)
        for corruption in names():
            for severity in SEVERITIES:
                case = apply(base, corruption, severity, seed=0)
                reached.add(evaluate(case.request).decision)
    expected = {Decision.WAIT_FOR_MORE_DATA, Decision.REJECT, Decision.SHOW_WITH_WARNING}
    if policy.max_decision is Decision.SHOW:
        expected.add(Decision.SHOW)
    missing = expected - reached
    assert not missing, f"{claim_type} never reached {[d.value for d in missing]}"


@pytest.mark.parametrize(
    "claim_type",
    [p.claim_type for p in all_policies() if p.max_decision is not Decision.SHOW],
)
def test_policy_ceilings_are_never_exceeded(claim_type):
    """Sleep-quality and anomaly claims may never be displayed without their limitation."""
    policy = get_policy(claim_type)
    assert policy is not None
    for supportable in (True, False):
        base = clean_case(claim_type, 0, supportable=supportable)
        cases = [base] + [
            apply(base, c, s, seed=0) for c in names() for s in SEVERITIES
        ]
        for case in cases:
            response = evaluate(case.request)
            assert response.decision is not Decision.SHOW, (
                f"{claim_type} returned a bare SHOW under "
                f"{case.corruption}/{case.severity}"
            )


def test_the_anomaly_claim_never_names_a_condition():
    """Its whole contract is 're-measure this', never 'you have that'."""
    forbidden = (
        "diagnos", "disease", "infection", "covid", "apnea", "arrhythmi", "condition",
        "illness", "treat", "medication", "see a doctor", "atrial",
    )
    base = clean_case("PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION", 0)
    cases = [base] + [apply(base, c, s, seed=0) for c in names() for s in SEVERITIES]
    for case in cases:
        explanation = evaluate(case.request).explanation
        # The fixed scope note itself says "not whether you have any condition"; the
        # check is about the generated body, so the note is removed first.
        body = explanation.replace(SCOPE_NOTE, "").lower()
        for word in forbidden:
            assert word not in body, f"explanation mentioned {word!r}: {explanation}"


def test_the_anomaly_claim_requires_the_signal_to_be_named():
    case = clean_case("PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION", 0)
    request = case.request.model_copy(
        update={"claim": case.request.claim.model_copy(update={"signal": None})}
    )
    response = evaluate(request)
    assert response.decision is Decision.REJECT
    assert "CLAIM_OUT_OF_SCOPE" in [c.value for c in response.reason_codes]


def test_the_inverted_claim_is_supported_by_absent_evidence():
    """RECOVERY_EVIDENCE_INCOMPLETE asserts insufficiency, so an empty bundle supports it."""
    case = clean_case("RECOVERY_EVIDENCE_INCOMPLETE", 0, supportable=True)
    empty = case.request.model_copy(update={"observations": [], "baseline": []})
    response = evaluate(empty)
    assert response.decision in SHOWN
    assert "NO_OBSERVATIONS" not in [c.value for c in response.reason_codes]


def test_the_inverted_claim_is_contradicted_by_complete_evidence():
    case = clean_case("RECOVERY_EVIDENCE_INCOMPLETE", 0, supportable=False)
    response = evaluate(case.request)
    assert response.decision is Decision.REJECT
    assert "EVIDENCE_COMPLETE" in [c.value for c in response.reason_codes]


def test_the_inverted_claim_counts_contaminated_inputs_as_inadequate():
    """Present but unusable is not the same as present, per the claim contract."""
    case = clean_case("RECOVERY_EVIDENCE_INCOMPLETE", 0, supportable=False)
    contaminated = apply(case, "motion_artifact", "severe", seed=0)
    response = evaluate(contaminated.request)
    assert response.decision in SHOWN


def test_the_inverted_claim_still_abstains_when_the_window_cannot_be_placed():
    """Not knowing which day it is differs from knowing the day's evidence is thin."""
    case = clean_case("RECOVERY_EVIDENCE_INCOMPLETE", 0, supportable=True)
    no_tz = apply(case, "drop_timezone", "mild", seed=0)
    response = evaluate(no_tz.request)
    assert response.decision is Decision.WAIT_FOR_MORE_DATA
    assert "TIMEZONE_UNKNOWN" in [c.value for c in response.reason_codes]
