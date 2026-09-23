# Limitations

Read this before drawing any conclusion from this repository. It is ordered by how badly
each item would mislead someone who skipped it.

## 1. No result here is evidence of real-world performance

The only evaluation that exists runs on seeded synthetic evidence. The engine's
unsupported-show rate of 0.0000 is agreement between two independent implementations of one
written contract — `engine/` decides, `engine/measure.py` labels — across 17 injected
failure modes. That makes the suite a strong regression harness and a weak benchmark.

It does not show that the contract's thresholds are correct for real people, because the
evidence was generated from Gaussian baselines with independent days and no sensor-error
model. A gate that fires exactly when the specification says it should can still be
specified wrong.

## 2. The thresholds are reasoned, not fitted

Every number in `docs/claim-contracts.md` — 14 baseline days, 60% wear coverage, 1.5 SD to
display, a 3 bpm absolute floor — comes from domain reasoning about sensor error, reporting
conventions, and baseline statistics. **None was fitted to outcomes.** They are the most
likely part of this system to be wrong, and they are the reason Stage 2 exists.

## 3. The synthetic suite cannot validate the thresholds

The threshold sweep in the evaluation report settles this. Holding the gates fixed and
moving the display thresholds from permissive (0.30 / 0.40) to strict (0.85 / 0.94) leaves
the unsupported-show rate at 0.0000 throughout: every unsupportable case in the suite is
withheld by a deterministic gate, so the score-based path never decides one.

That is the fail-closed design behaving as intended, and it means the suite gives **no
evidence at all** about whether 1.5 SD is the right point to display a resting-heart-rate
claim, or 0.55 the right point to warn. Those numbers are currently unvalidated by
anything. The only thing the sweep measures about them is the cost of tightening: at
0.85 / 0.94, over-abstention rises to 0.42 while risk does not improve, because there was
no risk left to remove.

Picking an operating point honestly requires real data and a stated cost for a wrong
`SHOW` relative to a needless abstention. Neither exists here.

## 4. There is no learned component and no calibration

`claim_support_probability` is a deterministic score, not a calibrated probability. Every
response carries `support_is_calibrated: false` for that reason. No calibration curve,
expected calibration error, Brier score, or selective-risk-versus-coverage curve exists,
because producing an honest one needs held-out real subjects.

Consequently baselines B3 (learned, no abstention) and B4 (hybrid, calibrated) from
`docs/evaluation-protocol.md` are unimplemented and declared as such rather than faked. The
project's own success criterion — that a learned component must beat the rules baseline on
held-out subjects — has not been tested. If it is tested and fails, the documented outcome
is that negative result.

## 5. No public dataset has been touched

All five rows in `data/DATASETS.md` are marked UNVERIFIED and nothing has been downloaded.
The citations, URLs, sizes, and licence names there were drafted from memory during
AI-assisted planning and must be checked against primary sources before any file is
fetched. No dataset name from that file should appear in a README, paper, or resume bullet
until its row is verified.

## 6. The novelty claim is unverified

`docs/related-work.md` states the position conservatively and lists seven verification
tasks, none started. In particular, the vendors (Apple, Fitbit, Garmin, Oura) visibly
withhold metrics until a baseline exists, which is claim-aware abstention in production;
whether any of it is exposed as a reusable typed layer is an open question. FHIR's
`dataAbsentReason` and the Open mHealth / IEEE 1752 schemas may already model part of this,
and the reason codes here should map onto them where they overlap. **No citation in that
document has been checked against its primary source.** If verification shows this
combination is already published, the honest response is to reposition the project as a
careful open implementation and say so.

## 7. Personal Fitbit data proves integration and nothing more

The owner's device history is short. Baseline-dependent claims therefore return
`WAIT_FOR_MORE_DATA`, which is correct rather than a failure, and it means the personal
data cannot validate anything. It is never used as training data.

