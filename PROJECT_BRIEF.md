# Wearable Insight Reliability Engine — Project Brief

## 1. Executive summary

Build a domain-specific decision engine that answers one question:

> **Does the available wearable evidence responsibly support this health or fitness claim right now?**

Wearable applications routinely turn incomplete, stale, noisy, or contradictory sensor data into confident-looking conclusions such as “you recovered poorly,” “your sleep was bad,” or “your resting heart rate is elevated.” This project sits between wearable data pipelines and user-facing insights. It checks the evidence behind a proposed claim and returns a typed, machine-readable decision:

- `SHOW`
- `SHOW_WITH_WARNING`
- `WAIT_FOR_MORE_DATA`
- `REJECT`

The response also includes calibrated confidence, exact reason codes, evidence quality, and a human-readable explanation. It is not another recovery score, dashboard, chatbot, or diagnostic model. It is a reusable **claim-level trust layer** for any wearable, sleep, fitness, wellness, or health application.

Working title: **Wearable Insight Reliability Engine**. The product name remains open.

## 2. Why this is worth building

Most wearable products focus on producing more insights. This project focuses on knowing when an insight should **not** be shown. That is useful beyond WHOOP because every wearable platform faces the same problems:

- sensors are noisy during movement;
- syncs can be delayed or incomplete;
- devices can be worn loosely or removed;
- different signals can disagree;
- a new user may not yet have a stable personal baseline;
- a conclusion may require evidence that the device did not collect.

The engine can reduce false alarms, misleading coaching, and unjustified certainty while giving product code a stable API instead of scattered one-off quality checks.

The novelty claim must be stated carefully. Signal-quality assessment, missing-data checks, and confidence scoring already exist. The research hypothesis is that there is value in combining them into a reusable, typed, claim-aware decision layer with explicit abstention and fail-closed behavior. We must validate that experimentally; we must not claim that no one has ever attempted related work.

## 3. Product users

Primary users are developers and product teams building:

- wearable and fitness applications;
- sleep and recovery applications;
- remote monitoring and wellness tools;
- coaching systems that convert sensor data into recommendations;
- research pipelines that need to decide whether a derived observation is publishable.

The end user benefits indirectly: fewer unreliable claims and clearer explanations when data is insufficient.

## 4. Scope

### In scope

- Evaluate whether evidence supports a proposed, predefined claim.
- Check coverage, freshness, signal quality, cross-signal consistency, baseline maturity, and claim-specific requirements.
- Use deterministic safety rules plus a learned reliability model.
- Return structured decisions that application code can consume.
- Abstain when evidence is insufficient.
- Explain every decision using traceable evidence and reason codes.
- Train and evaluate using public wearable datasets, then demonstrate on the owner's Fitbit/Google Health data.
- Expose the engine through a documented API and a small demonstration interface.

### Out of scope

- Medical diagnosis or treatment advice.
- Replacing a clinician.
- Inventing missing measurements.
- A generic conversational health assistant.
- A universal recovery/readiness score.
- Claiming raw Fitbit Air accelerometer access. Existing research in this repository shows that raw motion telemetry is not available through the supported API and the private BLE path is inaccessible without device credentials.
- Bypassing pairing, extracting secrets, guessing keys, or defeating device security.
- Training a large language model from scratch.

## 5. Core contract

The input is a proposed claim plus the evidence available to support it.

Example request:

```json
{
  "subject_id": "demo-user-001",
  "claim": {
    "type": "RESTING_HEART_RATE_ELEVATED",
    "statement": "Your resting heart rate is elevated today.",
    "target_window": {
      "start": "2026-09-22T00:00:00Z",
      "end": "2026-09-23T00:00:00Z"
    }
  },
  "observations": [
    {
      "signal": "resting_heart_rate",
      "value": 72,
      "unit": "bpm",
      "measured_at": "2026-09-23T11:00:00Z",
      "source": "google_health"
    }
  ],
  "context": {
    "sync_completed_at": "2026-09-23T12:00:00Z",
    "device_worn_minutes": 1180,
    "baseline_days": 4
  }
}
```

Example response:

```json
{
  "decision": "WAIT_FOR_MORE_DATA",
  "confidence": 0.93,
  "claim_support_probability": 0.28,
  "reason_codes": [
    "BASELINE_NOT_MATURE",
    "INSUFFICIENT_HISTORICAL_COVERAGE"
  ],
  "evidence": {
    "coverage_score": 0.81,
    "freshness_score": 0.98,
    "signal_quality_score": 0.76,
    "consistency_score": 0.69,
    "baseline_maturity_score": 0.13
  },
  "explanation": "Today's measurement is recent, but four baseline days are not enough to determine a reliable personal elevation.",
  "retry": {
    "recommended": true,
    "after": "P7D",
    "required_evidence": ["at_least_14_valid_baseline_days"]
  },
  "model_version": "reliability-engine-0.1.0",
  "policy_version": "claim-policy-0.1.0"
}
```

