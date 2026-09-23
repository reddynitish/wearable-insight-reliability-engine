# Synthetic evaluation report

**SYNTHETIC EVIDENCE ONLY. These numbers measure agreement between the engine and the claim contract it implements. They are not a measurement of real-world performance and must not be reported as one.**

- generated: `2026-09-23T18:45:35.284577+00:00`
- model version: `reliability-engine-0.1.0`
- policy version: `claim-policy-0.1.0`
- cases: 3120 total, 3057 scored, 63 excluded as borderline
- subjects: 30 (pseudonymous, synthetic)
- claim types: 6; corruptions: 17; severities: 3

Reproduce with:

```bash
./.venv/bin/python -m eval.run_eval --seeds 5
```

## Baseline comparison

`unsupported-show rate` is the primary metric: the share of cases whose evidence did not support the claim that were nonetheless displayed. `SHOW_WITH_WARNING` counts as displayed. Coverage is reported beside it because abstaining on everything would score a perfect 0.

| baseline | unsupported-show rate (95% CI) | over-abstention | decision coverage | macro F1 | p95 latency |
|---|---|---|---|---|---|
| `B0_always_show` | 1.0000 [1.0000, 1.0000] | 0.0000 | 1.0000 | 0.1417 | 0.000 ms |
| `B1_data_present` | 0.9583 [0.9436, 0.9723] | 0.3309 | 0.8803 | 0.1364 | 0.001 ms |
| `B2_rules_engine` | 0.0000 [0.0000, 0.0000] | 0.0000 | 0.2699 | 0.7752 | 0.110 ms |

Not implemented, and not claimed:

- `B3_learned_no_abstention` — requires public datasets; see data/DATASETS.md
- `B4_hybrid_calibrated` — requires public datasets and a calibration split

## By claim type (engine only)

| group | cases | unsupported-show | over-abstention | coverage |
|---|---|---|---|---|
| `ACTIVITY_LOAD_HIGH` | 510 | 0.0000 | 0.0000 | 0.2412 |
| `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` | 511 | 0.0000 | 0.0000 | 0.2192 |
| `RECOVERY_EVIDENCE_INCOMPLETE` | 520 | 0.0000 | 0.0000 | 0.6673 |
| `RESTING_HEART_RATE_ELEVATED` | 514 | 0.0000 | 0.0000 | 0.1265 |
| `SLEEP_DURATION_LOW` | 491 | 0.0000 | 0.0000 | 0.2016 |
| `SLEEP_QUALITY_REDUCED` | 511 | 0.0000 | 0.0000 | 0.1546 |

## By corruption (engine only)

| group | cases | unsupported-show | over-abstention | coverage |
|---|---|---|---|---|
| `conflict_summary_detail` | 171 | 0.0000 | 0.0000 | 0.2690 |
| `delay_sync` | 180 | 0.0000 | 0.0000 | 0.1389 |
| `destabilize_baseline` | 156 | 0.0000 | 0.0000 | 0.2949 |
| `drop_contiguous` | 180 | 0.0000 | 0.0000 | 0.2222 |
| `drop_random` | 179 | 0.0000 | 0.0000 | 0.3240 |
| `drop_timezone` | 180 | 0.0000 | 0.0000 | 0.0833 |
| `dropout_flags` | 179 | 0.0000 | 0.0000 | 0.2961 |
| `duplicate_samples` | 177 | 0.0000 | 0.0000 | 0.5085 |
| `flatline` | 177 | 0.0000 | 0.0000 | 0.4520 |
| `implausible_spike` | 180 | 0.0000 | 0.0000 | 0.0833 |
| `motion_artifact` | 179 | 0.0000 | 0.0000 | 0.3073 |
| `none` | 60 | 0.0000 | 0.0000 | 0.5000 |
| `shift_baseline` | 163 | 0.0000 | 0.0000 | 0.3252 |
| `shift_day_boundary` | 180 | 0.0000 | 0.0000 | 0.1667 |
| `shorten_wear` | 180 | 0.0000 | 0.0000 | 0.1944 |
| `shuffle_order` | 177 | 0.0000 | 0.0000 | 0.5085 |
| `stale_data` | 180 | 0.0000 | 0.0000 | 0.1667 |
| `truncate_baseline` | 179 | 0.0000 | 0.0000 | 0.1899 |

## By severity (engine only)

| group | cases | unsupported-show | over-abstention | coverage |
|---|---|---|---|---|
| `mild` | 996 | 0.0000 | 0.0000 | 0.3293 |
| `moderate` | 996 | 0.0000 | 0.0000 | 0.2470 |
| `none` | 60 | 0.0000 | 0.0000 | 0.5000 |
| `severe` | 1005 | 0.0000 | 0.0000 | 0.2199 |

