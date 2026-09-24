# Wearable Insight Reliability Engine

A typed trust layer between wearable data and the claims an app makes about it. It answers
one question — *does the available evidence responsibly support this claim right now?* —
and returns one of four decisions with the evidence it used.

| decision | meaning |
|---|---|
| `SHOW` | the evidence meets the claim's contract |
| `SHOW_WITH_WARNING` | supportable, but a limitation must be disclosed alongside it |
| `WAIT_FOR_MORE_DATA` | explicit abstention: more data or history could settle this |
| `REJECT` | evidence contradicts the claim, breaks a hard requirement, or the claim is out of scope |

Not a recovery score, dashboard, chatbot, or diagnostic tool. It doesn't produce health
conclusions; it decides whether one is supportable.

---

## Results

**Validated on 2 real wearable datasets — 87 subjects, 9,269 subject-days of Fitbit data.**

| | |
|---|---|
| Unsupported-show rate on corrupted **real** evidence | **0.0000** across 4,572 cases |
| Real-world decision coverage (resting heart rate) | **7.5%** PMData · **8.3%** LifeSnaps |
| Thresholds corrected because real data disproved them | **3** |
| Engine defects found by its own tests and harnesses | **9** |
| Tests | **327** |

### Against the baselines the protocol requires

All five baselines are implemented. B0–B2 are measured on the synthetic suite (3,064 scored
cases, 30 subjects, 17 failure modes × 3 severities); B3–B4 are measured on real PMData
evidence (15,765 cases, 4-fold subject-held-out), so the two blocks are **not** directly
comparable and are kept separate.

| baseline | unsupported-show ↓ | coverage | macro F1 | measured on |
|---|---|---|---|---|
| B0 always show | 1.0000 | 1.0000 | 0.14 | synthetic |
| B1 data-present check | 0.9583 | 0.8805 | 0.14 | synthetic |
| **B2 rules engine (ships)** | **0.0000** | 0.2693 | **0.78** | synthetic |
| B2 rules engine | **0.0000** | 0.0187 | — | real (PMData) |
| B3 learned, no gates | 0.2333 | 0.2304 | — | real (PMData) |
| B4 hybrid | **0.0000** | 0.0187 | — | real (PMData) |

Coverage differs between the two blocks because the case mixes differ — the real-data table
includes 14 corruptions per clean day, so most of its rows *should* be withheld. What is
comparable is B2 against B3/B4 within the real block.

### Three headline findings

**1. Real data disproved three of my own thresholds.** Every threshold started as domain
reasoning, never measured. PMData showed two were wrong by *shape*, not decimals:

| threshold | was | fired on | now |
|---|---|---|---|
| RHR baseline stability | 8.0 bpm | **0.0%** of 1,451 days — twice the observed max, could never act | 5.0 |
| Activity baseline stability | 45 min | **48.7%** of 2,055 days — withheld half of all activity claims | 90 |
| RHR min baseline | 14 days | measured settling time is **48 days** | 28 |

Adopted only after A/B-ing on real data (activity coverage 5.3% → 9.1%) and confirming the
safety check still returned 0.0000.

**2. Machine learning was tested and rejected.** The brief's own success criterion. On
15,765 real cases, 4-fold subject-held-out:

- Models learn the contract nearly perfectly — **ROC-AUC 0.979**
- But without gates they display **23.3% of inadequate evidence**
- With gates they reproduce the engine exactly and add nothing

*When the decision rule is a known deterministic function of observable features, a learned
approximation can only add error.* Verdict: drop it, ship the rules engine.

**3. The thresholds generalise to a second, untuned cohort.**

| | PMData (16, athletic) | LifeSnaps (71, general) |
|---|---|---|
| `show_z ≥ 1.5` fires on | 10.5% of days | **11.0%** |
| decision coverage | 7.5% | **8.3%** |
| baseline settling time | 48 days | 40 days |
| unsupported-show, corrupted | 0.0000 | **0.0000** |

### What this does **not** establish

