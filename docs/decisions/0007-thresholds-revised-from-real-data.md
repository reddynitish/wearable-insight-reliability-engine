# 0007 — Thresholds revised from real data, not re-argued

**Status:** accepted, 2026-09-23

## Context

Every threshold in `claim-policy-0.1.0` was reasoned from domain knowledge about sensor
error and baseline statistics. `docs/limitations.md` said plainly that they were the most
likely part of the system to be wrong, and the synthetic threshold sweep proved the
synthetic suite could not test them: all of its unsupportable cases were withheld by gates,
so the thresholds never decided one.

PMData (16 subjects, ~5 months each, Fitbit Versa 2, CC BY 4.0) is the first evidence about
any of them.

## Decision

Revise the thresholds that real data showed to be mis-specified, adopt the revision only
after measuring its effect and re-running the safety check, and record both. Bump to
`claim-policy-0.2.0`.

Three were wrong by *shape*, not by decimal places:

| threshold | was | now | what the data showed |
|---|---|---|---|
| RHR `max_baseline_sd` | 8.0 bpm | 5.0 | fired on 0.0% of 1451 subject-days; sat at 2× the observed maximum |
| activity `max_baseline_sd` | 45 min | 90 | fired on 48.7% of 2055 subject-days; the top reason activity claims were withheld |
| sleep-quality `max_baseline_sd` | 12 pts | 6.0 | fired on 0.3% of 1663 nights |
| RHR `min_baseline_days` | 14 | 28 | a personal baseline takes a median of 48 days to settle |

One was confirmed: the 3 bpm absolute-delta floor sits just above the p95 of real
consecutive-day change (2.32 bpm).

The comparison was run with a policy override (`evaluate(..., policy=...)`) rather than by
editing the registry, because a comparison run against a policy that has already been
adopted is not a comparison.

## Consequences

- Real decision coverage: activity 5.3% → 9.1%, resting heart rate 8.2% → 7.5%, sleep
  unchanged. The engine now answers roughly twice as many activity questions and slightly
  fewer heart-rate ones, both for stated reasons.
- Safety unchanged: 0.0000 unsupported-show across 1356 corrupted real subject-days under
  the revision. A revision that bought coverage by weakening gates would have been a
  regression, and that had to be measured rather than assumed.
- Nine tests failed on adoption because they encoded the old bands. That is the tests
  working: a threshold change should not be silently absorbable.
- **These values are now tuned to 16 largely athletic Norwegian adults on one device
  model.** That is better than tuned to nothing, and it is not general. The revision is
  defensible because it corrects gates that demonstrably could not act at all or fired on
  half of all days; it is not a claim that these numbers are right for everyone.
- `min_baseline_days` is a compromise, and worth revisiting. The data says 48 days; the
  product says a new user cannot wait seven weeks. 28 with a disclosed 14–27 band splits
  that, and the split is a judgement, not a finding.
