# Dataset manifest

**One dataset is verified and downloaded: PMData.** The rest of this table remains
unverified and untouched.

**Verification status of the remaining rows: UNVERIFIED.** Their citations, URLs, sizes,
and license names were drafted from memory during AI-assisted planning. Every row must
be checked against its primary source and marked `VERIFIED <date>` *before* any file is
fetched, any derived artifact is published, or any dataset name appears in a README,
paper, or resume bullet. Do not treat an unverified row as a fact.

The PMData row below shows why that rule exists: a web search summary reported its licence
as CC BY-NC 4.0, and the dataset's own page states CC BY 4.0. The primary source wins, and
the discrepancy is recorded rather than quietly resolved.

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

## Verified and in use

### PMData

| field | value |
|---|---|
| Status | **VERIFIED 2026-09-23, downloaded** |
| Canonical page | https://datasets.simula.no/pmdata/ |
| Direct download | `https://datasets.simula.no/downloads/pmdata.zip` |
| Size | 1.4 GB (zip), as stated on the page and confirmed on download |
| Licence | **CC BY 4.0** — https://creativecommons.org/licenses/by/4.0/ — per the dataset's own page. Permits use, adaptation and redistribution with attribution. A search summary claimed CC BY-NC 4.0; the primary source says otherwise and is authoritative here. |
| Required citation | Thambawita, Hicks, Borgli, Stensland, Jha, Svensen, Pettersen, Johansen, Johansen, Pettersen, Nordvang, Pedersen, Gjerdrum, Grønli, Fredriksen, Eg, Hansen, Fagernes, Claudi, Biørn-Hansen, Nguyen, Kupka, Hammer, Jain, Riegler, Halvorsen. **"PMData: A Sports Logging Dataset."** *Proceedings of the 11th ACM Multimedia Systems Conference (MMSys '20)*, 2020, pp. 231–236. DOI [10.1145/3339825.3394926](https://dl.acm.org/doi/10.1145/3339825.3394926) |
| Attribution requirement | The licence text on the page requires that any document or paper using or reporting results from PMData cite the article above, link the licence, and indicate if changes were made. This repository does all three: here, in the README, and in every evaluation artifact produced from it. |
| Ethics | Participants signed a form permitting collection and publication (stated on the dataset page). |
| Credentials required | None. Public direct download. |
| Subjects | 16 participants |
| Duration | ~5 months per participant, November 2019 – March 2020 |
| Device | Fitbit Versa 2 |
| Scale | 20,991,392 heart-rate measurements; 1,836 days of sleep scores; 2,440 activity sessions |

**Why this dataset, specifically.** Almost every wearable dataset gives one session or one
night per person. This engine's claims are defined against a *personal baseline* of 7 to 14
valid days, so a single session cannot exercise them at all — the engine would correctly
abstain on every case and the evaluation would learn nothing. PMData gives roughly five
months per subject, which is the first thing that makes the baseline-dependent claims
testable. It is also Fitbit data, the same device family as the personal integration path,
so the field semantics carry over.

**Files used, per participant:**

| file | canonical signal | feeds |
|---|---|---|
| `fitbit/resting_heart_rate.json` | `resting_heart_rate` (daily) | `RESTING_HEART_RATE_ELEVATED` |
| `fitbit/sleep.json` | `sleep_duration`, `sleep_efficiency` (per night) | `SLEEP_DURATION_LOW`, `SLEEP_QUALITY_REDUCED` |
| `fitbit/*_active_minutes.json` | `active_minutes` (daily) | `ACTIVITY_LOAD_HIGH` |
| `fitbit/heart_rate.json` | `heart_rate` (intraday) | consistency checks, wear-time estimation |
| `fitbit/steps.json` | `steps` (per minute) | wear-time proxy, activity consistency |

**Not used:** food images, `googledocs/reporting.csv`, and the PMSys subjective reports
(`wellness.csv`, `srpe.csv`, `injury.csv`). They are self-reported rather than sensor-derived,
and this engine judges sensor evidence.

**Handling.** `data/datasets/` is gitignored; no PMData file, participant record, or
derived per-subject table is committed. Participant identifiers are used as the dataset's
own pseudonyms (`p01`…`p16`), rewritten to `pmdata-pNN` on ingest.

## Remaining candidates (unverified, not downloaded)

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
| 2026-09-23 | PMData verified against its primary source and downloaded; licence discrepancy between a search summary (CC BY-NC 4.0) and the dataset page (CC BY 4.0) recorded, primary source adopted |
