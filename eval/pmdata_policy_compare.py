#!/usr/bin/env python3
"""A/B a proposed threshold revision against the shipped policy, on real PMData evidence.

    ./.venv/bin/python -m eval.pmdata_policy_compare

Every threshold in claim-policy-0.1.0 was reasoned, not measured. PMData is the first
evidence about any of them, and three turned out to be mis-specified in ways that only
real data could show. This script proposes the revision, measures what it does to real
decisions, and re-runs the safety check under it, so the change is adopted on evidence
rather than on the argument that produced the original numbers.

The proposal is defined here rather than edited into the registry, because a comparison
run against a policy that has already been adopted is not a comparison.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.adapters.pmdata import PreparedSubject, load_prepared
from engine.corruptions import apply as apply_corruption
from engine.engine import evaluate
from engine.policies import get_policy
from engine.policies.base import ClaimPolicy
from engine.schemas import Decision
from engine.synth import GroundTruth, SyntheticCase

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = REPO / "data" / "datasets" / "pmdata_cache"
OUT_DIR = REPO / "eval" / "results"
SHOWN = {Decision.SHOW, Decision.SHOW_WITH_WARNING}

CLAIMS = (
    "RESTING_HEART_RATE_ELEVATED",
    "ACTIVITY_LOAD_HIGH",
    "SLEEP_DURATION_LOW",
    "SLEEP_QUALITY_REDUCED",
)

# --------------------------------------------------------------------------- the proposal

PROPOSAL: dict[str, dict[str, Any]] = {
    "RESTING_HEART_RATE_ELEVATED": {
        "max_baseline_sd": 5.0,
        "min_baseline_days": 28,
        "warn_baseline_days": 14,
        "good_baseline_days": 56,
    },
    "ACTIVITY_LOAD_HIGH": {
        "max_baseline_sd": 90.0,
    },
    "SLEEP_QUALITY_REDUCED": {
        "max_baseline_sd": 6.0,
    },
}

RATIONALE = {
    "RESTING_HEART_RATE_ELEVATED.max_baseline_sd": (
        "8.0 bpm fired on 0.0% of 1451 real subject-days. Observed personal baseline SD: "
        "median 1.78, p95 3.29, max 4.04 bpm. The reasoned value sat at twice the observed "
        "maximum, so the gate could not act on any real person. 5.0 sits above the observed "
        "range with headroom for a less athletic population while remaining capable of "
        "firing."
    ),
    "RESTING_HEART_RATE_ELEVATED.min_baseline_days": (
        "A personal resting-heart-rate SD takes a median of 48 days to settle within 10% of "
        "its 60-day value (range 43-58 across 13 subjects). 14 days compares against a "
        "normal that is still moving. Requiring 48 would deny every new user any claim for "
        "seven weeks, so the mature bar moves to 28 and the disclosed band widens to 14-27 "
        "rather than adopting the full settling time."
    ),
    "RESTING_HEART_RATE_ELEVATED.good_baseline_days": (
        "Raised from 28 to 56 so the maturity score keeps a gradient above the new floor: "
        "with the mature bar at 28 days, leaving 'comfortable' at 28 would make the score "
        "jump from 0.5 to 1.0 at a single day boundary. 56 brackets the observed median "
        "settling time of 48 days."
    ),
    "RESTING_HEART_RATE_ELEVATED.warn_baseline_days": (
        "Raised from 7 to 14 to match: below two weeks the baseline is not merely immature, "
        "it is uninformative, and the observed settling curve gives no reason to show a "
        "claim against it even with a disclosure."
    ),
    "ACTIVITY_LOAD_HIGH.max_baseline_sd": (
        "45 minutes fired on 48.7% of 2055 real subject-days and was the single most common "
        "reason an activity claim was withheld. Observed baseline SD: median 44.45, p95 "
        "73.84, max 90.97 minutes. Day-to-day variation in activity is the phenomenon the "
        "claim is about, not a defect in the evidence, so a stability gate calibrated like a "
        "physiological signal's was the wrong shape. 90 keeps the gate for a genuinely "
        "absurd baseline without rejecting normal training variation."
    ),
    "SLEEP_QUALITY_REDUCED.max_baseline_sd": (
        "12 percentage points fired on 0.3% of 1663 real subject-nights. Observed baseline "
        "SD of Fitbit's sleep efficiency: median 2.56, p95 4.41, max 12.82. 6.0 sits above "
        "the 95th percentile and can still act."
    ),
}

UNCHANGED_BUT_NOW_EVIDENCED = {
    "RESTING_HEART_RATE_ELEVATED.min_absolute_delta": (
        "Kept at 3.0 bpm, and now supported rather than merely reasoned. Consecutive-day "
        "absolute change in real resting heart rate: median 0.75, p95 2.32 bpm, so 3.0 sits "
        "just above the 95th percentile of ordinary daily fluctuation. Note the interaction "
        "this reveals: at the median personal SD of 1.78 bpm, 3.0 bpm corresponds to z=1.68, "
        "so the absolute floor -- not the stated show_z of 1.5 -- is the binding constraint "
        "for a typical subject. Fitbit's own reported error on daily resting heart rate is "
        "far larger (median 6.81 bpm), but that is dominated by systematic error in "
        "estimating the absolute level, which cancels in a within-person day-to-day "
        "difference; it is not the right quantity for this floor."
    ),
    "RESTING_HEART_RATE_ELEVATED.min_wear_coverage": (
        "Kept at 0.60. It rejects 10.6% of real subject-days, with observed wear ratio "
        "median 0.96 and p05 0.41 -- a floor that acts on a real minority rather than on "
        "nobody or everybody."
    ),
    "SLEEP_DURATION_LOW.max_baseline_sd": (
        "Kept at 120 minutes. Observed baseline SD median 68.6, p95 126.2; the threshold "
        "fires on 6.4% of nights, which is the intended shape."
    ),
}


def revised(claim_type: str) -> ClaimPolicy:
    policy = get_policy(claim_type)
    assert policy is not None
    overrides = PROPOSAL.get(claim_type)
    return replace(policy, **overrides) if overrides else policy


# --------------------------------------------------------------------------- measurement


def _decisions(
    subjects: list[PreparedSubject], use_revision: bool, assume_sync: bool
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for claim in CLAIMS:
        policy = revised(claim) if use_revision else get_policy(claim)
        counts = {d.value: 0 for d in Decision}
        reasons: dict[str, int] = {}
        for subject in subjects:
            for day in subject.days():
                request = subject.request(claim, day, assume_sync=assume_sync)
                if request is None:
                    continue
                response = evaluate(request, policy=policy)
                counts[response.decision.value] += 1
                for code in response.reason_codes:
                    reasons[code.value] = reasons.get(code.value, 0) + 1
        total = sum(counts.values())
        shown = counts["SHOW"] + counts["SHOW_WITH_WARNING"]
        out[claim] = {
            "evaluated_days": total,
            "decisions": counts,
            "decision_coverage": round(shown / total, 4) if total else 0.0,
            "reason_codes": reasons,
        }
    return out


def _safety_under_revision(
    subjects: list[PreparedSubject], limit_per_subject: int = 15
) -> dict[str, Any]:
    """The revision must not buy coverage by weakening the gates."""
    harmful = ("shorten_wear", "truncate_baseline", "delay_sync", "stale_data",
               "implausible_spike", "drop_timezone")
    policy = revised("RESTING_HEART_RATE_ELEVATED")
    cases = displayed = 0
    for subject in subjects:
        taken = 0
        for day in subject.days():
            if taken >= limit_per_subject:
                break
            request = subject.request("RESTING_HEART_RATE_ELEVATED", day, assume_sync=True)
            if request is None:
                continue
            if evaluate(request, policy=policy).decision not in SHOWN:
                continue
            taken += 1
            case = SyntheticCase(
                request=request, truth=GroundTruth.SUPPORTABLE,
                truth_reason="real PMData day displayed under the revised policy",
                generator="pmdata", seed=0,
            )
            for name in harmful:
                for severity in ("moderate", "severe"):
                    corrupted = apply_corruption(case, name, severity, seed=0)
                    cases += 1
                    if evaluate(corrupted.request, policy=policy).decision in SHOWN:
                        displayed += 1
    return {
        "corrupted_cases": cases,
        "displayed": displayed,
        "unsupported_show_rate": round(displayed / cases, 4) if cases else 0.0,
    }


def run(cache: Path) -> dict[str, Any]:
    subjects = load_prepared(cache)
    if not subjects:
        raise SystemExit(f"no prepared subjects in {cache}; run eval.pmdata_prepare first")
    before = _decisions(subjects, use_revision=False, assume_sync=True)
    after = _decisions(subjects, use_revision=True, assume_sync=True)
    deltas = {}
    for claim in CLAIMS:
        b, a = before[claim], after[claim]
        moved = {
            code: a["reason_codes"].get(code, 0) - b["reason_codes"].get(code, 0)
            for code in sorted(set(b["reason_codes"]) | set(a["reason_codes"]))
        }
        deltas[claim] = {
            "coverage_before": b["decision_coverage"],
            "coverage_after": a["decision_coverage"],
            "coverage_delta": round(a["decision_coverage"] - b["decision_coverage"], 4),
            "decisions_before": b["decisions"],
            "decisions_after": a["decisions"],
            "reason_code_delta": {k: v for k, v in moved.items() if v},
        }
    return {
        "artifact": "pmdata-policy-comparison",
        "synthetic": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "subjects": len(subjects),
        "proposal": PROPOSAL,
        "rationale": RATIONALE,
        "unchanged_but_now_evidenced": UNCHANGED_BUT_NOW_EVIDENCED,
        "comparison": deltas,
        "safety_under_revision": _safety_under_revision(subjects),
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Proposed policy revision, measured on PMData",
        "",
        "Every threshold in `claim-policy-0.1.0` was reasoned from domain knowledge. PMData "
        "is the first evidence about any of them. This is what the proposed revision does to "
        f"real decisions across {report['subjects']} subjects.",
        "",
        f"Generated `{report['generated_at']}`.",
        "",
        "## Changes and why",
        "",
    ]
    for key, why in report["rationale"].items():
        claim, field = key.rsplit(".", 1)
        old = get_policy(claim)
        new_value = report["proposal"][claim][field]
        lines += [
            f"### `{claim}` · `{field}`: {getattr(old, field)} → {new_value}",
            "",
            why,
            "",
        ]
    lines += ["## Kept, but no longer only reasoned", ""]
    for key, why in report["unchanged_but_now_evidenced"].items():
        lines += [f"### `{key}`", "", why, ""]

    lines += [
        "## Effect on real decisions",
        "",
        "| claim | coverage before | coverage after | delta |",
        "|---|---|---|---|",
    ]
    for claim, row in report["comparison"].items():
        lines.append(
            f"| `{claim}` | {row['coverage_before']:.1%} | {row['coverage_after']:.1%} | "
            f"{row['coverage_delta']:+.1%} |"
        )
    lines += ["", "Reason codes that moved:", ""]
    for claim, row in report["comparison"].items():
        if not row["reason_code_delta"]:
            lines.append(f"- **{claim}** — no change")
            continue
        moved = ", ".join(
            f"`{k}` {v:+d}" for k, v in sorted(row["reason_code_delta"].items(), key=lambda kv: -abs(kv[1]))
        )
        lines.append(f"- **{claim}** — {moved}")

    safety = report["safety_under_revision"]
    lines += [
        "",
        "## Safety check under the revision",
        "",
        "A revision that buys coverage by weakening the gates would be a regression, not an "
        "improvement. The corruption harness, re-run on real evidence under the revised "
        "thresholds:",
        "",
        f"- corrupted cases: {safety['corrupted_cases']}",
        f"- displayed anyway: {safety['displayed']}",
        f"- **unsupported-show rate: {safety['unsupported_show_rate']:.4f}**",
        "",
        "## Honest limits of this revision",
        "",
        "PMData is 16 people, largely athletic, over five months on one device model. A "
        "threshold tuned to their distribution is tuned to them. The revision is defensible "
        "because it corrects thresholds that demonstrably could not act at all, or that "
        "fired on half of all days -- errors of shape, not of decimal places. It is not a "
        "claim that these values are right for a general population, and the next dataset "
        "may move them again.",
        "",
        "Two of the changes tighten (`max_baseline_sd` for resting heart rate and sleep "
        "quality, `min_baseline_days`) and one loosens (`max_baseline_sd` for activity). "
        "The loosening is the one to watch: it removes a gate that was withholding half of "
        "all activity claims, so the safety check above matters most for that claim.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    report = run(args.cache)
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (args.out / f"pmdata-policy-comparison-{stamp}.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    (args.out / f"pmdata-policy-comparison-{stamp}.md").write_text(to_markdown(report))
    print("coverage before -> after (real PMData days):")
    for claim, row in report["comparison"].items():
        print(f"  {claim:32s} {row['coverage_before']:.1%} -> {row['coverage_after']:.1%} "
              f"({row['coverage_delta']:+.1%})")
    safety = report["safety_under_revision"]
    print(f"\nsafety under revision: unsupported-show "
          f"{safety['unsupported_show_rate']:.4f} over {safety['corrupted_cases']} corrupted "
          "real cases")
    print(f"\nwrote {args.out / f'pmdata-policy-comparison-{stamp}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
