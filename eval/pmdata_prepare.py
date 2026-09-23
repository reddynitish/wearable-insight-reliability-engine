#!/usr/bin/env python3
"""One-time reduction of PMData into a compact per-subject cache.

    ./.venv/bin/python -m eval.pmdata_prepare

Streams each participant's heart_rate.json (~114 MB) and steps.json once and writes
per-day coverage summaries plus the small daily series. Everything lands in
`data/datasets/pmdata_cache/`, which is gitignored: no participant record is committed.

Run this before `eval.pmdata_eval`. It takes roughly a minute per participant.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from engine.adapters.pmdata import (
    CITATION,
    DEFAULT_TZ,
    load_daily_series,
    load_sleep,
    load_wellness,
    participants,
    summarise_days,
)

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO / "data" / "datasets" / "pmdata"
DEFAULT_CACHE = REPO / "data" / "datasets" / "pmdata_cache"


def prepare(root: Path, cache: Path, tz_name: str, force: bool = False) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dataset": "PMData",
        "citation": CITATION,
        "timezone_assumed": tz_name,
        "timezone_note": (
            "Fitbit exports naive device-local timestamps and PMData does not state a "
            "zone. This is an assumption, and it shifts which samples fall in which day."
        ),
        "subjects": {},
    }
    for participant in participants(root):
        target = cache / f"{participant.name}.json"
        if target.is_file() and not force:
            print(f"{participant.name}: cached")
            manifest["subjects"][participant.name] = json.loads(target.read_text())["counts"]
            continue
        start = time.time()
        summaries = summarise_days(participant, tz_name)
        daily = load_daily_series(participant, tz_name)
        sleep = load_sleep(participant, tz_name)
        wellness = load_wellness(participant)
        payload = {
            "subject_id": f"pmdata-{participant.name}",
            "timezone": tz_name,
            "citation": CITATION,
            "day_summaries": {k: v.as_dict() for k, v in summaries.items()},
            "daily": daily,
            "sleep": [
                {
                    **{k: v for k, v in record.items() if k not in ("start", "end")},
                    "start": record["start"].isoformat(),
                    "end": record["end"].isoformat(),
                }
                for record in sleep
            ],
            "wellness": wellness,
            "counts": {
                "days_with_heart_rate": len(summaries),
                "days_with_resting_heart_rate": sum(
                    1 for v in daily.values() if "resting_heart_rate" in v
                ),
                "days_with_active_minutes": sum(
                    1 for v in daily.values() if "active_minutes" in v
                ),
                "sleep_periods": len(sleep),
                "wellness_reports": len(wellness),
            },
        }
        target.write_text(json.dumps(payload))
        manifest["subjects"][participant.name] = payload["counts"]
        print(f"{participant.name}: {payload['counts']} in {time.time() - start:.0f}s")
    (cache / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--timezone", default=DEFAULT_TZ)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"PMData not found at {args.root}")
        print("Download it first; see data/DATASETS.md.")
        return 1
    manifest = prepare(args.root, args.cache, args.timezone, args.force)
    total = {
        key: sum(s[key] for s in manifest["subjects"].values())
        for key in next(iter(manifest["subjects"].values()))
    }
    print(f"\n{len(manifest['subjects'])} subjects cached at {args.cache}")
    print(json.dumps(total, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
