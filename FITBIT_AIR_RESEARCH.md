# Fitbit Air motion-data feasibility research

Goal: can a Mac, talking directly to a Fitbit Air over BLE, obtain enough motion data to classify wrist gestures?
Constraints: Fitbit Air only; no factory reset, no un-pairing, no firmware, read-only/observational first.

## Environment
- macOS 26.6.2 (Darwin 25.6.0), Apple Silicon, BT controller BCM_4388C2
- Python 3.13.11 (conda) -> venv at `.venv` with `bleak`
- No Go/Rust/Wireshark installed (none needed so far)
- Repos: `repos/openwatch`, `repos/golden-gate`
- **CoreBluetooth note:** the sandboxed Bash tool aborts (SIGABRT, exit 134) on any CoreBluetooth use
  because the process has no Bluetooth TCC permission. BLE scripts must run from the app's Terminal panel.

## Phase 1 - what OpenWatch actually gives us (important correction to the premise)
OpenWatch (`denysvitali/openwatch`) is primarily an Oudmon/QWatch smartwatch app. Its Fitbit Air support is a
**read-only, capture-derived profile**, not a working implementation:
- `lib/core/ble/ble_constants.dart`: UUID table for Fitbit Air (abbaff00/01/02, abbafd00-03, ac2f0045/0145/2845,
  4eee1c00-07, standard Heart Rate 0x180D / 0x2A37).
- `lib/core/protocol/fitbit_gattlink.dart`: *structural decoder only* for Gattlink header -> IPv4 -> UDP -> DTLS record
  header. No transmit path, no handshake, no state machine.
- `PROTOCOL.md` s2.0: states the DTLS records are epoch-1 application data protected by a **per-device PSK**;
  plaintext (CoAP/protobuf) is not recoverable from the capture. "OpenWatch must not send ... inferred application
  commands on this profile."
- Link Status (abbafd01) = 9-byte LE: conn interval, latency, supervision timeout, MTU, ConnectionMode.
  abbafd02 = flags (bonded/encrypted/DLE...) + DLE fields. 4eee1c00 service: semantics unknown.
- Only *standard* Heart Rate Measurement (0x2A37) is decoded as a public value.

So there is **no existing Fitbit Air client** to port. Phase 8 ("reuse OpenWatch's negotiation/Gattlink/network setup")
has nothing to reuse beyond the packet parser. Golden Gate (`repos/golden-gate`) is the actual Gattlink/link-status
implementation (C, with Android/Apple platform code).

## Phase 3 - discovery
- Scan 1 (15 s, 85 devices) and scan 2 (45 s, 121 devices): no `abba*` service UUIDs, no Fitbit mfg data, no
  name containing Fitbit/Air. Most devices are neighbours' (WHOOP, TV, LED strips ...) - not touched.
- Only candidate: `FITBIT-AIR-LOCAL-HANDLE`, RSSI -43, one advertisement in 45 s, service UUID `0xFD62`,
  service data under `0x180A` = `4304875a959f5e4f`. (0xFD62 is believed to be a Fitbit SIG-assigned UUID - unverified.)
- Connection attempt to it failed: it stopped advertising (not seen in the following 30 s).
- The user's iPhone was advertising at -29 dBm (84 adverts/45 s) => iPhone Bluetooth radio is still ON
  (Control Center toggle only disconnects accessories; it does not power the radio off). The Fitbit is
  probably still attached to it / advertising sparsely.

## Phase 3/4 - device identified and GATT enumerated (2026-09-21 19:01)
- Fitbit Air = CoreBluetooth id `FITBIT-AIR-LOCAL-HANDLE`. Advertises service `0xFD62` + service data under 0x180A
  (`43 04 <6 bytes>`, bytes change between adverts). Appeared after iPhone BT was turned fully off and the tracker was woken.
- Direct Mac connection works without pairing. MTU 247. Log: `logs/gatt-20260921-190135.json`.
- Device Information: manufacturer `Fitbit`, model `67`, firmware `67.20001.253.2`, hw rev empty.
- **All requested UUIDs PRESENT**: abbaff00 (svc), abbaff01 (write-no-resp), abbaff02 (notify), abbafd00 (svc), abbafd01 (notify+read).
- Also present: abbafd02 (notify+read), abbafd03 (read; read timed out), ac2f0045 svc (ac2f0145 read, ac2f2845 write),
  Heart Rate 0x180D (2a37 notify), and svc 4eee1c00 with 4eee1c01..07 (notify/indicate/write/read mix).
