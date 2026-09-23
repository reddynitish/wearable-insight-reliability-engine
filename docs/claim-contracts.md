# Claim contracts (frozen for v0.1)

Status: **`claim-policy-0.2.0`.** Changing any threshold in this document requires a new
`policy_version` and a new row in the change log at the bottom.

Thresholds marked **[PMData]** were revised or confirmed against real measurements — 16
subjects, ~5 months each, Fitbit Versa 2 (Thambawita et al., MMSys '20, CC BY 4.0). Every
other number in this document is still reasoned, not measured. The evidence and the
before/after effect on real decisions are in
`eval/results/pmdata-policy-comparison-*.md`.

A *claim contract* is the machine-checkable specification of what evidence a claim
needs before the engine may return `SHOW`. Each contract is implemented as a
versioned `ClaimPolicy` in `engine/policies/` and is the single source of truth for
the deterministic gates.

## 0. Vocabulary

| Term | Meaning |
|---|---|
| target window | the period the claim is *about* (e.g. "today") |
| baseline window | the trailing period used to establish the subject's personal normal |
| coverage | observed evidence / expected evidence inside a window |
| freshness | age of the newest relevant observation and of the last successful sync |
| effect size | deviation from personal baseline expressed in baseline standard deviations (`z`) |
| hard gate | deterministic condition whose failure forces `REJECT` or `WAIT_FOR_MORE_DATA` regardless of any model score |

All timestamps are timezone-aware UTC internally. Day boundaries are resolved in the
subject's reported IANA timezone and then converted; a missing timezone is itself a
gate failure for any claim with a day-boundary-dependent window (see
`TIMEZONE_UNKNOWN`).

## 1. Shared preconditions (apply to every claim type)

These are evaluated before any claim-specific rule.

| ID | Condition | Failing decision |
|---|---|---|
| `UNSUPPORTED_CLAIM_TYPE` | claim type has no registered policy | `REJECT` |
| `MISSING_REQUIRED_SIGNAL` | a signal listed in `required_signals` has zero usable observations | `WAIT_FOR_MORE_DATA` |
| `IMPLAUSIBLE_VALUES` | > 20% of target-window observations for a required signal fall outside its physiological range | `REJECT` |
| `WINDOW_NOT_CLOSED` | `evaluated_at` precedes `target_window.end` and the policy requires a closed window | `WAIT_FOR_MORE_DATA` |
| `SYNC_INCOMPLETE` | `context.sync_completed_at` precedes `target_window.end` | `WAIT_FOR_MORE_DATA` |
| `STALE_DATA` | newest relevant observation older than `max_data_age` | `WAIT_FOR_MORE_DATA` |
| `TIMEZONE_UNKNOWN` | day-boundary-dependent window with no subject timezone | `WAIT_FOR_MORE_DATA` |
| `DUPLICATE_OBSERVATIONS` | identical (signal, measured_at) pairs present | deduplicated, recorded as a quality note |
| `OUT_OF_ORDER_OBSERVATIONS` | observations not monotonic in `measured_at` | sorted, recorded as a quality note |

Out-of-range values are dropped from feature computation *and* counted; they never
silently disappear. Deduplication keeps the first occurrence and records the count.

## 2. Physiological plausibility ranges

Used only to reject impossible readings, not to judge health. Ranges are deliberately
wide; they exclude sensor faults, not unusual people.

| Signal | Unit | Plausible range | Rationale |
|---|---|---|---|
| `heart_rate` | bpm | 25 – 220 | below/above this is a sensor fault or a non-resting artifact |
| `resting_heart_rate` | bpm | 30 – 120 | daily aggregate; outside this a device error is far more likely |
| `hrv_rmssd` | ms | 1 – 300 | |
| `spo2` | % | 50 – 100 | |
| `respiratory_rate` | breaths/min | 4 – 40 | |
| `sleep_duration` | minutes | 0 – 1080 | 18 h upper bound for one sleep period |
| `sleep_efficiency` | % | 0 – 100 | |
| `steps` | count | 0 – 100000 | per day |
| `active_minutes` | minutes | 0 – 1440 | per day |
| `wear_minutes` | minutes | 0 – 1440 | per day |

A value exactly on a boundary is **inside** the range.

## 3. Claim contracts

### 3.1 `RESTING_HEART_RATE_ELEVATED`

> "Your resting heart rate is elevated today."

| Requirement | Value |
|---|---|
| required signals | `resting_heart_rate` (target window) |
| supporting signals | `heart_rate`, `wear_minutes`, `steps`, `sleep_duration` |
| target window | one closed subject-local day |
| resolution | one daily aggregate, or ≥ 1 valid aggregate per target window |
| min wear coverage | 0.60 of the target window; overnight coverage ≥ 0.50 |
| max data age | 36 h since measurement, 24 h since sync |
| min baseline | **[PMData]** 28 valid days; `SHOW_WITH_WARNING` band 14–27 days; < 14 days ⇒ `WAIT_FOR_MORE_DATA` |
| baseline stability | **[PMData]** baseline day-to-day SD ≤ 5 bpm, and no detected level shift > 6 bpm between baseline halves |
| effect threshold | `z ≥ 1.5` against baseline mean/SD **and** **[PMData]** absolute delta ≥ 3 bpm |
| warn band | `1.0 ≤ z < 1.5`, or any single nonfatal limitation |
| contradiction | `z ≤ 0.5` ⇒ `CLAIM_CONTRADICTED` ⇒ `REJECT` |
| known confounders | intense exercise in the preceding 24 h, alcohol, illness, altitude change, device change, ambient heat, first night with a new wear position |
| consistency checks | RHR elevation with no change in activity load, sleep, or HRV is *weaker* evidence, not stronger; a same-day `hrv_rmssd` rise > 1 SD while RHR rises > 1.5 SD is flagged `CONFLICTING_SIGNALS` |

The absolute-delta floor exists because a very tight baseline (SD ≈ 1 bpm) makes
`z ≥ 1.5` reachable by a 2 bpm change that is inside device measurement error.

**[PMData] What real data changed here, and what it confirmed.**

- *Baseline length, 14 → 28 days.* A personal resting-heart-rate SD takes a median of 48
  days to settle within 10% of its 60-day value (range 43–58 across 13 subjects with
  enough history). Fourteen days was comparing against a normal that was still moving.
  Requiring 48 would deny every new user a claim for seven weeks, so the mature bar moved
  to 28 and the disclosed band widened to 14–27 rather than adopting the full settling
  time. Cost on real data: resting-heart-rate decision coverage fell from 8.2% to 7.5%.
- *Baseline stability, 8 → 5 bpm.* The reasoned 8 bpm fired on **0.0%** of 1451 real
  subject-days: observed SD is median 1.78, p95 3.29, max 4.04, so the gate sat at twice
  the observed maximum and could never act on anyone. 5.0 stays above the observed range
  while remaining capable of firing.
- *Absolute-delta floor, 3 bpm, confirmed.* Consecutive-day absolute change in real
  resting heart rate is median 0.75, p95 2.32 bpm, so 3 bpm sits just above ordinary daily
  fluctuation. **But it also revealed an interaction:** at the median personal SD of 1.78
  bpm, 3 bpm corresponds to `z = 1.68`, so the absolute floor — not the stated `show_z` of
  1.5 — is what actually binds for a typical person. The stated z threshold is effectively
  unreachable on its own. Both are kept, and the precedence is now documented rather than
  accidental.
- *A number deliberately not used.* Fitbit reports its own error on daily resting heart
  rate, median 6.81 bpm, which would argue for a far higher floor. It is not the right
  quantity: that error is dominated by systematic error in estimating the absolute level,
  which cancels in a within-person day-to-day difference.

### 3.2 `SLEEP_DURATION_LOW`

| Requirement | Value |
|---|---|
| required signals | `sleep_duration` |
| supporting signals | `sleep_efficiency`, `wear_minutes`, `heart_rate` |
| target window | one closed subject-local sleep day |
| min coverage | recorded sleep period must cover ≥ 0.80 of its own span with no gap > 60 min |
| max data age | 36 h measurement, 24 h sync |
| min baseline | 7 valid nights; warn band 5–6; < 5 ⇒ `WAIT_FOR_MORE_DATA` |
| baseline stability | SD ≤ 120 min |
| effect threshold | `z ≤ -1.0` **and** absolute delta ≥ 45 min below baseline mean |
| warn band | `-1.0 < z ≤ -0.7` |
| contradiction | `z ≥ -0.25` ⇒ `REJECT` |
| hard gate | a single recorded sleep period < 120 min with daytime wear gaps is treated as `DEVICE_NOT_WORN`, not as short sleep |
| known confounders | naps counted as main sleep, travel, shift work, unrecorded second sleep period |

The `DEVICE_NOT_WORN` gate matters: the most common cause of "2 h of sleep" in
wearable data is a device on a nightstand, not a sleepless night. Failing closed here
is the whole point of the product.

### 3.3 `SLEEP_QUALITY_REDUCED`

| Requirement | Value |
|---|---|
| required signals | `sleep_efficiency`, `sleep_duration` |
| supporting signals | sleep stages, `hrv_rmssd`, `respiratory_rate` |
| target window | one closed sleep period |
| min coverage | 0.85 of the sleep period with stage or efficiency data |
| max data age | 36 h measurement, 24 h sync |
| min baseline | 14 nights; warn band 10–13; < 10 ⇒ `WAIT_FOR_MORE_DATA` |
| baseline stability | **[PMData]** baseline SD ≤ 6 points (was 12, which fired on 0.3% of 1663 real nights; observed median 2.56, p95 4.41) |
| effect threshold | `z ≤ -1.0` on efficiency **and** absolute drop ≥ 5 percentage points |
| contradiction | `z ≥ -0.25` ⇒ `REJECT` |
| consistency | efficiency drop while duration rose ≥ 1 SD ⇒ `SUMMARY_DETAIL_MISMATCH` unless stage data explains it |
| note | consumer sleep staging disagrees materially with polysomnography; this claim ships with a permanent `LOW_REFERENCE_AGREEMENT` disclosure and can never exceed `SHOW_WITH_WARNING` until Stage-2 validation against a PSG-labelled dataset exists |

### 3.4 `ACTIVITY_LOAD_HIGH`

| Requirement | Value |
|---|---|
| required signals | `active_minutes` or `steps` |
| supporting signals | `heart_rate`, `active_energy`, `exercise` sessions |
| target window | one closed subject-local day |
| min wear coverage | 0.70 of waking hours |
| max data age | 36 h measurement, 24 h sync |
| min baseline | 14 days; warn band 7–13; < 7 ⇒ `WAIT_FOR_MORE_DATA` |
| baseline stability | **[PMData]** baseline SD ≤ 90 minutes (was 45) |
| effect threshold | `z ≥ 1.5` and absolute delta ≥ 20% above baseline mean |
| contradiction | `z ≤ 0.5` ⇒ `REJECT` |
| consistency | high step count with flat heart rate and no exercise session ⇒ `CONTEXT_CONFLICT` (vehicle/handlebar artifact) |

This is the one claim family where the underlying measurement (step counting) is
comparatively reliable, which makes it a useful control in evaluation: if the engine
abstains as often here as it does on sleep staging, the abstention logic is
mis-calibrated.

**[PMData] The stability gate here was the worst-specified threshold in the whole
contract.** At 45 minutes it fired on 48.7% of 2055 real subject-days and was the single
most common reason an activity claim was withheld — the engine was refusing half of all
activity claims because people's activity varies from day to day. Observed baseline SD is
median 44.45, p95 73.84, max 90.97 minutes. Day-to-day variation in activity *is* the
phenomenon this claim is about, not a defect in the evidence, so a stability gate
calibrated like a physiological signal's was the wrong shape entirely. Raising it to 90
nearly doubled real decision coverage, 5.3% → 9.1%, with no loss on the safety check.

### 3.5 `RECOVERY_EVIDENCE_INCOMPLETE`

> "There is not enough evidence to judge your recovery."

This claim is inverted: it asserts *insufficiency*, so its evidence contract is the
mirror image of the others. It is supported when the recovery inputs
(`resting_heart_rate`, `hrv_rmssd`, `sleep_duration`) are jointly inadequate.

| Requirement | Value |
|---|---|
| required signals | none individually required |
| target window | one closed subject-local day |
| supported when | ≥ 1 of the three recovery inputs is missing, stale, below coverage, or below baseline maturity |
| contradiction | all three present, fresh, above coverage, and with mature baselines ⇒ `REJECT` (evidence *is* complete) |
| never | this claim must never be escalated into a statement about the subject's actual recovery state |

### 3.6 `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION`

> "One of your readings is unusual enough to be worth re-measuring."

| Requirement | Value |
|---|---|
| required signals | the anomalous signal, named in the claim |
| target window | the interval containing the anomaly |
| min coverage | 0.50 of the anomaly interval |
| max data age | 12 h measurement, 12 h sync (a stale anomaly is not actionable) |
| min baseline | 14 days |
| effect threshold | `|z| ≥ 3.0` sustained across ≥ 2 independent observations |
| hard gate | a single-sample excursion, or any excursion with `MOTION_ARTIFACT` or `SENSOR_DROPOUT` on the same interval, ⇒ `WAIT_FOR_MORE_DATA` |
| hard ceiling | this claim may never return `SHOW` without warning; the maximum decision is `SHOW_WITH_WARNING`, and the explanation must say "re-measure", never name a condition |

## 4. Decision thresholds (v0.1, rules-only)

The rules engine computes five evidence scores in `[0, 1]` and a claim-support
probability. With no learned model present, support is a deterministic function of
effect size and evidence quality.

| Decision | Condition |
|---|---|
| `REJECT` | any hard-reject gate fires, or support ≤ `reject_below` (default 0.20) |
| `WAIT_FOR_MORE_DATA` | any wait gate fires, or `reject_below` < support < `warn_above` (default 0.55) |
| `SHOW_WITH_WARNING` | `warn_above` ≤ support < `show_above` (default 0.75), or support ≥ `show_above` with ≥ 1 nonfatal limitation |
| `SHOW` | support ≥ `show_above`, no gate fired, no nonfatal limitation |

A `SHOW_WITH_WARNING` reached by the support score alone, with no gate fired, carries the
reason code `MARGINAL_SUPPORT` and a limitation stating the score and the display threshold.
Announcing "supportable, with limitations" and then listing none would be incoherent, and
the marginality is itself the limitation worth disclosing.

Gate precedence: reject gates outrank wait gates. Within a decision the reason codes
are reported in the order the dimensions are evaluated (coverage, freshness, quality,
consistency, baseline, claim support), which keeps traces diffable across versions.

`confidence` is confidence **in the decision**, not in the claim. An abstention with
overwhelming evidence of insufficiency is a high-confidence `WAIT_FOR_MORE_DATA`.
Conflating the two is the most common way a system like this misleads a reader, so
the two numbers are always reported separately.

## 5. Change log

| policy_version | date | change |
|---|---|---|
| `claim-policy-0.1.0` | 2026-09-23 | initial freeze: six claim families, thresholds above |
| `claim-policy-0.1.0` | 2026-09-23 | reporting clarification, no threshold change: a score-driven `SHOW_WITH_WARNING` now reports `MARGINAL_SUPPORT`; a `WINDOW_MISALIGNED` finding is fatal only when it removes a signal the claim requires, and a note otherwise |
| `claim-policy-0.2.0` | 2026-09-23 | first evidence-based revision, from PMData. `RESTING_HEART_RATE_ELEVATED`: `min_baseline_days` 14→28, `warn_baseline_days` 7→14, `good_baseline_days` 28→56, `max_baseline_sd` 8.0→5.0. `ACTIVITY_LOAD_HIGH`: `max_baseline_sd` 45→90. `SLEEP_QUALITY_REDUCED`: `max_baseline_sd` 12→6. `min_absolute_delta` confirmed at 3.0 and its precedence over `show_z` documented. Effect on real decisions and the safety re-check: `eval/results/pmdata-policy-comparison-*.md` |
