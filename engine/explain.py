"""Rendering explanations from the decision trace.

Every sentence is assembled from a field of the structured record: a gate's `detail`,
a measured feature, or the retry envelope. Nothing is generated freely, there is no
language model in this path (ADR 0003), and the fidelity test in
tests/test_explanation_fidelity.py asserts that each reported reason code's detail text
appears in the rendered explanation.

The prose is therefore more repetitive than a writer would produce. That is the trade:
an explanation that cannot drift from the evidence it claims to describe.
"""
from __future__ import annotations

from engine import SCOPE_NOTE
from engine.features import EvidenceFeatures
from engine.gates import Gate
from engine.policies.base import ClaimPolicy
from engine.schemas import Decision, Retry
from engine.signals import label, spec

_LEAD = {
    Decision.SHOW: "The available evidence supports this statement.",
    Decision.SHOW_WITH_WARNING: (
        "The available evidence supports this statement, but with limitations that should "
        "be shown alongside it."
    ),
    Decision.WAIT_FOR_MORE_DATA: (
        "There is not yet enough reliable evidence to decide this one way or the other."
    ),
    Decision.REJECT: "The available evidence does not support this statement.",
}


def render(
    decision: Decision,
    gates: list[Gate],
    reported_codes: list[str],
    features: EvidenceFeatures,
    policy: ClaimPolicy,
    retry: Retry,
) -> str:
    parts = [_LEAD[decision]]

    effect = _effect_sentence(features)
    if effect:
        parts.append(effect)

    details = _details_for(gates, reported_codes)
    if details:
        joined = "; ".join(details)
        prefix = (
            "The limitations are"
            if decision is Decision.SHOW_WITH_WARNING
            else "This is because"
        )
        parts.append(f"{prefix}: {joined}.")

    if retry.recommended and retry.after:
        needed = ", ".join(item.replace("_", " ") for item in retry.required_evidence)
        tail = f" once there is {needed}" if needed else ""
        parts.append(f"Asking again after {_human_duration(retry.after)} may resolve this{tail}.")
    elif decision is Decision.REJECT and not retry.recommended:
        parts.append("Waiting will not change this, because the evidence argues against the statement rather than being incomplete.")

    if decision in (Decision.SHOW, Decision.SHOW_WITH_WARNING):
        parts.append(SCOPE_NOTE)

    return " ".join(parts)


def _effect_sentence(features: EvidenceFeatures) -> str | None:
    """State the measured change in the subject's own terms, from trace fields only."""
    sup = features.support
    base = features.baseline
    if not sup.available or sup.signal is None or sup.target_value is None:
        return None
    if base.mean is None or sup.z is None:
        return None
    unit = spec(sup.signal).unit
    direction = "above" if sup.z >= 0 else "below"
    return (
        f"Measured {label(sup.signal)} was {sup.target_value:.4g} {unit} against a personal "
        f"baseline of {base.mean:.4g} {unit} over {base.valid_days} day(s), "
        f"{abs(sup.z):.1f} SD {direction} it."
    )


def _details_for(gates: list[Gate], reported_codes: list[str]) -> list[str]:
    """One detail sentence per reported reason code, in reported order.

    When several gates share a code, the first is used and the rest stay in the trace.
    Keeping them out of the prose avoids the same reason being read as two problems.
    """
    seen: set[str] = set()
    details: list[str] = []
    for code in reported_codes:
        for gate in gates:
            if gate.code.value == code and code not in seen:
                seen.add(code)
                details.append(gate.detail)
                break
    return details


def _human_duration(iso: str) -> str:
    table = {
        "PT0S": "no delay",
        "PT1H": "an hour",
        "PT2H": "two hours",
        "PT6H": "six hours",
        "PT12H": "twelve hours",
        "P1D": "a day",
        "P7D": "a week",
        "P14D": "two weeks",
    }
    return table.get(iso, iso)