- abbafd01 = `180000004800f70000` -> interval 30 ms, latency 0, supervision timeout 720 ms, MTU 247, mode DEFAULT.
- abbafd02 = `01000000000000` -> flags 0x01 = bonded (to the iPhone), not encrypted on this link.

## Phase 5/6 - passive capture + movement test
Subscribed (notify/indicate, no writes) to: abbafd01, abbafd02, abbaff02, 2a37, 4eee1c01, 1c04, 1c05, 1c06, 1c07.
- Baseline, 40 s still: `captures/capture-20260921-190232.jsonl`
- Movement test, ~3 min, cues still/wave/still/punch/still/rotation/still/typing/still/drink (15 s each):
  `captures/capture-20260921-190819.jsonl` (label start/end markers are in the file).
- **Result: exactly 2 notifications in the entire session** - the initial abbafd01/abbafd02 values on subscribe.
  Zero packets on Gattlink RX (abbaff02), Heart Rate, or any 4eee1c0x characteristic, during still or any movement.
  Packet rate: 0 Hz for every stream in every labelled segment. No payload changed with movement (nothing to correlate).

## Findings so far
- The tracker does NOT stream raw or processed motion data over BLE unprompted. Conclusion A (raw IMU directly available)
  is ruled out for the passive surface. Passive evidence is consistent with D (IMU stays on-device) or with B/C being
  gated behind the Gattlink->IP->UDP->DTLS session, which we have not opened.
- Not yet distinguishable: B (raw data inside the DTLS session) vs D. Need an active Gattlink session to tell.

## Failed / blocked approaches
- CoreBluetooth from the sandboxed Bash tool: SIGABRT (no TCC). Use Terminal panel.
- OpenWatch has no Fitbit Air client to port (parser only).
- Heart Rate service is silent (Fitbit only broadcasts HR when a broadcast mode is enabled) and would not be motion anyway.

## Exact next technical blocker
Active path = Gattlink handshake (Golden Gate) over abbaff01/02 -> IPv4 169.254.0.x -> UDP 5684 -> DTLS 1.2 with a per-device PSK.
The PSK is provisioned during account pairing and lives on the phone/Google side; we do not have it and will not extract it
from the phone or Google's servers. Without it the application layer (where any raw data would be) cannot be read.
Answered: it does not (see Phase 8). The private channel is closed to an unbonded Mac.

## Status log
- [x] Workspace, venv, repos cloned
- [x] Discovery, direct connect, GATT enumeration, known-UUID verification
- [x] Notification subscription + packet logging (JSONL)
- [x] Controlled movement test
- [x] Analysis of passive streams (nothing to analyse: 0 packets)
- [x] Active Gattlink probe (see below)

## Phase 8 - active Gattlink probe (2026-09-21 19:18)
- Note: the tracker's CoreBluetooth id rotated (`FITBIT-AIR-LOCAL-HANDLE-...` -> `FITBIT-AIR-LOCAL-HANDLE-...`, resolvable private address) and it now
  advertises the name **"Google Fitbit Air"** with service 0xFD62. Scripts now discover it by the 0xFD62 service (`auto`).
- `scripts/gattlink_probe.py`: subscribed to abbaff02 (+abbafd01/02, 2a37), then wrote the documented Gattlink
  Reset Request (`0x80`, per golden-gate `gg_gattlink.c`) to abbaff01 (write-without-response); retried at 1 s, 3 tries total,
  then listened ~30 s. Log: `captures/gattlink-probe-20260921-191822.jsonl`.
- **Result: no response.** Zero packets on abbaff02 (no Reset Complete, no Reset Request, no data, no DTLS). The tracker
  stayed in the initial state from our side (AWAITING_RESET_COMPLETE_SELF_INITIATED). Only the two subscribe-time
  Link Status notifications arrived.
- Interpretation (not proven): the tracker ignores/gates Gattlink from a peer that is not its bonded/authenticated host
  (abbafd02 flag 0x01 = bonded to another central; our link is unencrypted). We cannot distinguish "silently dropped
  by the GATT layer" from "ignored by the Gattlink stack" without a second, different probe.
- Not attempted (deliberately): pairing/bonding the Mac, DTLS ClientHello with guessed PSK, extracting the PSK from the phone or Google.

## Phase 8b - read-only sweep of every readable characteristic (2026-09-21 ~19:25)
Log: `logs/read-all-console.txt`, `logs/read-all-*.json`.
- Device Info readable (Fitbit / model 67 / fw 67.20001.253.2). **Serial number changed between sessions**
  (`53cda3016161` -> `e5e3a03aeb5e`) together with the rotating BLE address: the tracker rotates its identifiers for privacy.