- **No reference standard.** Both datasets are wearable-only — no chest ECG, no
  polysomnography. On real data the only labelled cases are ones deliberately corrupted.
- **No fairness claim.** No subgroup difference was detectable (gender −1.8%
  [−5.3%, +1.7%]), but n=67 can't rule one out, and skin tone — the likeliest mechanism via
  PPG quality — is in neither dataset.
- **Two cohorts, both Fitbit volunteers.** Not the general population.
- **No calibration for the shipped engine.** `claim_support_probability` is a deterministic
  score; every response says `support_is_calibrated: false`.

Full detail: [`docs/limitations.md`](docs/limitations.md) ·
[`eval/results/`](eval/results/)

---

## Why

Wearable apps turn incomplete, stale, or noisy sensor data into confident statements —
"you recovered poorly", "your resting heart rate is elevated". Sensors are noisy during
movement, syncs arrive late, devices come off, signals disagree, and a new user has no
baseline. Most products add more insights. This one decides when an insight should not be
shown, and says why in machine-readable terms.

## 60-second demo

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m engine.cli demo --claim RESTING_HEART_RATE_ELEVATED
```

The same claim under eight evidence conditions. Abridged:

```
-- clean, effect present
  decision   SHOW
             Measured resting heart rate was 63.5 bpm against a personal baseline of
             57.53 bpm over 21 day(s), 2.2 SD above it.

-- short personal baseline
  decision   WAIT_FOR_MORE_DATA
  reasons    BASELINE_NOT_MATURE, INSUFFICIENT_HISTORICAL_COVERAGE
  retry      after P7D once there is a_longer_personal_baseline
```

Inject one documented failure and watch the decision move:

```bash
./.venv/bin/python -m engine.cli corrupt --corruption shorten_wear --severity severe
```

Or run it as a service with a before/after page at `http://127.0.0.1:8000/`:

```bash
./.venv/bin/uvicorn engine.api.app:app --reload
```

## The contract

```jsonc
// POST /v1/decisions
{
  "subject_id": "demo-user-001",
  "claim": {
    "type": "RESTING_HEART_RATE_ELEVATED",
    "target_window": {"start": "2026-09-22T00:00:00Z", "end": "2026-09-23T00:00:00Z"}
  },
  "observations": [
    {"signal": "resting_heart_rate", "value": 72, "unit": "bpm",
     "measured_at": "2026-09-23T11:00:00Z", "source": "google_health"}
  ],
  "baseline": [{"signal": "resting_heart_rate", "value": 58.4, "day": "2026-09-21T00:00:00Z"}],
  "context": {"sync_completed_at": "2026-09-23T12:00:00Z", "device_worn_minutes": 1180,
              "timezone": "America/New_York"}
}
```

```jsonc
{
  "decision": "WAIT_FOR_MORE_DATA",
  "confidence": 0.93,                    // confidence in the DECISION
  "claim_support_probability": 0.28,     // how much evidence supports the CLAIM
  "support_is_calibrated": false,
  "reason_codes": ["BASELINE_NOT_MATURE", "INSUFFICIENT_HISTORICAL_COVERAGE"],
  "evidence": {"coverage_score": 0.87, "freshness_score": 1.0, "signal_quality_score": 1.0,
               "consistency_score": 1.0, "baseline_maturity_score": 0.14},
  "explanation": "There is not yet enough reliable evidence to decide ...",
  "retry": {"recommended": true, "after": "P7D",
            "required_evidence": ["a_longer_personal_baseline"]},
  "trace": { "gates": [...], "features": {...}, "thresholds": {...} },
  "model_version": "reliability-engine-0.1.0",
  "policy_version": "claim-policy-0.2.0"
}
```

Three things are easy to misread:

- **`confidence` is confidence in the decision, not the claim.** An abstention backed by
  unambiguous evidence of insufficiency is a *high*-confidence abstention
  ([ADR 0004](docs/decisions/0004-separate-confidence-from-support.md)).
