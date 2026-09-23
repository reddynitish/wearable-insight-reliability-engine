#!/usr/bin/env python3
"""Scan until a Fitbit-looking advertiser (service 0xFD62/0xFD63, abba*, adabfb00, or Fitbit mfg data)
appears, then immediately connect and enumerate GATT. Logs every sighting."""
import asyncio, json, sys, time
from pathlib import Path
from bleak import BleakScanner
sys.path.insert(0, str(Path(__file__).parent))
import enumerate_gatt

LOGS = Path(__file__).resolve().parent.parent / "logs"
SIG = ("0000fd62", "0000fd63", "abbaff00", "abbafd00", "adabfb00")


def looks_fitbit(adv):
    return any(s.lower().startswith(SIG) for s in adv.service_uuids) or 0x0DA5 in adv.manufacturer_data


async def main(timeout):
    found = asyncio.get_event_loop().create_future()
    log = (LOGS / f"watch-{time.strftime('%Y%m%d-%H%M%S')}.jsonl").open("w")

    def cb(dev, adv):
        if looks_fitbit(adv):
            rec = dict(t=time.time(), addr=dev.address, rssi=adv.rssi, name=adv.local_name,
                       services=adv.service_uuids, mfg={hex(k): v.hex() for k, v in adv.manufacturer_data.items()},
                       sdata={k: v.hex() for k, v in adv.service_data.items()})
            log.write(json.dumps(rec) + "\n"); log.flush()
            print("SIGHTING", rec)
            if not found.done():
                found.set_result(dev.address)

    async with BleakScanner(cb):
        try:
            addr = await asyncio.wait_for(found, timeout)
        except asyncio.TimeoutError:
            print(f"no Fitbit-like advertiser within {timeout}s"); return 2
    print("candidate:", addr)
    await asyncio.sleep(1)
    return await enumerate_gatt.main(addr, True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 300)))
