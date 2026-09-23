#!/usr/bin/env python3
"""Pull your Fitbit Air data out of the Google Health API and save it as JSON + CSV.

Usage:
  gh_fetch.py                     # last 7 days, all default data types
  gh_fetch.py --days 30
  gh_fetch.py --types steps,heart-rate
  gh_fetch.py --rollup steps --window 60   # 60-second buckets (finest the API allows is 1s)

Data lands in google_health/data/.
"""
import argparse, csv, json, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh_auth import get_token

BASE = "https://health.googleapis.com/v4"
OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)

DEFAULT_TYPES = [
    "steps", "distance", "active-minutes", "active-zone-minutes", "activity-level",
    "active-energy-burned", "sedentary-period", "exercise",   # activity_and_fitness scope
    "heart-rate", "daily-resting-heart-rate", "heart-rate-variability",
    "oxygen-saturation", "vo2-max",                            # health_metrics scope
    "sleep",                                                    # sleep scope
]
# These reject a time filter - fetch unfiltered and narrow client-side.
NO_FILTER = {"exercise", "sleep"}
# `list` is unsupported for these; they are rollUp-only.
ROLLUP_ONLY = {"floors", "total-calories"}
# rollUp windows are capped by data type: 14 days for heart-rate/calories, 90 for most others
MAX_DAYS = {"heart-rate": 14, "total-calories": 14}


def hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


def list_points(tok, dtype, start, end, page_size=10000):
    """GET /users/me/dataTypes/{dataType}/dataPoints, with an ISO-8601 time filter
    where the data type accepts one."""
    url = f"{BASE}/users/me/dataTypes/{dtype}/dataPoints"
    field = dtype.replace("-", "_")
    params = {"page_size": page_size}
    if dtype not in NO_FILTER:
        params["filter"] = (f'{field}.interval.start_time >= "{start}" AND '
                            f'{field}.interval.start_time < "{end}"')
    out, token, pages = [], None, 0
    while True:
        if token:
            params["page_token"] = token
        r = requests.get(url, headers=hdr(tok), params=params, timeout=60)
        if r.status_code == 403 and "scope" in r.text.lower():
            print(f"  ! {dtype}: missing OAuth scope - add it in Cloud Console and re-run gh_auth.py")
            return out
        if r.status_code != 200:
            print(f"  ! {dtype}: HTTP {r.status_code} {r.text[:200]}")
            return out
        j = r.json()
        got = j.get("dataPoints", [])
        out.extend(got)
        pages += 1
        token = j.get("nextPageToken")
        print(f"  {dtype}: page {pages}, +{len(got)} (total {len(out)})")
        if not token or pages >= 50:
            return out


def rollup(tok, dtype, start, end, window_secs):
    """POST dataPoints:rollUp - intraday aggregation into fixed windows."""
    url = f"{BASE}/users/me/dataTypes/{dtype}/dataPoints:rollUp"
    body = {"range": {"startTime": start, "endTime": end}, "windowSize": f"{window_secs}s"}
    r = requests.post(url, headers=hdr(tok), json=body, timeout=120)
    if r.status_code != 200:
        print(f"  ! rollUp {dtype}: HTTP {r.status_code} {r.text[:300]}")
        return []
    return r.json().get("dataPoints", [])


def flatten(dp):
    """Best-effort flatten of a data point for CSV."""
    row = {}

    def walk(o, prefix=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{prefix}{k}.")
        elif isinstance(o, list):
            row[prefix.rstrip(".")] = json.dumps(o)
        else:
            row[prefix.rstrip(".")] = o
    walk(dp)
    return row


def save(name, points):
    if not points:
        print(f"  {name}: no data points returned")
        return
    jf = OUT / f"{name}.json"
    jf.write_text(json.dumps(points, indent=2))
    rows = [flatten(p) for p in points]
    cols = sorted({k for r in rows for k in r})
    cf = OUT / f"{name}.csv"
    with cf.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"  saved {len(points)} points -> {jf.name}, {cf.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--types", default=",".join(DEFAULT_TYPES))
    ap.add_argument("--rollup", help="data type to roll up into fixed windows")
    ap.add_argument("--window", type=int, default=60, help="rollUp window in seconds (min 1)")
    a = ap.parse_args()

    tok = get_token()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ts = time.strftime("%Y%m%d-%H%M%S")

    if a.rollup:
        days = min(a.days, MAX_DAYS.get(a.rollup, 90))
        start = (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        end = now.isoformat().replace("+00:00", "Z")
        print(f"rollUp {a.rollup} {start} -> {end} @ {a.window}s windows")
        save(f"rollup-{a.rollup}-{a.window}s-{ts}", rollup(tok, a.rollup, start, end, a.window))
        return

    for dtype in [t.strip() for t in a.types.split(",") if t.strip()]:
        days = min(a.days, MAX_DAYS.get(dtype, 90))
        start = (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        end = now.isoformat().replace("+00:00", "Z")
        print(f"\n{dtype}: {start} -> {end}")
        save(f"{dtype}-{ts}", list_points(tok, dtype, start, end))

    print(f"\nall output in {OUT}")


if __name__ == "__main__":
    main()