## By evidence dimension (engine only)

| group | cases | unsupported-show | over-abstention | coverage |
|---|---|---|---|---|
| `baseline` | 498 | 0.0000 | 0.0000 | 0.2671 |
| `consistency` | 171 | 0.0000 | 0.0000 | 0.2690 |
| `coverage` | 539 | 0.0000 | 0.0000 | 0.2468 |
| `freshness` | 360 | 0.0000 | 0.0000 | 0.1528 |
| `integrity` | 714 | 0.0000 | 0.0000 | 0.3151 |
| `none` | 60 | 0.0000 | 0.0000 | 0.5000 |
| `signal_quality` | 715 | 0.0000 | 0.0000 | 0.2839 |

## By ground-truth evidence state (engine only)

| group | cases | unsupported-show | over-abstention | coverage |
|---|---|---|---|---|
| `SUPPORTABLE` | 825 | 0.0000 | 0.0000 | 1.0000 |
| `UNSUPPORTABLE_CONTRADICTED` | 646 | 0.0000 | 0.0000 | 0.0000 |
| `UNSUPPORTABLE_INSUFFICIENT` | 1094 | 0.0000 | 0.0000 | 0.0000 |
| `UNSUPPORTABLE_UNTRUSTWORTHY` | 492 | 0.0000 | 0.0000 | 0.0000 |

## Corruption catalogue

| corruption | dimension | degrades evidence | rationale |
|---|---|---|---|
| `conflict_summary_detail` | consistency | yes | the daily summary and the interval detail disagree, the signature of two different syncs or two different days being combined |
| `delay_sync` | freshness | yes | the last successful sync finished before the window ended, so part of the window has not reached the server yet |
| `destabilize_baseline` | baseline | yes | the baseline swings so widely that no change can be distinguished from ordinary day-to-day variation |
| `drop_contiguous` | coverage | yes | a contiguous block of the window is missing, as happens when a device is charged mid-window; wear time is reduced consistently with the hole |
| `drop_random` | coverage | yes | samples are missing at random across the window, as happens with an unreliable sync; the summary aggregates survive while the detail thins out |
| `drop_timezone` | integrity | yes | the subject's timezone is missing or invalid, so a calendar-day window cannot be placed; this is the quiet source of off-by-one-day insights |
| `dropout_flags` | signal_quality | yes | the pipeline reports sensor dropout or device removal across part of the window |
| `duplicate_samples` | integrity | no | samples are duplicated by a retried sync. The engine must deduplicate and still decide; abstaining here would be over-abstention, so this stays SUPPORTABLE |
| `flatline` | signal_quality | yes | the sensor sticks and repeats one value; a stuck reading looks like admirably steady physiology unless it is checked for |
| `implausible_spike` | signal_quality | yes | impossible values appear in the required signal, which indicates a sensor or pipeline fault rather than a physiological event |
| `motion_artifact` | signal_quality | yes | optical measurements are contaminated by movement, the dominant failure mode for wrist PPG; values survive but are not trustworthy |
| `shift_baseline` | baseline | yes | the baseline contains a level shift, so its mean describes neither the earlier nor the later period |
| `shift_day_boundary` | integrity | yes | the window is placed a few hours off, as happens after travel or a DST change, so the evidence describes a different day than the claim |
| `shorten_wear` | coverage | yes | the device was worn for much less of the window than the claim requires |
| `shuffle_order` | integrity | no | samples arrive out of chronological order. The engine must sort and still decide; this stays SUPPORTABLE for the same reason as duplication |
| `stale_data` | freshness | yes | the whole evidence bundle is old: evaluation happens long after the newest measurement and the last sync |
| `truncate_baseline` | baseline | yes | the personal baseline is too short to say what is normal for this person, the situation every new user is in |

## Excluded borderline cases

63 case(s) breached no contract clause but landed between the contradiction and display thresholds. Neither showing nor abstaining is demonstrably correct for them, so they are excluded from the rates above rather than scored against a label we cannot justify.

