# Wearable Insight Reliability Engine

A typed trust layer that sits between wearable data and the claims an app wants to make
about it. It answers one question:

> Does the available wearable evidence responsibly support this health or fitness claim
> right now?

and returns one of four decisions, with the evidence it used:

| decision | meaning |
|---|---|
| `SHOW` | the evidence meets the claim's contract |
| `SHOW_WITH_WARNING` | supportable, but a limitation must be disclosed alongside it |
| `WAIT_FOR_MORE_DATA` | an explicit abstention: more data or history could settle this |
| `REJECT` | the evidence contradicts the claim, breaks a hard requirement, or the claim is out of scope |

It is **not** a recovery score, a dashboard, a chatbot, or a diagnostic tool. It does not
produce health conclusions; it decides whether one is supportable. Full specification in
[PROJECT_BRIEF.md](PROJECT_BRIEF.md).

## Why

Wearable apps routinely turn incomplete, stale, or noisy sensor data into confident
statements — "you recovered poorly", "your resting heart rate is elevated". The sensors
are noisy during movement, syncs arrive late, devices come off, signals disagree, and a
new user has no personal baseline yet. Most products add more insights. This one decides
when an insight should not be shown, and says why in machine-readable terms.

## 60-second demo

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m engine.cli demo --claim RESTING_HEART_RATE_ELEVATED
```

That prints the same claim under eight evidence conditions. Abridged:

```
-- clean, effect present
  decision   SHOW
  explanation
             The available evidence supports this statement. Measured resting heart rate
             was 63.5 bpm against a personal baseline of 57.53 bpm over 21 day(s), 2.2 SD
             above it. ...

