"""The evaluation harness: reproducible, and a regression guard on unsafe SHOWs."""
from __future__ import annotations

import json

from engine.schemas import Decision
from engine.synth import GroundTruth
from eval import metrics as M
from eval.baselines import BASELINES, NOT_IMPLEMENTED
from eval.run_eval import run, to_markdown
from eval.suite import REFERENCE_DECISION, build

SUITE = build(seeds=2)


def test_the_suite_is_deterministic():
    again = build(seeds=2)
    assert [c.truth for c in SUITE.cases] == [c.truth for c in again.cases]
    assert [c.request.model_dump(mode="json") for c in SUITE.cases[:50]] == [
        c.request.model_dump(mode="json") for c in again.cases[:50]
    ]


def test_the_suite_covers_every_claim_type_corruption_and_severity():
    summary = SUITE.summary()
    assert len(summary["claim_types"]) == 6
    assert len(summary["corruptions"]) >= 17
    assert summary["severities"] == ["mild", "moderate", "severe"]
    assert summary["scored_cases"] > 1000


def test_the_suite_contains_both_supportable_and_unsupportable_cases():
    """A suite of only failures cannot detect over-abstention."""
    truths = {c.truth for c in SUITE.scored}
    assert GroundTruth.SUPPORTABLE in truths
    assert GroundTruth.UNSUPPORTABLE_INSUFFICIENT in truths
    assert GroundTruth.UNSUPPORTABLE_CONTRADICTED in truths
    assert GroundTruth.UNSUPPORTABLE_UNTRUSTWORTHY in truths


def test_every_scored_case_has_a_reference_decision():
    for case in SUITE.scored:
        assert case.truth in REFERENCE_DECISION


def test_borderline_cases_are_excluded_with_a_stated_reason():
    for case in SUITE.excluded:
        assert case.truth is GroundTruth.BORDERLINE_EXCLUDED
        assert "between contradiction and display" in case.truth_reason


def _records(fn):
    return [
        M.Record(
            subject_id=c.request.subject_id,
            claim_type=c.request.claim.type,
            truth=c.truth,
            reference_decision=REFERENCE_DECISION[c.truth],
            decision=fn(c.request),
            corruption=c.corruption,
            severity=c.severity,
            dimension=None,
        )
        for c in SUITE.scored
    ]


def test_the_engine_shows_no_unsupported_claim_on_the_synthetic_suite():
    """The regression guard. A rise here means a gate stopped firing."""
    records = _records(BASELINES["B2_rules_engine"])
    assert M.unsupported_show_rate(records) == 0.0


def test_the_engine_does_not_over_abstain_on_the_synthetic_suite():
    records = _records(BASELINES["B2_rules_engine"])
    assert M.over_abstention_rate(records) == 0.0


def test_the_engine_still_answers_a_useful_share_of_cases():
    """Paired with the two rates above: abstaining on everything is not a pass."""
    records = _records(BASELINES["B2_rules_engine"])
    supportable = [r for r in records if r.truth is GroundTruth.SUPPORTABLE]
    assert len(supportable) > 100
    assert M.decision_coverage(records) > 0.15


def test_the_engine_beats_both_implemented_baselines():
    engine = M.unsupported_show_rate(_records(BASELINES["B2_rules_engine"]))
    for name in ("B0_always_show", "B1_data_present"):
        assert M.unsupported_show_rate(_records(BASELINES[name])) > engine


def test_always_show_has_the_worst_possible_unsupported_show_rate():
    assert M.unsupported_show_rate(_records(BASELINES["B0_always_show"])) == 1.0
    assert M.decision_coverage(_records(BASELINES["B0_always_show"])) == 1.0


def test_learned_baselines_are_declared_unimplemented_rather_than_faked():
    assert set(NOT_IMPLEMENTED) == {"B3_learned_no_abstention", "B4_hybrid_calibrated"}
    for reason in NOT_IMPLEMENTED.values():
        assert "dataset" in reason


def test_bootstrap_resamples_subjects_not_cases():
    records = _records(BASELINES["B1_data_present"])
    interval = M.bootstrap(records, M.unsupported_show_rate, resamples=200)
    assert interval.n == len({r.subject_id for r in records})
    assert interval.low <= interval.point <= interval.high


def test_the_report_is_labelled_synthetic_everywhere_it_matters():
    report = run(seeds=1)
    assert report["synthetic"] is True
    assert "SYNTHETIC" in report["warning"]
    markdown = to_markdown(report)
    assert "SYNTHETIC EVIDENCE ONLY" in markdown
    assert "not a measurement of real-world performance" in markdown
    # The report must be honest about what a zero rate here does and does not mean.
    assert "regression harness" in markdown
    assert "weak **benchmark**" in markdown
    json.dumps(report)  # must be serialisable


def test_the_report_records_provenance_for_reproduction():
    report = run(seeds=1)
    assert report["model_version"]
    assert report["policy_version"]
    assert report["suite"]["seeds"] == [0]
    assert report["python"]
