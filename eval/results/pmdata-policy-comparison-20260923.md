# Proposed policy revision, measured on PMData

Every threshold in `claim-policy-0.1.0` was reasoned from domain knowledge. PMData is the first evidence about any of them. This is what the proposed revision does to real decisions across 16 subjects.

Generated `2026-09-23T19:33:12.598855+00:00`.

## Changes and why

### `RESTING_HEART_RATE_ELEVATED` · `max_baseline_sd`: 8.0 → 5.0

8.0 bpm fired on 0.0% of 1451 real subject-days. Observed personal baseline SD: median 1.78, p95 3.29, max 4.04 bpm. The reasoned value sat at twice the observed maximum, so the gate could not act on any real person. 5.0 sits above the observed range with headroom for a less athletic population while remaining capable of firing.

### `RESTING_HEART_RATE_ELEVATED` · `min_baseline_days`: 14 → 28

A personal resting-heart-rate SD takes a median of 48 days to settle within 10% of its 60-day value (range 43-58 across 13 subjects). 14 days compares against a normal that is still moving. Requiring 48 would deny every new user any claim for seven weeks, so the mature bar moves to 28 and the disclosed band widens to 14-27 rather than adopting the full settling time.

### `RESTING_HEART_RATE_ELEVATED` · `good_baseline_days`: 28 → 56

Raised from 28 to 56 so the maturity score keeps a gradient above the new floor: with the mature bar at 28 days, leaving 'comfortable' at 28 would make the score jump from 0.5 to 1.0 at a single day boundary. 56 brackets the observed median settling time of 48 days.

### `RESTING_HEART_RATE_ELEVATED` · `warn_baseline_days`: 7 → 14

Raised from 7 to 14 to match: below two weeks the baseline is not merely immature, it is uninformative, and the observed settling curve gives no reason to show a claim against it even with a disclosure.

### `ACTIVITY_LOAD_HIGH` · `max_baseline_sd`: 45.0 → 90.0

45 minutes fired on 48.7% of 2055 real subject-days and was the single most common reason an activity claim was withheld. Observed baseline SD: median 44.45, p95 73.84, max 90.97 minutes. Day-to-day variation in activity is the phenomenon the claim is about, not a defect in the evidence, so a stability gate calibrated like a physiological signal's was the wrong shape. 90 keeps the gate for a genuinely absurd baseline without rejecting normal training variation.

### `SLEEP_QUALITY_REDUCED` · `max_baseline_sd`: 12.0 → 6.0

12 percentage points fired on 0.3% of 1663 real subject-nights. Observed baseline SD of Fitbit's sleep efficiency: median 2.56, p95 4.41, max 12.82. 6.0 sits above the 95th percentile and can still act.

## Kept, but no longer only reasoned

### `RESTING_HEART_RATE_ELEVATED.min_absolute_delta`

Kept at 3.0 bpm, and now supported rather than merely reasoned. Consecutive-day absolute change in real resting heart rate: median 0.75, p95 2.32 bpm, so 3.0 sits just above the 95th percentile of ordinary daily fluctuation. Note the interaction this reveals: at the median personal SD of 1.78 bpm, 3.0 bpm corresponds to z=1.68, so the absolute floor -- not the stated show_z of 1.5 -- is the binding constraint for a typical subject. Fitbit's own reported error on daily resting heart rate is far larger (median 6.81 bpm), but that is dominated by systematic error in estimating the absolute level, which cancels in a within-person day-to-day difference; it is not the right quantity for this floor.

### `RESTING_HEART_RATE_ELEVATED.min_wear_coverage`

Kept at 0.60. It rejects 10.6% of real subject-days, with observed wear ratio median 0.96 and p05 0.41 -- a floor that acts on a real minority rather than on nobody or everybody.

### `SLEEP_DURATION_LOW.max_baseline_sd`

Kept at 120 minutes. Observed baseline SD median 68.6, p95 126.2; the threshold fires on 6.4% of nights, which is the intended shape.

## Effect on real decisions

| claim | coverage before | coverage after | delta |
|---|---|---|---|
| `RESTING_HEART_RATE_ELEVATED` | 8.2% | 7.5% | -0.7% |
| `ACTIVITY_LOAD_HIGH` | 5.3% | 9.1% | +3.8% |
| `SLEEP_DURATION_LOW` | 14.5% | 14.5% | +0.0% |
| `SLEEP_QUALITY_REDUCED` | 3.6% | 3.6% | +0.0% |

Reason codes that moved:

- **RESTING_HEART_RATE_ELEVATED** — `BASELINE_NOT_MATURE` +215, `INSUFFICIENT_HISTORICAL_COVERAGE` +91, `CLAIM_CONTRADICTED` -69, `MARGINAL_SUPPORT` -20, `EFFECT_BELOW_RELIABLE_THRESHOLD` -12
- **ACTIVITY_LOAD_HIGH** — `BASELINE_UNSTABLE` -1046, `CLAIM_CONTRADICTED` +640, `MARGINAL_SUPPORT` +36
- **SLEEP_DURATION_LOW** — no change
- **SLEEP_QUALITY_REDUCED** — no change

## Safety check under the revision

A revision that buys coverage by weakening the gates would be a regression, not an improvement. The corruption harness, re-run on real evidence under the revised thresholds:

- corrupted cases: 1356
- displayed anyway: 0
- **unsupported-show rate: 0.0000**

## Honest limits of this revision

PMData is 16 people, largely athletic, over five months on one device model. A threshold tuned to their distribution is tuned to them. The revision is defensible because it corrects thresholds that demonstrably could not act at all, or that fired on half of all days -- errors of shape, not of decimal places. It is not a claim that these values are right for a general population, and the next dataset may move them again.

Two of the changes tighten (`max_baseline_sd` for resting heart rate and sleep quality, `min_baseline_days`) and one loosens (`max_baseline_sd` for activity). The loosening is the one to watch: it removes a gate that was withholding half of all activity claims, so the safety check above matters most for that claim.

