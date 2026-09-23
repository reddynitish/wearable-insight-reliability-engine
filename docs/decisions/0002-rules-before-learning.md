# 0002 — Rules engine before any learned component

**Status:** accepted, 2026-09-23

## Context

The interesting part of the project is the learned reliability estimator. The useful part
may well be the deterministic gates. Building the model first would leave no baseline to
measure it against, and the evaluation protocol requires one.

## Decision

Ship a complete, tested, deterministic engine (v0.1) first. Learned components may only
be added after they beat the rules baseline (B2) on held-out subjects, per
`docs/evaluation-protocol.md` §5.

## Consequences

- v0.1 is fully useful on its own and is the fallback if modelling fails.
- `claim_support_probability` in v0.1 is a rules-derived score, **not** a calibrated
  probability, and is documented as such everywhere it appears.
- Hard gates stay deterministic permanently, even after a model is added. A model may
  lower confidence; it may never overrule a gate into `SHOW`. This is what "fail closed"
  means operationally.
- If the learned component never beats B2, the published result is that negative finding.
