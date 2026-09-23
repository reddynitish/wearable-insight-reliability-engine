#!/usr/bin/env python3
"""Re-run the Gattlink handshake INSIDE the ~8.31 s ATT service window.

Session 1's probe subscribed to several characteristics first and retried over 30 s, so most of
it ran after the tracker had already stopped servicing this central. This version:
  - subscribes to Gattlink TX (abbaff02) ONLY, immediately
  - sends the Reset Request (0x80) at t+~0.3 s, retrying at 1 s intervals, all inside the window
  - listens through the window and well past it
  - repeats over several reconnects (the window resets on reconnect)

Protocol verified against repos/golden-gate/xp/gattlink/gg_gattlink.c:
  PACKET_TYPE_CONTROL = 0x80, CONTROL_RESET_REQUEST = 0x0, CONTROL_RESET_COMPLETE = 0x1
Only documented Gattlink control bytes are sent. No other writes.
Usage: gattlink_in_window.py [rounds]
"""
import asyncio, json, sys, struct, time
from pathlib import Path
from bleak import BleakClient, BleakScanner

ROOT = Path(__file__).resolve().parent.parent
FD62 = "0000fd62-0000-1000-8000-00805f9b34fb"
TX = "abbaff01-e56a-484c-b832-8b17cf6cbfe8"   # we write here
RX = "abbaff02-e56a-484c-b832-8b17cf6cbfe8"   # tracker notifies here
WINDOW = 4
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 4


def parse(b: bytes) -> str:
    if not b:
        return "empty"
    h = b[0]
    if h & 0x80:
        t = h & 0x7F
        if t == 0:
            return "CONTROL Reset Request"
        if t == 1:
            return (f"CONTROL Reset Complete minVer={b[1]} maxVer={b[2]} rxWin={b[3]} txWin={b[4]}"
                    if len(b) >= 5 else "CONTROL Reset Complete (short)")
        return f"CONTROL unknown type {t}"
    off, ack = 0, None
    if h & 0x40:
        ack = h & 0x1F
        off = 1
    if off == len(b):
        return f"DATA ack-only ack={ack}"
    psn = b[off] & 0x1F
    pl = b[off + 1:]
    s = f"DATA psn={psn} ack={ack} payload={len(pl)}B"
    if len(pl) >= 28 and pl[0] >> 4 == 4:
        ihl = (pl[0] & 15) * 4
        s += f" IPv4 {'.'.join(map(str, pl[12:16]))}->{'.'.join(map(str, pl[16:20]))} proto={pl[9]}"
        if pl[9] == 17:
            sp, dp = struct.unpack(">HH", pl[ihl:ihl + 4])
            s += f" UDP {sp}->{dp}"
            d = pl[ihl + 8:]
            if len(d) >= 13:
                s += (f" DTLS type={d[0]} ver={d[1]:02x}{d[2]:02x} "
                      f"epoch={int.from_bytes(d[3:5],'big')} len={int.from_bytes(d[11:13],'big')}")
    return s


async def find(timeout=120):
    print("scanning...", flush=True)
    return await BleakScanner.find_device_by_filter(
        lambda d, adv: FD62 in [u.lower() for u in adv.service_uuids]
        or "fitbit" in (adv.local_name or "").lower(), timeout=timeout)


async def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = ROOT / "captures" / f"gattlink-inwindow-{ts}.jsonl"
    f = path.open("w")

    def log(**r):
        r["t"] = time.time()
        f.write(json.dumps(r) + "\n")
        f.flush()

    dev = await find()
    if not dev:
        print("device not found - wake the tracker")
        return
    total_rx = 0

    for rnd in range(1, ROUNDS + 1):
        print(f"\n=== round {rnd} ===", flush=True)
        try:
            async with BleakClient(dev, timeout=30) as c:
                t0 = time.time()
                log(kind="connected", round=rnd, mtu=c.mtu_size)
                print(f"connected mtu={c.mtu_size}", flush=True)
                rx_count = {"n": 0}

                def on_rx(_, data):
                    b = bytes(data)
                    rx_count["n"] += 1
                    d = parse(b)
                    log(kind="rx", round=rnd, since=round(time.time() - t0, 3), hex=b.hex(), decoded=d)
                    print(f"  t+{time.time()-t0:5.2f}s  RX {b.hex()}  {d}", flush=True)

                await c.start_notify(RX, on_rx)
                log(kind="subscribed", round=rnd, since=round(time.time() - t0, 3))
                print(f"  t+{time.time()-t0:5.2f}s  subscribed to Gattlink TX (abbaff02)", flush=True)

                # Reset Requests, all inside the ~8.31 s window
                for attempt in range(1, 7):
                    since = time.time() - t0
                    if since > 7.8:
                        break
                    try:
                        await c.write_gatt_char(TX, bytes([0x80]), response=False)
                        log(kind="tx", round=rnd, since=round(since, 3), hex="80",
                            note=f"Reset Request #{attempt}")
                        print(f"  t+{since:5.2f}s  TX 80  Reset Request #{attempt}", flush=True)
                    except Exception as e:
                        log(kind="tx_error", round=rnd, since=round(since, 3), error=repr(e))
                        print(f"  t+{since:5.2f}s  TX FAILED {e!r}", flush=True)
                    await asyncio.sleep(1.0)

                # listen past the window too - notifications may behave differently from ATT requests
                while time.time() - t0 < 25:
                    await asyncio.sleep(0.5)
                log(kind="round_end", round=rnd, rx=rx_count["n"])
                print(f"  round {rnd}: {rx_count['n']} packet(s) received on abbaff02", flush=True)
                total_rx += rx_count["n"]
        except Exception as e:
            log(kind="round_error", round=rnd, error=repr(e))
            print(f"  round error {e!r}", flush=True)
        if rnd < ROUNDS:
            await asyncio.sleep(5)
            d2 = await find(timeout=60)
            if d2:
                dev = d2

    log(kind="summary", total_rx=total_rx)
    print(f"\nTOTAL Gattlink packets received: {total_rx}")
    f.close()
    print("saved", path, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
