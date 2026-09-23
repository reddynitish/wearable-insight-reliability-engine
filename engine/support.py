"""The deterministic claim-support score.

This is v0.1's stand-in for the learned reliability estimator. It is a transparent
function of effect size and evidence quality, anchored so that the policy's own named
effect thresholds land on the policy's own decision cut points. That property is what
makes the rules baseline (B2 in docs/evaluation-protocol.md) a fair comparison for a
later model rather than a strawman.

It is **not a calibrated probability.** Nothing here has been fitted to outcomes. Every
response carries `support_is_calibrated: false` for exactly this reason (ADR 0002).
"""
from __future__ import annotations

from typing import Any

from engine.features import EvidenceFeatures
from engine.gates import InputAdequacy
from engine.policies.base import ClaimPolicy
from engine.scoring import clamp, interpolate

# How much each evidence dimension contributes to the quality factor. Coverage and
# signal quality carry the most weight because they bound what any downstream analysis
# can possibly establish; consistency carries the least because a conflict usually fires
# a gate of its own rather than needing to be priced into the score.
DIMENSION_WEIGHTS = {
    "coverage": 0.22,
    "freshness": 0.20,
    "signal_quality": 0.22,
    "consistency": 0.16,
    "baseline_maturity": 0.20,
}

# Evidence quality scales the effect, but never to zero: a strong effect on mediocre
# evidence should land in the abstention band, not in the contradiction band, because
# "we cannot tell" is a different statement from "this is false".
QUALITY_FLOOR = 0.35

SUPPORT_MIN = 0.01
SUPPORT_MAX = 0.97

# Support anchors for the insufficiency claim, as a function of how many of its inputs
# are inadequate. One missing input out of three already supports the claim; the
# increments above that reflect accumulating certainty, not a linear scale.
_INSUFFICIENCY_ANCHORS = [(0.0, 0.05), (0.34, 0.62), (0.67, 0.82), (1.0, 0.95)]


def effect_component(z_directional: float, policy: ClaimPolicy) -> float:
    """Map a directional effect size onto [0, 1], anchored at the policy's thresholds."""
    anchors = [
        (policy.contradict_z - 1.5, 0.02),
        (policy.contradict_z, 0.20),
        (policy.warn_z, 0.55),
        (policy.show_z, 0.80),
        (policy.show_z + 1.5, 0.95),
    ]
    return interpolate(z_directional, anchors)


def evidence_factor(features: EvidenceFeatures) -> tuple[float, float]:
    """(weighted evidence mean, the multiplier it implies)."""
    scores = {
        "coverage": features.coverage.score,
        "freshness": features.freshness.score,
        "signal_quality": features.quality.score,
        "consistency": features.consistency.score,
        "baseline_maturity": features.baseline.score,
    }
    weighted = sum(scores[k] * w for k, w in DIMENSION_WEIGHTS.items())
    return weighted, QUALITY_FLOOR + (1.0 - QUALITY_FLOOR) * weighted


def deviation_support(
    features: EvidenceFeatures, policy: ClaimPolicy
) -> tuple[float, dict[str, Any]]:
    weighted, factor = evidence_factor(features)
    sup = features.support
    if not sup.available or sup.z_directional is None:
        # No computable effect means the evidence supports the claim to no measurable
        # degree. It does not mean the claim is false, which is why an accompanying gate
        # drives this to an abstention rather than letting the low score reject it.
        return 0.10, {
            "effect_available": False,
            "evidence_weighted_mean": round(weighted, 4),
            "evidence_factor": round(factor, 4),
            "note": "no effect size could be computed from the submitted evidence",
        }
    effect = effect_component(sup.z_directional, policy)
    score = clamp(effect * factor, SUPPORT_MIN, SUPPORT_MAX)
    return score, {
        "effect_available": True,
        "z_directional": round(sup.z_directional, 4),
        "effect_component": round(effect, 4),
        "evidence_weighted_mean": round(weighted, 4),
        "evidence_factor": round(factor, 4),
        "formula": "support = effect_component * (0.35 + 0.65 * evidence_weighted_mean)",
    }


def insufficiency_support(
    adequacy: list[InputAdequacy], features: EvidenceFeatures
) -> tuple[float, dict[str, Any]]:
    if not adequacy:
        return 0.50, {"inputs_checked": 0,
                      "note": "no recovery inputs were declared for this claim"}
    inadequate = [a for a in adequacy if not a.adequate]
    fraction = len(inadequate) / len(adequacy)
    score = clamp(interpolate(fraction, _INSUFFICIENCY_ANCHORS), SUPPORT_MIN, SUPPORT_MAX)
    return score, {
        "inputs_checked": len(adequacy),
        "inputs_inadequate": len(inadequate),
        "inadequate_fraction": round(fraction, 4),
        "inadequate_signals": [a.signal.value for a in inadequate],
        "formula": "support interpolates the fraction of recovery inputs that are inadequate",
    }
