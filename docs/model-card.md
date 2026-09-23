# Model card — Wearable Insight Reliability Engine v0.1

There is no trained model in v0.1. This card documents the deterministic decision system
that occupies the model's place, because the thing that needs describing is the decision
procedure, and calling it "just rules" would hide the fact that it encodes specific,
falsifiable thresholds about people's physiology.

## Overview

| | |
|---|---|
| Name | Wearable Insight Reliability Engine |
| Version | `reliability-engine-0.1.0` / policy `claim-policy-0.1.0` |
| Date | 2026-09-23 |
| Type | Deterministic rules engine with versioned claim contracts. No learned component. |
| Input | A proposed claim, a target window, canonical observations, per-day baseline values, and collection context |
| Output | One of `SHOW`, `SHOW_WITH_WARNING`, `WAIT_FOR_MORE_DATA`, `REJECT`, with evidence scores, reason codes, a decision trace, an explanation, and retry requirements |
| Licence | See repository |

## Intended use

**In scope.** Deciding whether the evidence available at decision time supports one of six
predefined claims about a wearable-derived metric, for developers and product teams
building wearable, sleep, fitness, wellness, or remote-monitoring software, and for
research pipelines deciding whether a derived observation is reportable.

**Out of scope, and unsafe if used this way.** Medical diagnosis, treatment decisions,
triage, screening, replacing a clinician, or any setting where an abstention would delay
care. The engine has no notion of clinical risk: its `REJECT` means "the evidence does not
support this statement", never "you are fine". It has never been evaluated on a clinical
population, against a clinical reference standard, or in a deployed product.

**Also out of scope.** Claims it has no policy for. Those return `REJECT` with
`UNSUPPORTED_CLAIM_TYPE` rather than a best guess.

## How it decides

1. **Normalisation.** Deduplicate, sort, range-check, and place observations relative to
   the target window. Everything removed is counted into the trace; nothing is imputed.
2. **Evidence features.** Six groups: coverage, freshness, signal quality, cross-signal
   consistency, baseline maturity, and effect size. `None` means "not determinable" and is
   never replaced by a default.
3. **Deterministic gates.** Conditions from the claim contract that force `REJECT`,
   `WAIT_FOR_MORE_DATA`, a nonfatal warning, or a recorded note.
4. **Support score.** A transparent function of effect size scaled by evidence quality:
   `support = effect_component × (0.35 + 0.65 × weighted_evidence_mean)`, with the effect
   anchored so that each policy's named `contradict_z` / `warn_z` / `show_z` land on its
   own decision cut points when evidence is perfect.
5. **Aggregation.** Reject gates outrank wait gates, which outrank the score; a warn gate
   caps a `SHOW` at `SHOW_WITH_WARNING`; the policy's ceiling caps everything.
6. **Explanation.** Rendered from trace fields only.

### Scoring conventions that are easy to misread

- Each evidence score is `0.5` at exactly the policy's minimum acceptable level, `1.0` at
  or above its comfortable level, below `0.5` beneath the floor. **Not probabilities.**
- `confidence` is confidence in the **decision**. `claim_support_probability` is support
  for the **claim**. A high-confidence abstention on strong-looking evidence is a normal
  and correct output.
- `claim_support_probability` is **not calibrated**. Every response says so via
  `support_is_calibrated: false`.

## Metrics

The primary metric is the **unsupported-show rate**: `P(displayed | evidence did not
support the claim)`, counting `SHOW_WITH_WARNING` as displayed. Reported alongside
decision coverage, because abstaining on everything would score a perfect zero.

### Results — synthetic evidence only

3057 scored cases, 30 synthetic subjects, 6 claim types, 17 corruption types × 3
severities. 63 further cases excluded as borderline (no contract breach, effect between
the contradiction and display thresholds).

| baseline | unsupported-show (95% CI) | over-abstention | coverage | macro F1 |
|---|---|---|---|---|
| always show | 1.0000 [1.0000, 1.0000] | 0.0000 | 1.0000 | 0.14 |
| data-present check | 0.9583 [0.9436, 0.9723] | 0.0000 | 0.8803 | 0.14 |
| this engine | 0.0000 [0.0000, 0.0000] | 0.0000 | 0.2699 | 0.78 |

Intervals bootstrap over subjects. Reproduce with
`./.venv/bin/python -m eval.run_eval --seeds 5`.

**Interpretation.** Labels come from `engine/measure.py`, an independent second
implementation of the same written contract, so a zero rate is agreement between two
readings of one specification — a regression result, not a benchmark result. The evidence
is Gaussian-generated with no sensor-error model. Nothing here measures real-world
performance.

### Metrics that do not exist yet

Calibration curves, expected calibration error, Brier score, selective-risk-versus-coverage
curves, out-of-distribution performance, subgroup performance, and any result on real
wearable data. All require the public datasets in `data/DATASETS.md`, none of which has
been downloaded.

## Training data

None. There is no trained component. The thresholds in `docs/claim-contracts.md` were set
from domain reasoning about sensor error, reporting conventions, and baseline statistics —
**not fitted to outcomes**. They are the most likely thing in this system to be wrong for
real people, and Stage 2 of the plan exists to test them.

## Evaluation data

Seeded synthetic evidence from `engine/synth.py`, degraded by `engine/corruptions.py`. See
`docs/data-card.md`. Known-wrong assumptions: Gaussian baselines, independent days, no
sensor-error model, no population variation, no device heterogeneity.

## Ethical considerations

- **Health framing.** Outputs concern data quality, not health status. Explanations
  carry a scope note where a claim is displayed, and a test asserts no explanation
  contains diagnostic or advisory language.
- **Abstention has a cost.** `WAIT_FOR_MORE_DATA` is the safe answer for an insight
  feature and the wrong answer if anyone ever puts this in a care pathway. It must not be.
- **Privacy.** Pseudonymous subject identifiers, enforced at the schema boundary
  (an email address or phone number is rejected). No credential or personal export is read
  by the engine; the Google Health adapter cannot make a network call. Decision traces
  contain measurements, so they are personal data wherever they are logged.
- **Fairness.** Untested. The engine compares each person only against their own baseline,
  which avoids population-reference bias by construction, but PPG signal quality is known
  to vary with skin tone, tattoos, and perfusion, and a quality gate that fires more often
  for some people would ration insights unevenly. Nothing here measures that, and no
  claim of fairness is made. Testing it needs datasets with the relevant metadata.
- **Failure mode to watch.** Systematic over-abstention for a subgroup would look like
  safety in the aggregate metrics while denying that subgroup any insight at all. Coverage
  is therefore reported beside every risk number, and should be reported per subgroup as
  soon as subgroup metadata exists.

## Maintenance

Any threshold change requires a new `policy_version`, a row in the change log in
`docs/claim-contracts.md`, and a re-run of the evaluation. Decision traces carry both
`model_version` and `policy_version` so a stored decision can be reproduced.

## Changelog

| version | date | change |
|---|---|---|
| `reliability-engine-0.1.0` | 2026-09-23 | initial deterministic engine, six claim policies |