The explanation must be derived from the structured decision trace. An optional language model may rewrite an explanation for clarity, but it must not change the decision, introduce evidence, diagnose a condition, or give unsupported advice.

## 6. Initial claim families

Start with a narrow set of claims for which requirements can be defined and tested:

1. `RESTING_HEART_RATE_ELEVATED`
2. `SLEEP_DURATION_LOW`
3. `SLEEP_QUALITY_REDUCED`
4. `ACTIVITY_LOAD_HIGH`
5. `RECOVERY_EVIDENCE_INCOMPLETE`
6. `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION`

Each claim type needs a versioned claim policy specifying:

- required signals;
- acceptable sampling/aggregation resolution;
- minimum coverage;
- maximum data age;
- minimum personal-baseline length;
- known confounders;
- consistency checks;
- hard rejection conditions;
- thresholds for showing, warning, waiting, and rejecting.

Avoid starting with broad claims such as “you are stressed” or “you will get sick.” Those are harder to ground and carry greater risk.

## 7. Decision semantics

### `SHOW`

Evidence meets the claim policy, hard safety checks pass, and calibrated support is above the display threshold.

### `SHOW_WITH_WARNING`

The main claim is supportable, but one or more nonfatal limitations must be disclosed. Example: slightly reduced overnight coverage with otherwise consistent evidence.

### `WAIT_FOR_MORE_DATA`

The engine cannot responsibly decide yet, but additional collection, synchronization, or baseline history could resolve the uncertainty. This is an explicit abstention, not a failure.

### `REJECT`

The evidence contradicts the claim, violates a hard requirement, is too unreliable to rescue, or the claim is outside the engine's supported scope.

The most important product metric is not ordinary accuracy alone. It is the rate at which the system incorrectly returns `SHOW` for an unsupported claim. The engine should fail closed when uncertainty is material.

## 8. Evidence dimensions and reason codes

### Coverage

- expected versus observed samples/windows;
- total wear time;
- overnight coverage;
- missing intervals and whether they overlap the claim window.

Possible reasons: `INSUFFICIENT_COVERAGE`, `LONG_DATA_GAP`, `DEVICE_NOT_WORN`.

### Freshness

- time since measurement;
- time since successful synchronization;
- whether the target window is complete.

Possible reasons: `STALE_DATA`, `SYNC_INCOMPLETE`, `WINDOW_NOT_CLOSED`.

### Signal quality

- motion artifact;
- sensor dropout;
- implausible ranges or transitions;
- agreement with reference signals where available;
- upstream quality flags.

Possible reasons: `MOTION_ARTIFACT`, `SENSOR_DROPOUT`, `IMPLAUSIBLE_VALUES`, `LOW_SIGNAL_QUALITY`.

### Consistency

- agreement between related signals;
- conflicts between summaries and raw/interval data;
- unexplained discontinuities;
- compatibility with context such as exercise and wear state.

Possible reasons: `CONFLICTING_SIGNALS`, `SUMMARY_DETAIL_MISMATCH`, `CONTEXT_CONFLICT`.

### Baseline maturity

- number of valid baseline days;
- stability and representativeness of the baseline;
- changes in device, wear behavior, schedule, or timezone.

Possible reasons: `BASELINE_NOT_MATURE`, `BASELINE_UNSTABLE`, `BASELINE_SHIFT_DETECTED`.

### Claim support

- required evidence present;
- effect size relative to measurement uncertainty and baseline variance;
- whether alternative explanations remain unresolved.

Possible reasons: `MISSING_REQUIRED_SIGNAL`, `EFFECT_BELOW_RELIABLE_THRESHOLD`, `CLAIM_CONTRADICTED`, `UNSUPPORTED_CLAIM_TYPE`.

## 9. System architecture

```text
Dataset / Google Health adapters
              |
              v
Canonical time-series and metadata model
              |
              v
Windowing + evidence feature extraction
              |
       +------+------+
       |             |
       v             v
Deterministic     Learned reliability
hard gates        and quality estimators
       |             |
       +------+------+
              v
Versioned claim policy + calibrated decision thresholds
              |
              v
Typed decision, evidence trace, reason codes, explanation
              |
              v
REST API + evaluation dashboard/demo
```