- **Evidence scores: 0.5 means "exactly at this claim's minimum acceptable level."** 1.0 is
  comfortably sufficient, below 0.5 is under the floor. Not probabilities.
- **`claim_support_probability` is not calibrated.** It's a deterministic score.

An unknown claim type returns `200` with a `REJECT` — a caller still needs a decision it can
branch on. A naive timestamp or personal-looking `subject_id` returns `422`.

## Claim policies

Six claim families, each with a versioned evidence contract in
[`docs/claim-contracts.md`](docs/claim-contracts.md):

| claim | required signals | baseline | ceiling |
|---|---|---|---|
| `RESTING_HEART_RATE_ELEVATED` | `resting_heart_rate` | 28 days | `SHOW` |
| `SLEEP_DURATION_LOW` | `sleep_duration` | 7 nights | `SHOW` |
| `SLEEP_QUALITY_REDUCED` | `sleep_efficiency`, `sleep_duration` | 14 nights | `SHOW_WITH_WARNING` |
| `ACTIVITY_LOAD_HIGH` | `active_minutes` | 14 days | `SHOW` |
| `RECOVERY_EVIDENCE_INCOMPLETE` | — (inverted claim) | 14 days | `SHOW` |
| `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` | the named signal | 14 days | `SHOW_WITH_WARNING` |

Two carry permanent ceilings. `SLEEP_QUALITY_REDUCED` can never be shown without its
`LOW_REFERENCE_AGREEMENT` disclosure, because consumer sleep staging disagrees materially
with clinical measurement. The anomaly claim can only ever say "re-measure"; a test asserts
its explanations contain no diagnostic language.

`RECOVERY_EVIDENCE_INCOMPLETE` is inverted: it asserts *insufficiency*, so an empty evidence
bundle is its strongest support, not a reason to abstain.

```bash
./.venv/bin/python -m engine.cli policies      # inspect the live contract
```

## How a decision is made

```text
request ──> normalise ──> evidence features ──> deterministic gates ──┐
            (dedupe,      (coverage, freshness,                       │
             sort,         quality, consistency,          ┌───────────┴─ reject > wait >
             range-check,  baseline, effect size)         │              score; warn caps
             window-place)                               support        a SHOW; policy
                                                          score         ceiling caps all
                                                             │               │
                                                             └───────┬───────┘
                                                                     v
                                          typed decision + evidence trace + explanation
```

Gates outrank the score in both directions. That's the fail-closed mechanism: a learned
component can lower confidence and cannot argue the engine into displaying a claim whose
evidence failed a hard requirement. A property test asserts this over the input space.

Explanations are assembled only from trace fields — no language model
([ADR 0003](docs/decisions/0003-no-llm-in-v01.md)). A test asserts every reported reason
code's detail appears verbatim, and that no explanation contains diagnostic or advisory
language. Diagram and module map: [`docs/architecture.md`](docs/architecture.md).

---

## Evaluation in detail

Primary metric: **unsupported-show rate** — the share of cases whose evidence did *not*
support the claim that were displayed anyway. `SHOW_WITH_WARNING` counts as displayed; a
warning next to an unjustified conclusion is still an unjustified conclusion on screen.
Protocol and splits were fixed before any modelling:
[`docs/evaluation-protocol.md`](docs/evaluation-protocol.md).

### Synthetic suite — a regression harness, not a benchmark

```bash
./.venv/bin/python -m eval.run_eval --seeds 5
```

3,064 scored cases (56 excluded as borderline), 30 subjects, 17 corruption types × 3
severities. Intervals bootstrap over **subjects**, not cases.

Sweeping the display thresholds with gates held fixed: **100% of the engine's abstentions
come from deterministic gates, not threshold choices.** That's the design working — and the
sharpest limitation of the suite, because if thresholds never decide an unsupportable case,
the suite can't tell whether they're any good. That's what the real datasets are for.

Labels come from [`engine/measure.py`](engine/measure.py), a deliberately separate second
implementation of the same contract, so a zero rate is agreement between two readings of one
document. Where they disagreed during development, the disagreement was resolved by checking
which matched [`docs/claim-contracts.md`](docs/claim-contracts.md) — sometimes fixing the
engine, sometimes the labeller, all in the git history.

