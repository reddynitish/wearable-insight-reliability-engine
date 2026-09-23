#!/usr/bin/env python3
"""Run the synthetic evaluation and write a reproducible manifest plus a report.

    ./.venv/bin/python -m eval.run_eval --seeds 5

Everything this produces is labelled synthetic, in the artifact itself and not only in
the prose around it. These numbers measure whether the engine's decisions agree with the
claim contract it implements; they say nothing about real sensor behaviour, and no
resume bullet may cite them as real-world performance (docs/evaluation-protocol.md).
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from engine import MODEL_VERSION, POLICY_VERSION
from engine.corruptions import describe
from engine.engine import evaluate
from engine.schemas import Decision
from engine.synth import GroundTruth
from eval import metrics as M
from eval.baselines import BASELINES, NOT_IMPLEMENTED
from eval.suite import REFERENCE_DECISION, build
from eval.sweep import gate_contribution, sweep, to_markdown as sweep_markdown

OUT_DIR = Path(__file__).resolve().parent / "results"


class _OperatingPointView:
    """Lets to_markdown render operating points read back from the JSON artifact."""

    def __init__(self, **row):
        self.__dict__.update(row)


def _dimension(case) -> str | None:
    for tag in case.tags:
        if tag.startswith("dim:"):
            return tag[4:]
    return None


def run(seeds: int) -> dict:
    suite = build(seeds=seeds)
    scored = suite.scored

    results: dict[str, dict] = {}
    for name, fn in BASELINES.items():
        records: list[M.Record] = []
        for case in scored:
            start = time.perf_counter()
            decision = fn(case.request)
            latency_ms = (time.perf_counter() - start) * 1000.0
            records.append(
                M.Record(
                    subject_id=case.request.subject_id,
                    claim_type=case.request.claim.type,
                    truth=case.truth,
                    reference_decision=REFERENCE_DECISION[case.truth],
                    decision=decision,
                    corruption=case.corruption,
                    severity=case.severity,
                    dimension=_dimension(case),
                    latency_ms=latency_ms,
                )
            )
        latencies = [r.latency_ms for r in records if r.latency_ms is not None]
        entry = {
            "unsupported_show_rate": M.bootstrap(records, M.unsupported_show_rate).as_dict(),
            "unsupported_show_rate_strict": round(
                M.unsupported_show_rate(records, strict=True), 4
            ),
            "over_abstention_rate": M.bootstrap(records, M.over_abstention_rate).as_dict(),
            "decision_coverage": M.bootstrap(records, M.decision_coverage).as_dict(),
            "macro_f1": round(M.macro_f1(records), 4),
            "per_class": M.per_class(records),
            "decisions": {
                d.value: sum(1 for r in records if r.decision is d) for d in Decision
            },
            "latency_ms": {
                "p50": round(M.percentile(latencies, 50), 4),
                "p95": round(M.percentile(latencies, 95), 4),
                "p99": round(M.percentile(latencies, 99), 4),
            },
        }
        if name == "B2_rules_engine":
            entry["by_claim_type"] = M.breakdown(records, lambda r: r.claim_type)
            entry["by_corruption"] = M.breakdown(records, lambda r: r.corruption or "none")
            entry["by_severity"] = M.breakdown(records, lambda r: r.severity or "none")
            entry["by_dimension"] = M.breakdown(records, lambda r: r.dimension or "none")
            entry["by_ground_truth"] = M.breakdown(records, lambda r: r.truth.value)
        results[name] = entry

    operating_points = sweep(scored)
    contribution = gate_contribution(scored)

    return {
        "artifact": "synthetic-evaluation",
        "synthetic": True,
        "warning": (
            "SYNTHETIC EVIDENCE ONLY. These numbers measure agreement between the engine "
            "and the claim contract it implements. They are not a measurement of "
            "real-world performance and must not be reported as one."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "policy_version": POLICY_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "suite": suite.summary(),
        "corruption_catalogue": [
            {"name": c.name, "dimension": c.dimension, "rationale": c.rationale,
             "degrades_evidence": c.degrades_evidence}
            for c in describe()
        ],
        "baselines": results,
        "operating_points": [p.as_dict() for p in operating_points],
        "gate_contribution": contribution,
        "baselines_not_implemented": NOT_IMPLEMENTED,
        "excluded_borderline": [
            {
                "claim_type": c.request.claim.type,
                "corruption": c.corruption,
                "severity": c.severity,
                "reason": c.truth_reason,
            }
            for c in suite.excluded
        ],
    }


def to_markdown(report: dict) -> str:
    s = report["suite"]
    lines = [
        "# Synthetic evaluation report",
        "",
        f"**{report['warning']}**",
        "",
        f"- generated: `{report['generated_at']}`",
        f"- model version: `{report['model_version']}`",
        f"- policy version: `{report['policy_version']}`",
        f"- cases: {s['total_cases']} total, {s['scored_cases']} scored, "
        f"{s['excluded_borderline']} excluded as borderline",
        f"- subjects: {s['subjects']} (pseudonymous, synthetic)",
        f"- claim types: {len(s['claim_types'])}; corruptions: {len(s['corruptions'])}; "
        f"severities: {len(s['severities'])}",
        "",
        "Reproduce with:",
        "",
        "```bash",
        "./.venv/bin/python -m eval.run_eval --seeds "
        f"{len(s['seeds'])}",
        "```",
        "",
        "## Baseline comparison",
        "",
        "`unsupported-show rate` is the primary metric: the share of cases whose evidence "
        "did not support the claim that were nonetheless displayed. `SHOW_WITH_WARNING` "
        "counts as displayed. Coverage is reported beside it because abstaining on "
        "everything would score a perfect 0.",
        "",
        "| baseline | unsupported-show rate (95% CI) | over-abstention | decision coverage | macro F1 | p95 latency |",
        "|---|---|---|---|---|---|",
    ]
    for name, entry in report["baselines"].items():
        usr = entry["unsupported_show_rate"]
        oar = entry["over_abstention_rate"]
        cov = entry["decision_coverage"]
        lines.append(
            f"| `{name}` | {usr['point']:.4f} [{usr['ci95_low']:.4f}, {usr['ci95_high']:.4f}] | "
            f"{oar['point']:.4f} | {cov['point']:.4f} | {entry['macro_f1']:.4f} | "
            f"{entry['latency_ms']['p95']:.3f} ms |"
        )
    lines += [
        "",
        "Not implemented, and not claimed:",
        "",
    ]
    for name, why in report["baselines_not_implemented"].items():
        lines.append(f"- `{name}` — {why}")

    engine = report["baselines"]["B2_rules_engine"]
    for title, key in (
        ("By claim type", "by_claim_type"),
        ("By corruption", "by_corruption"),
        ("By severity", "by_severity"),
        ("By evidence dimension", "by_dimension"),
        ("By ground-truth evidence state", "by_ground_truth"),
    ):
        lines += [
            "",
            f"## {title} (engine only)",
            "",
            "| group | cases | unsupported-show | over-abstention | coverage |",
            "|---|---|---|---|---|",
        ]
        for name, row in engine[key].items():
            lines.append(
                f"| `{name}` | {row['cases']} | {row['unsupported_show_rate']:.4f} | "
                f"{row['over_abstention_rate']:.4f} | {row['decision_coverage']:.4f} |"
            )

    lines += [
        "",
        "## Corruption catalogue",
        "",
        "| corruption | dimension | degrades evidence | rationale |",
        "|---|---|---|---|",
    ]
    for c in report["corruption_catalogue"]:
        lines.append(
            f"| `{c['name']}` | {c['dimension']} | {'yes' if c['degrades_evidence'] else 'no'} | "
            f"{c['rationale']} |"
        )

    lines += sweep_markdown(
        [_OperatingPointView(**row) for row in report["operating_points"]],
        report["gate_contribution"],
    )

    excluded = report["excluded_borderline"]
    lines += [
        "",
        "## Excluded borderline cases",
        "",
        f"{len(excluded)} case(s) breached no contract clause but landed between the "
        "contradiction and display thresholds. Neither showing nor abstaining is "
        "demonstrably correct for them, so they are excluded from the rates above rather "
        "than scored against a label we cannot justify.",
        "",
    ]
    for row in excluded[:20]:
        lines.append(
            f"- `{row['claim_type']}` / `{row['corruption']}` / {row['severity']}: {row['reason']}"
        )
    if len(excluded) > 20:
        lines.append(f"- ... and {len(excluded) - 20} more (see the JSON artifact)")

    lines += [
        "",
        "## How to read these numbers",
        "",
        "The engine's unsupported-show rate here reflects agreement between two separate "
        "implementations of the same written contract: `engine/` decides, and "
        "`engine/measure.py` labels. Where they disagreed during development, the "
        "disagreement was resolved by checking which one matched "
        "`docs/claim-contracts.md` -- sometimes fixing the engine, sometimes fixing the "
        "labeller. Both kinds of fix are recorded in the git history.",
        "",
        "That makes this suite a strong **regression harness** and a weak **benchmark**. "
        "It proves the gates fire where the contract says they should, across 17 failure "
        "modes and 3 severities. It cannot show that the contract's thresholds are the "
        "right thresholds for real people, because the evidence here was generated from "
        "Gaussian baselines with no sensor-error model. Only the public-dataset work in "
        "`data/DATASETS.md` can do that.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=5,
                        help="number of synthetic subjects per claim type (default 5)")
    parser.add_argument("--out", type=Path, default=OUT_DIR,
                        help="output directory for the JSON and Markdown artifacts")
    args = parser.parse_args()

    report = run(args.seeds)
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    json_path = args.out / f"synthetic-{stamp}.json"
    md_path = args.out / f"synthetic-{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    md_path.write_text(to_markdown(report))

    engine = report["baselines"]["B2_rules_engine"]
    print(report["warning"])
    print()
    print(f"cases scored: {report['suite']['scored_cases']} "
          f"(excluded borderline: {report['suite']['excluded_borderline']})")
    for name, entry in report["baselines"].items():
        usr = entry["unsupported_show_rate"]
        cov = entry["decision_coverage"]["point"]
        print(f"  {name:20s} unsupported-show {usr['point']:.4f} "
              f"[{usr['ci95_low']:.4f}, {usr['ci95_high']:.4f}]  coverage {cov:.4f}  "
              f"macroF1 {entry['macro_f1']:.4f}")
    default = next((p for p in report["operating_points"] if p["is_default"]), None)
    if default:
        print(f"  default operating point: coverage {default['decision_coverage']:.4f}, "
              f"unsupported-show {default['unsupported_show_rate']:.4f}")
    contribution = report["gate_contribution"]
    print(f"  safety attributable to deterministic gates: "
          f"{contribution['share_attributable_to_gates']:.1%}")
    print()
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
