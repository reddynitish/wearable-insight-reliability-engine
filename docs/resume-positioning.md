# Resume positioning

PROJECT_BRIEF.md section 20 is explicit: do not write these as completed achievements until
the corresponding evidence exists. This file separates what the repository currently
demonstrates from what it does not, so that nothing unsupported leaks into an application.

Rule of thumb: **a number may appear in a bullet only if a reader can reproduce it with a
command from this repository, and only with the qualifier the artifact itself carries.**

## Claimable now, exactly as written

> Built a typed reliability engine for wearable insights that evaluates coverage,
> freshness, signal quality, cross-signal consistency, and personal-baseline maturity
> before returning `SHOW` / `SHOW_WITH_WARNING` / `WAIT_FOR_MORE_DATA` / `REJECT`, with
> machine-readable reason codes and an explanation traceable to a structured evidence
> record. Six versioned claim contracts, 36 reason codes, FastAPI service, 276 tests.

> Designed a leakage-safe evaluation harness — 17 seeded data-quality failure modes across
> three severities, with ground-truth labels produced by a second, independent
> implementation of the claim contract so the labels do not come from the code under test.
> On synthetic evidence it reduced unsupported insight display from 1.0000 (always-show) and
> 0.9583 (data-present check) to 0.0000 at 0.27 decision coverage, with zero
> over-abstention, across 3057 cases and 30 held-out synthetic subjects.

> Showed by threshold sweep that 100% of the engine's abstentions on that suite come from
> deterministic gates rather than tunable thresholds, and documented the consequence: the
> synthetic suite cannot validate the threshold values, so it is a regression harness rather
> than a benchmark.

> Established through direct BLE investigation that the target device exposes no accessible
> motion stream — an ~8.31 s unbonded ATT service window and an authenticated DTLS channel
> requiring device credentials — and redirected the project from sensor access to evidence
> reliability on the supported API path.

**The qualifier "on synthetic evidence" is not optional in the second bullet.** Dropping it
turns a reproducible engineering result into a false claim about real-world performance.

## Not claimable, and why

| Tempting claim | Why not |
|---|---|
| "Reduced unsupported insights by X% on wearable data" | No public dataset has been downloaded. The only data is synthetic. |
| "Calibrated confidence estimates" | No calibration exists. Every response carries `support_is_calibrated: false`. |
| "Trained a signal-quality model" | No learned component exists. |
| "Validated against polysomnography / chest ECG" | No reference-standard data has been touched. |
| "Novel claim-level reliability layer" | `docs/related-work.md` lists seven verification tasks, none started. |
| "Evaluated on N wearable datasets" | N is zero. |
| "Production-ready health service" | No persistence, no deployment, no retention path, no clinical review. |
| "Improved accuracy over a machine-learning baseline" | Baselines B3 and B4 are unimplemented by design, and declared so. |

## The template to fill in once evidence exists

Section 20 of the brief, with the placeholders left visible so they cannot be filled in
from memory:

> Reduced unsupported insight display by **[measured percentage]** at **[measured decision
> coverage]** versus **[named baseline]** across subject-held-out tests on **[datasets]**.

> Designed a FastAPI service and reproducible evaluation pipeline spanning **[number]**
> claim policies, **[number]** wearable datasets, and **[number]** controlled data-quality
> failure modes.

What has to happen first, in order:

1. Verify the `data/DATASETS.md` rows against primary sources; download PPG-DaLiA.
2. Build the Stage-2 signal-quality estimator against its chest-ECG reference.
3. Implement B3 and B4 with subject-held-out splits and a separate calibration split.
4. Publish the calibration curve and the risk-coverage curve — including if the learned
   component loses to the rules baseline, which is a publishable result and a real one.
5. Verify `docs/related-work.md` and rewrite the novelty sentence to match the findings.

## Interview-safe summary

The honest one-paragraph version, which is stronger than an inflated one because every
sentence survives a follow-up question:

> I built a claim-level trust layer for wearable data: given a proposed statement and the
> evidence available, it returns a typed decision about whether that statement is
> supportable, with reason codes and a traceable evidence record. The design point is that
> deterministic gates outrank any score, so a future model can lower confidence but cannot
> talk the system into displaying a claim whose evidence failed a hard requirement — I
> verify that with property tests. It is evaluated on a seeded corruption harness with 17
> failure modes, labelled by a separate implementation of the contract so the evaluation
> is not circular. That evaluation is synthetic, which makes it a regression harness rather
> than a benchmark; the public-dataset validation and the learned component are the next
> stage and are not done. The project started as an attempt to read raw motion off a Fitbit
> Air, and the BLE investigation showed that is not possible on the supported path, which is
> what redirected it here.
