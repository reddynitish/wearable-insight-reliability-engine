"""The comparison baselines required by docs/evaluation-protocol.md section 5.

B0 and B1 exist to give the engine's numbers a scale. An unsupported-show rate means
nothing on its own: B0 shows what the status quo costs, and B1 shows how far a cheap
data-present check gets you.

B3 (learned, no abstention) and B4 (hybrid) are not implemented: they need public
datasets. Their absence is the honest state of the project, not an oversight.
"""
from __future__ import annotations

from engine.engine import evaluate
from engine.schemas import Decision, EvaluationRequest
from engine.policies import get_policy


def b0_always_show(request: EvaluationRequest) -> Decision:
    """Show the proposed claim unconditionally: what most products do today."""
    return Decision.SHOW


def b1_data_present(request: EvaluationRequest) -> Decision:
    """Show when the required signals have any observation at all in the window.

    No coverage, freshness, quality, consistency, or baseline check. This is the
    one-line guard that a reliability layer is supposed to replace.
    """
    policy = get_policy(request.claim.type)
    if policy is None:
        return Decision.REJECT
    window = request.claim.target_window
    required = policy.required_signals or tuple(
        s for s in (request.claim.signal,) if s is not None
    )
    if not required:
        required = policy.insufficiency_inputs
    for signal in required:
        present = any(
            o.signal == signal
            and (
                window.start <= o.measured_at <= window.end
                or (
                    o.window_start is not None
                    and o.window_end is not None
                    and o.window_start < window.end
                    and o.window_end > window.start
                )
            )
            for o in request.observations
        )
        if not present:
            return Decision.WAIT_FOR_MORE_DATA
    return Decision.SHOW


def b2_rules_engine(request: EvaluationRequest) -> Decision:
    """The v0.1 deterministic engine: the real baseline any model must beat."""
    return evaluate(request).decision


BASELINES = {
    "B0_always_show": b0_always_show,
    "B1_data_present": b1_data_present,
    "B2_rules_engine": b2_rules_engine,
}

NOT_IMPLEMENTED = {
    "B3_learned_no_abstention": "requires public datasets; see data/DATASETS.md",
    "B4_hybrid_calibrated": "requires public datasets and a calibration split",
}