- Read OK: abbafd01, abbafd02 (Link Status).
- **Read TIMED OUT (no answer, not an ATT error):** abbafd03, ac2f0145, 4eee1c03, 4eee1c06, 4eee1c07.
  A timeout (instead of "insufficient authentication/encryption") means the tracker's firmware silently withholds
  answers on its private services from a non-bonded central. Same behaviour as the Gattlink probe.
- Conclusion: every private surface (Gattlink, 4eee1c00, ac2f0045) is gated to the bonded host. Only Device Info,
  Link Status and an (unused) Heart Rate service are open.

---

# SESSION 2 (2026-09-22) — corrections + the real blocker

## Phase 9 — motion test v2 (`scripts/motion_test_v2.py`, `scripts/analyze_v2.py`)
Fixed two gaps in session 1: (a) v1 never proved the link was alive during the silence,
(b) v1 never tested the ADVERTISEMENT surface against motion.
Capture: `captures/motion-v2-20260922-171936.jsonl`, console `logs/motion-v2-console.txt`.

**Phase A — advertisement surface (not connected), 80 s of cued movement:**
| segment | adverts | rate |
|---|---|---|
| still | 1 | 0.050 Hz |
| wave | 2 | 0.133 Hz |
| still | 0 | 0 |
| punch | 0 | 0 |
| wrist rotation | 1 | 0.067 Hz |

Service data payload = **1 distinct value** (`4304d1238787c97d`) for the whole phase —
byte-identical during "still" and "wave". It is a rotating privacy token, not telemetry.
RSSI varies (−42…−60) but that is radio path loss, not a device-reported signal.
**Advert surface carries no motion information and is ~0.05 Hz — 3 orders of magnitude
below the ~20–50 Hz needed for gesture recognition.**

**Phase B — connected GATT surface, 190 s of cued movement**
(still/typing/still/wave/still/punch/still/rotation/still/drinking/still/lifting weights):
- 9 characteristics subscribed (abbafd01, abbafd02, abbaff02, 2a37, 4eee1c01/04/05/06/07).
- **2 notifications total**, both at subscribe time, both before any movement. 0.000 Hz in
  every single labelled movement segment. Reproduces session 1 exactly.

## Phase 10 — THE ACTUAL BLOCKER: an ~8.31 s ATT service window (`scripts/att_window.py`)
Capture: `captures/att-window-20260922-172916.jsonl`, console `logs/att-window-console.txt`.
Polled three plain reads (2A26 firmware, 2A24 model, abbafd01) once per second after connect,
3 independent connect/disconnect rounds:

| round | last successful read | first failure |
|---|---|---|
| 1 | t+8.3098 s | t+9.371 s |
| 2 | t+8.3084 s | t+9.369 s |
| 3 | t+8.3086 s | t+9.370 s |

Inside the window every read succeeds in 45–64 ms. After it, **every** ATT request times out
(5 s, no ATT error response at all) while CoreBluetooth still reports `is_connected=True`.
Disconnecting and reconnecting **fully restores** the window. This is deterministic to ~2 ms.

=> The tracker services ATT for an unbonded central for ~8.31 s per connection, then stops
   responding to that central entirely. This is a firmware policy, not a link failure.

## Phase 11 — CORRECTION to session 1's "firmware withholds private reads"
`scripts/fast_sweep.py` re-read every readable characteristic, private ones FIRST, inside the
8 s window, reconnecting to cover the list.
Capture: `captures/fast-sweep-20260922-173807.jsonl`, console `logs/fast-sweep-console.txt`.

**Session 1 was wrong about 3 of the 5 "withheld" characteristics** — that sweep read Device
Information first and ran past the 8 s cutoff before reaching them:

| characteristic | session 1 | session 2 (read early) |
|---|---|---|
| ac2f0145 | "timed out" | **READS**: `ac2f284581824be591e02992e6b40ebb` (= the UUID of ac2f2845; Golden Gate Confirmation Service "ephemeral characteristic pointer") |
| 4eee1c06 | "timed out" | **READS**: `00` |
| 4eee1c07 | "timed out" | **READS**: `00` |
| abbafd03 | "timed out" | refuses — `BleakGATTProtocolError` (a real ATT error, i.e. genuine permission gate; Golden Gate calls this "bondSecure") |
| 4eee1c03 | "timed out" | refuses — `BleakGATTProtocolError` |

