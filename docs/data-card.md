# Data card

Three kinds of data touch this project, with different rules. This card covers all three.

## 1. Synthetic evidence (the only data v0.1 is evaluated on)

| | |
|---|---|
| Source | `engine/synth.py`, degraded by `engine/corruptions.py` |
| Size | 3120 cases at `--seeds 5` (3057 scored, 63 excluded as borderline) |
| Subjects | 30 synthetic, pseudonymous (`synth-<claim>-<seed>`) |
| Composition | 6 claim types × 2 base cases × (1 clean + 17 corruptions × 3 severities) |
| Labels | Ground-truth evidence state, measured by `engine/measure.py` against the claim contract |
| Committed | No. Generated deterministically from seeds; only the evaluation report is committed |

### How it is generated

Each claim type has a generator producing clean, fully-evidenced data with either a real
effect (`SUPPORTABLE`) or no effect (`UNSUPPORTABLE_CONTRADICTED`). Corruptions then
degrade the evidence in one documented way at one of three severities.

### How it is labelled

Labels are **measured, not assumed**. `engine/measure.py` reads the corrupted request plus
the claim policy's thresholds and reports which contract clauses the evidence breaks. A
corruption expected to breach something but which did not — because the case had too few
samples, or because that requirement does not apply to this claim — is labelled by what the
evidence actually contains. Assuming "delayed sync therefore unsupportable" is wrong for a
claim whose policy does not require a sync after the window, and those false failures would
make the headline metric meaningless.

`measure.py` is a deliberately separate implementation of the same arithmetic as
`engine/features.py`. Labelling with the code under test would make the evaluation
tautological.

### Assumptions that are known to be wrong

- Baselines are Gaussian and days are independent. Real resting heart rate is
  autocorrelated and seasonal.
- No sensor-error model. Motion artifact is represented as a flag plus additive jitter,
  not as PPG waveform corruption.
- No population variation, no device heterogeneity, no missing-not-at-random structure.
- Corruption severities are chosen to straddle the policy thresholds, so the suite is
  denser near decision boundaries than reality is.

**Consequence: this data tests decision logic, not physiology.** It cannot support any
claim about real-world performance, and the evaluation report says so in its own text.

## 2. Public datasets (planned, none downloaded)

See [`data/DATASETS.md`](../data/DATASETS.md). Five candidates: PPG-DaLiA, SleepAccel,
WESAD, MMASH, DREAMT.

**Status: nothing downloaded, every metadata row marked UNVERIFIED.** Citations, URLs,
sizes, and licence names in that file were drafted from memory during AI-assisted planning
and must be checked against primary sources before any file is fetched or any dataset name
appears in a README, paper, or resume bullet.

Rules that hold regardless: no blind merging across datasets, provenance preserved per
record, subject-held-out splits only, at least one dataset held out entirely for external
transfer, pseudonymous identifiers rewritten on ingest, and no dataset file or derived
subject-level table committed (`data/datasets/` is gitignored).

## 3. Personal Google Health data (demonstration only)

| | |
|---|---|
| Source | The owner's Fitbit Air, via the Google Health API (`google_health/gh_fetch.py`) |
| Available | steps, distance, calories, heart rate, resting heart rate, HRV, SpO2, respiratory rate, VO2 max, active minutes, exercise, sleep with stages |
| **Not available at any tier** | raw accelerometer and gyroscope samples — see `FITBIT_AIR_RESEARCH.md` |
| Extent | A short history: the device was owned for a brief period |
| Committed | **No.** `google_health/data/` is gitignored, and `tools/secret_scan.py` fails the build if anything from it becomes tracked |

### Handling

- Used for integration demonstration only. **Never training data, never evidence of
  validity.** Because the history is short, baseline-dependent claims correctly return
  `WAIT_FOR_MORE_DATA`.
- The adapter reads already-fetched JSON. It imports no auth module, holds no token, and
  makes no network call; a test enforces this through the import graph.
- The subject identifier is a pseudonym supplied on the command line. The schema rejects
  anything containing an `@` or resembling a phone number.
- Two fields the export does not carry — wear time and sync-completion time — are left
  unset rather than inferred, so decisions come back with `COVERAGE_UNKNOWN` and
  `SYNC_STATE_UNKNOWN` attached.
- The demo's output contains the owner's own measurements, because an explanation states
  the value it is about. It prints a reminder not to commit or publish that output.

### Adapter field mapping is unverified

`engine/adapters/google_health.py` maps Google Health data points to canonical
observations using candidate key lists written from the fetch client and the documented API
shape, **not** by inspecting personal exports. Points it cannot map are counted and
reported by `demo/google_health_demo.py --report`, never converted with a guessed value.

## 4. BLE research artifacts (preserved, not evidence)

`captures/`, `logs/`, and `scripts/` hold the completed direct-BLE investigation: GATT
enumerations, advertisement records, and notification payloads from the owner's own device.
They contain no personal health measurements and no credentials — the investigation never
obtained any — and are kept as the evidence base for a negative result
([ADR 0006](decisions/0006-ble-research-closed.md)). `engine/` imports nothing from them.

## Retention and deletion

Nothing is stored server-side by anything in this repository. The demo holds data in memory
for the length of the process and writes nothing. A deployed service would need its own
retention and deletion path for decision traces, which are personal data; none is
implemented here because nothing is deployed.
