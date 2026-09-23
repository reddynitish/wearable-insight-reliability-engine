#!/usr/bin/env python3
"""Run the Mac as a Golden Gate HUB: a BLE peripheral exposing the services a Fitbit
tracker expects its host to provide. This is the avenue never tested before - every
previous attempt had the Mac as a GATT client only.

Per repos/golden-gate/platform/apple/apps/host/Source/BluetoothConfiguration+Defaults.swift,
the Hub hosts:
  Link Configuration Service  ABBAFC00-E56A-484C-B832-8B17CF6CBFE8
      ABBAFC01  preferred connection configuration  (read, notify)
      ABBAFC02  preferred connection mode           (read, notify)
      ABBAFC03  general purpose command             (notify)
  Gattlink Service (hub-hosted / "LocalGattlinkNode" path)
      FD62 service, ABBAFF01 (write -> we receive), ABBAFF02 (notify -> we send)

We advertise these and log EVERY interaction the tracker makes with us: connections,
subscriptions, reads, writes. If the tracker talks to a hub-shaped peer, this is where
it shows up.

Passive server: we answer reads with Golden Gate's documented default values and never
initiate anything destructive.

Usage: hub_peripheral.py [seconds]
"""
import json, sys, time, threading
from pathlib import Path

import objc
from CoreBluetooth import (
    CBPeripheralManager, CBMutableService, CBMutableCharacteristic, CBUUID,
    CBAdvertisementDataLocalNameKey, CBAdvertisementDataServiceUUIDsKey,
    CBCharacteristicPropertyRead, CBCharacteristicPropertyNotify,
    CBCharacteristicPropertyWrite, CBCharacteristicPropertyWriteWithoutResponse,
    CBAttributePermissionsReadable, CBAttributePermissionsWriteable,
)
from Foundation import NSObject, NSData, NSRunLoop, NSDate

ROOT = Path(__file__).resolve().parent.parent
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 180

LINK_CONF_SVC = "ABBAFC00-E56A-484C-B832-8B17CF6CBFE8"
PREF_CONN_CONF = "ABBAFC01-E56A-484C-B832-8B17CF6CBFE8"
PREF_CONN_MODE = "ABBAFC02-E56A-484C-B832-8B17CF6CBFE8"
GEN_PURPOSE_CMD = "ABBAFC03-E56A-484C-B832-8B17CF6CBFE8"
GATTLINK_SVC = "ABBAFF00-E56A-484C-B832-8B17CF6CBFE8"
GATTLINK_RX = "ABBAFF01-E56A-484C-B832-8B17CF6CBFE8"   # tracker writes here
GATTLINK_TX = "ABBAFF02-E56A-484C-B832-8B17CF6CBFE8"   # we notify here

# Golden Gate LinkConnectionConfiguration.default -> fast mode only.
# ModeConfiguration.fast = min 12, max 12, slaveLatency 0, supervisionTimeout 20
# rawValue layout: mask byte, then min(u16 LE), max(u16 LE), slaveLatency(u8), supervisionTimeout(u8)
PREF_CONF_VALUE = bytes([0x01, 0x0C, 0x00, 0x0C, 0x00, 0x00, 0x14])
PREF_MODE_VALUE = bytes([0x01])   # fast

LOG = (ROOT / "captures" / f"hub-peripheral-{time.strftime('%Y%m%d-%H%M%S')}.jsonl").open("w")


def log(**r):
    r["t"] = time.time()
    LOG.write(json.dumps(r) + "\n")
    LOG.flush()
    print("  " + json.dumps(r), flush=True)


def u(s):
    return CBUUID.UUIDWithString_(s)