### PMData — 16 subjects, ~5 months each, Fitbit Versa 2

[Thambawita et al., MMSys '20](https://dl.acm.org/doi/10.1145/3339825.3394926), CC BY 4.0.

```bash
./.venv/bin/python -m eval.pmdata_prepare   # once, ~15 min, needs the 1.4 GB download
./.venv/bin/python -m eval.pmdata_eval
```

Chosen because this engine's claims need a personal baseline of 7–28 valid days — a dataset
with one session per subject can't exercise them at all.

| claim | days | coverage | most common reason for withholding |
|---|---|---|---|
| `RESTING_HEART_RATE_ELEVATED` | 1,658 | 7.5% | `CLAIM_CONTRADICTED` — most days it isn't elevated |
| `ACTIVITY_LOAD_HIGH` | 2,396 | 9.1% | `CLAIM_CONTRADICTED`, `INSUFFICIENT_HISTORICAL_COVERAGE` |
| `SLEEP_DURATION_LOW` | 1,879 | 14.5% | `CLAIM_CONTRADICTED` |
| `SLEEP_QUALITY_REDUCED` | 1,879 | 3.6% | `EFFECT_BELOW_RELIABLE_THRESHOLD` |

An insight firing on 7.5% of days is about one a fortnight — a sane rate. Nobody knew this
number before; it was guessed.

**The 3 bpm absolute floor was confirmed:** real consecutive-day change is median 0.75, p95
2.32 bpm. It also exposed an interaction — at the median personal SD, 3 bpm is `z = 1.68`,
so that floor, not the stated `show_z` of 1.5, is what actually binds.

### LifeSnaps — 71 subjects, ~3 months each, Fitbit Sense

[Yfantidou et al., *Scientific Data* 9, 663 (2022)](https://doi.org/10.1038/s41597-022-01764-x),
CC BY 4.0. **No threshold is tuned on it** — this is the dataset-held-out external check.

```bash
./.venv/bin/python -m eval.lifesnaps_eval
```

Results in the table above. The stability gate tightened from 8.0 → 5.0 bpm now fires on
**0.3%** of days in a general population where it fired on 0.0% among athletes — a gate
starting to do its job.

**The subgroup analysis is worth reading, because the first version of it was wrong.**
Per-subject *medians* differ more than twofold by gender (5.1% vs 2.3%) and look alarming.
Bootstrapping over subjects gives **−1.8% [−5.3%, +1.7%]** — comfortably containing zero —
and the mechanism markers are near-identical (wear 0.71 vs 0.69, baseline SD 1.88 vs 2.03).
The median was sampling noise. Every subgroup comparison now carries an interval, and a test
asserts two samples from one distribution come back as *not distinguishable*.

### Learned baselines — built, measured, rejected

```bash
./.venv/bin/python -m eval.learned
```

| baseline | coverage | unsupported-show | ROC-AUC | Brier | ECE |
|---|---|---|---|---|---|
| **B2** rules engine | 0.0187 | **0.0000** | — | — | — |
| **B3** gradient boosting, no gates | 0.2304 | **0.2333** | 0.979 | 0.038 | 0.003 |
| **B4** gradient boosting, hybrid | 0.0187 | **0.0000** | 0.979 | 0.038 | 0.003 |

Guards against a rigged experiment: the model gets **raw measurements only**, never the
band-scored features that already encode thresholds; calibration is fitted on subjects
disjoint from both training and test; and the verdict demands a real margin so a tie isn't
recorded as a win.

The one thing the learned path offered is calibration (Brier 0.038, ECE 0.003), which the
engine has no equivalent of. Worth knowing; not a reason to put a model in the decision path.

---

## Fitbit / Google Health integration

```bash
./.venv/bin/python google_health/gh_fetch.py --days 30      # see google_health/README.md
./.venv/bin/python demo/google_health_demo.py --report
./.venv/bin/python demo/google_health_demo.py --timezone America/New_York
```

The adapter reads JSON the fetch client already wrote. It does not import the auth module,
hold a token, or make a network call — a test enforces that through the import graph — so no
part of the engine can reach an OAuth secret. Its field mapping is **unverified against live
payloads**; `--report` prints exactly what did and did not convert.

Two fields the export doesn't carry are wear time and sync-completion time. The demo leaves
both unset rather than inventing them, so decisions come back disclosing `COVERAGE_UNKNOWN`
and `SYNC_STATE_UNKNOWN`.

**A design lesson the data handed over:** no Fitbit export carries a sync-completion
timestamp. Without one the engine flags `SYNC_STATE_UNKNOWN` on *every* day, capping every
decision at `SHOW_WITH_WARNING` — zero clean `SHOW`s across 1,658 real days. The contract
asks for a field real exports don't have, so an integrating app must supply it from its own
sync layer.

**Expect `WAIT_FOR_MORE_DATA` on a short personal history.** Abstaining is correct when the
history isn't there — the product thesis working, not the demo failing.

**Raw accelerometer and gyroscope data are unavailable at any tier.** That's established,
not assumed: [`FITBIT_AIR_RESEARCH.md`](FITBIT_AIR_RESEARCH.md) records three sessions of
direct BLE investigation ending at an authenticated DTLS channel that can't be opened
without the device's own credentials. That investigation is closed
([ADR 0006](docs/decisions/0006-ble-research-closed.md)).

## Layout

```
engine/            schemas, policies, features, gates, decisions, explanations
  policies/        six versioned claim contracts
  adapters/        Google Health, PMData and LifeSnaps -> canonical observations
  api/             FastAPI surface and the before/after demo page
  measure.py       independent contract measurement, used only to label evaluation cases
  corruptions.py   17 seeded failure injections
  synth.py         seeded synthetic evidence generators
eval/              synthetic suite, baselines, metrics, threshold sweep, PMData
                   preparation / evaluation / policy A-B, learned baselines, LifeSnaps
tests/             327 tests
docs/              claim contracts, evaluation protocol, related work, cards, ADRs
tools/             secret scanner, BLE log sanitiser
scripts/ captures/ logs/     the completed BLE research, preserved read-only
```

`engine/` imports nothing from `scripts/` and no networking library — a decision is
reproducible offline. Tests enforce both.

## Development

```bash
./.venv/bin/python -m pytest                    # 327 tests
./.venv/bin/python -m eval.run_eval --seeds 5   # synthetic evaluation
./.venv/bin/python tools/secret_scan.py         # pre-publish gate
```

In a container, which carries no credentials and no personal data:

```bash
docker build -t reliability-engine . && docker run --rm -p 8000:8000 reliability-engine
```

## Scope

This system does not diagnose disease, recommend treatment, or replace a clinician. It
reports whether measurements support a statement, never whether a person has a condition.
Read [`docs/limitations.md`](docs/limitations.md) before drawing any conclusion from this
repository.

## Documents

- [PROJECT_BRIEF.md](PROJECT_BRIEF.md) — the authoritative specification
- [docs/claim-contracts.md](docs/claim-contracts.md) — the frozen evidence contracts
- [docs/evaluation-protocol.md](docs/evaluation-protocol.md) — metrics and splits, fixed before modelling
- [docs/limitations.md](docs/limitations.md) — what this does not establish
- [docs/architecture.md](docs/architecture.md) — diagram and module map
- [docs/model-card.md](docs/model-card.md) / [docs/data-card.md](docs/data-card.md)
- [docs/decisions/](docs/decisions/) — seven architecture decision records
- [docs/related-work.md](docs/related-work.md) — scoping review; **citations unverified**
- [data/DATASETS.md](data/DATASETS.md) — dataset manifest with licences and checksums
- [FITBIT_AIR_RESEARCH.md](FITBIT_AIR_RESEARCH.md) — the completed BLE feasibility research
