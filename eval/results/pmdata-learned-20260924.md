# Learned baselines B3 and B4 on real PMData evidence

**Verdict: Drop the learned component; ship the rules engine.**

- generated `2026-09-24T15:07:10.763777+00:00`
- engine `reliability-engine-0.1.0`, policy `claim-policy-0.2.0`
- 15765 rows, 16 subjects, 4-fold subject-held-out cross-validation
- target: evidence adequacy under the claim contract (17.8% positive)

Reproduce:

```bash
./.venv/bin/python -m eval.learned
```

## Results

| baseline | coverage | unsupported-show | ROC-AUC | Brier | ECE (10-bin) |
|---|---|---|---|---|---|
| **B2** rules engine | 0.0187 | **0.0000** | — | — | — |
| **B3** logistic_regression, no gates | 0.2259 | **0.2367** | 0.9730 | 0.0420 | 0.0066 |
| **B4** logistic_regression, hybrid | 0.0186 | **0.0000** | 0.9730 | 0.0420 | 0.0066 |
| **B3** gradient_boosting, no gates | 0.2304 | **0.2333** | 0.9792 | 0.0377 | 0.0034 |
| **B4** gradient_boosting, hybrid | 0.0187 | **0.0000** | 0.9792 | 0.0377 | 0.0034 |

At the engine's own operating point (unsupported-show 0.0000), the best model reaches 0.0132 coverage against the engine's 0.0187 — a change of -0.0055.

## What this means

**The models learned the contract well.** ROC-AUC of 0.97–0.98 on held-out subjects says the raw features carry nearly all the information the gates use, and that a model can rank adequate evidence above inadequate evidence almost perfectly. The features are not the problem.

**B3 is unsafe.** Without gates, the learned estimator displays roughly a quarter of inadequate evidence. Ranking well is not the same as deciding well: a probabilistic score has no way to express 'this requirement was not met', so it trades safety for coverage at every threshold.

**B4 is identical to the engine, not better than it.** The hybrid can only choose among cases the gates already allowed through, so it reproduces the engine's decisions and adds nothing. And at the engine's own zero-risk operating point the model is slightly *worse* on coverage.

**Why, and this is the general point:** when the decision rule is a known deterministic function of observable features, a learned approximation of it can only add error. There is no hidden signal for the model to discover, because the contract is written down. Machine learning earns its place where the target is *not* a known function of the inputs — which is the Stage-2 problem (is this PPG window trustworthy?), not this one.

**The one thing the learned path did offer** is calibration. The engine's `claim_support_probability` is explicitly not calibrated; the gradient-boosted model reaches a Brier score of 0.0377 and an ECE of 0.0034 on held-out subjects. That is worth keeping in mind if the product ever wants a graded confidence rather than a typed decision. It is not a reason to put a model in the decision path.

## Limits of this experiment

- Labels come from `engine/measure.py`, which reads the same policy thresholds the engine enforces. A model given the *band-scored* features would have been handed the answer, so only raw measurements were used — but the target is still a deterministic function of those measurements, and that is precisely why the result came out as it did. This experiment can show that ML does not help here; it cannot show that ML would not help against a target with real label noise.
- 16 subjects, four folds. Small.
- Calibration was fitted on subjects disjoint from both training and test, which is the correct procedure, but with 16 subjects each calibration set is 3–4 people.

