# 0006 — Direct-BLE motion access is closed, not paused

**Status:** accepted, 2026-09-23

## Context

`FITBIT_AIR_RESEARCH.md` documents three sessions of direct BLE investigation of the
Fitbit Air. The outcome: no accessible motion stream, an ~8.31 s unbonded ATT service
window, and an authenticated DTLS (Golden Gate) channel that cannot be opened without
the device's own credentials.

## Decision

The investigation is complete. No further work on pairing bypass, credential extraction,
key guessing, or private-channel access — those are out of scope permanently, not
pending. The supported data path is Google Health API only. The research artifacts
(scripts, captures, logs) are preserved read-only as the evidence base for the negative
result.

## Consequences

- Raw accelerometer/gyroscope data is unavailable at any tier, so motion-artifact
  features derived from *personal* data are impossible. The corresponding Stage-2 work
  depends entirely on public datasets that ship raw accelerometer data (PPG-DaLiA).
- Engine code lives in `engine/`, entirely separate from `scripts/`, `captures/`, `logs/`.
  The two share no imports.
- The negative result is itself worth documenting: it is the reason the project pivoted
  from "read the sensor" to "judge the evidence".
