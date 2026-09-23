#!/usr/bin/env python3
"""Evaluate the engine against PMData: real Fitbit measurements, 16 subjects, ~5 months.

    ./.venv/bin/python -m eval.pmdata_prepare      # once, ~15 minutes
    ./.venv/bin/python -m eval.pmdata_eval

Dataset: Thambawita et al., "PMData: A Sports Logging Dataset", MMSys '20,
doi:10.1145/3339825.3394926, CC BY 4.0. No changes were made to the data; it is read,
reduced to per-day summaries, and evaluated. Nothing derived from it is committed.

Four things are measured, in descending order of how much they can change a decision
about the product:

1. **Do the claim contract's thresholds describe real people?** Every number in
   docs/claim-contracts.md was reasoned, not fitted. This compares each to the observed
   distribution across 16 subjects and roughly 2,000 subject-days. It is the first
   evidence of any kind about whether those numbers are sane.
2. **What does the engine actually do on real days?** The decision distribution and the
   reason codes that drive it. Nobody knew this number before.
3. **Do the gates hold up against real noise?** The corruption harness applied to real
   evidence, so the labels are known and the underlying data is not Gaussian.
4. **Exploratory only:** whether displayed claims line up with the subjects' own
   self-reported wellness. Self-report is not a reference standard and this proves nothing;
   it is reported because it is the only external signal the dataset carries.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from engine import MODEL_VERSION, POLICY_VERSION
from engine.adapters.pmdata import CITATION, PreparedSubject, load_prepared
from engine.corruptions import SEVERITIES, apply as apply_corruption
from engine.engine import evaluate
from engine.policies import get_policy
from engine.schemas import Decision
from engine.scoring import mean, percentile, sample_sd
from engine.signals import Signal
from engine.synth import GroundTruth, SyntheticCase

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = REPO / "data" / "datasets" / "pmdata_cache"
OUT_DIR = REPO / "eval" / "results"

CLAIMS = (
    "RESTING_HEART_RATE_ELEVATED",
    "ACTIVITY_LOAD_HIGH",
    "SLEEP_DURATION_LOW",
    "SLEEP_QUALITY_REDUCED",
)
SHOWN = {Decision.SHOW, Decision.SHOW_WITH_WARNING}

# Corruptions applied to real evidence. Chosen because each one's breach is unambiguous on
# a day the engine would otherwise display, so the label needs no judgement call.
REAL_DATA_CORRUPTIONS = (
    "shorten_wear",
    "truncate_baseline",
    "delay_sync",
    "stale_data",
    "implausible_spike",
    "drop_timezone",
    "duplicate_samples",   # benign: must NOT change the decision
    "shuffle_order",       # benign
)
BENIGN = {"duplicate_samples", "shuffle_order"}


def _dist(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(mean(values), 3),
        "sd": round(sample_sd(values), 3),
        "min": round(min(values), 3),
        "p05": round(percentile(values, 5), 3),
        "p25": round(percentile(values, 25), 3),
        "median": round(percentile(values, 50), 3),
        "p75": round(percentile(values, 75), 3),
        "p95": round(percentile(values, 95), 3),
        "max": round(max(values), 3),
    }


def _share(values: list[float], predicate) -> float:
    return 0.0 if not values else sum(1 for v in values if predicate(v)) / len(values)


# --------------------------------------------------------------------- 1. thresholds


def threshold_report(subjects: list[PreparedSubject]) -> dict[str, Any]:
    """Compare every reasoned threshold in the contract to what real data actually does."""
    rhr_policy = get_policy("RESTING_HEART_RATE_ELEVATED")
    sleep_policy = get_policy("SLEEP_DURATION_LOW")
    assert rhr_policy is not None and sleep_policy is not None

    wear_ratios: list[float] = []
    overnight_ratios: list[float] = []
    max_gaps: list[float] = []
    baseline_sds: list[float] = []
    half_shifts: list[float] = []
    rhr_z: list[float] = []
    rhr_abs_delta: list[float] = []
    sleep_sds: list[float] = []
    per_subject: dict[str, Any] = {}

    for subject in subjects:
        subject_sds: list[float] = []
        for day, summary in subject.day_summaries.items():
            wear_ratios.append(summary.wear_minutes / 1440.0)
            overnight_ratios.append(summary.overnight_wear_minutes / 480.0)
            max_gaps.append(summary.max_gap_minutes)

        rhr_days = sorted(
            d for d, v in subject.daily.items() if "resting_heart_rate" in v
        )
        for index, day in enumerate(rhr_days):
            if index < rhr_policy.min_baseline_days:
                continue
            window = rhr_days[max(0, index - 60):index]
            values = [
                subject.daily[d]["resting_heart_rate"]
                for d in window
                if (subject.wear_ratio(d) or 0) >= 0.60
            ]
            if len(values) < rhr_policy.min_baseline_days:
                continue
            sd = sample_sd(values)
            baseline_sds.append(sd)
            subject_sds.append(sd)
            if len(values) >= 6:
                half = len(values) // 2
                half_shifts.append(abs(mean(values[half:]) - mean(values[:half])))
            effective_sd = max(sd, 1.5)
            delta = subject.daily[day]["resting_heart_rate"] - mean(values)
            rhr_z.append(delta / effective_sd)
            rhr_abs_delta.append(abs(delta))

        by_date = subject.sleep_records_by_date()
        nights = sorted(by_date)
        for index, night in enumerate(nights):
            if index < sleep_policy.min_baseline_days:
                continue
            values = [by_date[n]["minutes_asleep"] for n in nights[max(0, index - 60):index]]
            if len(values) >= sleep_policy.min_baseline_days:
                sleep_sds.append(sample_sd(values))

        per_subject[subject.subject_id] = {
            "days_with_heart_rate": len(subject.day_summaries),
            "days_with_resting_heart_rate": len(rhr_days),
            "sleep_nights": len(by_date),
            "median_wear_ratio": round(
                percentile(
                    [s.wear_minutes / 1440.0 for s in subject.day_summaries.values()], 50
                ), 3
            ) if subject.day_summaries else None,
            "median_baseline_sd_bpm": round(percentile(subject_sds, 50), 3) if subject_sds else None,
        }

    return {
        "subject_days": len(wear_ratios),
        "per_subject": per_subject,
        "checks": [
            {
                "threshold": "min_wear_coverage",
                "contract_value": rhr_policy.min_wear_coverage,
                "observed": _dist(wear_ratios),
                "share_below_threshold": round(
                    _share(wear_ratios, lambda v: v < rhr_policy.min_wear_coverage), 4
                ),
                "verdict_note": (
                    "Share of real subject-days the coverage gate would reject outright."
                ),
            },
            {
                "threshold": "min_overnight_coverage",
                "contract_value": rhr_policy.min_overnight_coverage,
                "observed": _dist(overnight_ratios),
                "share_below_threshold": round(
                    _share(overnight_ratios, lambda v: v < (rhr_policy.min_overnight_coverage or 0)), 4
                ),
            },
            {
                "threshold": "max_gap_minutes",
                "contract_value": rhr_policy.max_gap_minutes,
                "observed": _dist(max_gaps),
                "share_above_threshold": round(
                    _share(max_gaps, lambda v: v > rhr_policy.max_gap_minutes), 4
                ),
            },
            {
                "threshold": "max_baseline_sd (resting heart rate, bpm)",
                "contract_value": rhr_policy.max_baseline_sd,
                "observed": _dist(baseline_sds),
                "share_above_threshold": round(
                    _share(baseline_sds, lambda v: v > (rhr_policy.max_baseline_sd or 0)), 4
                ),
                "verdict_note": (
                    "Share of real subject-days whose own history the engine would call "
                    "too unstable to compare against."
                ),
            },
            {
                "threshold": "max_baseline_shift (resting heart rate, bpm)",
                "contract_value": rhr_policy.max_baseline_shift,
                "observed": _dist(half_shifts),
                "share_above_threshold": round(
                    _share(half_shifts, lambda v: v > (rhr_policy.max_baseline_shift or 0)), 4
                ),
            },
            {
                "threshold": "show_z (resting heart rate)",
                "contract_value": rhr_policy.show_z,
                "observed": _dist(rhr_z),
                "share_above_threshold": round(
                    _share(rhr_z, lambda v: v >= rhr_policy.show_z), 4
                ),
                "verdict_note": (
                    "How often a real person would see this claim if only effect size "
                    "gated it. An insight firing on a large share of all days is not an "
                    "insight."
                ),
            },
            {
                "threshold": "min_absolute_delta (resting heart rate, bpm)",
                "contract_value": rhr_policy.min_absolute_delta,
                "observed": _dist(rhr_abs_delta),
                "share_below_threshold": round(
                    _share(rhr_abs_delta, lambda v: v < rhr_policy.min_absolute_delta), 4
                ),
            },
            {
                "threshold": "max_baseline_sd (sleep duration, minutes)",
                "contract_value": sleep_policy.max_baseline_sd,
                "observed": _dist(sleep_sds),
                "share_above_threshold": round(
                    _share(sleep_sds, lambda v: v > (sleep_policy.max_baseline_sd or 0)), 4
                ),
            },
        ],
    }


def baseline_stability_report(subjects: list[PreparedSubject]) -> dict[str, Any]:
    """How many days of history it actually takes before a personal normal settles.

    The contract asks for 14. This measures, per subject, the first day count at which the
    running standard deviation lands within 10% of its own 60-day value and stays there.
    """
    results: dict[str, Any] = {}
    settled: list[float] = []
    for subject in subjects:
        days = sorted(d for d, v in subject.daily.items() if "resting_heart_rate" in v)
        values = [
            subject.daily[d]["resting_heart_rate"]
            for d in days
            if (subject.wear_ratio(d) or 0) >= 0.60
        ]
        if len(values) < 40:
            continue
        reference = sample_sd(values[:60])
        if reference <= 0:
            continue
        first_settled = None
        for n in range(5, min(len(values), 60) + 1):
            running = [sample_sd(values[:k]) for k in range(n, min(len(values), 60) + 1)]
            if all(abs(r - reference) <= 0.10 * reference for r in running):
                first_settled = n
                break
        results[subject.subject_id] = {
            "valid_days": len(values),
            "sd_60_day_bpm": round(reference, 3),
            "days_until_sd_settles": first_settled,
        }
        if first_settled is not None:
            settled.append(float(first_settled))
    return {
        "per_subject": results,
        "days_until_settled": _dist(settled),
        "contract_min_baseline_days": 14,
        "note": (
            "A settling day count above the contract's 14 means the engine starts "
            "comparing against a personal normal that is still moving."
        ),
    }


# --------------------------------------------------------------------- 2. real decisions


def decision_report(subjects: list[PreparedSubject], assume_sync: bool) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {c: {d.value: 0 for d in Decision} for c in CLAIMS}
    reasons: dict[str, dict[str, int]] = {c: {} for c in CLAIMS}
    per_subject_coverage: dict[str, dict[str, float]] = {}
    evaluated = 0

    for subject in subjects:
        subject_counts: dict[str, list[int]] = {c: [0, 0] for c in CLAIMS}
        for claim in CLAIMS:
            for day in subject.days():
                request = subject.request(claim, day, assume_sync=assume_sync)
                if request is None:
                    continue
                response = evaluate(request)
                evaluated += 1
                counts[claim][response.decision.value] += 1
                subject_counts[claim][0] += 1
                subject_counts[claim][1] += response.decision in SHOWN
                for code in response.reason_codes:
                    reasons[claim][code.value] = reasons[claim].get(code.value, 0) + 1
        per_subject_coverage[subject.subject_id] = {
            claim: round(done / total, 4) if total else 0.0
            for claim, (total, done) in subject_counts.items()
        }

    summary = {}
    for claim in CLAIMS:
        total = sum(counts[claim].values())
        shown = counts[claim]["SHOW"] + counts[claim]["SHOW_WITH_WARNING"]
        summary[claim] = {
            "evaluated_days": total,
            "decisions": counts[claim],
            "decision_coverage": round(shown / total, 4) if total else 0.0,
            "top_reason_codes": dict(
                sorted(reasons[claim].items(), key=lambda kv: -kv[1])[:8]
            ),
        }
    return {
        "assume_sync": assume_sync,
        "evaluated_requests": evaluated,
        "by_claim": summary,
        "per_subject_decision_coverage": per_subject_coverage,
    }


# --------------------------------------------------------------------- 3. corruptions


def corruption_report(subjects: list[PreparedSubject], limit_per_subject: int = 25) -> dict[str, Any]:
    """The corruption harness applied to real evidence, so labels are known.

    Only days the engine already displays are corrupted: those are the ones where a gate
    failing to fire would be a visible product error.
    """
    results: dict[str, dict[str, int]] = {}
    violations: list[dict[str, Any]] = []
    benign_changes: list[dict[str, Any]] = []
    base_cases = 0

    for subject in subjects:
        taken = 0
        for day in subject.days():
            if taken >= limit_per_subject:
                break
            request = subject.request("RESTING_HEART_RATE_ELEVATED", day, assume_sync=True)
            if request is None:
                continue
            base = evaluate(request)
            if base.decision not in SHOWN:
                continue
            taken += 1
            base_cases += 1
            case = SyntheticCase(
                request=request,
                truth=GroundTruth.SUPPORTABLE,
                truth_reason="real PMData subject-day the engine displays",
                generator="pmdata",
                seed=0,
            )
            for name in REAL_DATA_CORRUPTIONS:
                for severity in ("moderate", "severe"):
                    corrupted = apply_corruption(case, name, severity, seed=0)
                    decision = evaluate(corrupted.request).decision
                    key = f"{name}/{severity}"
                    bucket = results.setdefault(
                        key, {"cases": 0, "withheld": 0, "displayed": 0}
                    )
                    bucket["cases"] += 1
                    if decision in SHOWN:
                        bucket["displayed"] += 1
                    else:
                        bucket["withheld"] += 1
                    if name in BENIGN:
                        if decision is not base.decision:
                            benign_changes.append(
                                {"subject": subject.subject_id, "day": day,
                                 "corruption": key, "from": base.decision.value,
                                 "to": decision.value}
                            )
                    elif corrupted.truth is not GroundTruth.SUPPORTABLE and decision in SHOWN:
                        violations.append(
                            {"subject": subject.subject_id, "day": day, "corruption": key,
                             "truth": corrupted.truth.value, "decision": decision.value,
                             "breaches": corrupted.breaches}
                        )
    harmful = {k: v for k, v in results.items() if k.split("/")[0] not in BENIGN}
    total_harmful = sum(v["cases"] for v in harmful.values())
    displayed_harmful = sum(v["displayed"] for v in harmful.values())
    return {
        "real_days_used_as_base": base_cases,
        "by_corruption": dict(sorted(results.items())),
        "harmful_corruption_cases": total_harmful,
        "unsupported_show_rate_on_real_evidence": round(
            displayed_harmful / total_harmful, 4
        ) if total_harmful else 0.0,
        "violations": violations[:20],
        "benign_corruptions_that_changed_the_decision": benign_changes[:20],
    }


# --------------------------------------------------------------------- 4. exploratory


def wellness_association(subjects: list[PreparedSubject]) -> dict[str, Any]:
    """Exploratory only. Self-report is not a reference standard for anything here."""
    shown_scores: dict[str, list[float]] = {}
    other_scores: dict[str, list[float]] = {}
    for subject in subjects:
        for day in subject.days():
            report = subject.wellness.get(day)
            if not report:
                continue
            request = subject.request("RESTING_HEART_RATE_ELEVATED", day, assume_sync=True)
            if request is None:
                continue
            displayed = evaluate(request).decision in SHOWN
            target = shown_scores if displayed else other_scores
            for key, value in report.items():
                target.setdefault(key, []).append(value)
    keys = sorted(set(shown_scores) | set(other_scores))
    return {
        "caveat": (
            "Self-reported wellness is collected at a varying time of day, on ordinal "
            "1-5 scales, and is not a reference standard for resting heart rate. No "
            "significance testing, no correction for multiple comparisons, no control "
            "for subject. This is a look, not a result, and nothing here validates the "
            "engine."
        ),
        "days_with_claim_displayed": len(next(iter(shown_scores.values()), [])),
        "days_without": len(next(iter(other_scores.values()), [])),
        "means": {
            key: {
                "displayed": round(mean(shown_scores[key]), 3) if shown_scores.get(key) else None,
                "not_displayed": round(mean(other_scores[key]), 3) if other_scores.get(key) else None,
            }
            for key in keys
        },
    }


# --------------------------------------------------------------------- report


def run(cache: Path, limit_per_subject: int) -> dict[str, Any]:
    subjects = load_prepared(cache)
    if not subjects:
        raise SystemExit(f"no prepared subjects in {cache}; run eval.pmdata_prepare first")
    manifest_path = cache / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    return {
        "artifact": "pmdata-evaluation",
        "synthetic": False,
        "dataset": "PMData",
        "citation": CITATION,
        "licence": "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); no changes made",
        "timezone_assumed": manifest.get("timezone_assumed"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "policy_version": POLICY_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "subjects": len(subjects),
        "thresholds": threshold_report(subjects),
        "baseline_stability": baseline_stability_report(subjects),
        "decisions_without_sync_field": decision_report(subjects, assume_sync=False),
        "decisions_with_assumed_sync": decision_report(subjects, assume_sync=True),
        "corruptions_on_real_evidence": corruption_report(subjects, limit_per_subject),
        "exploratory_wellness": wellness_association(subjects),
    }


def to_markdown(report: dict[str, Any]) -> str:
    t = report["thresholds"]
    lines = [
        "# PMData evaluation — real Fitbit measurements",
        "",
        f"**Dataset:** {report['citation']}",
        "",
        f"- licence: {report['licence']}",
        f"- subjects: {report['subjects']}; subject-days with heart rate: "
        f"{t['subject_days']}",
        f"- assumed timezone: `{report['timezone_assumed']}` (Fitbit exports carry no zone)",
        f"- engine `{report['model_version']}`, policy `{report['policy_version']}`",
        f"- generated: `{report['generated_at']}`",
        "",
        "Reproduce:",
        "",
        "```bash",
        "./.venv/bin/python -m eval.pmdata_prepare",
        "./.venv/bin/python -m eval.pmdata_eval",
        "```",
        "",
        "## 1. Do the contract's thresholds describe real people?",
        "",
        "Every threshold in `docs/claim-contracts.md` was reasoned from domain knowledge, "
        "not fitted. This is the first evidence about whether any of them is sane. "
        "`share` is the fraction of real subject-days the threshold would act on.",
        "",
        "| threshold | contract | observed median | observed p05–p95 | share affected |",
        "|---|---|---|---|---|",
    ]
    for check in t["checks"]:
        obs = check["observed"]
        if not obs.get("n"):
            continue
        share = check.get("share_below_threshold", check.get("share_above_threshold"))
        lines.append(
            f"| `{check['threshold']}` | {check['contract_value']} | {obs['median']} | "
            f"{obs['p05']} – {obs['p95']} | {share:.1%} |"
        )

    stability = report["baseline_stability"]
    settled = stability["days_until_settled"]
    lines += [
        "",
        "## 2. How long does a personal baseline actually take to settle?",
        "",
        f"The contract requires {stability['contract_min_baseline_days']} valid days before "
        "a resting-heart-rate claim may be displayed. Measured per subject as the first day "
        "count at which the running standard deviation stays within 10% of its 60-day value:",
        "",
    ]
    if settled.get("n"):
        lines.append(
            f"- median **{settled['median']:.0f} days**, range {settled['min']:.0f}–"
            f"{settled['max']:.0f}, across {settled['n']} subjects with enough history"
        )
    lines.append("")
    lines.append("| subject | valid days | 60-day SD (bpm) | days until SD settles |")
    lines.append("|---|---|---|---|")
    for subject_id, row in sorted(stability["per_subject"].items()):
        lines.append(
            f"| `{subject_id}` | {row['valid_days']} | {row['sd_60_day_bpm']} | "
            f"{row['days_until_sd_settles']} |"
        )

    for key, title in (
        ("decisions_without_sync_field", "without the sync field (what a real export gives you)"),
        ("decisions_with_assumed_sync", "with sync assumed complete"),
    ):
        block = report[key]
        lines += [
            "",
            f"## 3. What the engine does on real days — {title}",
            "",
            f"{block['evaluated_requests']} requests evaluated.",
            "",
            "| claim | days | SHOW | SHOW_WITH_WARNING | WAIT | REJECT | coverage |",
            "|---|---|---|---|---|---|---|",
        ]
        for claim, row in block["by_claim"].items():
            d = row["decisions"]
            lines.append(
                f"| `{claim}` | {row['evaluated_days']} | {d['SHOW']} | "
                f"{d['SHOW_WITH_WARNING']} | {d['WAIT_FOR_MORE_DATA']} | {d['REJECT']} | "
                f"{row['decision_coverage']:.1%} |"
            )
        lines += ["", "Most frequent reason codes:", ""]
        for claim, row in block["by_claim"].items():
            codes = ", ".join(f"`{k}` ({v})" for k, v in row["top_reason_codes"].items())
            lines.append(f"- **{claim}** — {codes or 'none'}")

    corr = report["corruptions_on_real_evidence"]
    lines += [
        "",
        "## 4. Do the gates hold against real noise?",
        "",
        f"The corruption harness applied to {corr['real_days_used_as_base']} real subject-days "
        "that the engine displays. Labels are known because the corruption is known, and the "
        "underlying data is real rather than Gaussian.",
        "",
        f"**Unsupported-show rate on real evidence: {corr['unsupported_show_rate_on_real_evidence']:.4f}** "
        f"across {corr['harmful_corruption_cases']} corrupted cases.",
        "",
        "| corruption | cases | withheld | displayed |",
        "|---|---|---|---|",
    ]
    for name, row in corr["by_corruption"].items():
        lines.append(
            f"| `{name}` | {row['cases']} | {row['withheld']} | {row['displayed']} |"
        )
    if corr["benign_corruptions_that_changed_the_decision"]:
        lines += ["", "**Benign corruptions that changed a decision (these are bugs):**", ""]
        for row in corr["benign_corruptions_that_changed_the_decision"]:
            lines.append(f"- {row}")
    else:
        lines += [
            "",
            "Duplication and reordering changed no decision on any real day, which is the "
            "required behaviour: the engine must absorb them rather than abstain.",
        ]

    wellness = report["exploratory_wellness"]
    lines += [
        "",
        "## 5. Exploratory: self-reported wellness",
        "",
        f"> {wellness['caveat']}",
        "",
        f"Days with the claim displayed: {wellness['days_with_claim_displayed']}; without: "
        f"{wellness['days_without']}.",
        "",
        "| self-report | claim displayed | claim not displayed |",
        "|---|---|---|",
    ]
    for key, row in wellness["means"].items():
        lines.append(f"| {key} | {row['displayed']} | {row['not_displayed']} |")

    lines += [
        "",
        "## What this evaluation does and does not establish",
        "",
        "**Does:** the contract's thresholds have now been compared against real wearable "
        "measurements from 16 people over five months, the engine's real-world decision "
        "coverage is known rather than guessed, and the gates have been tested against "
        "corruptions of real evidence rather than of Gaussian evidence.",
        "",
        "**Does not:** PMData subjects are a small, self-selected, largely athletic cohort, "
        "and none of them is a reference standard. There is still no ground truth for "
        "whether an uncorrupted real day's claim was *actually* supportable — only the "
        "corrupted cases carry known labels. Nothing here is a calibration result, and the "
        "learned component still does not exist.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--limit-per-subject", type=int, default=25,
                        help="real displayed days per subject used as corruption bases")
    args = parser.parse_args()

    report = run(args.cache, args.limit_per_subject)
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (args.out / f"pmdata-{stamp}.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / f"pmdata-{stamp}.md").write_text(to_markdown(report))

    print(f"PMData: {report['subjects']} subjects, "
          f"{report['thresholds']['subject_days']} subject-days")
    print("\nthreshold checks (share of real subject-days affected):")
    for check in report["thresholds"]["checks"]:
        if not check["observed"].get("n"):
            continue
        share = check.get("share_below_threshold", check.get("share_above_threshold"))
        print(f"  {check['threshold']:48s} contract={check['contract_value']:<8} "
              f"median={check['observed']['median']:<8} share={share:.1%}")
    print("\nreal decision coverage (sync field absent / assumed):")
    for claim in CLAIMS:
        a = report["decisions_without_sync_field"]["by_claim"][claim]
        b = report["decisions_with_assumed_sync"]["by_claim"][claim]
        print(f"  {claim:32s} {a['decision_coverage']:.1%} / {b['decision_coverage']:.1%} "
              f"over {a['evaluated_days']} days")
    corr = report["corruptions_on_real_evidence"]
    print(f"\nunsupported-show on corrupted REAL evidence: "
          f"{corr['unsupported_show_rate_on_real_evidence']:.4f} "
          f"({corr['harmful_corruption_cases']} cases)")
    print(f"\nwrote {args.out / f'pmdata-{stamp}.json'}")
    print(f"wrote {args.out / f'pmdata-{stamp}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
