#!/usr/bin/env python3
"""Passive capture: subscribe to every notify/indicate characteristic, log to JSONL. NO writes to the device.

Usage: capture.py <addr> <seconds> [--schedule "still:20,wave:20,..."] [--say]
With --schedule, label start/end markers are written into the same JSONL (kind="label") and spoken via `say`.
"""
import asyncio, json, sys, time, subprocess
from pathlib import Path
from bleak import BleakClient, BleakScanner

LOGS = Path(__file__).resolve().parent.parent / "captures"
LOGS.mkdir(exist_ok=True)


def say(text, enabled):
    print(f">>> {text}", flush=True)
    if enabled:
        subprocess.Popen(["say", text])


async def main(addr, seconds, schedule, use_say):
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = LOGS / f"capture-{ts}.jsonl"
    f = path.open("w")
    counts = {}

    def emit(rec):
        rec["t"] = time.time()
        f.write(json.dumps(rec) + "\n"); f.flush()

    dev = await BleakScanner.find_device_by_address(addr, timeout=30)
    if not dev:
        print("device not found"); return 2
    async with BleakClient(dev, timeout=30) as c:
        emit(dict(kind="connected", mtu=c.mtu_size))
        subscribed = []
        for s in c.services:
            for ch in s.characteristics:
                if not ({"notify", "indicate"} & set(ch.properties)):
                    continue

                def make(uuid):
                    def cb(_, data: bytearray):
                        counts[uuid] = counts.get(uuid, 0) + 1
                        emit(dict(kind="notify", uuid=uuid, len=len(data), hex=bytes(data).hex()))
                    return cb

                try:
                    await c.start_notify(ch, make(ch.uuid))
                    subscribed.append(ch.uuid)
                    emit(dict(kind="subscribed", uuid=ch.uuid))
                    print("subscribed", ch.uuid)
                except Exception as e:
                    emit(dict(kind="subscribe_error", uuid=ch.uuid, error=repr(e)))
                    print("subscribe FAILED", ch.uuid, repr(e))
        t0 = time.time()
        if schedule:
            say("get ready", use_say); await asyncio.sleep(12)
            for label, dur in schedule:
                say(f"{label}", use_say)
                emit(dict(kind="label", label=label, phase="start"))
                await asyncio.sleep(dur)
                emit(dict(kind="label", label=label, phase="end"))
        else:
            while time.time() - t0 < seconds:
                await asyncio.sleep(5)
                print(f"[{time.time()-t0:5.0f}s] counts={ {k[:8]+k[-4:]:v for k,v in counts.items()} }", flush=True)
        say("stop", use_say)
        for u in subscribed:
            try:
                await asyncio.wait_for(c.stop_notify(u), 3)
            except Exception:
                pass
        emit(dict(kind="done", counts=counts))
        try:
            await asyncio.wait_for(c.disconnect(), 5)
        except Exception:
            pass
    f.close()
    print("saved", path, "counts", counts)
    return 0


if __name__ == "__main__":
    a = sys.argv
    sched = None
    if "--schedule" in a:
        raw = a[a.index("--schedule") + 1]
        sched = [(x.split(":")[0], float(x.split(":")[1])) for x in raw.split(",")]
    sys.exit(asyncio.run(main(a[1], float(a[2]), sched, "--say" in a)))
