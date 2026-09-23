"""Wearable Insight Reliability Engine.

Decides whether the available wearable evidence responsibly supports a proposed
health or fitness claim, and returns a typed decision with a traceable evidence
record. It does not diagnose, treat, or produce medical advice.

See PROJECT_BRIEF.md for the specification and docs/claim-contracts.md for the
frozen claim contracts.
"""

MODEL_VERSION = "reliability-engine-0.1.0"
POLICY_VERSION = "claim-policy-0.1.0"

SCOPE_NOTE = (
    "This is a data-reliability assessment, not a medical assessment. It reports "
    "whether the available measurements support the statement, not whether you have "
    "any condition."
)

__all__ = ["MODEL_VERSION", "POLICY_VERSION", "SCOPE_NOTE"]