Suggested stack:

- Python for ingestion, modeling, and service code;
- FastAPI with Pydantic schemas for the typed API;
- Polars or pandas for offline pipelines;
- scikit-learn for strong tabular baselines;
- PyTorch only where time-series models materially outperform simpler approaches;
- PostgreSQL for metadata and decisions, with Parquet for research datasets;
- pytest for tests;
- MLflow or a lightweight local experiment registry for reproducibility;
- Docker for a reproducible demo.

Technology choices are provisional. Prefer the smallest stack that can produce trustworthy evaluation.

## 10. Modeling strategy

### Stage 1: rules-only baseline

Implement the canonical schema, claim policies, coverage/freshness checks, and decision API first. A strong rules-only baseline is required to determine whether machine learning adds value.

### Stage 2: signal and window quality estimators

Train models to estimate whether a signal window is trustworthy using datasets that include higher-quality reference measurements. Examples include wrist PPG compared with chest ECG and wearable sleep estimates compared with polysomnography.

### Stage 3: claim-level reliability estimator

Combine quality features, coverage, freshness, consistency, baseline maturity, and claim requirements into calibrated claim-support estimates. Begin with interpretable models such as logistic regression and gradient-boosted trees. Compare against time-series architectures only when justified by results.

### Stage 4: selective prediction

Tune abstention thresholds so that the engine can trade coverage for safety. Measure performance at multiple operating points instead of choosing a single arbitrary threshold.

### Stage 5: explanation layer

Generate explanations from the evidence trace and predefined reason templates. If an LLM is used, validate its output against the structured record and prohibit new factual or medical claims.

## 11. Public dataset plan

The owner had the Fitbit for only a short period, so public datasets provide the research foundation. Personal Fitbit data is for integration and demonstration, not for claiming broad model validity.

| Dataset | Useful signals/reference | Intended use | Access caveat |
|---|---|---|---|
| PPG-DaLiA | Wrist PPG and accelerometer with chest ECG ground truth; 15 subjects | Motion artifact, heart-rate reliability, signal-quality training and evaluation | Approximately 2.7 GB; CC BY 4.0 |
| SleepAccel | Apple Watch heart rate and accelerometer paired with PSG sleep labels | Sleep evidence quality and wearable-vs-reference disagreement | Verify current download terms before redistribution |
| WESAD | Wrist and chest physiological signals across affect/stress conditions; 15 subjects | Cross-signal consistency and context-dependent quality | Research/noncommercial constraints must be checked |
| MMASH | 24-hour heart rate, activity, sleep, and psychological data; 22 participants | Daily coverage, multimodal consistency, and baseline experiments | Open via PhysioNet; cite required sources |
| DREAMT | Smartwatch signals paired with PSG sleep labels; 100 participants | External sleep reliability validation | Restricted access; do not make it a critical path |

Before downloading or publishing derived artifacts, create `data/DATASETS.md` containing the canonical URL, citation, license, access date, expected files, checksums when available, and redistribution constraints for each dataset.

Do not merge all datasets blindly. Their devices, sampling rates, populations, protocols, and labels differ. Preserve dataset provenance and perform external-dataset evaluation.

## 12. Corruption and counterfactual test harness

The engine must be tested under controlled evidence failures. Starting from valid windows, generate realistic corruptions:

- contiguous and random missingness;
- shortened wear duration;
- delayed synchronization and stale timestamps;
- motion-contaminated PPG;
- sensor dropout and flatlining;
- implausible spikes;
- conflicting heart-rate/activity/sleep summaries;
- unstable or insufficient personal baselines;
- timezone/day-boundary errors;
- duplicated and out-of-order samples.

Every corruption needs a documented rationale and severity. Do not leak the corruption label directly into model features. Hold out people, sessions, and preferably datasets so the system cannot memorize subjects or acquisition protocols.

## 13. Evaluation

### Required comparisons

- always show the proposed claim;
- simple data-present/data-missing checks;
- deterministic claim-policy baseline;
- learned model without abstention;
- full hybrid engine with calibrated abstention.

### Core metrics

- **unsupported-show rate**: unsupported claims incorrectly returned as `SHOW`;
- selective risk versus coverage;
- macro F1 across all four decisions;
- per-class precision and recall, especially `SHOW` precision;
- Brier score and expected calibration error;
- out-of-distribution and external-dataset performance;
- robustness by corruption type and severity;
- decision latency and throughput;
- explanation fidelity: every stated reason must be present in the evidence trace.

