"""Selective risk versus decision coverage, by sweeping the display thresholds.

docs/evaluation-protocol.md section 4 requires performance at multiple operating points
rather than at one arbitrary threshold, and Stage 4 of the brief is about tuning abstention
against explicit cost assumptions. This produces the curve that discussion needs.

What is swept: the support-score cut points `warn_above` and `show_above` in the claim
policy, holding every deterministic gate fixed. That is the honest thing to vary, because
the gates are the contract and the thresholds are the tunable part. A consequence worth
stating up front: where gates already decide a case, moving a threshold changes nothing, so
the curve is flat over those regions. That flatness is a finding about where the engine's
safety actually comes from, not a defect in the sweep.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from engine.decide import decide
from engine.features import extract
from engine.gates import assess_inputs, evaluate as evaluate_gates
from engine.normalize import normalize
from engine.policies import get_policy
from engine.policies.base import DecisionThresholds
from engine.schemas import Decision
from engine.support import deviation_support, insufficiency_support
from engine.synth import GroundTruth, SyntheticCase

# Display thresholds to sweep. `show_above` moves with `warn_above` because a band that
# inverts is not a valid policy.
OPERATING_POINTS: tuple[tuple[float, float], ...] = (
    (0.30, 0.40),
    (0.40, 0.50),
    (0.45, 0.60),
    (0.55, 0.75),  # the shipped default
    (0.65, 0.82),
    (0.75, 0.88),
    (0.85, 0.94),
)


@dataclass
class OperatingPoint:
    warn_above: float
    show_above: float
    cases: int
    decision_coverage: float
    unsupported_show_rate: float
    over_abstention_rate: float
    shown_unsupportable: int
    withheld_supportable: int
    is_default: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "warn_above": self.warn_above,
            "show_above": self.show_above,
            "cases": self.cases,
            "decision_coverage": round(self.decision_coverage, 4),
            "unsupported_show_rate": round(self.unsupported_show_rate, 4),
            "over_abstention_rate": round(self.over_abstention_rate, 4),
            "shown_unsupportable": self.shown_unsupportable,
            "withheld_supportable": self.withheld_supportable,
            "is_default": self.is_default,
        }


def _decide_at(case: SyntheticCase, thresholds: DecisionThresholds) -> Decision:
    """Re-decide one case under different display thresholds, gates unchanged.

    The feature and gate work is repeated per operating point rather than cached, which is
    slower but keeps this path identical to the production one -- a sweep that took a
    shortcut through the engine would not be measuring the engine.
    """
    policy = get_policy(case.request.claim.type)
    if policy is None:
        return Decision.REJECT
    tuned = replace(policy, thresholds=thresholds)
    norm = normalize(case.request, now=case.request.evaluated_at)
    feats = extract(norm, case.request, tuned)
    gates = evaluate_gates(norm, case.request, tuned, feats)
    if tuned.mode == "insufficiency":
        support, _ = insufficiency_support(
            assess_inputs(norm, case.request, tuned, feats), feats
        )
    else:
        support, _ = deviation_support(feats, tuned)
    return decide(support, gates, tuned).decision


def sweep(cases: list[SyntheticCase]) -> list[OperatingPoint]:
    shown = {Decision.SHOW, Decision.SHOW_WITH_WARNING}
    points: list[OperatingPoint] = []
    for warn_above, show_above in OPERATING_POINTS:
        thresholds = DecisionThresholds(
            reject_below=0.20, warn_above=warn_above, show_above=show_above
        )
        displayed = bad_displayed = good_withheld = 0
        bad_total = good_total = 0
        for case in cases:
            decision = _decide_at(case, thresholds)
            is_shown = decision in shown
            displayed += is_shown
            if case.truth is GroundTruth.SUPPORTABLE:
                good_total += 1
                good_withheld += not is_shown
            else:
                bad_total += 1
                bad_displayed += is_shown
        points.append(
            OperatingPoint(
                warn_above=warn_above,
                show_above=show_above,
                cases=len(cases),
                decision_coverage=displayed / len(cases) if cases else 0.0,
                unsupported_show_rate=bad_displayed / bad_total if bad_total else 0.0,
                over_abstention_rate=good_withheld / good_total if good_total else 0.0,
                shown_unsupportable=bad_displayed,
                withheld_supportable=good_withheld,
                is_default=(warn_above, show_above) == (0.55, 0.75),
            )
        )
    return points


def gate_contribution(cases: list[SyntheticCase]) -> dict[str, Any]:
    """How much of the engine's safety comes from gates rather than from thresholds.

    Measured by deciding every case at the most permissive operating point in the sweep:
    whatever is still withheld there was withheld by a gate, not by a threshold.
    """
    permissive = DecisionThresholds(reject_below=0.20, warn_above=0.30, show_above=0.40)
    shown = {Decision.SHOW, Decision.SHOW_WITH_WARNING}
    unsupportable = [c for c in cases if c.truth is not GroundTruth.SUPPORTABLE]
    held_by_gates = sum(
        1 for c in unsupportable if _decide_at(c, permissive) not in shown
    )
    return {
        "unsupportable_cases": len(unsupportable),
        "withheld_even_at_the_most_permissive_thresholds": held_by_gates,
        "share_attributable_to_gates": round(
            held_by_gates / len(unsupportable), 4
        ) if unsupportable else 0.0,
        "note": (
            "Cases still withheld at warn_above=0.30 / show_above=0.40 were withheld by a "
            "deterministic gate. That share is the part of the engine's safety that no "
            "threshold choice, and no future learned score, can undo."
        ),
    }


def to_markdown(points: list[OperatingPoint], contribution: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## Selective risk versus coverage (engine, synthetic)",
        "",
        "Display thresholds swept with every deterministic gate held fixed. The shipped "
        "default is marked.",
        "",
        "| warn_above | show_above | coverage | unsupported-show | over-abstention | shown unsupportable | withheld supportable |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in points:
        mark = " **(default)**" if p.is_default else ""
        lines.append(
            f"| {p.warn_above:.2f}{mark} | {p.show_above:.2f} | {p.decision_coverage:.4f} | "
            f"{p.unsupported_show_rate:.4f} | {p.over_abstention_rate:.4f} | "
            f"{p.shown_unsupportable} | {p.withheld_supportable} |"
        )
    lines += [
        "",
        f"Of {contribution['unsupportable_cases']} unsupportable cases, "
        f"{contribution['withheld_even_at_the_most_permissive_thresholds']} "
        f"({contribution['share_attributable_to_gates']:.1%}) are withheld even at the most "
        "permissive thresholds in this sweep, so they were withheld by a deterministic gate "
        "rather than by a threshold choice. That share is the part of the engine's safety "
        "that no threshold tuning, and no future learned score, can undo.",
        "",
        "The curve is flat wherever gates already decide the case. That is the point of the "
        "design rather than a limitation of the sweep -- but it also means this synthetic "
        "suite cannot recommend an operating point. Choosing one needs real data and an "
        "explicit cost for a wrong SHOW against a needless abstention.",
        "",
    ]
    return lines
