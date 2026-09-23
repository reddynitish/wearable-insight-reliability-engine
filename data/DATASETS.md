# Dataset manifest

**Download status: nothing in this table has been downloaded yet.** The v0.1 rules
engine is validated on seeded synthetic evidence only (see
`docs/evaluation-protocol.md`). This file exists so that the first download is a
documented, license-checked act rather than an accident.

**Verification status of the metadata below: UNVERIFIED.** The citations, URLs, sizes,
and license names were drafted from memory during AI-assisted planning. Every row must
be checked against its primary source and marked `VERIFIED <date> <initials>` *before*
any file is fetched, any derived artifact is published, or any dataset name appears in
a README, paper, or resume bullet. Do not treat an unverified row as a fact.

No dataset file, subject-level record, or derived feature table from any of these
sources is committed to this repository. `data/datasets/` is gitignored.

## Verification checklist (per row, before first download)

1. Open the canonical landing page; confirm it still exists and still hosts the data.
2. Copy the citation exactly as the provider asks for it.
3. Read the licence in full; record redistribution and derived-work terms verbatim.
4. Record the access date, file list, and byte sizes actually observed.
5. Compute and record SHA-256 for each downloaded archive.
6. Confirm whether credentialed access, a data use agreement, or training is required.
7. Confirm whether derived features may be published, and at what aggregation level.
8. Flip the row's status to `VERIFIED`.

## Table

| Dataset | Status | Signals / reference | Intended use in this project | Access notes to verify |
|---|---|---|---|---|
| PPG-DaLiA | UNVERIFIED, not downloaded | wrist PPG + 3-axis accelerometer + chest ECG reference, 15 subjects, activities of daily living | Stage-2 signal-quality and motion-artifact models; the only planned source of a *reference-grade* heart-rate ground truth | believed hosted on the UCI Machine Learning Repository; believed ~2.7 GB; believed CC BY 4.0; citation believed Reiss et al., "Deep PPG", *Sensors*, 2019 |
| SleepAccel | UNVERIFIED, not downloaded | Apple Watch heart rate + accelerometer with polysomnography sleep labels | Stage-2 sleep-evidence quality; wearable-vs-PSG disagreement, which is the empirical basis for the `LOW_REFERENCE_AGREEMENT` disclosure on `SLEEP_QUALITY_REDUCED` | believed hosted on PhysioNet; citation believed Walch et al., *Sleep*, 2019; PhysioNet open licences still require citation and forbid re-identification attempts |
| WESAD | UNVERIFIED, not downloaded | wrist + chest physiological signals across baseline/stress/amusement conditions, 15 subjects | cross-signal consistency features; context-dependent quality | believed research/non-commercial only; believed Schmidt et al., ICMI 2018; confirm whether a request form is required |
| MMASH | UNVERIFIED, not downloaded | 24 h heart rate, actigraphy, sleep, psychological questionnaires, 22 participants | daily coverage and baseline-maturity experiments; closest match to the engine's day-level windows | believed hosted on PhysioNet; citation believed Rossi et al., 2020 |
| DREAMT | UNVERIFIED, not downloaded | smartwatch signals with PSG sleep labels, ~100 participants | external-dataset transfer check for sleep claims | believed credentialed/restricted access on PhysioNet; **explicitly not on the critical path** — the project must produce its result without it |

## Rules that hold regardless of verification

- **No blind merging.** Devices, sampling rates, populations, protocols, and label
  definitions differ across all five. Every canonical record keeps
  `dataset`, `subject_id`, `session_id`, and `device` provenance, and evaluation
  reports per-dataset numbers before any pooled number.
- **Subject-held-out splits only.** Windows from one person never appear in both train
  and test. At least one dataset is held out entirely as an external-transfer check.
- **Pseudonymous IDs.** Subject identifiers are rewritten to `<dataset>-<n>` on ingest.
  No original participant identifier enters the canonical store.
- **Personal Fitbit/Google Health data is never training data.** It is a demonstration
  input only, and its short history is expected to produce `WAIT_FOR_MORE_DATA`.
- **Synthetic corruption is labelled as synthetic** everywhere it is reported, and is
  never summed together with naturally occurring failures into one headline metric.

## Change log

| date | change |
|---|---|
| 2026-09-23 | manifest created; all rows unverified; no downloads performed |