Report confidence intervals and subject-level performance, not only pooled sample metrics. Use subject-held-out splits. A project result is not credible if training and test windows from the same person overlap.

### Success criterion

The project is worth continuing if the hybrid engine meaningfully reduces unsupported `SHOW` decisions under natural and corrupted conditions while retaining useful decision coverage, and if the learned component improves upon the deterministic baseline on held-out subjects or datasets. If it does not, publish the negative result and retain the simpler rules engine.

## 14. Existing repository context

This repository already contains two useful foundations:

1. `FITBIT_AIR_RESEARCH.md` documents direct Fitbit Air BLE research. The work found no accessible motion stream, a short unbonded ATT service window, and an authenticated DTLS channel that cannot responsibly be opened without the device's credentials. Treat this investigation as complete unless genuinely new authorized evidence appears.
2. `google_health/` contains a supported Google Health API client. Activity data has been fetched successfully. The API can provide derived metrics such as steps, heart rate, resting heart rate, HRV, sleep, SpO2, and exercise when the corresponding scopes and data are available. It cannot provide raw accelerometer or gyroscope samples.

Sensitive files such as `credentials.json`, `token.json`, personal exports, and subject identifiers must remain uncommitted. Never print secrets in logs, prompts, examples, tests, or documentation.

## 15. Fitbit/Google Health demonstration

Use personal Fitbit data only after the public-dataset pipeline and rules baseline exist. The demo should:

1. fetch authorized Google Health data;
2. normalize it into the canonical schema;
3. propose a small set of supported claims;
4. run the reliability engine;
5. show the typed decision and evidence trace;
6. allow the evaluator to simulate missing, stale, or conflicting data and observe the decision change.

Because the personal history is short, the expected and correct output for baseline-dependent claims may be `WAIT_FOR_MORE_DATA`. That behavior demonstrates the product thesis instead of weakening it.

## 16. Safety, privacy, and scientific integrity

- The system does not diagnose disease or recommend treatment.
- Health-related outputs should include appropriate scope language without drowning the demo in disclaimers.
- Collect the minimum data required and provide deletion/export paths in any deployed demo.
- Use pseudonymous subject identifiers.
- Keep credentials and raw personal exports local and ignored by Git.
- Record data and model provenance for every decision.
- Version policies, feature definitions, datasets, models, and thresholds.
- Never invent experiment results, user counts, performance gains, or medical validation.
- Distinguish synthetic corruption performance from naturally occurring failure performance.
- Check dataset licenses and required citations before publishing code, samples, or model weights.

## 17. AI-assisted development workflow

This project should visibly demonstrate responsible use of AI in software and research work:

- use AI to accelerate literature discovery, design alternatives, scaffolding, tests, data documentation, and code review;
- require primary-source verification for scientific, clinical, dataset, and API claims;
- review generated code and run tests before accepting it;
- keep a decision log for important architectural and experimental choices;
- use AI-generated explanations only as a presentation layer over structured evidence;
- document which parts were AI-assisted without pretending that generated output is independently validated.

The valuable AI story is not “an LLM reads Fitbit data.” It is that AI-assisted engineering helps build and test a constrained, auditable decision system whose outputs remain grounded.

## 18. Phased implementation plan

### Phase 0 — research specification

- Freeze initial claim types and their evidence contracts.
- Create the dataset manifest with licenses and citations.
- Write a short related-work review covering signal-quality indices, data-quality frameworks, uncertainty calibration, selective prediction, and wearable evidence reliability.
- Define metrics and leakage-safe subject splits before training.

### Phase 1 — typed rules engine

- Create canonical data and decision schemas.
- Implement coverage, freshness, range, baseline, and consistency features.
- Implement versioned claim policies.
- Add unit and property-based tests for boundary conditions.
- Expose a local FastAPI endpoint.

### Phase 2 — dataset and corruption pipeline

- Add dataset adapters without committing restricted/raw data.
- Normalize timestamps, units, subject/session identifiers, and provenance.
- Implement deterministic, seeded corruption transforms.
- Produce reproducible evaluation manifests.

### Phase 3 — learned reliability models

- Establish simple interpretable baselines.
- Train with subject-held-out splits.
- Calibrate probabilities on separate validation subjects.
- Evaluate external-dataset transfer and subgroup performance where metadata permits.

### Phase 4 — policy aggregation and abstention

- Combine hard gates with calibrated model outputs.
- Tune decision thresholds against explicit cost assumptions.
- Produce evidence traces and templated explanations.
- Add regression tests for unsafe `SHOW` outcomes.

