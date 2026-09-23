"""Explanation fidelity: prose may never say more, or less, than the trace supports.

PROJECT_BRIEF.md section 13 makes this a required metric and section 19 makes it a
release condition. Here it is a test rather than a metric, because a value below 1.0
would be a bug.
"""
from __future__ import annotations

import pytest
from conftest import rhr_request

from engine import SCOPE_NOTE
from engine.corruptions import SEVERITIES, apply, names
from engine.engine import evaluate
from engine.schemas import Decision
from engine.synth import GENERATORS, clean_case

ALL_CASES = [
    apply(clean_case(ct, 0, supportable=sup), c, s, seed=0)
    for ct in sorted(GENERATORS)
    for sup in (True, False)
    for c in names()
    for s in SEVERITIES
] + [
    clean_case(ct, 0, supportable=sup) for ct in sorted(GENERATORS) for sup in (True, False)
]


def test_every_reported_reason_code_appears_in_the_trace():
    for case in ALL_CASES:
        response = evaluate(case.request)
        traced = {g.code for g in response.trace.gates}
        for code in response.reason_codes:
            assert code in traced, (
                f"{case.request.claim.type}/{case.corruption}: reason {code.value} is not "
                "in the decision trace"
            )


def test_every_reported_reason_codes_detail_appears_in_the_explanation():
    """The number in the prose is the number in the trace, not a paraphrase of it."""
    for case in ALL_CASES:
        response = evaluate(case.request)
        for code in response.reason_codes:
            detail = next(g.detail for g in response.trace.gates if g.code is code)
            assert detail in response.explanation, (
                f"{case.request.claim.type}/{case.corruption}: explanation omits the "
                f"detail for {code.value}"
            )


def test_the_explanation_never_introduces_a_reason_that_did_not_fire():
    """No reason-code name may appear in prose unless that gate is in the trace."""
    from engine.reasons import ReasonCode

    for case in ALL_CASES[:120]:
        response = evaluate(case.request)
        fired = {g.code.value for g in response.trace.gates}
        for code in ReasonCode:
            if code.value in response.explanation:
                assert code.value in fired


def test_every_gate_in_the_trace_carries_its_own_numbers_or_a_stated_reason():
    for case in ALL_CASES[:200]:
        response = evaluate(case.request)
        for gate in response.trace.gates:
            assert gate.detail.strip(), f"{gate.code.value} has an empty detail"
            assert gate.dimension is not None
            assert gate.outcome is not None


def test_show_explanations_carry_the_scope_note_and_abstentions_do_not():
    shown = evaluate(rhr_request())
    assert shown.decision is Decision.SHOW
    assert SCOPE_NOTE in shown.explanation

    withheld = evaluate(rhr_request(n_baseline=2))
    assert withheld.decision is Decision.WAIT_FOR_MORE_DATA
    assert SCOPE_NOTE not in withheld.explanation


def test_no_explanation_gives_medical_advice():
    forbidden = (
        "you should", "we recommend that you", "consult", "see a doctor", "diagnos",
        "treatment", "medication", "prescri", "disease", "you are sick", "you are ill",
    )
    for case in ALL_CASES:
        body = evaluate(case.request).explanation.replace(SCOPE_NOTE, "").lower()
        for phrase in forbidden:
            assert phrase not in body, f"explanation contained {phrase!r}: {body}"


def test_no_explanation_reports_a_number_the_trace_does_not_hold():
    """Spot check: the effect sentence's values must match the trace fields exactly."""
    response = evaluate(rhr_request())
    support = response.trace.features["claim_support"]
    baseline = response.trace.features["baseline"]
    assert f"{support['target_value']:.4g}" in response.explanation
    assert f"{baseline['mean']:.4g}" in response.explanation
    assert f"{baseline['valid_days']} day(s)" in response.explanation


def test_explanations_are_deterministic():
    first = evaluate(rhr_request()).explanation
    second = evaluate(rhr_request()).explanation
    assert first == second


@pytest.mark.parametrize("decision_case", ["show", "warn", "wait", "reject"])
def test_each_decision_type_produces_a_non_empty_explanation(decision_case):
    request = {
        "show": rhr_request(),
        "warn": rhr_request(sync_offset_hours=None),
        "wait": rhr_request(n_baseline=3),
        "reject": rhr_request(z=0.1),
    }[decision_case]
    response = evaluate(request)
    assert len(response.explanation) > 60
    assert response.explanation[0].isupper()
    assert response.explanation.rstrip().endswith(".")