-- short personal baseline
  decision   WAIT_FOR_MORE_DATA
  reasons    BASELINE_NOT_MATURE, INSUFFICIENT_HISTORICAL_COVERAGE
  retry      after P7D once there is a_longer_personal_baseline
  explanation
             There is not yet enough reliable evidence to decide this one way or the
             other. This is because: 1 valid baseline day(s) are available; this claim
             needs 14 to establish what is normal for this person; ...
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
    "statement": "Your resting heart rate is elevated today.",
    "target_window": {"start": "2026-09-22T00:00:00Z", "end": "2026-09-23T00:00:00Z"}
  },
  "observations": [
    {"signal": "resting_heart_rate", "value": 72, "unit": "bpm",
     "measured_at": "2026-09-23T11:00:00Z", "source": "google_health"}
  ],
  "baseline": [
    {"signal": "resting_heart_rate", "value": 58.4, "day": "2026-09-21T00:00:00Z"}
  ],
  "context": {
    "sync_completed_at": "2026-09-23T12:00:00Z",
    "device_worn_minutes": 1180,
    "timezone": "America/New_York"
  }
}
```

```jsonc
{
  "decision": "WAIT_FOR_MORE_DATA",
  "confidence": 0.93,                    // confidence in the DECISION
  "claim_support_probability": 0.28,     // how much the evidence supports the CLAIM
  "support_is_calibrated": false,        // v0.1 has no learned component
  "reason_codes": ["BASELINE_NOT_MATURE", "INSUFFICIENT_HISTORICAL_COVERAGE"],
  "evidence": {
    "coverage_score": 0.87, "freshness_score": 1.0, "signal_quality_score": 1.0,
    "consistency_score": 1.0, "baseline_maturity_score": 0.14
  },
  "explanation": "There is not yet enough reliable evidence to decide ...",
  "limitations": [],
  "retry": {"recommended": true, "after": "P7D",
            "required_evidence": ["a_longer_personal_baseline"]},
  "trace": { "gates": [...], "features": {...}, "thresholds": {...} },
  "model_version": "reliability-engine-0.1.0",
  "policy_version": "claim-policy-0.1.0"
}
```

Three things in there are easy to misread, so they are stated once here:

- **`confidence` is confidence in the decision, not in the claim.** An abstention backed
  by unambiguous evidence of insufficiency is a *high*-confidence abstention. The two
  numbers are separate on purpose ([ADR 0004](docs/decisions/0004-separate-confidence-from-support.md)).
- **Evidence scores use one convention: 0.5 means "exactly at this claim's minimum
  acceptable level".** 1.0 means comfortably sufficient, below 0.5 means under the floor.
  They are not probabilities.
- **`claim_support_probability` is not a calibrated probability in v0.1.** It is a
  deterministic score, which is why every response carries
  `support_is_calibrated: false`.

An unknown claim type returns `200` with a `REJECT`, not a `4xx`: a caller still needs a
decision it can branch on. A naive timestamp or a personal-looking `subject_id` returns
`422`, because those are caller bugs, not evidence states.

## Claim policies

Six claim families are implemented, each with a versioned evidence contract in
[docs/claim-contracts.md](docs/claim-contracts.md):

| claim | required signals | baseline | ceiling |
|---|---|---|---|
| `RESTING_HEART_RATE_ELEVATED` | `resting_heart_rate` | 14 days | `SHOW` |
| `SLEEP_DURATION_LOW` | `sleep_duration` | 7 nights | `SHOW` |
| `SLEEP_QUALITY_REDUCED` | `sleep_efficiency`, `sleep_duration` | 14 nights | `SHOW_WITH_WARNING` |
| `ACTIVITY_LOAD_HIGH` | `active_minutes` | 14 days | `SHOW` |
| `RECOVERY_EVIDENCE_INCOMPLETE` | — (inverted claim) | 14 days | `SHOW` |
| `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` | the named signal | 14 days | `SHOW_WITH_WARNING` |

Two carry permanent ceilings. `SLEEP_QUALITY_REDUCED` can never be shown without its
`LOW_REFERENCE_AGREEMENT` disclosure, because consumer sleep staging disagrees materially
with clinical measurement and no validation against polysomnography exists here yet. The
anomaly claim can only ever say "re-measure this"; it never names a cause, and a test
asserts its explanations contain no diagnostic language.

`RECOVERY_EVIDENCE_INCOMPLETE` is inverted: it asserts *insufficiency*, so it is supported
when the recovery inputs are inadequate and rejected when they are all fine. An empty
evidence bundle is its strongest support, not a reason to abstain.

Inspect the live contract without reading the code:

```bash
./.venv/bin/python -m engine.cli policies
curl -s localhost:8000/v1/claim-types | jq '.[0]'
```

## How a decision is made

Diagram and module map: [docs/architecture.md](docs/architecture.md).

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

Gates outrank the score in both directions. That is the fail-closed mechanism: when a
learned component is added, it will be able to lower confidence and will not be able to
argue the engine into displaying a claim whose evidence failed a hard requirement. A
property test asserts this over the input space.

Explanations are assembled only from fields of the trace — no language model in the path
([ADR 0003](docs/decisions/0003-no-llm-in-v01.md)). A test asserts every reported reason
code's detail text appears verbatim in the explanation, and that no explanation contains
diagnostic or advisory language.

## Evaluation

The primary metric is the **unsupported-show rate**: the share of cases whose evidence did
not support the claim that were displayed anyway. `SHOW_WITH_WARNING` counts as displayed
— a warning next to an unjustified conclusion is still an unjustified conclusion on the
screen. Protocol, label taxonomy, and splits were fixed before any modelling in
[docs/evaluation-protocol.md](docs/evaluation-protocol.md).

```bash
./.venv/bin/python -m eval.run_eval --seeds 5
```

**Synthetic evidence only, 3057 scored cases over 30 synthetic subjects, 17 corruption
types × 3 severities:**

| baseline | unsupported-show rate (95% CI) | over-abstention | decision coverage | macro F1 |
|---|---|---|---|---|
| B0 always show | 1.0000 [1.0000, 1.0000] | 0.0000 | 1.0000 | 0.14 |
| B1 data-present check | 0.9583 [0.9436, 0.9723] | 0.0000 | 0.8803 | 0.14 |
| **B2 rules engine (this)** | **0.0000 [0.0000, 0.0000]** | **0.0000** | 0.2699 | 0.78 |
| B3 learned, no abstention | built and tested on real data — see below | | | |
| B4 hybrid, calibrated | built and tested on real data — see below | | | |

Intervals bootstrap over **subjects**, not cases, because cases from one subject share a
baseline and a window.

### Where the safety actually comes from

The report also sweeps the display thresholds with every gate held fixed:

| warn_above | show_above | coverage | unsupported-show | over-abstention |
|---|---|---|---|---|
| 0.30 | 0.40 | 0.2699 | 0.0000 | 0.0000 |
| **0.55 (default)** | **0.75** | **0.2699** | **0.0000** | **0.0000** |
| 0.65 | 0.82 | 0.2676 | 0.0000 | 0.0085 |
| 0.85 | 0.94 | 0.1560 | 0.0000 | 0.4218 |

All 2232 unsupportable cases are withheld even at the most permissive thresholds, so
**100% of the engine's safety on this suite comes from deterministic gates, not from
threshold choices.** That is the design working — no threshold tuning, and no future
learned score, can undo a gate.

It is also the sharpest limitation of the suite: if the thresholds never decide an
unsupportable case, the suite cannot tell whether the thresholds are any good. Choosing an
operating point needs real data and an explicit cost for a wrong `SHOW` against a needless
abstention. Nothing here provides either.

### What that 0.0000 does and does not mean

It means the deterministic gates fire wherever the written contract says they should,
across every failure mode in the catalogue, and that the engine does not over-abstain on
the benign ones. As a regression harness that is valuable: a rise means a gate stopped
firing.

It is **not** a benchmark result. The labels come from
[`engine/measure.py`](engine/measure.py), a deliberately separate second implementation of
the same contract the engine implements, so a zero rate is an agreement result between two
readings of one document. Where they disagreed during development the disagreement was
resolved by checking which matched [docs/claim-contracts.md](docs/claim-contracts.md) —
sometimes fixing the engine, sometimes the labeller, all of it in the git history. And the
evidence itself is generated from Gaussian baselines with no sensor-error model, so it
tests the decision logic, not physiology.

Baselines B3 and B4 have been built and tested against real data, and the measured answer
was that they do not earn their place — see below.

## Results on real data — PMData

The engine has been evaluated against **PMData**: 16 subjects, ~5 months each, Fitbit
Versa 2. ([Thambawita et al., MMSys '20](https://dl.acm.org/doi/10.1145/3339825.3394926),
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), no changes made to the data.)
2186 subject-days, 1658 with daily resting heart rate.

```bash
./.venv/bin/python -m eval.pmdata_prepare   # once, ~15 min, needs the 1.4 GB download
./.venv/bin/python -m eval.pmdata_eval
```

It was chosen for one reason: this engine's claims are defined against a personal baseline
of 7–14 valid days, so a dataset with one session per subject cannot exercise them at all.
PMData gives months per subject, and it is Fitbit data, so the field semantics carry over
to the personal integration path.

### What the engine actually does on real days

| claim | days | decision coverage | most common reason for withholding |
|---|---|---|---|
| `RESTING_HEART_RATE_ELEVATED` | 1658 | 7.5% | `CLAIM_CONTRADICTED` (most days it just isn't elevated) |
| `ACTIVITY_LOAD_HIGH` | 2396 | 9.1% | `CLAIM_CONTRADICTED`, `INSUFFICIENT_HISTORICAL_COVERAGE` |
| `SLEEP_DURATION_LOW` | 1879 | 14.5% | `CLAIM_CONTRADICTED` |
| `SLEEP_QUALITY_REDUCED` | 1879 | 3.6% | `CLAIM_CONTRADICTED`, `EFFECT_BELOW_RELIABLE_THRESHOLD` |

An insight that fires on 7.5% of days is roughly one a fortnight, which is a sane rate for
"your resting heart rate is elevated". Nobody knew this number before; it was guessed.

### The gates hold on real noise

The corruption harness re-run on real subject-days the engine displays:
**unsupported-show rate 0.0000 across 1488 corrupted cases.** Duplication and reordering
changed no decision, which is the required behaviour.

### Three thresholds were wrong, and real data showed it

This is the part that mattered. Every threshold was reasoned, never measured, and
`docs/limitations.md` said so. Now:

| threshold | was | fired on | now |
|---|---|---|---|
| RHR `max_baseline_sd` | 8.0 bpm | **0.0%** of 1451 subject-days | 5.0 |
| activity `max_baseline_sd` | 45 min | **48.7%** of 2055 subject-days | 90 |
| sleep-quality `max_baseline_sd` | 12 pts | 0.3% of 1663 nights | 6.0 |
| RHR `min_baseline_days` | 14 | — | 28, disclosed band 14–27 |

- The resting-heart-rate stability gate sat at **twice the observed maximum** (real SD:
  median 1.78, p95 3.29, max 4.04 bpm). It could never act on anyone.
- The activity stability gate was the opposite error: it withheld **half of all activity
  claims** because people's activity varies day to day — which is the phenomenon the claim
  is about, not a defect in the evidence.
- A personal baseline takes a median of **48 days** to settle within 10% of its 60-day
  value, not 14. Requiring 48 would deny a new user any claim for seven weeks, so the bar
  moved to 28 with a disclosed 14–27 band. That split is a judgement, not a finding.
- The 3 bpm absolute floor was **confirmed** — real consecutive-day change is median 0.75,
  p95 2.32 bpm. It also exposed an interaction: at the median personal SD, 3 bpm is
  `z = 1.68`, so that floor, not the stated `show_z` of 1.5, is what actually binds.

Adopted as `claim-policy-0.2.0` only after measuring the effect (activity coverage
5.3% → 9.1%, resting heart rate 8.2% → 7.5%) and confirming the safety check still returned
0.0000 — a revision that buys coverage by weakening gates is a regression, not an
improvement. Full before/after:
[`eval/results/pmdata-policy-comparison-*.md`](eval/results/).

### Does machine learning help? Measured: no

The brief's own success criterion is whether a learned component beats the deterministic
baseline on held-out subjects. It was tested on real PMData evidence, 15,765 cases,
4-fold subject-held-out cross-validation, with calibration fitted on subjects disjoint from
both training and test:

| baseline | coverage | unsupported-show | ROC-AUC | Brier | ECE |
|---|---|---|---|---|---|
| **B2** rules engine | 0.0187 | **0.0000** | — | — | — |
| **B3** gradient boosting, no gates | 0.2304 | **0.2333** | 0.979 | 0.038 | 0.003 |
| **B4** gradient boosting, hybrid | 0.0187 | **0.0000** | 0.979 | 0.038 | 0.003 |

**Verdict: drop the learned component, ship the rules engine.**

The models learn the contract nearly perfectly — ROC-AUC 0.98 on held-out subjects, so the
features carry the information. But:

- **B3 is unsafe.** Without gates it displays **23% of inadequate evidence**. Ranking well
  is not the same as deciding well: a probability has no way to say "this requirement was
  not met".
- **B4 is identical to the engine**, because the gates decide first and the model only
  re-ranks what they let through. At the engine's own zero-risk operating point the model
  reaches *lower* coverage.

The general point: **when the decision rule is a known deterministic function of observable
features, a learned approximation can only add error.** There is no hidden signal to
discover, because the contract is written down. ML belongs at the Stage-2 question instead
— *is this PPG window trustworthy?* — where the target genuinely is not a known function of
the inputs.

The one thing the learned path offered is calibration (Brier 0.038, ECE 0.003), which the
engine has no equivalent of. Worth knowing; not a reason to put a model in the decision
path. Full write-up and the caveats that limit how far this travels:
[`eval/results/pmdata-learned-*.md`](eval/results/).

### Do the revised thresholds generalise? Second cohort says yes

`claim-policy-0.2.0` was set from PMData — 16 largely athletic Norwegian adults on a Fitbit
Versa 2. A threshold tuned to one cohort is a hypothesis about the next one, so it was
tested against **LifeSnaps**: 71 geographically distributed participants, ~3 months each,
Fitbit Sense. ([Yfantidou et al., *Scientific Data* 9, 663 (2022)](https://doi.org/10.1038/s41597-022-01764-x),
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).) **No threshold was tuned on it.**

| | PMData (16 athletes) | LifeSnaps (71 general) |
|---|---|---|
| `show_z ≥ 1.5` fires on | 10.5% of days | **11.0%** |
| `min_wear_coverage` rejects | 10.6% | 6.7% |
| `min_absolute_delta` blocks | 79.5% | 71.5% |
| baseline settles after | 48 days | 40 days |
| resting-heart-rate decision coverage | 7.5% | **8.3%** |
| unsupported-show on corrupted real evidence | 0.0000 | **0.0000** (3084 cases) |

The thresholds hold. The effect-size threshold fires on almost exactly the same share of
days in both cohorts, and decision coverage lands within a point. The tightened stability
gate (8.0 → 5.0 bpm) now fires on **0.3%** of days in a general population where it fired
on 0.0% among athletes — which is what a working gate should do.

Both cohorts also confirm the compromise in `min_baseline_days`: a personal baseline takes
40–48 days to settle and the contract requires 28, so the gap is real and documented rather
than resolved.

### Fairness: no disparity detectable, and the study is too small to rule one out

LifeSnaps carries age, gender and BMI, which made the first subgroup analysis possible. The
risk here is not biased conclusions — the engine compares each person only to themselves —
but uneven **access**: a quality gate firing more often for one group would ration insights
while looking perfectly safe in aggregate.

| group | mean decision coverage | difference (95% CI) |
|---|---|---|
| male (n=41) vs female (n=26) | 7.2% vs 5.4% | −1.8% [−5.3%, +1.7%] — not distinguishable |
| age <30 (n=36) vs ≥30 (n=30) | 5.9% vs 7.0% | −1.1% [−4.7%, +2.4%] — not distinguishable |
| BMI <25 (n=46) vs ≥25 (n=20) | 5.5% vs 8.4% | +2.9% [−1.2%, +7.4%] — not distinguishable |

**This result is worth reading carefully, because the first version of it was wrong.** The
per-subject *medians* differ more than twofold by gender (5.1% vs 2.3%) and look alarming.
Bootstrapping over subjects shows the interval comfortably contains zero, and the mechanism
markers are near-identical (wear 0.71 vs 0.69, baseline SD 1.88 vs 2.03). The median was
sampling noise. Every subgroup comparison now carries an interval for exactly that reason.

The honest conclusion is **not** "the engine is fair" — it is that no disparity is
detectable at n=67, and that is far too small to rule out one that matters.

### One design lesson the data handed over

No Fitbit export carries a sync-completion timestamp. Without one the engine discloses
`SYNC_STATE_UNKNOWN` on **every** day, which caps every decision at `SHOW_WITH_WARNING` —
zero clean `SHOW`s across 1658 real days. The contract asks for a field that real exports
do not have, so an integrating app must supply it from its own sync layer. Both modes are
measured side by side in the report.

### What this still does not establish

PMData is 16 largely athletic adults on one device model, and none of them is a reference
standard. There is still no ground truth for whether an *uncorrupted* real day's claim was
actually supportable — only the corrupted cases carry known labels. The thresholds are now
tuned to these 16 people, which is better than tuned to nothing and is not general. No
calibration, no learned component.

## Fitbit / Google Health integration

The supported data path is Google Health API → [`engine/adapters/google_health.py`](engine/adapters/google_health.py).

```bash
./.venv/bin/python google_health/gh_fetch.py --days 30      # see google_health/README.md
./.venv/bin/python demo/google_health_demo.py --report
./.venv/bin/python demo/google_health_demo.py --timezone America/New_York
```

The adapter reads JSON that the fetch client already wrote. It does not import the auth
module, hold a token, or make a network call — a test enforces that through the import
graph — so no part of the engine can reach an OAuth secret. Its field mapping is
**unverified against live payloads**, written from the fetch client and the documented API
shape rather than by inspecting personal exports; `--report` prints exactly what did and
did not convert.

Two things the export does not carry are wear time and sync-completion time. The demo
leaves both unset rather than inventing them, so decisions come back with
`COVERAGE_UNKNOWN` and `SYNC_STATE_UNKNOWN` attached.

**Expect `WAIT_FOR_MORE_DATA`.** The owner's device history is short and the
baseline-dependent claims need 7 to 14 valid days. Abstaining is the correct answer when
the history is not there, and that is the product thesis working rather than the demo
failing. Personal data is a demonstration of integration only; it is never training data
and never evidence of validity.

**Raw accelerometer and gyroscope data are unavailable at any tier**, through both the
legacy Fitbit Web API and the Google Health API. That is established, not assumed:
[FITBIT_AIR_RESEARCH.md](FITBIT_AIR_RESEARCH.md) records three sessions of direct BLE
investigation of the Fitbit Air, ending at an authenticated DTLS channel that cannot be
opened without the device's own credentials. That investigation is closed
([ADR 0006](docs/decisions/0006-ble-research-closed.md)); motion-artifact features
therefore depend entirely on public datasets that ship raw accelerometer data.

## Layout

```
engine/            the engine: schemas, policies, features, gates, decisions, explanations
  policies/        six versioned claim contracts
  adapters/        Google Health, PMData and LifeSnaps -> canonical observations
  api/             FastAPI surface and the before/after demo page
  measure.py       independent contract measurement, used only to label evaluation cases
  corruptions.py   17 seeded failure injections
  synth.py         seeded synthetic evidence generators