The Google Health adapter's field mapping is unverified against live payloads. It was
written from the fetch client and the documented API shape, not by inspecting personal
exports, and its candidate key lists are deliberately generous. Run
`demo/google_health_demo.py --report` to see what does and does not convert on a real
export; unmapped points are counted and dropped, never converted with a guessed value.

## 8. Raw motion data is unavailable, which constrains the whole Stage-2 plan

Neither the legacy Fitbit Web API nor the Google Health API exposes accelerometer or
gyroscope samples, and `FITBIT_AIR_RESEARCH.md` documents three sessions establishing that
the private BLE path ends at an authenticated DTLS channel requiring the device's own
credentials. That investigation is closed ([ADR 0006](decisions/0006-ble-research-closed.md)).

Motion-artifact quality estimation therefore depends entirely on public datasets that ship
raw accelerometer data — principally PPG-DaLiA. If that dataset turns out to be
unobtainable or unusable, the signal-quality stage has no personal-data fallback.

## 9. Fairness and subgroup performance are untested

The engine compares each person only to their own baseline, which avoids population-
reference bias by construction. But PPG signal quality is known to vary with skin tone,
tattoos, perfusion, and wear position, and a quality gate that fires more often for some
people would ration insights unevenly while looking safe in the aggregate. Nothing here
measures that. No fairness claim is made. Testing it needs datasets carrying the relevant
metadata, and coverage must be reported per subgroup, not only pooled.

## 10. Known engine-level gaps

- **Coverage depends on the caller.** Wear time and sync-completion time are context
  fields. When they are absent the engine discloses `COVERAGE_UNKNOWN` and
  `SYNC_STATE_UNKNOWN` and caps the decision at `SHOW_WITH_WARNING`, but it cannot verify a
  wear figure a caller supplies. A caller that reports wear time optimistically will get
  optimistic decisions.
- **Day boundaries need a timezone.** Without one, day-window claims abstain with
  `TIMEZONE_UNKNOWN`. DST transitions inside a target window are not specially handled.
- **The sync-reporting allowance is a heuristic.** An aggregate stamped up to 12 hours
  after a window, capped at half the window length, is attributed to it and the attribution
  is reported. A device that reports later than that will look like missing data.
- **Consistency rules are few and hand-written.** Six rules covering the pairs the six
  policies name. Real contradictions this engine will not notice certainly exist.
- **Only one effect model.** Deviation from a personal mean in baseline standard
  deviations. It assumes an approximately stationary baseline and will misjudge a person
  with a genuine trend, which the stability and level-shift gates catch bluntly rather than
  modelling.
- **The corruption catalogue is not exhaustive** and, for some claim types, some
  corruptions breach nothing measurable — `conflict_summary_detail` produces no detectable
  conflict for `ACTIVITY_LOAD_HIGH`, for instance, because no consistency rule compares that
  claim's summary to interval detail. The evaluation report's per-corruption breakdown makes
  those gaps visible rather than hiding them.
- **No persistence, no deployment.** No database, no retention or deletion path for
  decision traces, which contain personal measurements wherever they are logged.

## 11. AI-assisted development

This repository was built with AI assistance: scaffolding, tests, documentation, and code
review. Generated code was executed and tested; generated *facts* were not independently
verified, which is exactly why `docs/related-work.md` and `data/DATASETS.md` carry explicit
UNVERIFIED markers rather than reading as settled. Treat every scientific, dataset, and API
claim in this repository as requiring primary-source confirmation unless a test or a
committed artifact demonstrates it.

## What would change these limitations

In order of value:

1. Verify the `data/DATASETS.md` rows and download PPG-DaLiA. Build the signal-quality
   estimator with real PPG and a chest-ECG reference.
2. Verify `docs/related-work.md`, especially FHIR `dataAbsentReason` and vendor gating,
   then rewrite the novelty paragraph to match what is found.
3. Implement B3 and B4 with subject-held-out splits and a separate calibration split, and
   publish the calibration curve and the risk-coverage curve — including if the learned
   component loses to the rules baseline.
4. Report subgroup coverage wherever metadata permits it.
