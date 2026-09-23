# Related work (scoping review)

**Verification status: UNVERIFIED AI-ASSISTED DRAFT.** This document was drafted with AI
assistance to map the territory and to state the novelty claim honestly. The works named
below are well known in their fields, but **no citation here has been checked against its
primary source in this repository yet**, and no bibliographic detail (year, venue, exact
title, exact claim) should be repeated elsewhere until the checklist in §6 is complete.
Its purpose right now is to constrain the project's novelty claim, not to serve as a
reference list.

## 1. Why this review exists

The project brief makes a narrow novelty claim, and it is worth restating before
surveying anything:

> Signal-quality assessment, missing-data checks, and confidence scoring already exist.
> The hypothesis is that combining them into a reusable, typed, **claim-aware** decision
> layer with explicit abstention and fail-closed behaviour is useful.

So the review's job is adversarial: find the prior art that would make this redundant.
Four literatures are relevant, and none of them individually covers the claim-level
decision.

## 2. Signal quality indices for wearable physiological data

There is a mature body of work on deciding whether a *window of signal* is usable —
template-matching and beat-detection agreement for PPG and ECG, accelerometer-based
motion-artifact detection, and perfusion/skin-contact heuristics. Orphanidou and
colleagues' work on PPG/ECG signal quality indices is the standard reference point;
motion-artifact reduction using a co-located accelerometer is a long-standing technique,
and PPG-DaLiA exists precisely to support it.

**What it gives this project:** the Stage-2 estimators, more or less directly.
**What it does not give:** any notion of a claim. An SQI says "this 8-second window is
clean". It does not say "a statement about your resting heart rate today is supportable".

## 3. Data quality frameworks and dimensions

Data-quality dimensions — completeness, timeliness, consistency, accuracy, validity —
are standard in data management, and health-data-specific frameworks (notably the
Kahn et al. harmonised terminology for EHR data quality, widely used in the OHDSI
community) formalise conformance / completeness / plausibility checks. Wearable-specific
work on wear-time validation in actigraphy (the Choi and Troiano wear-time algorithms
are the classical examples) addresses coverage directly.

**What it gives this project:** the vocabulary and the coverage/freshness/plausibility
checks, which the engine implements per claim rather than per table.
**What it does not give:** typed, machine-consumable, per-assertion output. These
frameworks produce dataset-level quality reports for analysts. The engine produces a
runtime decision for application code on one claim about one person in one window.

## 4. Uncertainty calibration

Modern classifiers are typically miscalibrated; post-hoc correction (Platt scaling,
isotonic regression, temperature scaling) is the standard remedy, and proper scoring
rules plus reliability diagrams are the standard measurement. Niculescu-Mizil and
Caruana's comparison of calibration methods, and Guo and colleagues' work on
temperature scaling for modern neural networks, are the usual entry points.

**What it gives this project:** Stage-3 methodology, and the reason the evaluation
protocol demands a separate calibration split.
**What it does not give:** a decision policy. A calibrated probability is an input to the
engine, not its output.

## 5. Selective prediction, abstention, and conformal methods

Classification with a reject option goes back to Chow's early work on the error/reject
tradeoff; the modern treatment (El-Yaniv and Wiener's selective-classification framework,
and later neural variants such as SelectiveNet) formalises risk-coverage curves, which is
exactly the tradeoff surface this project reports. Conformal prediction provides
distribution-free coverage guarantees on prediction sets, with Vovk and colleagues'
algorithmic-learning-in-a-random-world line of work as the foundation and more recent
tutorial treatments making it practical.

**What it gives this project:** the correct way to present abstention (risk vs coverage,
never a single threshold), and a candidate upgrade path for Stage 4 — conformal
thresholds would let the engine make a formal statement about its `SHOW` error rate.
**What it does not give:** the domain semantics. Selective prediction abstains from a
*label*; this engine abstains from *displaying an assertion*, and must additionally say
which evidence was missing and what would resolve it.

## 6. Adjacent product-shaped work worth checking before any novelty claim

These are the things most likely to make the novelty claim wrong, and they are the
priority for verification:

1. **Apple / Fitbit / Garmin / Oura insight-gating behaviour.** All of them visibly
   withhold metrics until a baseline exists ("Oura needs N nights"). This is claim-aware
   abstention in production. The open question is whether any of it is exposed as a
   reusable, typed layer, or whether it is per-feature product logic. Verify from
   published developer documentation only — not from reverse engineering.
2. **Open-mHealth / IEEE 1752 mobile health schemas.** These standardise wearable data
   representation. If one of them already carries a per-assertion reliability envelope,
   the canonical model here should adopt it rather than invent one.
3. **HL7 FHIR Observation `dataAbsentReason`, `status`, and device-metric quality
   fields.** FHIR already models "this measurement is absent, and here is why". The
   engine's reason codes should map onto FHIR concepts where they overlap; failing to
   check this would be the most embarrassing gap in the design.
4. **Clinical decision support with deferral / "insufficient evidence" outputs**, and
   the machine-learning-safety literature on out-of-distribution detection and
   fail-closed design.
5. **Data-validation libraries** (Great Expectations, Pandera, Deequ, Evidently). They do
   typed data-quality assertions well. The distinction to verify is that they validate
   *datasets against expectations*, not *claims against available evidence*, and produce
   no abstention decision for a runtime caller.

## 7. Provisional novelty position

Stated conservatively, and to be rewritten once §6 is verified:

- The **components** are established: SQIs, data-quality dimensions, wear-time
  validation, calibration, selective prediction.
- Claim-aware gating **exists in products**, apparently as bespoke per-feature logic.
- What appears not to exist as a reusable artifact is the **combination**: a versioned,
  typed, claim-level contract over evidence, with deterministic hard gates, explicit
  abstention, machine-readable reason codes, and a traceable evidence-to-explanation
  path, evaluated with unsupported-show rate as the primary metric.

If verification shows that this combination is published or shipped as a reusable layer,
the honest move is to reposition the project as a careful open implementation and
evaluation of a known idea, and say so in the README. The evaluation is the contribution
either way.

## 8. Verification checklist

| # | Item | Status |
|---|---|---|
| 1 | Every citation in §§2–5 checked against its primary source; exact title/venue/year recorded | not started |
| 2 | Vendor baseline-gating behaviour documented from official developer docs | not started |
| 3 | Open-mHealth / IEEE 1752 reliability fields reviewed | not started |
| 4 | FHIR `dataAbsentReason` and device-metric quality mapped to this engine's reason codes | not started |
| 5 | Data-validation library comparison table written | not started |
| 6 | Literature search rerun with recorded queries, databases, and dates | not started |
| 7 | Novelty paragraph in README rewritten to match findings | not started |

Until all seven are done, the README says the novelty claim is unverified. It currently
does.

## Change log

| date | change |
|---|---|
| 2026-09-23 | initial AI-assisted draft; all citations unverified |
