#!/usr/bin/env python3
"""Analyze a motion-v2 capture: per-label packet rates on both surfaces, payload variability,
and whether anything correlates with movement.
Usage: analyze_v2.py [capture.jsonl]
"""
import json, sys, glob
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob(
    __file__.rsplit("/", 2)[0] + "/captures/motion-v2-*.jsonl"))[-1]
recs = [json.loads(l) for l in open(path)]
print(f"capture: {path}\nrecords: {len(recs)}\n")

# Build labelled windows per surface
windows = []          # (surface, label, t0, t1)
open_lbl = {}
for r in recs:
    if r["kind"] != "label":
        continue
    key = (r["surface"], r["label"])
    if r["phase"] == "start":
        open_lbl[key] = r["t"]
    elif key in open_lbl:
        windows.append((r["surface"], r["label"], open_lbl.pop(key), r["t"]))


def in_window(t, w):
    return w[2] <= t <= w[3]


print("=" * 72)
print("PHASE A - advertisement surface (device NOT connected)")
print("=" * 72)
advs = [r for r in recs if r["kind"] == "adv"]
print(f"total adverts: {len(advs)}")
payloads = defaultdict(set)
for r in advs:
    for k, v in (r.get("sdata") or {}).items():
        payloads[k].add(v)
print(f"{'segment':<20}{'adverts':>9}{'rate Hz':>10}{'RSSI min/mean/max':>24}")
for surf, lbl, t0, t1 in windows:
    if surf != "adv":
        continue
    seg = [r for r in advs if in_window(r["t"], (surf, lbl, t0, t1))]
    dur = t1 - t0
    rs = [r["rssi"] for r in seg]
    rssi = f"{min(rs)}/{sum(rs)/len(rs):.1f}/{max(rs)}" if rs else "-"
    print(f"{lbl:<20}{len(seg):>9}{len(seg)/dur:>10.3f}{rssi:>24}")
print("\nservice-data payload uniqueness (does the broadcast payload change at all?):")
for k, vs in payloads.items():
    print(f"  {k[:8]}: {len(vs)} distinct value(s) across the whole phase")
    for v in list(vs)[:5]:
        print(f"     {v}")

print()
print("=" * 72)
print("PHASE B - connected GATT surface")
print("=" * 72)
conn = [r for r in recs if r["kind"] == "connected"]
print("connected:", conn[0] if conn else "NEVER CONNECTED")
subs = [r["uuid"] for r in recs if r["kind"] == "subscribed"]
errs = [r for r in recs if r["kind"] == "subscribe_error"]
print(f"subscribed characteristics: {len(subs)}")
for u in subs:
    print("   ", u)
for e in errs:
    print("   SUBSCRIBE FAILED", e["uuid"], e["error"])

live = [r for r in recs if r["kind"] == "liveness"]
ok = sum(1 for r in live if r["ok"])
print(f"\nLINK LIVENESS: {ok}/{len(live)} heartbeat reads succeeded "
      f"({'link provably alive throughout' if live and ok == len(live) else 'SEE FAILURES'})")
for r in live:
    if not r["ok"]:
        print("   FAIL", r.get("error"))

nots = [r for r in recs if r["kind"] == "notify"]
print(f"\ntotal notifications: {len(nots)}")
print(f"{'segment':<20}{'notifs':>8}{'rate Hz':>10}  uuids")
for surf, lbl, t0, t1 in windows:
    if surf != "gatt":
        continue
    seg = [r for r in nots if in_window(r["t"], (surf, lbl, t0, t1))]
    uu = sorted({r["uuid"][:8] for r in seg})
    print(f"{lbl:<20}{len(seg):>8}{len(seg)/(t1-t0):>10.3f}  {','.join(uu)}")

by_uuid = defaultdict(list)
for r in nots:
    by_uuid[r["uuid"]].append(r)
print("\nper-characteristic payload analysis:")
for u, rs in by_uuid.items():
    vals = {r["hex"] for r in rs}
    print(f"  {u[:8]}: {len(rs)} packets, {len(vals)} distinct payloads, lens={sorted({r['len'] for r in rs})}")
    for v in list(vals)[:4]:
        print(f"     {v}")

done = [r for r in recs if r["kind"] == "done"]
if done:
    print("\nfinal counts:", json.dumps(done[-1].get("counts")))
    print("liveness tally:", json.dumps(done[-1].get("liveness")))

print("\n" + "=" * 72)
print("VERDICT INPUTS")
print("=" * 72)
adv_rate = len(advs) / max(1e-9, sum(w[3] - w[2] for w in windows if w[0] == "adv"))
print(f"advert surface: {adv_rate:.3f} Hz mean, "
      f"{sum(len(v) for v in payloads.values())} distinct payload(s) -> "
      f"{'payload is static (rotating privacy token only)' if all(len(v) <= 1 for v in payloads.values()) else 'payload VARIES - investigate'}")
gatt_secs = sum(w[3] - w[2] for w in windows if w[0] == "gatt")
print(f"gatt surface: {len(nots)} notifications in {gatt_secs:.0f}s of labelled movement "
      f"({len(nots)/max(1e-9,gatt_secs):.4f} Hz)")
print(f"link alive during that silence: {ok}/{len(live)} heartbeats OK")