class Delegate(NSObject):
    def init(self):
        self = objc.super(Delegate, self).init()
        self.mgr = None
        self.added = 0
        self.tx_char = None
        return self

    # --- state ---
    def peripheralManagerDidUpdateState_(self, mgr):
        states = {0: "unknown", 1: "resetting", 2: "unsupported", 3: "unauthorized",
                  4: "poweredOff", 5: "poweredOn"}
        st = states.get(mgr.state(), str(mgr.state()))
        print(f"peripheral manager state: {st}", flush=True)
        log(kind="state", state=st)
        if mgr.state() != 5:
            return

        # Link Configuration Service (the one a Hub is supposed to expose)
        pcc = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            u(PREF_CONN_CONF),
            CBCharacteristicPropertyRead | CBCharacteristicPropertyNotify,
            None, CBAttributePermissionsReadable)
        pcm = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            u(PREF_CONN_MODE),
            CBCharacteristicPropertyRead | CBCharacteristicPropertyNotify,
            None, CBAttributePermissionsReadable)
        gpc = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            u(GEN_PURPOSE_CMD), CBCharacteristicPropertyNotify, None, 0)
        svc1 = CBMutableService.alloc().initWithType_primary_(u(LINK_CONF_SVC), True)
        svc1.setCharacteristics_([pcc, pcm, gpc])

        # Hub-hosted Gattlink service
        rx = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            u(GATTLINK_RX),
            CBCharacteristicPropertyWrite | CBCharacteristicPropertyWriteWithoutResponse,
            None, CBAttributePermissionsWriteable)
        tx = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            u(GATTLINK_TX), CBCharacteristicPropertyNotify, None, 0)
        self.tx_char = tx
        svc2 = CBMutableService.alloc().initWithType_primary_(u(GATTLINK_SVC), True)
        svc2.setCharacteristics_([rx, tx])

        self.mgr = mgr
        mgr.addService_(svc1)
        mgr.addService_(svc2)

    def peripheralManager_didAddService_error_(self, mgr, svc, err):
        log(kind="service_added", uuid=str(svc.UUID()), error=str(err) if err else None)
        self.added += 1
        if self.added == 2:
            adv = {
                CBAdvertisementDataLocalNameKey: "GG-Hub",
                CBAdvertisementDataServiceUUIDsKey: [u(LINK_CONF_SVC), u(GATTLINK_SVC)],
            }
            mgr.startAdvertising_(adv)

    def peripheralManagerDidStartAdvertising_error_(self, mgr, err):
        log(kind="advertising", error=str(err) if err else None)
        print("\n*** Now advertising as a Golden Gate Hub. Waiting for the tracker. ***\n", flush=True)

    # --- the interesting callbacks ---
    def peripheralManager_central_didSubscribeToCharacteristic_(self, mgr, central, ch):
        log(kind="SUBSCRIBE", central=str(central.identifier()), uuid=str(ch.UUID()),
            mtu=central.maximumUpdateValueLength())

    def peripheralManager_central_didUnsubscribeFromCharacteristic_(self, mgr, central, ch):
        log(kind="UNSUBSCRIBE", central=str(central.identifier()), uuid=str(ch.UUID()))

    def peripheralManager_didReceiveReadRequest_(self, mgr, req):
        uid = str(req.characteristic().UUID()).upper()
        val = b""
        if uid.startswith("ABBAFC01") or uid == PREF_CONN_CONF.upper():
            val = PREF_CONF_VALUE
        elif uid.startswith("ABBAFC02") or uid == PREF_CONN_MODE.upper():
            val = PREF_MODE_VALUE
        log(kind="READ_REQUEST", central=str(req.central().identifier()), uuid=uid,
            answered_with=val.hex())
        req.setValue_(NSData.dataWithBytes_length_(val, len(val)))
        mgr.respondToRequest_withResult_(req, 0)   # CBATTErrorSuccess

    def peripheralManager_didReceiveWriteRequests_(self, mgr, reqs):
        for req in reqs:
            v = req.value()
            b = bytes(v) if v is not None else b""
            log(kind="WRITE_REQUEST", central=str(req.central().identifier()),
                uuid=str(req.characteristic().UUID()), hex=b.hex(), len=len(b))
        mgr.respondToRequest_withResult_(reqs[0], 0)


def main():
    d = Delegate.alloc().init()
    mgr = CBPeripheralManager.alloc().initWithDelegate_queue_options_(d, None, None)
    print(f"running as GG hub for {DUR:.0f}s ...", flush=True)
    end = time.time() + DUR
    while time.time() < end:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.25))
    try:
        mgr.stopAdvertising()
    except Exception:
        pass
    log(kind="stopped")
    LOG.close()
    print("done", flush=True)


if __name__ == "__main__":
    main()
