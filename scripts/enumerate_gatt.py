#!/usr/bin/env python3
"""Connect to a device and dump its full GATT table (services/chars/descriptors/properties).
Read-only: only reads a whitelist of harmless characteristics (Device Info, Battery, Link Status).
No writes, no pairing requests. Usage: enumerate_gatt.py <CoreBluetooth-UUID> [--no-read]
"""
import asyncio, json, sys, time
from pathlib import Path
from bleak import BleakClient, BleakScanner

LOGS = Path(__file__).resolve().parent.parent / "logs"
KNOWN = {
    "abbaff00-e56a-484c-b832-8b17cf6cbfe8": "Gattlink service",
    "abbaff01-e56a-484c-b832-8b17cf6cbfe8": "Gattlink TX (phone->tracker, write-no-resp)",
    "abbaff02-e56a-484c-b832-8b17cf6cbfe8": "Gattlink RX (tracker->phone, notify)",
    "abbafd00-e56a-484c-b832-8b17cf6cbfe8": "Link Status service",
    "abbafd01-e56a-484c-b832-8b17cf6cbfe8": "Link Status: currentConnectionConfiguration",
    "abbafd02-e56a-484c-b832-8b17cf6cbfe8": "Link Status: currentConnectionStatus",
    "abbafd03-e56a-484c-b832-8b17cf6cbfe8": "Link Status: (fd03)",
    "ac2f0045-8182-4be5-91e0-2992e6b40ebb": "GATT cache validation service",
    "4eee1c00-4133-479b-8663-02c84bdc14be": "Unknown Fitbit telemetry service",
    "0000180d-0000-1000-8000-00805f9b34fb": "Heart Rate",
}
SAFE_READ_PREFIXES = ("00002a24", "00002a25", "00002a26", "00002a27", "00002a28", "00002a29",
                      "00002a19", "00002a00", "00002a01", "abbafd0")


def short(u):
    return u[:8]


async def main(addr, do_read):
    print(f"looking for {addr} ...")
    dev = await BleakScanner.find_device_by_address(addr, timeout=30)
    if dev is None:
        print("device not seen during 30s scan"); return 2
    print("found", dev)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = dict(address=addr, name=dev.name, time=ts, services=[])
    async with BleakClient(dev, timeout=30) as c:
        print("connected; mtu =", getattr(c, "mtu_size", None))
        out["mtu"] = getattr(c, "mtu_size", None)
        for s in c.services:
            sd = dict(uuid=s.uuid, handle=s.handle, description=s.description,
                      known=KNOWN.get(s.uuid.lower()), characteristics=[])
            print(f"\n[service] {s.uuid} {KNOWN.get(s.uuid.lower(), s.description)}")
            for ch in s.characteristics:
                cd = dict(uuid=ch.uuid, handle=ch.handle, properties=ch.properties,
                          known=KNOWN.get(ch.uuid.lower()), max_write=getattr(ch, "max_write_without_response_size", None),
                          descriptors=[dict(uuid=d.uuid, handle=d.handle) for d in ch.descriptors])
                print(f"   [char] {ch.uuid} {ch.properties} {KNOWN.get(ch.uuid.lower(), ch.description)}")
                for d in ch.descriptors:
                    print(f"        [desc] {d.uuid} h={d.handle}")
                if do_read and "read" in ch.properties and ch.uuid.lower().startswith(SAFE_READ_PREFIXES):
                    try:
                        v = await asyncio.wait_for(c.read_gatt_char(ch), 8)
                        cd["value_hex"] = v.hex()
                        try:
                            cd["value_text"] = v.decode("utf-8")
                        except UnicodeDecodeError:
                            pass
                        print(f"        value = {v.hex()} {cd.get('value_text', '')!r}")
                    except Exception as e:
                        cd["read_error"] = repr(e)
                        print(f"        read failed: {e!r}")
                sd["characteristics"].append(cd)
            out["services"].append(sd)
    present = {s["uuid"].lower() for s in out["services"]}
    chars = {c["uuid"].lower() for s in out["services"] for c in s["characteristics"]}
    print("\n=== Known Fitbit Air UUID check ===")
    for u in ["abbaff00-e56a-484c-b832-8b17cf6cbfe8", "abbafd00-e56a-484c-b832-8b17cf6cbfe8"]:
        print(f"service {u}: {'PRESENT' if u in present else 'absent'}")
    for u in ["abbaff01-e56a-484c-b832-8b17cf6cbfe8", "abbaff02-e56a-484c-b832-8b17cf6cbfe8",
              "abbafd01-e56a-484c-b832-8b17cf6cbfe8"]:
        print(f"char    {u}: {'PRESENT' if u in chars else 'absent'}")
    p = LOGS / f"gatt-{ts}.json"
    p.write_text(json.dumps(out, indent=2))
    print("saved", p)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], "--no-read" not in sys.argv)))
