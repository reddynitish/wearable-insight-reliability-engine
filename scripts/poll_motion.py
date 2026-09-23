#!/usr/bin/env python3
"""Last-chance motion test: rapid-poll every READABLE characteristic inside the ~8.31 s ATT
window, reconnecting continuously, while the wearer performs cued movements.

Everything readable is polled as fast as the link allows (~50-60 ms/read), giving roughly
2-4 Hz per characteristic during each connection. If ANY exposed value tracks movement -
an activity flag, a step counter, a wear-state bit - this is where it shows up.

Read-only. ATT reads only, no writes.
Usage: poll_motion.py [--say]
"""
import asyncio, json, subprocess, sys, time
from collections import defaultdict
from pathlib import Path
from bleak import BleakClient, BleakScanner

ROOT = Path(__file__).resolve().parent.parent
FD62 = "0000fd62-0000-1000-8000-00805f9b34fb"
USE_SAY = "--say" in sys.argv

# every characteristic that answered a read in the fast sweep
POLL = [
    ("4eee1c06", "4eee1c06-4133-479b-8663-02c84bdc14be"),
    ("4eee1c07", "4eee1c07-4133-479b-8663-02c84bdc14be"),
    ("abbafd01", "abbafd01-e56a-484c-b832-8b17cf6cbfe8"),
    ("abbafd02", "abbafd02-e56a-484c-b832-8b17cf6cbfe8"),
    ("2a23", "00002a23-0000-1000-8000-00805f9b34fb"),
    ("2a2a", "00002a2a-0000-1000-8000-00805f9b34fb"),
    ("2a50", "00002a50-0000-1000-8000-00805f9b34fb"),
]
SCHEDULE = [("still", 30), ("typing", 30), ("still", 25), ("wave", 30), ("punch", 30),
            ("wrist rotation", 30), ("drinking motion", 30), ("lifting weights", 30),
            ("still", 25)]


def cue(t):
    print(f"\n>>> {t}", flush=True)
    if USE_SAY:
        subprocess.Popen(["say", t])


async def find(timeout=60):
    return await BleakScanner.find_device_by_filter(
        lambda d, adv: FD62 in [u.lower() for u in adv.service_uuids]
        or "fitbit" in (adv.local_name or "").lower(), timeout=timeout)


async def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = ROOT / "captures" / f"poll-motion-{ts}.jsonl"
    f = path.open("w")

    def log(**r):
        r["t"] = time.time()
        f.write(json.dumps(r) + "\n")
        f.flush()

    print("scanning for Fitbit Air...", flush=True)
    dev = await find(120)
    if not dev:
        print("device not found - wake the tracker")
        return
    print(f"found {dev.address}", flush=True)

    state = {"label": "warmup", "reads": 0, "cycles": 0}
    values = defaultdict(set)
    stop = False

    async def poller():
        nonlocal dev
        while not stop:
            try:
                async with BleakClient(dev, timeout=20) as c:
                    state["cycles"] += 1
                    t0 = time.time()
                    log(kind="connected", cycle=state["cycles"], label=state["label"])
                    while time.time() - t0 < 7.6 and not stop:
                        for tag, uuid in POLL:
                            if time.time() - t0 > 7.9:
                                break
                            try:
                                v = await asyncio.wait_for(c.read_gatt_char(uuid), 1.5)
                                h = bytes(v).hex()
                                state["reads"] += 1
                                values[tag].add(h)
                                log(kind="read", label=state["label"], uuid=tag, hex=h,
                                    since=round(time.time() - t0, 2))
                            except Exception:
                                log(kind="read_fail", label=state["label"], uuid=tag,
                                    since=round(time.time() - t0, 2))
            except Exception as e:
                log(kind="conn_error", error=repr(e), label=state["label"])
                await asyncio.sleep(1)
            if stop:
                break
            d2 = await find(30)
            if d2:
                dev = d2

    task = asyncio.create_task(poller())
    cue("Get ready. Wear the tracker.")
    await asyncio.sleep(10)
    for label, dur in SCHEDULE:
        state["label"] = label
        r0, c0 = state["reads"], state["cycles"]
        cue(label)
        log(kind="label", label=label, phase="start")
        await asyncio.sleep(dur)
        log(kind="label", label=label, phase="end")
        print(f"    [{label}] {state['reads']-r0} reads over {state['cycles']-c0} connection(s)",
              flush=True)
    cue("done")
    stop = True
    task.cancel()
    try:
        await task
    except Exception:
        pass

    print("\n" + "=" * 70)
    print("DISTINCT VALUES PER CHARACTERISTIC (across the whole session)")
    print("=" * 70)
    for tag, _ in POLL:
        vs = values.get(tag, set())
        flag = "  <-- VARIES!" if len(vs) > 1 else ""
        print(f"  {tag:10} {len(vs)} distinct{flag}")
        for v in list(vs)[:6]:
            print(f"       {v}")
    log(kind="summary", reads=state["reads"], cycles=state["cycles"],
        distinct={k: sorted(v) for k, v in values.items()})
    f.close()
    print(f"\ntotal reads: {state['reads']} over {state['cycles']} connections")
    print("saved", path, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