### Phase 5 — Fitbit demo and packaging

- Connect the existing Google Health adapter.
- Build an interactive before/after corruption demo.
- Add Docker, API documentation, architecture diagram, model card, data card, and reproducibility instructions.
- Publish only after secret scanning, license review, and result verification.

## 19. Definition of done for a strong portfolio release

- Public repository contains no secrets or personal raw health data.
- README explains the problem in plain language and includes an honest 60-second demo.
- At least three claim policies are fully implemented and tested.
- The four typed decisions are returned through a stable API.
- Public-dataset ingestion is reproducible from documented sources.
- Evaluation uses subject-held-out splits and includes the rules-only baseline.
- Calibration and selective-risk plots are included.
- Corruption tests cover missingness, staleness, motion artifact, conflict, and baseline immaturity.
- Every explanation is traceable to structured evidence.
- Personal Fitbit data proves the integration but is not used to exaggerate validation.
- Limitations, dataset licenses, and nonmedical scope are explicit.
- Resume bullets contain only measured, reproducible results.

## 20. Resume positioning after results exist

Do not write these as completed achievements until the corresponding evidence exists. Use this structure later:

- Built a typed reliability engine for wearable insights that evaluates coverage, freshness, signal quality, cross-signal consistency, and personal-baseline maturity before returning `SHOW`, `WARN`, `WAIT`, or `REJECT` decisions.
- Reduced unsupported insight display by **[measured percentage]** at **[measured decision coverage]** versus **[named baseline]** across subject-held-out tests on **[datasets]**.
- Designed a FastAPI service and reproducible evaluation pipeline spanning **[number]** claim policies, **[number]** wearable datasets, and **[number]** controlled data-quality failure modes.

## 21. Immediate next task

Start with Phase 0 and Phase 1, not with a polished dashboard. The first concrete milestone is:

> Implement one end-to-end claim, `RESTING_HEART_RATE_ELEVATED`, using a typed request/response schema, deterministic evidence checks, unit tests, and a CLI or API demo that changes correctly under short baseline, stale sync, missing coverage, and contradictory evidence.

This creates a testable vertical slice and makes the later modeling work measurable.

---

## Copy-paste implementation prompt for an AI coding agent

You are working in the `fitbit-air-research` repository. Read `PROJECT_BRIEF.md`, `FITBIT_AIR_RESEARCH.md`, and `google_health/README.md` completely before making changes. This repository contains completed direct-BLE feasibility research and a supported Google Health client. Preserve that work and do not attempt credential extraction, pairing bypass, key guessing, or access to private Fitbit channels.

Build a domain-specific Wearable Insight Reliability Engine. Its purpose is not to generate health conclusions; it decides whether the available wearable evidence responsibly supports a predefined claim. The public API must return exactly one of `SHOW`, `SHOW_WITH_WARNING`, `WAIT_FOR_MORE_DATA`, or `REJECT`, plus calibrated confidence, claim-support probability, structured reason codes, evidence-dimension scores, a traceable explanation, retry requirements when appropriate, and model/policy versions.

Begin with a narrow vertical slice for `RESTING_HEART_RATE_ELEVATED`. Define typed schemas, a canonical observation model, and a versioned claim policy. Implement deterministic checks for data coverage, freshness, plausible ranges, required signals, minimum baseline length, baseline stability, effect size relative to baseline variation, and contradictory context. Default to `WAIT_FOR_MORE_DATA` or `REJECT` when evidence is materially insufficient; do not silently impute evidence or convert uncertainty into a confident result.

Write tests before or alongside implementation. Cover normal evidence, short baseline, stale synchronization, missing intervals, implausible measurements, unstable baseline, conflicting inputs, boundary thresholds, timezone boundaries, duplicated data, and out-of-order observations. Every explanation must be generated from the structured decision trace and must never introduce a medical diagnosis or unsupported advice. Do not add an LLM dependency for the first vertical slice.

Keep the design small and auditable. Prefer Python, FastAPI, Pydantic, and pytest unless the repository already establishes a better convention. Separate domain logic from API code and data adapters. Do not read, commit, log, or expose `credentials.json`, `token.json`, personal health exports, or secrets. Do not change the existing Google Health authentication flow unless the milestone requires an adapter, and use fixtures rather than personal data in tests.

Before claiming completion, run the relevant tests, demonstrate all four decision types using synthetic fixtures, inspect the repository for accidentally tracked secrets, and update documentation with the actual commands and limitations. Do not invent performance metrics. Clearly label work that still requires public-dataset validation.
