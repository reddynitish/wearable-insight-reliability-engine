#!/usr/bin/env python3
"""Read every readable characteristic INSIDE the ~8 s post-connect ATT window.

The tracker answers ATT requests for only ~8-9 s after connect, then goes silent.
A previous sweep read Device Information first and hit the cutoff before reaching the
private services - which was misread as "the firmware withholds these".
This script reads the UNKNOWN/private characteristics FIRST, with short timeouts,
and reconnects as many times as needed to cover the full list.

Read-only: ATT reads only, no writes.
Usage: fast_sweep.py [rounds]
"""
import asyncio, json, sys, time
from pathlib import Path
from bleak import BleakClient, BleakScanner

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "captures"
FD62 = "0000fd62-0000-1000-8000-00805f9b34fb"

# Priority order: the previously-unreadable private characteristics first.
PRIORITY = [
    "abbafd03-e56a-484c-b832-8b17cf6cbfe8",   # bond secure (Golden Gate LinkStatus)
    "ac2f0145-8182-4be5-91e0-2992e6b40ebb",   # confirmation svc ephemeral ptr
    "4eee1c03-4133-479b-8663-02c84bdc14be",
    "4eee1c06-4133-479b-8663-02c84bdc14be",
    "4eee1c07-4133-479b-8663-02c84bdc14be",
    "abbafd01-e56a-484c-b832-8b17cf6cbfe8",
    "abbafd02-e56a-484c-b832-8b17cf6cbfe8",
    "00002a23-0000-1000-8000-00805f9b34fb",
    "00002a2a-0000-1000-8000-00805f9b34fb",
    "00002a50-0000-1000-8000-00805f9b34fb",
    "00002a25-0000-1000-8000-00805f9b34fb",
    "00002a27-0000-1000-8000-00805f9b34fb",
    "00002a28-0000-1000-8000-00805f9b34fb",
]
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 4


async def find(timeout=120):
    print("scanning...", flush=True)
    return await BleakScanner.find_device_by_filter(
        lambda d, adv: FD62 in [u.lower() for u in adv.service_uuids]
        or "fitbit" in (adv.local_name or "").lower(), timeout=timeout)


async def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = OUT / f"fast-sweep-{ts}.jsonl"
    f = path.open("w")

    def log(**rec):
        rec["t"] = time.time()
        f.write(json.dumps(rec) + "\n")
        f.flush()

    got = {}          # uuid -> hex value
    failed = {}       # uuid -> last error
    dev = await find()
    if not dev:
        print("device not found - wake the tracker")
        return

    for rnd in range(1, ROUNDS + 1):
        todo = [u for u in PRIORITY if u not in got]
        if not todo:
            break
        print(f"\n=== round {rnd}: {len(todo)} characteristic(s) left ===", flush=True)
        try:
            async with BleakClient(dev, timeout=30) as c:
                t0 = time.time()
                log(kind="connected", round=rnd, mtu=c.mtu_size)
                print(f"connected mtu={c.mtu_size}", flush=True)
                # Rotate the start position each round so a characteristic that
                # always lands after the cutoff still gets an early slot eventually.
                order = todo[(rnd - 1) % len(todo):] + todo[:(rnd - 1) % len(todo)]
                for u in order:
                    since = time.time() - t0
                    if since > 12:
                        print(f"  (past the ATT window at t+{since:.1f}s, reconnecting)", flush=True)
                        break
                    try:
                        v = await asyncio.wait_for(c.read_gatt_char(u), 2.5)
                        h = bytes(v).hex()
                        got[u] = h
                        txt = bytes(v).decode("utf-8", "replace")
                        printable = "".join(ch if 32 <= ord(ch) < 127 else "." for ch in txt)
                        log(kind="read", round=rnd, since_connect=round(since, 2), uuid=u,
                            ok=True, len=len(v), hex=h, text=printable)
                        print(f"  t+{since:5.1f}s {u[:8]} OK  len={len(v):3d} {h}  |{printable}|", flush=True)
                    except Exception as e:
                        failed[u] = type(e).__name__
                        log(kind="read", round=rnd, since_connect=round(since, 2), uuid=u,
                            ok=False, error=type(e).__name__, still_connected=c.is_connected)
                        print(f"  t+{since:5.1f}s {u[:8]} FAIL {type(e).__name__}", flush=True)
        except Exception as e:
            log(kind="round_error", round=rnd, error=repr(e))
            print(f"round {rnd} error: {e!r}", flush=True)
        if rnd < ROUNDS and [u for u in PRIORITY if u not in got]:
            await asyncio.sleep(8)
            d2 = await find(timeout=60)
            if d2:
                dev = d2

    print("\n" + "=" * 70)
    print("SWEEP RESULT")
    print("=" * 70)
    for u in PRIORITY:
        if u in got:
            print(f"  READ OK   {u}  {got[u]}")
        else:
            print(f"  NO ANSWER {u}  ({failed.get(u, 'not attempted')})")
    log(kind="summary", got=got, failed=failed)
    f.close()
    print("\nsaved", path, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
