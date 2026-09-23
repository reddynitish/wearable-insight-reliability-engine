#!/usr/bin/env python3
"""Minimal Gattlink handshake probe (Golden Gate spec: xp/gattlink/gg_gattlink.c).

Sends ONLY documented Gattlink control packets to abbaff01:
  Reset Request  = 0x80
  Reset Complete = 0x81 <minVer> <maxVer> <maxRxWindow> <maxTxWindow>
Follows the state machine in docs/src/architecture/gattlink.md. Does NOT attempt DTLS.
Everything (tx + rx) is logged to captures/gattlink-probe-*.jsonl.
Usage: gattlink_probe.py <addr>
"""
import asyncio, json, sys, time, struct
from pathlib import Path
from bleak import BleakClient, BleakScanner

OUT = Path(__file__).resolve().parent.parent / "captures"
TX = "abbaff01-e56a-484c-b832-8b17cf6cbfe8"
RX = "abbaff02-e56a-484c-b832-8b17cf6cbfe8"
PASSIVE = ["abbafd01-e56a-484c-b832-8b17cf6cbfe8", "abbafd02-e56a-484c-b832-8b17cf6cbfe8",
           "00002a37-0000-1000-8000-00805f9b34fb"]
WINDOW = 4


def parse(b: bytes) -> str:
    if not b:
        return "empty"
    h = b[0]
    if h & 0x80:
        t = h & 0x7F
        if t == 0:
            return "CONTROL Reset Request"
        if t == 1:
            return f"CONTROL Reset Complete minVer={b[1]} maxVer={b[2]} rxWin={b[3]} txWin={b[4]}" if len(b) >= 5 else "CONTROL Reset Complete (short)"
        return f"CONTROL unknown type {t}"
    off, ack = 0, None
    if h & 0x40:
        ack = h & 0x1F; off = 1
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
                s += f" DTLS type={d[0]} ver={d[1]:02x}{d[2]:02x} epoch={int.from_bytes(d[3:5],'big')} len={int.from_bytes(d[11:13],'big')}"
                if d[0] == 22 and len(d) > 13:
                    s += f" handshake_msg_type={d[13]}"
    return s


async def main(addr):
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = OUT / f"gattlink-probe-{ts}.jsonl"
    f = path.open("w")
    inbox = asyncio.Queue()

    def emit(kind, **kw):
        rec = dict(t=time.time(), kind=kind, **kw)
        f.write(json.dumps(rec) + "\n"); f.flush()
        return rec

    print("waiting for tracker to advertise (wake it: tap/raise your wrist)...", flush=True)
    if addr == "auto":
        dev = await BleakScanner.find_device_by_filter(
            lambda d, adv: any(u.lower().startswith("0000fd62") for u in adv.service_uuids), timeout=120)
    else:
        dev = await BleakScanner.find_device_by_address(addr, timeout=120)
    if not dev:
        print("device not found in 120s (wake the tracker)"); return 2
    state = "INITIALIZED"
    async with BleakClient(dev, timeout=30) as c:
        emit("connected", mtu=c.mtu_size)

        def on_rx(_, data):
            b = bytes(data); d = parse(b)
            emit("rx", uuid="abbaff02", hex=b.hex(), decoded=d); print("RX ", b.hex(), d, flush=True)
            inbox.put_nowait(b)

        def on_other(uuid):
            def cb(_, data):
                emit("rx", uuid=uuid, hex=bytes(data).hex()); print("RX*", uuid[:8], bytes(data).hex(), flush=True)
            return cb

        for u in PASSIVE:
            try:
                await c.start_notify(u, on_other(u))
            except Exception as e:
                emit("subscribe_error", uuid=u, error=repr(e))
        await c.start_notify(RX, on_rx)
        print("subscribed to Gattlink RX; sending Reset Request")

        async def send(b: bytes, note):
            emit("tx", uuid="abbaff01", hex=b.hex(), note=note)
            print("TX ", b.hex(), note, flush=True)
            await c.write_gatt_char(TX, b, response=False)

        complete = bytes([0x81, 0x00, 0x00, WINDOW, WINDOW])
        deadline = time.time() + 30
        state = "AWAITING_RESET_COMPLETE_SELF_INITIATED"
        await send(bytes([0x80]), "Reset Request")
        last_req = time.time(); tries = 1
        while time.time() < deadline:
            try:
                b = await asyncio.wait_for(inbox.get(), 0.25)
            except asyncio.TimeoutError:
                # spec: retransmit reset request every 1 s while unanswered (max 3 tries total)
                if state.startswith("AWAITING_RESET_COMPLETE_SELF") and tries < 3 and time.time() - last_req > 1.0:
                    tries += 1
                    await send(bytes([0x80]), "Reset Request (retry)"); last_req = time.time()
                continue
            if b and b[0] & 0x80:
                t = b[0] & 0x7F
                if t == 0:      # Reset Request from remote
                    await send(complete, "Reset Complete (answering remote request)")
                    state = "AWAITING_RESET_COMPLETE_REMOTE_INITIATED"
                elif t == 1:    # Reset Complete from remote
                    if state.startswith("AWAITING_RESET_COMPLETE_SELF"):
                        await send(complete, "Reset Complete (answering remote complete)")
                    state = "READY"
            emit("state", state=state); print("state:", state, flush=True)
            if state == "READY":
                deadline = max(deadline, time.time() + 20)   # listen for data (e.g. DTLS from tracker)
                state = "READY-LISTENING"
        emit("end", state=state)
        print("final state:", state)
        for u in [RX] + PASSIVE:
            try:
                await asyncio.wait_for(c.stop_notify(u), 3)
            except Exception:
                pass
        try:
            await asyncio.wait_for(c.disconnect(), 5)
        except Exception:
            pass
    print("saved", path)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
