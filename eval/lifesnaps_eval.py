#!/usr/bin/env python3
"""External validation of claim-policy-0.2.0 on a second, independent cohort.

    ./.venv/bin/python -m eval.lifesnaps_eval

LifeSnaps: 71 participants, ~3 months each, Fitbit Sense (Yfantidou et al., Scientific Data
9, 663, 2022; data doi:10.5281/zenodo.7229547, CC BY 4.0, no changes made).

`claim-policy-0.2.0`'s thresholds were set from PMData -- 16 largely athletic Norwegian
adults on a Fitbit Versa 2. `docs/limitations.md` says a threshold tuned to one cohort is a
hypothesis about the next one. This tests that hypothesis. **No threshold is tuned here.**

It also carries age, gender and BMI, which makes the first subgroup analysis possible. The
failure mode that matters is not unfairness in what the engine says -- it compares each
person only to themselves -- but unfairness in *who gets an answer at all*: a quality gate
that fires more often for one group would ration insights unevenly while looking perfectly
safe in the aggregate.

Two caveats on comparing numbers across the datasets, both from the adapter docstring:
wear time is a different measurement here (tracked activity minutes, not minutes with a
heart-rate sample), and the sleep window is reconstructed because the daily table has no
clock times.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import random
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from engine import MODEL_VERSION, POLICY_VERSION
from engine.adapters.lifesnaps import CITATION, LifeSnapsSubject, load
from engine.corruptions import apply as apply_corruption
from engine.engine import evaluate
from engine.policies import get_policy
from engine.schemas import Decision
from engine.scoring import mean, percentile, sample_sd
from engine.synth import GroundTruth, SyntheticCase

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO / "data" / "datasets" / "lifesnaps"
OUT_DIR = REPO / "eval" / "results"

CLAIMS = ("RESTING_HEART_RATE_ELEVATED", "ACTIVITY_LOAD_HIGH",
          "SLEEP_DURATION_LOW", "SLEEP_QUALITY_REDUCED")
SHOWN = {Decision.SHOW, Decision.SHOW_WITH_WARNING}
HARMFUL = ("shorten_wear", "truncate_baseline", "delay_sync", "stale_data",
           "implausible_spike", "drop_timezone")
BENIGN = ("duplicate_samples", "shuffle_order")


def _dist(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values), "mean": round(mean(values), 3), "sd": round(sample_sd(values), 3),
        "p05": round(percentile(values, 5), 3), "median": round(percentile(values, 50), 3),
        "p95": round(percentile(values, 95), 3), "max": round(max(values), 3),
    }


def _share(values: list[float], predicate) -> float:
    return 0.0 if not values else sum(1 for v in values if predicate(v)) / len(values)


def threshold_report(subjects: list[LifeSnapsSubject]) -> dict[str, Any]:
    policy = get_policy("RESTING_HEART_RATE_ELEVATED")
    assert policy is not None
    wear, sds, shifts, zs, deltas, settle = [], [], [], [], [], []

    for subject in subjects:
        for day in subject.days():
            ratio = subject.wear_ratio(day)
            if ratio is not None:
                wear.append(ratio)
        rhr_days = [d for d in subject.days() if "resting_heart_rate" in subject.daily[d]]
        valid = [d for d in rhr_days if (subject.wear_ratio(d) or 0) >= 0.60]
        for index, day in enumerate(rhr_days):
            if index < policy.min_baseline_days:
                continue
            window = [d for d in rhr_days[max(0, index - 60):index]
                      if (subject.wear_ratio(d) or 0) >= 0.60]
            values = [subject.daily[d]["resting_heart_rate"] for d in window]
            if len(values) < policy.min_baseline_days:
                continue
            sd = sample_sd(values)
            sds.append(sd)
            if len(values) >= 6:
                half = len(values) // 2
                shifts.append(abs(mean(values[half:]) - mean(values[:half])))
            delta = subject.daily[day]["resting_heart_rate"] - mean(values)
            deltas.append(abs(delta))
            zs.append(delta / max(sd, 1.5))
        # Settling time, same definition as the PMData report.
        series = [subject.daily[d]["resting_heart_rate"] for d in valid]
        if len(series) >= 40:
            reference = sample_sd(series[:60])
            if reference > 0:
                for n in range(5, min(len(series), 60) + 1):
                    running = [sample_sd(series[:k])
                               for k in range(n, min(len(series), 60) + 1)]
                    if all(abs(r - reference) <= 0.10 * reference for r in running):
                        settle.append(float(n))
                        break

    return {
        "subject_days": len(wear),
        "checks": [
            {"threshold": "min_wear_coverage", "contract_value": policy.min_wear_coverage,
             "observed": _dist(wear),
             "share_affected": round(_share(wear, lambda v: v < policy.min_wear_coverage), 4)},
            {"threshold": "max_baseline_sd (resting heart rate, bpm)",
             "contract_value": policy.max_baseline_sd, "observed": _dist(sds),
             "share_affected": round(
                 _share(sds, lambda v: v > (policy.max_baseline_sd or 0)), 4)},
            {"threshold": "max_baseline_shift (resting heart rate, bpm)",
             "contract_value": policy.max_baseline_shift, "observed": _dist(shifts),
             "share_affected": round(
                 _share(shifts, lambda v: v > (policy.max_baseline_shift or 0)), 4)},
            {"threshold": "show_z (resting heart rate)", "contract_value": policy.show_z,
             "observed": _dist(zs),
             "share_affected": round(_share(zs, lambda v: v >= policy.show_z), 4)},
            {"threshold": "min_absolute_delta (resting heart rate, bpm)",
             "contract_value": policy.min_absolute_delta, "observed": _dist(deltas),
             "share_affected": round(
                 _share(deltas, lambda v: v < policy.min_absolute_delta), 4)},
        ],
        "baseline_settling_days": _dist(settle),
        "contract_min_baseline_days": policy.min_baseline_days,
    }


def decision_report(subjects: list[LifeSnapsSubject], assume_sync: bool = True) -> dict[str, Any]:
    by_claim: dict[str, dict[str, Any]] = {}
    per_subject: dict[str, dict[str, Any]] = {}
    for claim in CLAIMS:
        counts = {d.value: 0 for d in Decision}
        reasons: dict[str, int] = {}
        for subject in subjects:
            shown = total = 0
            for day in subject.days():
                request = subject.request(claim, day, assume_sync=assume_sync)
                if request is None:
                    continue
                response = evaluate(request)
                counts[response.decision.value] += 1
                total += 1
                shown += response.decision in SHOWN
                for code in response.reason_codes:
                    reasons[code.value] = reasons.get(code.value, 0) + 1
            if total:
                entry = per_subject.setdefault(subject.subject_id, {
                    "demographics": subject.demographics, "claims": {}})
                entry["claims"][claim] = {"days": total, "coverage": round(shown / total, 4)}
        total = sum(counts.values())
        shown_total = counts["SHOW"] + counts["SHOW_WITH_WARNING"]
        by_claim[claim] = {
            "evaluated_days": total, "decisions": counts,
            "decision_coverage": round(shown_total / total, 4) if total else 0.0,
            "top_reason_codes": dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:6]),
        }
    return {"by_claim": by_claim, "per_subject": per_subject}


def _bmi_band(raw: str) -> str:
    """The source encodes BMI inconsistently -- bare numbers and bands in the same column.

    Everything is collapsed onto the conventional 25 cutoff so the groups are comparable
    and large enough to say anything about.
    """
    raw = (raw or "").strip()
    if not raw or raw == "unknown":
        return "unknown"
    if raw.startswith("<"):
        return "under 25"
    if raw.startswith(">="):
        try:
            return "under 25" if float(raw[2:]) < 25 else "25 or over"
        except ValueError:
            return "25 or over"
    try:
        return "under 25" if float(raw) < 25 else "25 or over"
    except ValueError:
        return "unknown"


def _bootstrap_difference(a: list[float], b: list[float], resamples: int = 5000,
                          seed: int = 20260924) -> dict[str, Any]:
    """Percentile interval for mean(a) - mean(b), resampling subjects."""
    if len(a) < 2 or len(b) < 2:
        return {"point": None, "ci95_low": None, "ci95_high": None,
                "distinguishable_from_zero": None}
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        ra = [a[rng.randrange(len(a))] for _ in a]
        rb = [b[rng.randrange(len(b))] for _ in b]
        draws.append(mean(ra) - mean(rb))
    draws.sort()
    low = draws[int(0.025 * (len(draws) - 1))]
    high = draws[int(0.975 * (len(draws) - 1))]
    return {
        "point": round(mean(a) - mean(b), 4),
        "ci95_low": round(low, 4), "ci95_high": round(high, 4),
        "distinguishable_from_zero": not (low <= 0.0 <= high),
    }


def subgroup_report(per_subject: dict[str, Any]) -> dict[str, Any]:
    """Who gets an answer, with intervals rather than point estimates.

    A median of per-subject coverage is badly unstable at these group sizes: the medians
    here differ by more than 2x while the bootstrap interval on the mean difference
    comfortably contains zero. Reporting the medians alone would have manufactured a
    disparity out of sampling noise, so every comparison carries a subject-level bootstrap
    interval and the report states plainly when a difference is not distinguishable.
    """
    out: dict[str, Any] = {}
    for key in ("gender", "age_band", "bmi_band"):
        groups: dict[str, list[float]] = {}
        for entry in per_subject.values():
            raw = entry["demographics"].get(key, "unknown")
            label = _bmi_band(raw) if key == "bmi_band" else (raw or "unknown")
            row = entry["claims"].get("RESTING_HEART_RATE_ELEVATED")
            if row:
                groups.setdefault(label, []).append(row["coverage"])
        summary = {
            label: {
                "subjects": len(values),
                "mean_decision_coverage": round(mean(values), 4),
                "median_decision_coverage": round(percentile(values, 50), 4),
                "min": round(min(values), 4), "max": round(max(values), 4),
            }
            for label, values in sorted(groups.items())
        }
        named = [(k, v) for k, v in sorted(groups.items()) if k != "unknown" and len(v) >= 2]
        comparison = None
        if len(named) == 2:
            (label_a, values_a), (label_b, values_b) = named
            comparison = {
                "between": f"{label_a} minus {label_b}",
                **_bootstrap_difference(values_a, values_b),
            }
        out[key] = {"groups": summary, "difference": comparison}
    return out


def corruption_report(subjects: list[LifeSnapsSubject], limit: int = 8) -> dict[str, Any]:
    cases = displayed = benign_changed = benign_total = base = 0
    for subject in subjects:
        taken = 0
        for day in subject.days():
            if taken >= limit:
                break
            request = subject.request("RESTING_HEART_RATE_ELEVATED", day, assume_sync=True)
            if request is None:
                continue
            first = evaluate(request)
            if first.decision not in SHOWN:
                continue
            taken += 1
            base += 1
            case = SyntheticCase(request=request, truth=GroundTruth.SUPPORTABLE,
                                 truth_reason="real LifeSnaps subject-day the engine displays",
                                 generator="lifesnaps", seed=0)
            for name in HARMFUL:
                for severity in ("moderate", "severe"):
                    cases += 1
                    if evaluate(apply_corruption(case, name, severity, seed=0).request
                                ).decision in SHOWN:
                        displayed += 1
            for name in BENIGN:
                benign_total += 1
                if evaluate(apply_corruption(case, name, "severe", seed=0).request
                            ).decision is not first.decision:
                    benign_changed += 1
    return {
        "real_days_used_as_base": base, "corrupted_cases": cases, "displayed": displayed,
        "unsupported_show_rate": round(displayed / cases, 4) if cases else 0.0,
        "benign_cases": benign_total, "benign_decision_changes": benign_changed,
    }


def run(root: Path) -> dict[str, Any]:
    subjects = load(root)
    if not subjects:
        raise SystemExit(f"no LifeSnaps subjects under {root}")
    decisions = decision_report(subjects)
    return {
        "artifact": "lifesnaps-external-validation",
        "synthetic": False,
        "dataset": "LifeSnaps",
        "citation": CITATION,
        "role": "external validation only; no threshold is tuned on this dataset",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION, "policy_version": POLICY_VERSION,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "subjects": len(subjects),
        "thresholds": threshold_report(subjects),
        "decisions": decisions["by_claim"],
        "subgroups": subgroup_report(decisions["per_subject"]),
        "corruptions_on_real_evidence": corruption_report(subjects),
    }


def to_markdown(report: dict[str, Any], pmdata: dict[str, Any] | None) -> str:
    t = report["thresholds"]
    lines = [
        "# LifeSnaps external validation of `claim-policy-0.2.0`",
        "",
        f"**{report['role']}.**",
        "",
        f"- dataset: {report['citation']}",
        f"- {report['subjects']} participants, {t['subject_days']} subject-days",
        f"- engine `{report['model_version']}`, policy `{report['policy_version']}`",
        f"- generated `{report['generated_at']}`",
        "",
        "The thresholds under test were set from PMData: 16 largely athletic Norwegian "
        "adults on a Fitbit Versa 2. LifeSnaps is 71 geographically distributed "
        "participants on a Fitbit Sense. If the thresholds describe both, the revision "
        "generalises.",
        "",
        "## Do the PMData-derived thresholds hold on a different cohort?",
        "",
        "| threshold | contract | LifeSnaps median | LifeSnaps p05–p95 | share affected |",
        "|---|---|---|---|---|",
    ]
    for check in t["checks"]:
        o = check["observed"]
        if not o.get("n"):
            continue
        lines.append(
            f"| `{check['threshold']}` | {check['contract_value']} | {o['median']} | "
            f"{o['p05']} – {o['p95']} | {check['share_affected']:.1%} |"
        )
    if pmdata:
        lines += ["", "Side by side with the cohort the thresholds came from:", "",
                  "| threshold | PMData median | LifeSnaps median | PMData share | LifeSnaps share |",
                  "|---|---|---|---|---|"]
        pm = {c["threshold"]: c for c in pmdata["thresholds"]["checks"]}
        for check in t["checks"]:
            other = pm.get(check["threshold"])
            if not other or not other["observed"].get("n"):
                continue
            pshare = other.get("share_below_threshold", other.get("share_above_threshold", 0))
            lines.append(
                f"| `{check['threshold']}` | {other['observed']['median']} | "
                f"{check['observed']['median']} | {pshare:.1%} | "
                f"{check['share_affected']:.1%} |"
            )
    settle = t["baseline_settling_days"]
    if settle.get("n"):
        lines += [
            "",
            f"**Baseline settling time:** median {settle['median']:.0f} days "
            f"(p05 {settle['p05']:.0f}, p95 {settle['p95']:.0f}) across {settle['n']} "
            f"participants with enough history, against the contract's "
            f"{t['contract_min_baseline_days']}-day mature bar.",
        ]
    lines += ["", "## What the engine does on this cohort", "",
              "| claim | days | coverage | most common reasons |", "|---|---|---|---|"]
    for claim, row in report["decisions"].items():
        codes = ", ".join(f"`{k}`" for k in list(row["top_reason_codes"])[:3])
        lines.append(f"| `{claim}` | {row['evaluated_days']} | "
                     f"{row['decision_coverage']:.1%} | {codes} |")

    lines += ["", "## Subgroup analysis: who gets an answer", "",
              "The engine compares each person only to their own baseline, so it cannot be "
              "biased by a population reference. The risk is different: a quality gate that "
              "fires more often for one group rations insights unevenly while looking "
              "perfectly safe in the aggregate. Decision coverage for "
              "`RESTING_HEART_RATE_ELEVATED`, per participant, grouped:", ""]
    for key, block in report["subgroups"].items():
        lines += [f"**{key}**", "",
                  "| group | participants | mean coverage | median | min | max |",
                  "|---|---|---|---|---|---|"]
        for label, row in block["groups"].items():
            lines.append(f"| {label} | {row['subjects']} | "
                         f"{row['mean_decision_coverage']:.1%} | "
                         f"{row['median_decision_coverage']:.1%} | {row['min']:.1%} | "
                         f"{row['max']:.1%} |")
        diff = block.get("difference")
        if diff and diff.get("point") is not None:
            verdict = ("**distinguishable from zero**" if diff["distinguishable_from_zero"]
                       else "not distinguishable from zero")
            lines += ["",
                      f"Difference ({diff['between']}): {diff['point']:+.1%}, "
                      f"95% CI [{diff['ci95_low']:+.1%}, {diff['ci95_high']:+.1%}] "
                      f"— {verdict}. Bootstrap over subjects, 5000 resamples."]
        lines.append("")

    corr = report["corruptions_on_real_evidence"]
    lines += [
        "## Do the gates still hold on this cohort?",
        "",
        f"- {corr['real_days_used_as_base']} real subject-days the engine displays, "
        f"corrupted {corr['corrupted_cases']} ways",
        f"- **unsupported-show rate: {corr['unsupported_show_rate']:.4f}**",
        f"- benign corruptions that changed a decision: {corr['benign_decision_changes']} "
        f"of {corr['benign_cases']} (should be 0)",
        "",
        "## Caveats on comparing the two datasets",
        "",
        "- **Wear time is a different measurement.** PMData's wear minutes are minutes with "
        "an actual heart-rate sample; LifeSnaps has no such field, so wear is the sum of "
        "activity-level minute buckets. The coverage rows above are not measuring quite the "
        "same thing, and the LifeSnaps figure is the looser of the two.",
        "- **The sleep window is reconstructed** from time-in-bed anchored to a nominal "
        "07:00 wake, because the daily table has no clock times. Sleep results here are "
        "weaker evidence than the heart-rate results.",
        "- **Timezone is unknown** and the cohort is geographically distributed; UTC is "
        "assumed, so day boundaries are wrong for most participants by some offset.",
        "- Age and BMI are coarse bands in the source data, not values.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    report = run(args.root)
    pm_path = sorted(args.out.glob("pmdata-2*.json"))
    pmdata = json.loads(pm_path[-1].read_text()) if pm_path else None

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (args.out / f"lifesnaps-{stamp}.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / f"lifesnaps-{stamp}.md").write_text(to_markdown(report, pmdata))

    print(f"LifeSnaps: {report['subjects']} participants, "
          f"{report['thresholds']['subject_days']} subject-days\n")
    print("threshold checks (share of real subject-days affected):")
    for check in report["thresholds"]["checks"]:
        o = check["observed"]
        if not o.get("n"):
            continue
        print(f"  {check['threshold']:48s} contract={check['contract_value']:<7} "
              f"median={o['median']:<8} share={check['share_affected']:.1%}")
    settle = report["thresholds"]["baseline_settling_days"]
    if settle.get("n"):
        print(f"\nbaseline settles after a median of {settle['median']:.0f} days "
              f"(contract bar: {report['thresholds']['contract_min_baseline_days']})")
    print("\ndecision coverage:")
    for claim, row in report["decisions"].items():
        print(f"  {claim:32s} {row['decision_coverage']:.1%} over {row['evaluated_days']} days")
    print("\nsubgroup decision coverage (resting heart rate), mean per subject:")
    for key, block in report["subgroups"].items():
        parts = ", ".join(f"{label} {row['mean_decision_coverage']:.1%} "
                          f"(n={row['subjects']})" for label, row in block["groups"].items())
        print(f"  {key:10s} {parts}")
        diff = block.get("difference")
        if diff and diff.get("point") is not None:
            verdict = "DISTINGUISHABLE" if diff["distinguishable_from_zero"] else "not distinguishable"
            print(f"  {'':10s}   difference {diff['point']:+.1%} "
                  f"CI [{diff['ci95_low']:+.1%}, {diff['ci95_high']:+.1%}] -> {verdict}")
    corr = report["corruptions_on_real_evidence"]
    print(f"\nunsupported-show on corrupted real evidence: {corr['unsupported_show_rate']:.4f} "
          f"({corr['corrupted_cases']} cases)")
    print(f"benign corruptions that changed a decision: {corr['benign_decision_changes']}")
    print(f"\nwrote {args.out / f'lifesnaps-{stamp}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
