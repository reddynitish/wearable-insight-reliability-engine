#!/usr/bin/env python3
"""Capture the EXACT ATT error for the two characteristics that refuse to read,
and probe whether 4eee1c06 / 4eee1c07 ever change value (they read as 0x00).

Read-only.
Usage: att_error_detail.py
"""
import asyncio, json, time
from pathlib import Path
from bleak import BleakClient, BleakScanner

ROOT = Path(__file__).resolve().parent.parent
FD62 = "0000fd62-0000-1000-8000-00805f9b34fb"
REFUSED = ["abbafd03-e56a-484c-b832-8b17cf6cbfe8", "4eee1c03-4133-479b-8663-02c84bdc14be"]
WATCH = ["4eee1c06-4133-479b-8663-02c84bdc14be", "4eee1c07-4133-479b-8663-02c84bdc14be",
         "abbafd01-e56a-484c-b832-8b17cf6cbfe8", "abbafd02-e56a-484c-b832-8b17cf6cbfe8"]


async def find(timeout=120):
    print("scanning...", flush=True)
    return await BleakScanner.find_device_by_filter(
        lambda d, adv: FD62 in [u.lower() for u in adv.service_uuids]
        or "fitbit" in (adv.local_name or "").lower(), timeout=timeout)


async def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = ROOT / "captures" / f"att-error-{ts}.jsonl"
    f = path.open("w")

    def log(**r):
        r["t"] = time.time()
        f.write(json.dumps(r) + "\n")
        f.flush()

    dev = await find()
    if not dev:
        print("not found")
        return

    print("\n=== A: exact ATT error for the two refused characteristics ===", flush=True)
    async with BleakClient(dev, timeout=30) as c:
        for u in REFUSED:
            try:
                v = await asyncio.wait_for(c.read_gatt_char(u), 3)
                print(f"  {u[:8]} unexpectedly OK: {bytes(v).hex()}", flush=True)
                log(kind="refused_read", uuid=u, ok=True, hex=bytes(v).hex())
            except Exception as e:
                print(f"  {u[:8]} -> {type(e).__name__}: {e}", flush=True)
                log(kind="refused_read", uuid=u, ok=False,
                    error_type=type(e).__name__, error=str(e),
                    attrs={k: str(v) for k, v in vars(e).items()} if vars(e) else {})

    print("\n=== B: do 4eee1c06 / 4eee1c07 change with movement? ===", flush=True)
    print("Reading them inside the ~8s window, over several reconnects.", flush=True)
    for rnd in range(1, 7):
        try:
            dev2 = await find(timeout=60) or dev
            async with BleakClient(dev2, timeout=30) as c:
                t0 = time.time()
                while time.time() - t0 < 7.5:
                    vals = {}
                    for u in WATCH:
                        try:
                            v = await asyncio.wait_for(c.read_gatt_char(u), 2)
                            vals[u[:8]] = bytes(v).hex()
                        except Exception as e:
                            vals[u[:8]] = f"ERR:{type(e).__name__}"
                    log(kind="watch", round=rnd, since=round(time.time() - t0, 2), vals=vals)
                    print(f"  r{rnd} t+{time.time()-t0:4.1f}s " +
                          "  ".join(f"{k}={v}" for k, v in vals.items()), flush=True)
                    await asyncio.sleep(0.5)
        except Exception as e:
            print(f"  round {rnd} error {e!r}", flush=True)
        await asyncio.sleep(3)
    f.close()
    print("\nsaved", path, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
