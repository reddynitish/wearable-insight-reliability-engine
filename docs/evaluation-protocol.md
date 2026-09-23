# Evaluation protocol

Written **before** any model exists, so that metrics and splits cannot be chosen after
seeing results. Changing anything here requires a dated entry in the change log.

## 1. What is being measured

The engine's job is to decide whether evidence supports a claim. So the unit of
evaluation is a **(claim, evidence bundle)** pair, not a signal sample, and the label is
**whether the claim was actually supportable given the evidence available at decision
time** — not whether the claim was true in the world.

That distinction is the crux of the whole evaluation. A claim can be true and still
unsupportable; if the engine returns `SHOW` for a true claim on the basis of two hours
of wear data and a four-day baseline, that is a **failure**, because the same reasoning
would have fired on a false claim with the same evidence. Labels therefore come from
the evidence-generating process, which we control, not from an outcome oracle.

## 2. Label definition

For every evaluation case we record, from the generator or dataset, the ground-truth
evidence state:

- `SUPPORTABLE` — the claim's contract is met and the effect is real in the generating
  process.
- `UNSUPPORTABLE_INSUFFICIENT` — evidence is missing, stale, low-coverage, or the
  baseline is immature, so no responsible decision is possible.
- `UNSUPPORTABLE_CONTRADICTED` — the evidence points the other way.
- `UNSUPPORTABLE_UNTRUSTWORTHY` — evidence is present but corrupted (artifact,
  dropout, conflict) such that any conclusion drawn from it is unreliable.

Mapping to decisions, for scoring:

| Ground truth | Correct decision(s) | Acceptable | Failure |
|---|---|---|---|
| `SUPPORTABLE` | `SHOW` | `SHOW_WITH_WARNING` | `WAIT_FOR_MORE_DATA` (over-abstention), `REJECT` (serious) |
| `UNSUPPORTABLE_INSUFFICIENT` | `WAIT_FOR_MORE_DATA` | `REJECT` | **`SHOW`, `SHOW_WITH_WARNING`** |
| `UNSUPPORTABLE_CONTRADICTED` | `REJECT` | `WAIT_FOR_MORE_DATA` | **`SHOW`, `SHOW_WITH_WARNING`** |
| `UNSUPPORTABLE_UNTRUSTWORTHY` | `WAIT_FOR_MORE_DATA` or `REJECT` | — | **`SHOW`, `SHOW_WITH_WARNING`** |

## 3. Primary metric

**Unsupported-show rate (USR)**

```
USR = P(decision in {SHOW, SHOW_WITH_WARNING} | ground truth is any UNSUPPORTABLE_*)
```

`SHOW_WITH_WARNING` counts as a show. A warning next to an unjustified conclusion is
still an unjustified conclusion on the user's screen; treating it as a partial credit
would let the engine game its own headline number.

A secondary, stricter variant is reported alongside it:

```
USR_hard = P(decision == SHOW | ground truth is any UNSUPPORTABLE_*)
```

## 4. Secondary metrics

- **Decision coverage**: fraction of cases answered with `SHOW` or `SHOW_WITH_WARNING`.
  USR is meaningless without it — an engine that always abstains has USR 0.
- **Selective risk vs coverage curve**, sweeping the abstention thresholds; the headline
  comparison is risk at matched coverage, never a single operating point.
- **Macro F1** over the four decisions, plus per-class precision/recall, with `SHOW`
  precision called out separately.
- **Calibration**: Brier score and expected calibration error on
  `claim_support_probability`, with a reliability diagram. ECE is reported with its bin
  count because it is bin-sensitive.
- **Robustness**: USR and coverage broken down by corruption type × severity.
- **External transfer**: every metric recomputed on a dataset never used for training.
- **Explanation fidelity**: fraction of emitted reason codes that are present in the
  evidence trace, and fraction of trace-supported limitations that the explanation
  mentions. Target is 1.0 on the first; a value below 1.0 is a bug, not a metric.
- **Latency**: p50/p95/p99 per decision, single process, no warm cache.

## 5. Baselines (all five are required before any learned result is reported)

| # | Baseline | Purpose |
|---|---|---|
| B0 | always `SHOW` | the status quo the product is arguing against; establishes the USR ceiling |
| B1 | data-present / data-missing check only | the cheap thing most apps actually do |
| B2 | deterministic claim policy (v0.1 rules engine) | the honest engineering baseline |
| B3 | learned model, no abstention | isolates the value of abstention |
| B4 | hybrid: hard gates + calibrated model + tuned abstention | the proposed system |

**B4 must beat B2 on held-out subjects to justify any machine learning at all.** If it
does not, the documented outcome is "the rules engine was sufficient" and the learned
component is dropped. That negative result gets published in the README, not buried.

## 6. Splits

- **Subject-held-out, always.** Windows from one subject appear in exactly one of
  train / validation / test.
- **Session-held-out within subject** where sessions exist, so protocol memorisation is
  also blocked.
- **Dataset-held-out** for the external check: at least one dataset is never trained on.
- **Calibration on its own subjects**, disjoint from both train and test. Calibrating on
  the test set is the single easiest way to produce a fake calibration result.
- **Corruption labels never become features.** The corruption transform's identity and
  severity are stored in the manifest for analysis only, and a test asserts that no
  feature name derives from them.
- Split assignment is by hash of the pseudonymous subject id with a fixed seed, recorded
  in the evaluation manifest, so splits are reproducible and stable as data is added.

## 7. Reporting rules

- Confidence intervals on every headline number: bootstrap over **subjects**, not over
  samples, because samples within a subject are not independent. 2000 resamples,
  percentile interval, seed recorded.
- Subject-level distributions, not only pooled means: report median and worst-subject.
- Synthetic-corruption results and natural-failure results are reported in separate
  tables with separate captions.
- Every number in the README traces to a committed manifest and a rerunnable command.
- No number appears in a resume bullet until it appears in a committed evaluation
  artifact produced by a command that a reader can run.

## 8. Current state

| Item | Status |
|---|---|
| B0, B1, B2 implementable | yes, v0.1 |
| B3, B4 | not started; require public datasets |
| Public-dataset results | none exist |
| Synthetic-corruption results | see `eval/` output; labelled synthetic |
| Calibration results | none exist; `claim_support_probability` in v0.1 is a deterministic score and is **not** a calibrated probability |

The last row matters: v0.1 reports `claim_support_probability` because it is part of the
frozen response contract, but it is a rules-derived score. It must not be described as
calibrated until Stage 3 produces a calibration curve on held-out subjects.

## Change log

| date | change |
|---|---|
| 2026-09-23 | protocol written before any modelling; metrics and splits frozen |
