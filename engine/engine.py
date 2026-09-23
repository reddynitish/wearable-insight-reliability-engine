"""The public entry point: evaluate one claim against one evidence bundle."""
from __future__ import annotations

from datetime import datetime, timezone

from engine import MODEL_VERSION, POLICY_VERSION
from engine import explain, features as features_mod, gates as gates_mod, support as support_mod
from engine.decide import decide
from engine.normalize import normalize
from engine.policies import get_policy, supported_claim_types
from engine.reasons import Outcome, ReasonCode, spec
from engine.schemas import (
    Decision,
    DecisionResponse,
    DecisionTrace,
    EvaluationRequest,
    EvidenceScores,
    GateRecord,
    Retry,
)


def evaluate(request: EvaluationRequest, now: datetime | None = None) -> DecisionResponse:
    """Decide whether the submitted evidence supports `request.claim`.

    Never raises on unsupported input that a caller could plausibly send: an unknown
    claim type produces a typed REJECT, because a caller that cannot parse a 422 still
    needs a decision it can branch on.
    """
    evaluated_at = request.evaluated_at or now or datetime.now(timezone.utc)
    policy = get_policy(request.claim.type)
    if policy is None:
        return _unsupported_claim_type(request, evaluated_at)

    norm = normalize(request, now=evaluated_at)
    feats = features_mod.extract(norm, request, policy)
    fired = gates_mod.evaluate(norm, request, policy, feats)

    if policy.mode == "insufficiency":
        adequacy = gates_mod.assess_inputs(norm, request, policy, feats)
        support, components = support_mod.insufficiency_support(adequacy, feats)
        components["per_input"] = [
            {
                "signal": a.signal.value,
                "present": a.present,
                "fresh": a.fresh,
                "baseline_mature": a.baseline_mature,
                "shortfalls": list(a.reasons),
            }
            for a in adequacy
        ]
    else:
        support, components = support_mod.deviation_support(feats, policy)

    outcome = decide(support, fired, policy)
    reported = [c.value for c in outcome.reason_codes]

    trace = DecisionTrace(
        claim_type=policy.claim_type,
        target_window=request.claim.target_window,
        evaluated_at=evaluated_at,
        gates=[
            GateRecord(
                code=g.code,
                dimension=spec(g.code).dimension,
                outcome=g.outcome,
                detail=g.detail,
                measurements=g.measurements,
            )
            for g in fired
        ],
        features=feats.as_dict(),
        support_components={**components, **outcome.rationale},
        thresholds={
            "reject_below": policy.thresholds.reject_below,
            "warn_above": policy.thresholds.warn_above,
            "show_above": policy.thresholds.show_above,
            "contradict_z": policy.contradict_z,
            "warn_z": policy.warn_z,
            "show_z": policy.show_z,
            "min_absolute_delta": policy.min_absolute_delta,
            "min_baseline_days": float(policy.min_baseline_days),
            "min_wear_coverage": policy.min_wear_coverage,
            "max_measurement_age_hours": policy.max_measurement_age_hours,
        },
        observation_counts={s.value: n for s, n in sorted(
            norm.submitted_counts.items(), key=lambda kv: kv[0].value)},
        integrity_notes=list(norm.notes),
    )

    explanation = explain.render(
        outcome.decision, fired, reported, feats, policy, outcome.retry
    )

    return DecisionResponse(
        decision=outcome.decision,
        confidence=round(outcome.confidence, 4),
        claim_support_probability=round(support, 4),
        reason_codes=outcome.reason_codes,
        evidence=EvidenceScores(
            coverage_score=round(feats.coverage.score, 4),
            freshness_score=round(feats.freshness.score, 4),
            signal_quality_score=round(feats.quality.score, 4),
            consistency_score=round(feats.consistency.score, 4),
            baseline_maturity_score=round(feats.baseline.score, 4),
        ),
        explanation=explanation,
        limitations=outcome.limitations,
        retry=outcome.retry,
        trace=trace,
        claim_type=policy.claim_type,
        subject_id=request.subject_id,
        model_version=MODEL_VERSION,
        policy_version=POLICY_VERSION,
        support_is_calibrated=False,
    )


def _unsupported_claim_type(
    request: EvaluationRequest, evaluated_at: datetime
) -> DecisionResponse:
    code = ReasonCode.UNSUPPORTED_CLAIM_TYPE
    detail = (
        f"{request.claim.type!r} is not a claim type this engine has a policy for; "
        f"supported types are {', '.join(supported_claim_types())}"
    )
    gate = GateRecord(
        code=code,
        dimension=spec(code).dimension,
        outcome=Outcome.REJECT,
        detail=detail,
        measurements={"supported_claim_types": supported_claim_types()},
    )
    return DecisionResponse(
        decision=Decision.REJECT,
        confidence=spec(code).decisiveness,
        claim_support_probability=0.0,
        reason_codes=[code],
        evidence=EvidenceScores(
            coverage_score=0.0,
            freshness_score=0.0,
            signal_quality_score=0.0,
            consistency_score=0.0,
            baseline_maturity_score=0.0,
        ),
        explanation=(
            "The available evidence does not support this statement. This is because "
            f"{detail}. Waiting will not change this, because the engine has no evidence "
            "contract for this claim type."
        ),
        limitations=[],
        retry=Retry(recommended=False),
        trace=DecisionTrace(
            claim_type=request.claim.type,
            target_window=request.claim.target_window,
            evaluated_at=evaluated_at,
            gates=[gate],
        ),
        claim_type=request.claim.type,
        subject_id=request.subject_id,
    )
