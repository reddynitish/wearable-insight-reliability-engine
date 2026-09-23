# 0004 — Decision confidence is separate from claim support

**Status:** accepted, 2026-09-23

## Context

The response contract carries both `confidence` and `claim_support_probability`. Early
drafts of the engine conflated them, which produced the nonsensical output "confidence
0.28, decision WAIT_FOR_MORE_DATA" — low confidence in an abstention the engine was in
fact certain about.

## Decision

- `claim_support_probability` — how strongly the evidence supports the claim itself.
- `confidence` — how confident the engine is **in the decision it returned**.

An abstention backed by unambiguous evidence of insufficiency (no data at all, sync not
finished) is a *high*-confidence `WAIT_FOR_MORE_DATA`. Confidence is lowest when support
sits near a threshold boundary.

## Consequences

- Confidence is computed from distance to the nearest decision boundary and from how
  decisive the triggering gates were, not from the support score.
- A caller can use low confidence as a signal to re-evaluate after the next sync, and
  can use it to decide whether to log the decision for review.
- Two numbers is more contract surface than one, and a reader can still misread them, so
  both are defined in the README and in `docs/claim-contracts.md` §4.
