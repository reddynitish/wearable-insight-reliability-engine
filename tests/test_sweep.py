"""The threshold sweep: it must re-decide through the engine, not around it."""
from __future__ import annotations

from engine.policies.base import DecisionThresholds
from engine.engine import evaluate
from engine.schemas import Decision
from engine.synth import GroundTruth, clean_case
from eval import sweep as S
from eval.suite import build

SUITE = build(seeds=1)
DEFAULT = DecisionThresholds(reject_below=0.20, warn_above=0.55, show_above=0.75)


def test_the_sweep_at_default_thresholds_matches_the_engine():
    """If the sweep took a shortcut through the engine it would not be measuring it."""
    for case in SUITE.scored[:200]:
        assert S._decide_at(case, DEFAULT) is evaluate(case.request).decision


def test_raising_the_display_threshold_never_increases_coverage():
    points = S.sweep(SUITE.scored)
    coverages = [p.decision_coverage for p in points]
    assert coverages == sorted(coverages, reverse=True), coverages


def test_raising_the_display_threshold_never_increases_the_unsupported_show_rate():
    points = S.sweep(SUITE.scored)
    rates = [p.unsupported_show_rate for p in points]
    assert rates == sorted(rates, reverse=True), rates


def test_the_sweep_includes_the_shipped_default():
    points = S.sweep(SUITE.scored)
    assert sum(1 for p in points if p.is_default) == 1


def test_gates_not_thresholds_are_what_withhold_unsupportable_claims():
    """The design claim, measured: no threshold choice can undo a gate.

    On this suite every unsupportable case is withheld even at the most permissive
    thresholds swept. That is the engine behaving as designed -- and it also means this
    suite cannot validate the threshold values, which the report says in its own text.
    """
    contribution = S.gate_contribution(SUITE.scored)
    assert contribution["unsupportable_cases"] > 100
    assert contribution["share_attributable_to_gates"] == 1.0


def test_loosening_thresholds_cannot_display_a_gated_claim():
    permissive = DecisionThresholds(reject_below=0.01, warn_above=0.02, show_above=0.03)
    case = clean_case("RESTING_HEART_RATE_ELEVATED", 0)
    from engine.corruptions import apply

    for corruption in ("shorten_wear", "truncate_baseline", "delay_sync", "implausible_spike"):
        broken = apply(case, corruption, "severe", seed=0)
        assert S._decide_at(broken, permissive) not in (
            Decision.SHOW, Decision.SHOW_WITH_WARNING
        ), corruption


def test_the_sweep_reports_over_abstention_as_the_cost_of_safety():
    """A sweep that only reported risk would make over-abstention look free."""
    points = S.sweep(SUITE.scored)
    strictest = points[-1]
    assert strictest.over_abstention_rate > 0
    assert strictest.withheld_supportable > 0


def test_the_markdown_states_what_the_flat_curve_means():
    points = S.sweep(SUITE.scored)
    text = "\n".join(S.to_markdown(points, S.gate_contribution(SUITE.scored)))
    assert "cannot recommend an operating point" in text
    assert "deterministic gate" in text