eval/              synthetic suite, baselines, metrics, threshold sweep, PMData
                   preparation / evaluation / policy A-B, learned baselines B3+B4,
                   LifeSnaps external validation
tests/             327 tests
docs/              claim contracts, evaluation protocol, related work, cards, ADRs
demo/              Google Health demonstration script
tools/             secret scanner
scripts/ captures/ logs/     the completed BLE research, preserved read-only
google_health/     the supported API client (unchanged)
```

`engine/` imports nothing from `scripts/`, and a test enforces that separation.

## Development

```bash
./.venv/bin/python -m pytest                    # 327 tests
./.venv/bin/python -m eval.run_eval --seeds 5   # evaluation artifacts
./.venv/bin/python tools/secret_scan.py         # pre-publish gate
```

Or in a container, which carries no credentials and no personal data:

```bash
docker build -t reliability-engine . && docker run --rm -p 8000:8000 reliability-engine
```

## Limitations and scope

Read [docs/limitations.md](docs/limitations.md) before drawing any conclusion from this
repository. In short: no public-dataset validation, no learned component, no calibration,
synthetic evaluation only, one subject's short personal history, thresholds set from
domain reasoning rather than fitted to outcomes, and an unverified related-work claim.

This system does not diagnose disease, recommend treatment, or replace a clinician. It
reports whether measurements support a statement, never whether a person has a condition.

## Documents

- [PROJECT_BRIEF.md](PROJECT_BRIEF.md) — the authoritative specification
- [docs/claim-contracts.md](docs/claim-contracts.md) — the frozen evidence contracts
- [docs/evaluation-protocol.md](docs/evaluation-protocol.md) — metrics and splits, fixed before modelling
- [docs/related-work.md](docs/related-work.md) — scoping review; **citations unverified**
- [docs/model-card.md](docs/model-card.md) / [docs/data-card.md](docs/data-card.md)
- [docs/limitations.md](docs/limitations.md)
- [docs/resume-positioning.md](docs/resume-positioning.md) — what may and may not be claimed
- [docs/architecture.md](docs/architecture.md) — diagram and module map
- [docs/decisions/](docs/decisions/) — architecture decision records
- [data/DATASETS.md](data/DATASETS.md) — dataset manifest; PMData and LifeSnaps verified and in use, the rest unverified
- [FITBIT_AIR_RESEARCH.md](FITBIT_AIR_RESEARCH.md) — the completed BLE feasibility research
