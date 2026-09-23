#!/usr/bin/env python3
"""Pin down the exact ATT-responsiveness window of the Fitbit Air for an unbonded central.

Connects, then polls Firmware Revision (2A26, a plain Device Information read that is known
to work immediately after connect) once per second, recording success/latency/failure.
Then disconnects, reconnects, and repeats - to test whether a reconnect restores service.

Read-only. No writes at all (not even CCCD).
Usage: att_window.py [rounds] [seconds_per_round]
"""
import asyncio, json, sys, time
from pathlib import Path
from bleak import BleakClient, BleakScanner

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "captures"
FD62 = "0000fd62-0000-1000-8000-00805f9b34fb"
FW = "00002a26-0000-1000-8000-00805f9b34fb"
MODEL = "00002a24-0000-1000-8000-00805f9b34fb"
LINK_STATUS = "abbafd01-e56a-484c-b832-8b17cf6cbfe8"

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 45


async def find(timeout=120):
    print("scanning for Fitbit Air (0xFD62)...", flush=True)
    return await BleakScanner.find_device_by_filter(
        lambda d, adv: FD62 in [u.lower() for u in adv.service_uuids]
        or "fitbit" in (adv.local_name or "").lower(), timeout=timeout)


async def one_round(dev, rnd, log):
    print(f"\n--- round {rnd}: connecting ---", flush=True)
    t_conn = time.time()
    async with BleakClient(dev, timeout=30) as c:
        dt = time.time() - t_conn
        log(dict(kind="connected", round=rnd, connect_secs=round(dt, 3), mtu=c.mtu_size))
        print(f"connected in {dt:.2f}s mtu={c.mtu_size}", flush=True)
        t0 = time.time()
        i = 0
        first_fail = None
        last_ok = None
        while time.time() - t0 < DUR:
            i += 1
            for uuid, tag in ((FW, "2A26-fw"), (MODEL, "2A24-model"), (LINK_STATUS, "abbafd01-linkstatus")):
                since = time.time() - t0
                ts = time.time()
                try:
                    v = await asyncio.wait_for(c.read_gatt_char(uuid), 5)
                    lat = time.time() - ts
                    last_ok = since
                    log(dict(kind="read", round=rnd, since_connect=round(since, 2), uuid=tag,
                             ok=True, latency_ms=round(lat * 1000, 1), value=bytes(v).hex()))
                    print(f"  t+{since:5.1f}s {tag:20} OK   {lat*1000:6.1f}ms  {bytes(v).hex()}", flush=True)
                except Exception as e:
                    lat = time.time() - ts
                    if first_fail is None:
                        first_fail = since
                    log(dict(kind="read", round=rnd, since_connect=round(since, 2), uuid=tag,
                             ok=False, latency_ms=round(lat * 1000, 1), error=type(e).__name__,
                             still_connected=c.is_connected))
                    print(f"  t+{since:5.1f}s {tag:20} FAIL {lat*1000:6.1f}ms  "
                          f"{type(e).__name__} (is_connected={c.is_connected})", flush=True)
            await asyncio.sleep(1)
        log(dict(kind="round_summary", round=rnd, last_ok_at=last_ok, first_fail_at=first_fail))
        print(f"--- round {rnd}: last OK at t+{last_ok}s, first FAIL at t+{first_fail}s ---", flush=True)


async def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = OUT / f"att-window-{ts}.jsonl"
    f = path.open("w")

    def log(rec):
        rec["t"] = time.time()
        f.write(json.dumps(rec) + "\n")
        f.flush()

    dev = await find()
    if not dev:
        print("device not found - wake the tracker")
        return
    log(dict(kind="found", addr=dev.address, name=dev.name))
    for r in range(1, ROUNDS + 1):
        try:
            await one_round(dev, r, log)
        except Exception as e:
            log(dict(kind="round_error", round=r, error=repr(e)))
            print(f"round {r} error: {e!r}", flush=True)
        if r < ROUNDS:
            print("waiting 10s before reconnect...", flush=True)
            await asyncio.sleep(10)
            d2 = await find(timeout=60)
            if d2:
                dev = d2
    f.close()
    print("\nsaved", path, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
