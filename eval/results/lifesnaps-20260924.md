# LifeSnaps external validation of `claim-policy-0.2.0`

**external validation only; no threshold is tuned on this dataset.**

- dataset: Yfantidou et al., 'LifeSnaps, a 4-month multi-modal dataset capturing unobtrusive snapshots of our lives in the wild', Scientific Data 9, 663 (2022). doi:10.1038/s41597-022-01764-x; data doi:10.5281/zenodo.7229547. Licensed CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); no changes were made to the data.
- 71 participants, 7083 subject-days
- engine `reliability-engine-0.1.0`, policy `claim-policy-0.2.0`
- generated `2026-09-24T15:21:33.046953+00:00`

The thresholds under test were set from PMData: 16 largely athletic Norwegian adults on a Fitbit Versa 2. LifeSnaps is 71 geographically distributed participants on a Fitbit Sense. If the thresholds describe both, the revision generalises.

## Do the PMData-derived thresholds hold on a different cohort?

| threshold | contract | LifeSnaps median | LifeSnaps p05–p95 | share affected |
|---|---|---|---|---|
| `min_wear_coverage` | 0.6 | 0.923 | 0.587 – 1.0 | 6.7% |
| `max_baseline_sd (resting heart rate, bpm)` | 5.0 | 2.371 | 1.462 – 3.937 | 0.3% |
| `max_baseline_shift (resting heart rate, bpm)` | 6.0 | 1.736 | 0.165 – 5.818 | 4.4% |
| `show_z (resting heart rate)` | 1.5 | 0.016 | -1.854 – 1.984 | 11.0% |
| `min_absolute_delta (resting heart rate, bpm)` | 3.0 | 1.857 | 0.186 – 5.84 | 71.5% |

Side by side with the cohort the thresholds came from:

| threshold | PMData median | LifeSnaps median | PMData share | LifeSnaps share |
|---|---|---|---|---|
| `min_wear_coverage` | 0.959 | 0.923 | 10.6% | 6.7% |
| `max_baseline_sd (resting heart rate, bpm)` | 1.787 | 2.371 | 0.0% | 0.3% |
| `max_baseline_shift (resting heart rate, bpm)` | 1.244 | 1.736 | 1.0% | 4.4% |
| `show_z (resting heart rate)` | -0.126 | 0.016 | 10.5% | 11.0% |
| `min_absolute_delta (resting heart rate, bpm)` | 1.465 | 1.857 | 78.7% | 71.5% |

**Baseline settling time:** median 40 days (p05 24, p95 55) across 53 participants with enough history, against the contract's 28-day mature bar.

## What the engine does on this cohort

| claim | days | coverage | most common reasons |
|---|---|---|---|
| `RESTING_HEART_RATE_ELEVATED` | 4422 | 8.3% | `COVERAGE_UNKNOWN`, `BASELINE_NOT_MATURE`, `CLAIM_CONTRADICTED` |
| `ACTIVITY_LOAD_HIGH` | 7083 | 4.1% | `CLAIM_CONTRADICTED`, `INSUFFICIENT_COVERAGE`, `BASELINE_SHIFT_DETECTED` |
| `SLEEP_DURATION_LOW` | 3550 | 13.8% | `CLAIM_CONTRADICTED`, `INSUFFICIENT_HISTORICAL_COVERAGE`, `BASELINE_NOT_MATURE` |
| `SLEEP_QUALITY_REDUCED` | 3550 | 3.2% | `LOW_REFERENCE_AGREEMENT`, `CLAIM_CONTRADICTED`, `BASELINE_NOT_MATURE` |

## Subgroup analysis: who gets an answer

The engine compares each person only to their own baseline, so it cannot be biased by a population reference. The risk is different: a quality gate that fires more often for one group rations insights unevenly while looking perfectly safe in the aggregate. Decision coverage for `RESTING_HEART_RATE_ELEVATED`, per participant, grouped:

**gender**

| group | participants | mean coverage | median | min | max |
|---|---|---|---|---|---|
| FEMALE | 26 | 5.4% | 2.3% | 0.0% | 21.1% |
| MALE | 41 | 7.2% | 5.1% | 0.0% | 32.6% |
| unknown | 4 | 11.1% | 13.2% | 3.1% | 14.8% |

Difference (FEMALE minus MALE): -1.8%, 95% CI [-5.3%, +1.7%] — not distinguishable from zero. Bootstrap over subjects, 5000 resamples.

**age_band**

| group | participants | mean coverage | median | min | max |
|---|---|---|---|---|---|
| <30 | 36 | 5.9% | 3.6% | 0.0% | 21.1% |
| >=30 | 30 | 7.0% | 4.3% | 0.0% | 32.6% |
| unknown | 5 | 11.2% | 11.8% | 3.1% | 14.8% |

Difference (<30 minus >=30): -1.1%, 95% CI [-4.7%, +2.4%] — not distinguishable from zero. Bootstrap over subjects, 5000 resamples.

**bmi_band**

| group | participants | mean coverage | median | min | max |
|---|---|---|---|---|---|
| 25 or over | 20 | 8.4% | 5.5% | 0.0% | 32.6% |
| under 25 | 46 | 5.5% | 3.5% | 0.0% | 21.1% |
| unknown | 5 | 11.2% | 11.8% | 3.1% | 14.8% |

Difference (25 or over minus under 25): +2.9%, 95% CI [-1.2%, +7.4%] — not distinguishable from zero. Bootstrap over subjects, 5000 resamples.

## Do the gates still hold on this cohort?

- 257 real subject-days the engine displays, corrupted 3084 ways
- **unsupported-show rate: 0.0000**
- benign corruptions that changed a decision: 0 of 514 (should be 0)

## Caveats on comparing the two datasets

- **Wear time is a different measurement.** PMData's wear minutes are minutes with an actual heart-rate sample; LifeSnaps has no such field, so wear is the sum of activity-level minute buckets. The coverage rows above are not measuring quite the same thing, and the LifeSnaps figure is the looser of the two.
- **The sleep window is reconstructed** from time-in-bed anchored to a nominal 07:00 wake, because the daily table has no clock times. Sleep results here are weaker evidence than the heart-rate results.
- **Timezone is unknown** and the cohort is geographically distributed; UTC is assumed, so day boundaries are wrong for most participants by some offset.
- Age and BMI are coarse bands in the source data, not values.

