#!/usr/bin/env python3
"""Run the reliability engine over your own Google Health export.

    ./.venv/bin/python demo/google_health_demo.py --report
    ./.venv/bin/python demo/google_health_demo.py --timezone America/New_York
    ./.venv/bin/python demo/google_health_demo.py --timezone America/New_York \
        --corrupt shorten_wear --severity severe

Fetch the data first with `google_health/gh_fetch.py` (see google_health/README.md).

Privacy: this script reads `google_health/data/`, which is gitignored, holds it in memory
for the length of the process, and writes nothing. It never touches `credentials.json` or
`token.json` -- the adapter cannot, because it makes no network call. The subject
identifier is a pseudonym supplied on the command line, never an account name.

Its output does contain your own measurements, because a decision explanation states the
value it was about. Do not paste that output into an issue, a commit, or a README.

Expect WAIT_FOR_MORE_DATA. The device history here is short, the baseline-dependent claims
need 7 to 14 valid days, and abstaining is the correct answer when the history is not
there yet. That is the product thesis working, not the demo failing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from engine.adapters.google_health import (  # noqa: E402
    baseline_from_observations,
    daily_values,
    load_directory,
    window_observations,
)
from engine.corruptions import SEVERITIES, apply as apply_corruption, names as corruption_names  # noqa: E402
from engine.engine import evaluate  # noqa: E402
from engine.policies import get_policy, supported_claim_types  # noqa: E402
from engine.schemas import (  # noqa: E402
    Claim,
    EvaluationContext,
    EvaluationRequest,
    TimeWindow,
)
from engine.signals import Signal  # noqa: E402
from engine.synth import GroundTruth, SyntheticCase  # noqa: E402

U = timezone.utc
DEFAULT_DATA_DIR = REPO / "google_health" / "data"

# Claims this demo can assemble from a Google Health export. The two sleep claims and the
# anomaly claim need fields whose export shape is unverified, so they are attempted and
# reported rather than promised.
DEMO_CLAIMS = (
    "RESTING_HEART_RATE_ELEVATED",
    "ACTIVITY_LOAD_HIGH",
    "SLEEP_DURATION_LOW",
    "SLEEP_QUALITY_REDUCED",
    "RECOVERY_EVIDENCE_INCOMPLETE",
)

STATEMENTS = {
    "RESTING_HEART_RATE_ELEVATED": "Your resting heart rate is elevated today.",
    "ACTIVITY_LOAD_HIGH": "Your activity load was high today.",
    "SLEEP_DURATION_LOW": "You slept less than usual.",
    "SLEEP_QUALITY_REDUCED": "Your sleep quality was reduced.",
    "RECOVERY_EVIDENCE_INCOMPLETE": "There is not enough evidence to judge your recovery.",
}


def local_day_window(day: datetime, tz: ZoneInfo | None) -> TimeWindow:
    """The subject-local calendar day containing `day`, as a UTC window."""
    if tz is not None:
        local = day.astimezone(tz)
        start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return TimeWindow(
            start=start_local.astimezone(U), end=(start_local + timedelta(days=1)).astimezone(U)
        )
    start = day.astimezone(U).replace(hour=0, minute=0, second=0, microsecond=0)
    return TimeWindow(start=start, end=start + timedelta(days=1))


def claim_window(claim_type: str, observations, day_window: TimeWindow) -> TimeWindow | None:
    """The window this claim is actually about.

    A sleep claim is about a sleep period, not a calendar day: a night that starts at 23:00
    lies only 4% inside the day it ends in, so evaluating it against a calendar-day window
    correctly produces MISSING_REQUIRED_SIGNAL. The right fix is to ask the right question,
    so sleep claims use the recorded sleep period that ends inside the target day.
    """
    if not claim_type.startswith("SLEEP_"):
        return day_window
    periods = [
        o for o in observations
        if o.signal is Signal.SLEEP_DURATION
        and o.window_start is not None
        and o.window_end is not None
        and day_window.start < o.window_end <= day_window.end + timedelta(hours=12)
    ]
    if not periods:
        return None
    longest = max(periods, key=lambda o: (o.window_end - o.window_start))
    assert longest.window_start is not None and longest.window_end is not None
    return TimeWindow(start=longest.window_start, end=longest.window_end)


def build_request(
    claim_type: str,
    observations,
    window: TimeWindow,
    subject_id: str,
    tz_name: str | None,
    evaluated_at: datetime,
) -> EvaluationRequest:
    policy = get_policy(claim_type)
    assert policy is not None
    in_window = window_observations(observations, window)

    baseline = []
    signals = list(policy.required_signals) or list(policy.insufficiency_inputs)
    for signal in signals:
        aggregation = "sum" if policy.target_aggregation == "sum" else "mean"
        baseline.extend(
            baseline_from_observations(observations, signal, window, aggregation=aggregation)
        )

    # Wear time is not an exported data type. Steps presence per minute-bucket is the
    # closest honest proxy, and where there is no proxy the context field is left None so
    # the engine reports COVERAGE_UNKNOWN instead of receiving a fabricated number.
    worn = None
    wear_obs = [o for o in in_window if o.signal is Signal.WEAR_MINUTES]
    if wear_obs:
        worn = sum(o.value for o in wear_obs)

    latest = max((o.measured_at for o in observations), default=None)
    return EvaluationRequest(
        subject_id=subject_id,
        claim=Claim(
            type=claim_type,
            statement=STATEMENTS.get(claim_type),
            target_window=window,
        ),
        observations=in_window,
        baseline=baseline,
        context=EvaluationContext(
            # The export records no sync-completion time, so this is left unset and the
            # engine discloses SYNC_STATE_UNKNOWN rather than assuming the sync finished.
            sync_completed_at=None,
            device_worn_minutes=worn,
            expected_worn_minutes=window.duration_minutes,
            timezone=tz_name,
            device="fitbit-air",
        ),
        evaluated_at=evaluated_at,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--subject-id", default="owner-demo-001",
                        help="a pseudonym; never an account name or email")
    parser.add_argument("--timezone", default=None,
                        help="IANA zone, e.g. America/New_York. Required for day-window claims")
    parser.add_argument("--day", default=None,
                        help="YYYY-MM-DD subject-local day; default is the latest complete day")
    parser.add_argument("--claim", choices=supported_claim_types(), action="append")
    parser.add_argument("--corrupt", choices=corruption_names(), default=None,
                        help="inject a documented failure and show the decision change")
    parser.add_argument("--severity", choices=list(SEVERITIES), default="moderate")
    parser.add_argument("--report", action="store_true",
                        help="print the adapter conversion report and exit")
    parser.add_argument("--json", action="store_true", help="emit decisions as JSON")
    args = parser.parse_args()

    if not args.data_dir.is_dir():
        print(f"no export directory at {args.data_dir}")
        print("fetch data first: ./.venv/bin/python google_health/gh_fetch.py --days 30")
        return 1

    observations, report = load_directory(args.data_dir, device="fitbit-air")
    print(f"converted {report.mapped} observation(s) from {args.data_dir}")
    if not report.clean:
        print("adapter did not convert everything:")
        print(json.dumps(report.as_dict(), indent=2))
        print("The field mapping in engine/adapters/google_health.py is unverified against")
        print("live payloads. Anything listed above needs its key added there.")
    else:
        print("adapter report: every data point converted")
    if args.report:
        print(json.dumps(report.as_dict(), indent=2))
        counts: dict[str, int] = {}
        for obs in observations:
            counts[obs.signal.value] = counts.get(obs.signal.value, 0) + 1
        print("observations by signal:", json.dumps(dict(sorted(counts.items())), indent=2))
        for signal in (Signal.RESTING_HEART_RATE, Signal.SLEEP_DURATION, Signal.ACTIVE_MINUTES):
            days = daily_values(observations, signal)
            print(f"  {signal.value}: {len(days)} day(s) of history available")
        return 0

    if not observations:
        print("nothing to evaluate")
        return 1

    tz = None
    if args.timezone:
        try:
            tz = ZoneInfo(args.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            print(f"{args.timezone!r} is not a recognised IANA timezone")
            return 1
    else:
        print("\nno --timezone given: day-window claims will abstain with TIMEZONE_UNKNOWN,")
        print("which is the correct behaviour rather than a bug.")

    latest = max(o.measured_at for o in observations)
    if args.day:
        anchor = datetime.fromisoformat(args.day).replace(tzinfo=tz or U)
    else:
        anchor = (latest - timedelta(days=1)).astimezone(tz or U)
    window = local_day_window(anchor, tz)
    evaluated_at = max(latest, window.end + timedelta(hours=1))

    print(f"\ntarget window {window.start.isoformat()} -> {window.end.isoformat()}")
    print("This output contains your own measurements. Do not commit or publish it.\n")

    payload = []
    for claim_type in (args.claim or list(DEMO_CLAIMS)):
        target = claim_window(claim_type, observations, window)
        if target is None:
            print(f"=== {claim_type} ===")
            print("  skipped: no sleep period was recorded for this day, so there is no "
                  "window to ask about\n")
            continue
        request = build_request(
            claim_type, observations, target, args.subject_id, args.timezone, evaluated_at
        )
        if args.corrupt:
            case = SyntheticCase(
                request=request,
                truth=GroundTruth.SUPPORTABLE,
                truth_reason="personal export, not a labelled case",
                generator="google_health",
                seed=0,
            )
            request = apply_corruption(case, args.corrupt, args.severity, seed=0).request
        response = evaluate(request)
        if args.json:
            payload.append(response.model_dump(mode="json"))
            continue
        print(f"=== {claim_type} ===")
        print(f"  decision   {response.decision.value}")
        print(f"  confidence {response.confidence:.2f} in the decision; "
              f"claim support {response.claim_support_probability:.2f} (not calibrated)")
        ev = response.evidence
        print(f"  evidence   coverage {ev.coverage_score:.2f}  freshness {ev.freshness_score:.2f}  "
              f"quality {ev.signal_quality_score:.2f}  consistency {ev.consistency_score:.2f}  "
              f"baseline {ev.baseline_maturity_score:.2f}")
        print(f"  reasons    {', '.join(c.value for c in response.reason_codes) or 'none'}")
        print(f"  {response.explanation}\n")
    if args.json:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