Also re-read: 2a23=`0000000000000000`, 2a2a=`00`, 2a50=`00000000000000`, 2a27/2a28 empty,
serial 2a25 = `7c6b2a0d3f61` (rotates every session, as before).
Nothing resembling accelerometer/gyro samples, step counts, or activity state.

## Phase 12 — Golden Gate architecture: what session 1 missed, and why it still doesn't help
`repos/golden-gate/platform/apple/apps/host/Source/BluetoothConfiguration+Defaults.swift`:
- Link **Status** service `ABBAFD00` — hosted by the **Node (tracker)**. We have this.
- Link **Configuration** service `ABBAFC00` (chars FC01 preferred conn config, FC02 preferred
  conn mode, FC03 general purpose command) — hosted by the **Hub (phone)**, i.e. the central
  is expected to also run a GATT **server**. Our Mac exposed nothing. **Untested avenue.**
- Link service: `FD62` is the *current* Gattlink service UUID; `ABBAFF00` is the deprecated one.
  The tracker exposes both, and advertises FD62.
- Confirmation service `AC2F0045` with pointer char `AC2F0145` — matches what we read.
- Gattlink Reset Request = `0x80` — **session 1's probe was protocol-correct** (verified against
  `xp/gattlink/gg_gattlink.c`: PACKET_TYPE_CONTROL=0x80, RESET_REQUEST=0x0). Its "no response"
  result stands. Note it may also have been affected by the 8 s window.

Even if a Gattlink session opened, the payload above it is IPv4 → UDP **5684 (CoAPS)** →
**DTLS 1.2 with a per-device PSK** (OpenWatch PROTOCOL.md §2.0). The PSK is provisioned during
account pairing and lives on the phone/Google side. No PSK => no application layer => no data.

## VERDICT (session 2)
- raw motion data over BLE: **NO**
- processed motion data over BLE: **NO**
- movement-correlated data over BLE: **NO** (advert payload static; RSSI is path loss, not telemetry)
- **BLOCKED**, exact technical reason, two independent gates:
  1. **~8.31 s ATT service window.** An unbonded central gets ~8.31 s of ATT service per
     connection, then the firmware stops answering. Reproducible to ~2 ms over 3 rounds.
     Reconnecting resets it — so a poll loop could sustain reads, but nothing readable
     carries motion.
  2. **DTLS-PSK on the only rich channel.** All real telemetry rides Gattlink → IPv4 → UDP 5684
     → DTLS 1.2 with a per-device PSK held by the phone/Google account. Two chars (abbafd03
     "bondSecure", 4eee1c03) return a hard ATT permission error confirming a bonding gate.

## Untested / next avenues (in value order)
1. **Run the Mac as a Golden Gate Hub GATT server** (advertise + expose ABBAFC00 with FC01/FC02/FC03
   and a Gattlink service). The tracker expects the hub to be a peripheral too; we never were.
   Needs CBPeripheralManager via pyobjc (bleak cannot do peripheral mode). Would test whether the
   tracker initiates Gattlink toward a hub that looks correct. Still ends at the DTLS-PSK wall.
2. Re-run the Gattlink `0x80` probe **inside the 8 s window** (session 1's probe may have partly
   run past it) — cheap, worth doing.
3. Watch 4eee1c06 / 4eee1c07 (both `00`) for changes during movement, polled inside the window.
4. Deliberately NOT attempted: bonding/pairing the Mac, extracting the PSK from the phone or
   Google, DTLS with a guessed PSK, factory reset, firmware flash. Per your constraints.

## Scripts added this session
- `scripts/motion_test_v2.py` — advert-surface + connected-surface motion test with link-liveness heartbeat
- `scripts/analyze_v2.py` — per-label packet rates, payload variability, verdict inputs
- `scripts/att_window.py` — pins down the ATT service window (the key measurement)
- `scripts/fast_sweep.py` — reads all readable chars inside the window, priority-ordered, with reconnects
- `scripts/att_error_detail.py` — written, NOT yet run: captures the exact ATT error code for
  abbafd03 / 4eee1c03 and watches 4eee1c06/07 for movement-linked changes

---

# SESSION 3 (2026-09-22, same day, extended) — every remaining avenue tested

## Phase 13 — Gattlink handshake INSIDE the 8.31 s window (`scripts/gattlink_in_window.py`)
Capture: `captures/gattlink-inwindow-20260922-211532.jsonl`, console `logs/gattlink-inwindow-console.txt`.
Session 1's probe retried over 30 s, so most of it ran after the tracker had stopped servicing us.
This version subscribed to abbaff02 at t+0.06 s and sent Reset Request `0x80` six times per
connection at 1 s intervals, all inside the window, over 4 reconnects.

