"""Aggregating gates and the support score into one typed decision.

Precedence is fixed and deliberate:

1. any REJECT gate  -> REJECT
2. any WAIT gate    -> WAIT_FOR_MORE_DATA
3. otherwise the support score against the policy's cut points
4. any WARN gate caps a SHOW at SHOW_WITH_WARNING
5. the policy's own ceiling caps everything

Gates outrank the score in both directions, which is the mechanism that makes a later
learned model unable to argue the engine into displaying a claim whose evidence failed a
hard requirement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.gates import Gate
from engine.policies.base import ClaimPolicy
from engine.reasons import NEVER_WARN_ONLY, Outcome, ReasonCode, sort_key, spec
from engine.schemas import Decision, Retry
from engine.scoring import clamp, longest_duration

_DECISION_RANK = {
    Decision.REJECT: 0,
    Decision.WAIT_FOR_MORE_DATA: 1,
    Decision.SHOW_WITH_WARNING: 2,
    Decision.SHOW: 3,
}

# Support within this distance of a cut point makes the decision genuinely borderline.
BOUNDARY_WIDTH = 0.12


@dataclass
class DecisionOutcome:
    decision: Decision
    confidence: float
    reason_codes: list[ReasonCode]
    determining_gates: list[Gate]
    retry: Retry
    limitations: list[str]
    rationale: dict[str, Any]


def decide(
    support: float, gates: list[Gate], policy: ClaimPolicy
) -> DecisionOutcome:
    rejects = [g for g in gates if g.outcome is Outcome.REJECT]
    waits = [g for g in gates if g.outcome is Outcome.WAIT]
    warns = [g for g in gates if g.outcome is Outcome.WARN]
    t = policy.thresholds

    score_decision = _score_decision(support, policy)
    if rejects:
        decision, determining, driver = Decision.REJECT, rejects, "reject_gate"
    elif waits:
        decision, determining, driver = Decision.WAIT_FOR_MORE_DATA, waits, "wait_gate"
    else:
        decision, determining, driver = score_decision, [], "support_score"

    if decision is Decision.SHOW and warns:
        decision, driver = Decision.SHOW_WITH_WARNING, "warn_gate"
        determining = warns

    ceiling = policy.max_decision
    ceiling_applied = False
    if _DECISION_RANK[decision] > _DECISION_RANK[ceiling]:
        decision, ceiling_applied = ceiling, True

    confidence = _confidence(decision, driver, determining, support, policy)

    # A gate that must never be reduced to a footnote next to a displayed claim.
    if decision in (Decision.SHOW, Decision.SHOW_WITH_WARNING):
        offenders = [g.code for g in gates if g.code in NEVER_WARN_ONLY]
        if offenders:
            raise AssertionError(
                "internal invariant violated: a claim reached "
                f"{decision.value} while {[c.value for c in offenders]} had fired"
            )

    reported = rejects + waits + warns if decision is not Decision.SHOW else []
    # Notes are recorded in the trace but are not reasons for the decision, so they do
    # not enter reason_codes.
    reason_codes = sorted({g.code for g in reported}, key=sort_key)

    return DecisionOutcome(
        decision=decision,
        confidence=confidence,
        reason_codes=reason_codes,
        determining_gates=determining,
        retry=_retry(decision, determining),
        limitations=[g.detail for g in warns],
        rationale={
            "driver": driver,
            "support": round(support, 4),
            "score_only_decision": score_decision.value,
            "reject_gates": [g.code.value for g in rejects],
            "wait_gates": [g.code.value for g in waits],
            "warn_gates": [g.code.value for g in warns],
            "policy_ceiling": ceiling.value,
            "policy_ceiling_applied": ceiling_applied,
            "thresholds": {
                "reject_below": t.reject_below,
                "warn_above": t.warn_above,
                "show_above": t.show_above,
            },
        },
    )


def _score_decision(support: float, policy: ClaimPolicy) -> Decision:
    t = policy.thresholds
    if support <= t.reject_below:
        return Decision.REJECT
    if support < t.warn_above:
        return Decision.WAIT_FOR_MORE_DATA
    if support < t.show_above:
        return Decision.SHOW_WITH_WARNING
    return Decision.SHOW


def _confidence(
    decision: Decision,
    driver: str,
    determining: list[Gate],
    support: float,
    policy: ClaimPolicy,
) -> float:
    """Confidence in the *decision*, never in the claim (ADR 0004).

    A gate-driven abstention on unambiguous evidence is a high-confidence abstention.
    Score-driven decisions are least confident near a cut point.
    """
    t = policy.thresholds
    distance = min(
        abs(support - t.reject_below), abs(support - t.warn_above), abs(support - t.show_above)
    )
    score_confidence = 0.55 + 0.44 * min(1.0, distance / BOUNDARY_WIDTH)
    if driver in ("reject_gate", "wait_gate") and determining:
        return clamp(max(spec(g.code).decisiveness for g in determining), 0.0, 0.99)
    return clamp(score_confidence, 0.0, 0.99)


def _retry(decision: Decision, determining: list[Gate]) -> Retry:
    """What, if anything, would let the caller ask again usefully."""
    if decision is Decision.SHOW:
        return Retry(recommended=False)
    durations = [spec(g.code).retry_after for g in determining]
    required: list[str] = []
    for gate in determining:
        for item in spec(gate.code).required_evidence:
            if item not in required:
                required.append(item)
    after = longest_duration([d for d in durations if d])
    return Retry(recommended=after is not None, after=after, required_evidence=required)
