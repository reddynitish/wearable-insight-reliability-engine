# 0005 — Seeded synthetic evidence for v0.1 validation

**Status:** accepted, 2026-09-23

## Context

The public datasets in `data/DATASETS.md` are not downloaded, their licences are not
verified, and the owner's personal Fitbit history is short. The v0.1 gates still need to
be exercised across every failure mode.

## Decision

v0.1 is validated on a seeded synthetic evidence generator plus a deterministic
corruption harness (`engine/corruptions.py`, `eval/`). Every reported v0.1 number is
labelled synthetic, in the output itself and not only in prose.

## Consequences

- Full coverage of failure modes that would be rare or absent in a small real sample, and
  a known ground-truth evidence state for every case, which is what makes USR computable
  at all.
- Synthetic results say nothing about real-world sensor behaviour. They test the decision
  logic, not the physiology. The README must not blur this.
- The generator's assumptions (Gaussian baselines, independent day effects) are wrong in
  ways real data is not. Stage 2 replaces them; until then, any claim of real-world
  performance is unsupported.
- The corruption harness carries the corruption identity in the manifest only, never into
  features, so it stays usable once learned components arrive.