- `ACTIVITY_LOAD_HIGH` / `shift_baseline` / moderate: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (moderate) breached no requirement but left the effect at +0.55 SD, between contradiction and display; excluded from the headline rates
- `ACTIVITY_LOAD_HIGH` / `shift_baseline` / severe: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (severe) breached no requirement but left the effect at +0.75 SD, between contradiction and display; excluded from the headline rates
- `ACTIVITY_LOAD_HIGH` / `destabilize_baseline` / severe: the baseline swings so widely that no change can be distinguished from ordinary day-to-day variation (severe) breached no requirement but left the effect at +0.98 SD, between contradiction and display; excluded from the headline rates
- `ACTIVITY_LOAD_HIGH` / `shift_baseline` / severe: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (severe) breached no requirement but left the effect at +0.61 SD, between contradiction and display; excluded from the headline rates
- `ACTIVITY_LOAD_HIGH` / `shift_baseline` / moderate: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (moderate) breached no requirement but left the effect at +0.69 SD, between contradiction and display; excluded from the headline rates
- `ACTIVITY_LOAD_HIGH` / `shift_baseline` / severe: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (severe) breached no requirement but left the effect at +0.93 SD, between contradiction and display; excluded from the headline rates
- `ACTIVITY_LOAD_HIGH` / `shift_baseline` / moderate: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (moderate) breached no requirement but left the effect at +0.67 SD, between contradiction and display; excluded from the headline rates
- `ACTIVITY_LOAD_HIGH` / `shift_baseline` / severe: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (severe) breached no requirement but left the effect at +1.05 SD, between contradiction and display; excluded from the headline rates
- `ACTIVITY_LOAD_HIGH` / `destabilize_baseline` / severe: the baseline swings so widely that no change can be distinguished from ordinary day-to-day variation (severe) breached no requirement but left the effect at +0.88 SD, between contradiction and display; excluded from the headline rates
- `ACTIVITY_LOAD_HIGH` / `shift_baseline` / severe: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (severe) breached no requirement but left the effect at +0.69 SD, between contradiction and display; excluded from the headline rates
- `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` / `destabilize_baseline` / mild: the baseline swings so widely that no change can be distinguished from ordinary day-to-day variation (mild) breached no requirement but left the effect at +1.57 SD, between contradiction and display; excluded from the headline rates
- `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` / `shift_baseline` / mild: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (mild) breached no requirement but left the effect at +2.46 SD, between contradiction and display; excluded from the headline rates
- `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` / `destabilize_baseline` / mild: the baseline swings so widely that no change can be distinguished from ordinary day-to-day variation (mild) breached no requirement but left the effect at +1.98 SD, between contradiction and display; excluded from the headline rates
- `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` / `shift_baseline` / mild: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (mild) breached no requirement but left the effect at +2.83 SD, between contradiction and display; excluded from the headline rates
- `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` / `destabilize_baseline` / mild: the baseline swings so widely that no change can be distinguished from ordinary day-to-day variation (mild) breached no requirement but left the effect at +1.61 SD, between contradiction and display; excluded from the headline rates
- `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` / `shift_baseline` / mild: the baseline contains a level shift, so its mean describes neither the earlier nor the later period (mild) breached no requirement but left the effect at +2.80 SD, between contradiction and display; excluded from the headline rates
- `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` / `destabilize_baseline` / mild: the baseline swings so widely that no change can be distinguished from ordinary day-to-day variation (mild) breached no requirement but left the effect at +1.43 SD, between contradiction and display; excluded from the headline rates
- `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` / `destabilize_baseline` / severe: the baseline swings so widely that no change can be distinguished from ordinary day-to-day variation (severe) breached no requirement but left the effect at +1.38 SD, between contradiction and display; excluded from the headline rates
- `PHYSIOLOGICAL_ANOMALY_REQUIRES_CONFIRMATION` / `destabilize_baseline` / mild: the baseline swings so widely that no change can be distinguished from ordinary day-to-day variation (mild) breached no requirement but left the effect at +2.68 SD, between contradiction and display; excluded from the headline rates
- `RESTING_HEART_RATE_ELEVATED` / `destabilize_baseline` / moderate: the baseline swings so widely that no change can be distinguished from ordinary day-to-day variation (moderate) breached no requirement but left the effect at +0.84 SD, between contradiction and display; excluded from the headline rates
- ... and 43 more (see the JSON artifact)

## How to read these numbers

The engine's unsupported-show rate here reflects agreement between two separate implementations of the same written contract: `engine/` decides, and `engine/measure.py` labels. Where they disagreed during development, the disagreement was resolved by checking which one matched `docs/claim-contracts.md` -- sometimes fixing the engine, sometimes fixing the labeller. Both kinds of fix are recorded in the git history.

That makes this suite a strong **regression harness** and a weak **benchmark**. It proves the gates fire where the contract says they should, across 17 failure modes and 3 severities. It cannot show that the contract's thresholds are the right thresholds for real people, because the evidence here was generated from Gaussian baselines with no sensor-error model. Only the public-dataset work in `data/DATASETS.md` can do that.