**Result: 24 Reset Requests, 0 packets received on abbaff02.** The ATT writes are accepted
(no error, write-without-response), but the Gattlink stack never answers. The session-1
conclusion was correct and was NOT an artefact of the window.

## Phase 14 — Mac as a Golden Gate HUB / GATT server (`scripts/hub_peripheral.py`)
Capture: `captures/hub-peripheral-*.jsonl`, console `logs/hub-peripheral-console.txt`.
The one architectural avenue never tried: Golden Gate expects the **Hub** to be a peripheral
exposing Link Configuration `ABBAFC00` (FC01 preferred conn config, FC02 preferred conn mode,
FC03 general purpose command) plus a hub-hosted Gattlink service. Implemented with pyobjc
`CBPeripheralManager` (bleak cannot do peripheral mode), advertising as "GG-Hub" with both
service UUIDs, answering reads with Golden Gate's documented defaults.

**Result after 4 minutes advertising: ZERO interactions.** No connection, no subscription,
no read request, no write request. The tracker never approached a hub-shaped peer. It only
talks to its bonded host.

## Phase 15 — rapid poll of every readable characteristic during movement (`scripts/poll_motion.py`)
Capture: `captures/poll-motion-20260922-212226.jsonl`, console `logs/poll-motion-console.txt`.
Exploits the window discovery: connect, poll all 7 readable characteristics as fast as the link
allows (~50-60 ms/read), disconnect at 7.6 s, reconnect, repeat — continuously, while the wearer
performs cued movements. ~3 connections per 30 s segment.

**3443 successful reads, 0 failures**, segments: still / typing / wave / punch / wrist rotation /
drinking motion / lifting weights.

| characteristic | distinct values across the entire session |
|---|---|
| 4eee1c06 | 1 (`00`) |
| 4eee1c07 | 1 (`00`) |
| abbafd01 | 1 (`180000004800f70000`) |
| abbafd02 | 1 (`01000000000000`) |
| 2a23 | 1 (`0000000000000000`) |
| 2a2a | 1 (`00`) |
| 2a50 | 1 (`00000000000000`) |

**Not one byte changed, in any characteristic, during any movement.** No characteristic varied
even within a single segment. This is an airtight negative: the entire readable surface is static.

## FINAL VERDICT
- **raw motion data accessible: NO**
- **processed motion data accessible: NO**
- **useful movement-correlated data accessible: NO**
- **CURRENTLY BLOCKED.** Exact technical reasons, all four avenues now closed empirically:
  1. **Notifications:** 0 packets in 190 s of cued movement across 9 subscribed characteristics
     (2 sessions, reproduced). The tracker never pushes motion data.
  2. **Readable surface:** 3443 reads × 7 characteristics × 7 movement types = 0 variance.
     Nothing exposed correlates with movement.
  3. **Advertisements:** payload byte-identical between still and wave; ~0.05 Hz broadcast rate,
     ~1000x too slow for gesture recognition even if it carried data. It is a rotating privacy token.
  4. **Gattlink / private protocol:** 24 protocol-correct Reset Requests inside the ATT window ->
     0 responses. And acting as a proper Golden Gate Hub (GATT server with ABBAFC00) for 4 minutes
     -> 0 interactions. The private channel is closed to anything that is not the bonded host.
     Above it sits IPv4 -> UDP 5684 (CoAPS) -> DTLS 1.2 with a per-device PSK held by the
     phone/Google account, so even a successful Gattlink session yields ciphertext.
- Secondary finding: **~8.31 s ATT service window** per connection for an unbonded central
  (reproducible to ~2 ms), reset by reconnecting. Sustained polling is possible via reconnect
  cycling — it just has nothing useful to read.

## What would be required (all outside your constraints)
Gesture recognition needs ~20-50 Hz accelerometer data. The Fitbit Air keeps its IMU entirely
on-device and exposes it only inside the DTLS-PSK application channel. Reaching it would require
the per-device PSK from the phone/Google account, or firmware modification — both excluded.
Nothing was reset, flashed, erased, bonded or modified at any point.

## Scripts added this session
- `scripts/gattlink_in_window.py` — Gattlink reset handshake inside the ATT window
- `scripts/hub_peripheral.py` — Mac as Golden Gate Hub (CBPeripheralManager GATT server)
- `scripts/poll_motion.py` — rapid reconnect-polling of all readable chars during cued movement
- `scripts/att_error_detail.py` — ATT error detail + stability baseline
