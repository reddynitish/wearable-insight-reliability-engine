#!/usr/bin/env python3
"""Motion-correlation test v2. Fixes two gaps in the v1 run:
  (1) v1 never proved the BLE link was still alive during the silent 3 minutes.
      -> Phase B reads Firmware Revision every 10 s as a liveness heartbeat.
  (2) v1 never tested the ADVERTISEMENT surface against motion.
      -> Phase A monitors adverts (RSSI, service data, advert rate) while NOT connected.

Read-only: no writes to the device except CCCD subscribe (standard notification enable).
Usage: motion_test_v2.py [--say] [--phase a|b|both]
"""
import asyncio, json, sys, time, subprocess
from pathlib import Path
from bleak import BleakClient, BleakScanner

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "captures"
OUT.mkdir(exist_ok=True)
FD62 = "0000fd62-0000-1000-8000-00805f9b34fb"
FW_REV = "00002a26-0000-1000-8000-00805f9b34fb"

# (label, seconds)
SCHEDULE_A = [("still", 20), ("wave", 15), ("still", 15), ("punch", 15), ("wrist rotation", 15)]
SCHEDULE_B = [("still", 20), ("typing", 20), ("still", 15), ("wave", 15), ("still", 15),
              ("punch", 15), ("still", 15), ("wrist rotation", 15), ("still", 15),
              ("drinking motion", 15), ("still", 15), ("lifting weights", 15)]

USE_SAY = "--say" in sys.argv


def cue(text):
    print(f"\n>>> {text}", flush=True)
    if USE_SAY:
        subprocess.Popen(["say", text])


class Log:
    def __init__(self, path):
        self.f = path.open("w")
        self.path = path

    def emit(self, kind, **kw):
        rec = dict(t=time.time(), kind=kind, **kw)
        self.f.write(json.dumps(rec) + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


async def find(timeout=120):
    print("scanning for Fitbit Air (service 0xFD62)... wake it: tap it / raise your wrist", flush=True)
    return await BleakScanner.find_device_by_filter(
        lambda d, adv: FD62 in [u.lower() for u in adv.service_uuids]
        or (adv.local_name or "").lower().find("fitbit") >= 0,
        timeout=timeout,
    )


async def phase_a(log):
    """Advert-surface motion test: no connection, just watch what the tracker broadcasts."""
    print("\n===== PHASE A: advertisement surface (not connected) =====", flush=True)
    counts = {"adv": 0}
    state = {"label": "warmup"}

    def cb(d, adv):
        if FD62 not in [u.lower() for u in adv.service_uuids] and "fitbit" not in (adv.local_name or "").lower():
            return
        counts["adv"] += 1
        log.emit("adv", addr=d.address, name=adv.local_name, rssi=adv.rssi,
                 label=state["label"],
                 sdata={k: v.hex() for k, v in (adv.service_data or {}).items()},
                 mfg={str(k): v.hex() for k, v in (adv.manufacturer_data or {}).items()})

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    try:
        cue("Phase A. Wear the tracker. Get ready.")
        await asyncio.sleep(8)
        for label, dur in SCHEDULE_A:
            state["label"] = label
            n0 = counts["adv"]
            cue(label)
            log.emit("label", label=label, phase="start", surface="adv")
            await asyncio.sleep(dur)
            log.emit("label", label=label, phase="end", surface="adv")
            print(f"    [{label}] adverts seen: {counts['adv'] - n0} in {dur}s", flush=True)
    finally:
        await scanner.stop()
    print(f"Phase A done. total adverts={counts['adv']}", flush=True)


async def phase_b(log):
    """Connected surface: subscribe to everything + prove the link stays alive."""
    print("\n===== PHASE B: connected GATT surface =====", flush=True)
    dev = await find()
    if not dev:
        print("!! device not found; wake the tracker and re-run")
        log.emit("error", msg="device not found")
        return
    log.emit("found", addr=dev.address, name=dev.name)
    counts = {}
    async with BleakClient(dev, timeout=30) as c:
        log.emit("connected", addr=dev.address, mtu=c.mtu_size)
        print(f"connected {dev.address} mtu={c.mtu_size}", flush=True)
        subs = []
        for s in c.services:
            for ch in s.characteristics:
                if not ({"notify", "indicate"} & set(ch.properties)):
                    continue

                def make(u):
                    def h(_, data):
                        counts[u] = counts.get(u, 0) + 1
                        log.emit("notify", uuid=u, len=len(data), hex=bytes(data).hex())
                        print(f"  NOTIFY {u[:8]} {bytes(data).hex()}", flush=True)
                    return h

                try:
                    await c.start_notify(ch, make(ch.uuid))
                    subs.append(ch.uuid)
                    log.emit("subscribed", uuid=ch.uuid)
                except Exception as e:
                    log.emit("subscribe_error", uuid=ch.uuid, error=repr(e))
        print(f"subscribed to {len(subs)} characteristics", flush=True)

        alive = {"ok": 0, "fail": 0}

        async def heartbeat():
            """Prove the link is alive: if reads keep succeeding, silence is real silence."""
            while True:
                await asyncio.sleep(10)
                try:
                    v = await asyncio.wait_for(c.read_gatt_char(FW_REV), 8)
                    alive["ok"] += 1
                    log.emit("liveness", ok=True, connected=c.is_connected, value=bytes(v).decode(errors="replace"))
                except Exception as e:
                    alive["fail"] += 1
                    log.emit("liveness", ok=False, connected=c.is_connected, error=repr(e))
                    print(f"  !! liveness read failed: {e!r}", flush=True)

        hb = asyncio.create_task(heartbeat())
        try:
            cue("Phase B. Get ready.")
            await asyncio.sleep(10)
            for label, dur in SCHEDULE_B:
                n0 = sum(counts.values())
                cue(label)
                log.emit("label", label=label, phase="start", surface="gatt")
                await asyncio.sleep(dur)
                log.emit("label", label=label, phase="end", surface="gatt")
                print(f"    [{label}] notifications: {sum(counts.values()) - n0} "
                      f"(link alive: {alive['ok']} ok / {alive['fail']} fail)", flush=True)
        finally:
            hb.cancel()
            cue("done")
            log.emit("done", counts=counts, liveness=alive, connected=c.is_connected)
            for u in subs:
                try:
                    await asyncio.wait_for(c.stop_notify(u), 3)
                except Exception:
                    pass
    print(f"\nPhase B done. notification counts={counts} liveness={alive}", flush=True)


async def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    log = Log(OUT / f"motion-v2-{ts}.jsonl")
    phase = "both"
    if "--phase" in sys.argv:
        phase = sys.argv[sys.argv.index("--phase") + 1]
    try:
        if phase in ("a", "both"):
            await phase_a(log)
        if phase in ("b", "both"):
            await phase_b(log)
    finally:
        log.close()
    print("\nsaved", log.path, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
