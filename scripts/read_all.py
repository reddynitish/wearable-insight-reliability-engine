#!/usr/bin/env python3
"""Read-only sweep: read EVERY readable characteristic and descriptor value. No writes, no notifications.
Records value or the exact error (e.g. 'insufficient authentication' = gated behind bonding)."""
import asyncio, json, sys, time
from pathlib import Path
from bleak import BleakClient, BleakScanner

LOGS = Path(__file__).resolve().parent.parent / "logs"


async def main():
    print("waiting for tracker (0xFD62)...", flush=True)
    dev = await BleakScanner.find_device_by_filter(
        lambda d, adv: any(u.lower().startswith("0000fd62") for u in adv.service_uuids), timeout=120)
    if not dev:
        print("not found"); return 2
    res = []
    async with BleakClient(dev, timeout=30) as c:
        print("connected", dev.address, "mtu", c.mtu_size, flush=True)
        for s in c.services:
            for ch in s.characteristics:
                if "read" in ch.properties:
                    r = dict(service=s.uuid, char=ch.uuid, props=ch.properties)
                    try:
                        v = await asyncio.wait_for(c.read_gatt_char(ch), 8)
                        r["hex"] = bytes(v).hex()
                        try:
                            r["text"] = bytes(v).decode("utf-8")
                        except UnicodeDecodeError:
                            pass
                    except Exception as e:
                        r["error"] = repr(e)
                    res.append(r)
                    print(ch.uuid[:8], ch.properties, r.get("hex", r.get("error")), flush=True)
                for d in ch.descriptors:
                    if d.uuid.startswith("00002902"):
                        continue
                    try:
                        v = await asyncio.wait_for(c.read_gatt_descriptor(d.handle), 8)
                        res.append(dict(char=ch.uuid, descriptor=d.uuid, hex=bytes(v).hex()))
                        print("  desc", d.uuid[:8], bytes(v).hex(), flush=True)
                    except Exception as e:
                        res.append(dict(char=ch.uuid, descriptor=d.uuid, error=repr(e)))
        try:
            await asyncio.wait_for(c.disconnect(), 5)
        except Exception:
            pass
    p = LOGS / f"read-all-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(res, indent=2)); print("saved", p)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
