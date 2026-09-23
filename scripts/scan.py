#!/usr/bin/env python3
"""Scan for BLE devices and flag likely Fitbit Air candidates. Logs JSON."""
import asyncio, json, sys, time
from pathlib import Path
from bleak import BleakScanner

FITBIT_PREFIX = "abba"            # abbaff00 / abbafd00 private services
FITBIT_SVC = {"abbaff00-e56a-484c-b832-8b17cf6cbfe8", "abbafd00-e56a-484c-b832-8b17cf6cbfe8",
              "adabfb00-6e7d-4601-bda2-bffaa68956ba"}
FITBIT_COMPANY = 0x0DA5          # Fitbit, Inc. (Bluetooth SIG)
GOOGLE_COMPANY = 0x00E0
NAME_HINTS = ("fitbit", "air", "charge", "inspire", "google")

LOGS = Path(__file__).resolve().parent.parent / "logs"


async def main(duration: float):
    seen = {}

    def cb(dev, adv):
        seen[dev.address] = (dev, adv)

    async with BleakScanner(cb):
        await asyncio.sleep(duration)

    rows = []
    for addr, (dev, adv) in seen.items():
        svcs = [s.lower() for s in adv.service_uuids]
        mfg = {hex(k): v.hex() for k, v in adv.manufacturer_data.items()}
        score = 0
        reasons = []
        if any(s in FITBIT_SVC or s.startswith(FITBIT_PREFIX) for s in svcs):
            score += 5; reasons.append("fitbit-service-uuid")
        if FITBIT_COMPANY in adv.manufacturer_data:
            score += 4; reasons.append("mfg-fitbit-0x0da5")
        name = (adv.local_name or dev.name or "")
        if any(h in name.lower() for h in NAME_HINTS):
            score += 2; reasons.append("name-hint")
        rows.append(dict(address=addr, name=name, rssi=adv.rssi, services=svcs,
                         manufacturer_data=mfg, service_data={k: v.hex() for k, v in adv.service_data.items()},
                         tx_power=adv.tx_power, score=score, reasons=reasons))
    rows.sort(key=lambda r: (-r["score"], -r["rssi"]))
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = LOGS / f"scan-{ts}.json"
    out.write_text(json.dumps(rows, indent=2))
    print(f"{len(rows)} devices, log -> {out}\n")
    for r in rows:
        flag = "***" if r["score"] else "   "
        print(f"{flag} score={r['score']} rssi={r['rssi']:>4} {r['address']} name={r['name']!r} "
              f"svcs={[s[:8] for s in r['services']]} mfg={list(r['manufacturer_data'])} {r['reasons']}")


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 15))
