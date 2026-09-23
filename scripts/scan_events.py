#!/usr/bin/env python3
"""Long scan that logs EVERY advertisement event (JSONL) and summarises per device.
Usage: scan_events.py [seconds] [--service UUID]
"""
import asyncio, json, sys, time, collections
from pathlib import Path
from bleak import BleakScanner

LOGS = Path(__file__).resolve().parent.parent / "logs"


async def main(duration, services):
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = LOGS / f"adv-events-{ts}.jsonl"
    f = path.open("w")
    stats = collections.defaultdict(lambda: dict(n=0, rssi=[], name="", svcs=set(), mfg={}, sdata={}))

    def cb(dev, adv):
        t = time.time()
        f.write(json.dumps(dict(t=t, addr=dev.address, name=adv.local_name or dev.name, rssi=adv.rssi,
                                services=adv.service_uuids,
                                mfg={hex(k): v.hex() for k, v in adv.manufacturer_data.items()},
                                sdata={k: v.hex() for k, v in adv.service_data.items()})) + "\n")
        s = stats[dev.address]
        s["n"] += 1; s["rssi"].append(adv.rssi)
        s["name"] = adv.local_name or dev.name or s["name"]
        s["svcs"].update(adv.service_uuids)
        s["mfg"].update({hex(k): v.hex() for k, v in adv.manufacturer_data.items()})
        s["sdata"].update({k: v.hex() for k, v in adv.service_data.items()})

    async with BleakScanner(cb, service_uuids=services or None):
        await asyncio.sleep(duration)
    f.close()
    print(f"log -> {path}; {len(stats)} devices\n")
    for a, s in sorted(stats.items(), key=lambda kv: -max(kv[1]["rssi"])):
        if set(s["mfg"]) == {"0x4c"} and not s["name"]:
            continue  # anonymous Apple devices
        print(f"n={s['n']:>3} best={max(s['rssi']):>4} {a} {s['name']!r} svcs={[x[:8] for x in s['svcs']]} "
              f"mfg={list(s['mfg'])} sdata={[k[:8] for k in s['sdata']]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    dur = float(args[0]) if args and not args[0].startswith("--") else 40
    svcs = [args[i + 1] for i, a in enumerate(args) if a == "--service"]
    asyncio.run(main(dur, svcs))
