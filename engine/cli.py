#!/usr/bin/env python3
"""Command-line interface to the reliability engine.

    ./.venv/bin/python -m engine.cli demo
    ./.venv/bin/python -m engine.cli demo --claim SLEEP_DURATION_LOW
    ./.venv/bin/python -m engine.cli corrupt --claim RESTING_HEART_RATE_ELEVATED \
        --corruption shorten_wear --severity severe
    ./.venv/bin/python -m engine.cli evaluate request.json
    cat request.json | ./.venv/bin/python -m engine.cli evaluate -
    ./.venv/bin/python -m engine.cli policies
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from engine import MODEL_VERSION, POLICY_VERSION
from engine.corruptions import SEVERITIES, apply as apply_corruption, describe
from engine.engine import evaluate
from engine.policies import all_policies, supported_claim_types
from engine.reasons import SPECS, ReasonCode, sort_key
from engine.schemas import DecisionResponse, EvaluationRequest
from engine.synth import GENERATORS, clean_case

COLOR = {
    "SHOW": "\033[32m",
    "SHOW_WITH_WARNING": "\033[33m",
    "WAIT_FOR_MORE_DATA": "\033[34m",
    "REJECT": "\033[31m",
}
RESET = "\033[0m"


def _tint(text: str, decision: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{COLOR.get(decision, '')}{text}{RESET}"


def _print_decision(response: DecisionResponse, color: bool, verbose: bool) -> None:
    d = response.decision.value
    print(_tint(f"  decision   {d}", d, color))
    print(f"  confidence {response.confidence:.2f} in the decision; "
          f"claim support {response.claim_support_probability:.2f} "
          f"({'calibrated' if response.support_is_calibrated else 'not calibrated'})")
    ev = response.evidence
    print(f"  evidence   coverage {ev.coverage_score:.2f}  freshness {ev.freshness_score:.2f}  "
          f"quality {ev.signal_quality_score:.2f}  consistency {ev.consistency_score:.2f}  "
          f"baseline {ev.baseline_maturity_score:.2f}")
    codes = ", ".join(c.value for c in response.reason_codes) or "none"
    print(f"  reasons    {codes}")
    if response.retry.recommended:
        need = ", ".join(response.retry.required_evidence)
        print(f"  retry      after {response.retry.after}" + (f" once there is {need}" if need else ""))
    print(f"  explanation\n             {response.explanation}")
    if verbose:
        print("  trace")
        print(json.dumps(response.trace.model_dump(mode="json"), indent=2))


def cmd_demo(args: argparse.Namespace) -> int:
    """Show every decision type the engine can return, from synthetic fixtures."""
    claims = [args.claim] if args.claim else sorted(GENERATORS)
    print(f"engine {MODEL_VERSION} / policy {POLICY_VERSION}")
    print("All evidence below is synthetic. See docs/evaluation-protocol.md.\n")
    for claim_type in claims:
        scenarios = [
            ("clean, effect present", dict(supportable=True), None, None),
            ("clean, effect absent", dict(supportable=False), None, None),
            ("short personal baseline", dict(supportable=True), "truncate_baseline", "severe"),
            ("sync did not cover the window", dict(supportable=True), "delay_sync", "moderate"),
            ("device barely worn", dict(supportable=True), "shorten_wear", "severe"),
            ("impossible sensor values", dict(supportable=True), "implausible_spike", "moderate"),
            ("baseline swings too widely", dict(supportable=True), "destabilize_baseline", "severe"),
            ("duplicated samples (benign)", dict(supportable=True), "duplicate_samples", "severe"),
        ]
        print(f"=== {claim_type} ===")
        for title, kwargs, corruption, severity in scenarios:
            case = clean_case(claim_type, args.seed, **kwargs)
            if corruption:
                case = apply_corruption(case, corruption, severity, seed=args.seed)
            response = evaluate(case.request)
            print(f"\n-- {title}")
            print(f"  ground truth {case.truth.value}")
            _print_decision(response, args.color, args.verbose)
        print()
    return 0


def cmd_corrupt(args: argparse.Namespace) -> int:
    """Before/after for one injected failure."""
    case = clean_case(args.claim, args.seed, supportable=not args.effect_absent)
    before = evaluate(case.request)
    corrupted = apply_corruption(case, args.corruption, args.severity, seed=args.seed)
    after = evaluate(corrupted.request)
    print(f"{args.claim} / {args.corruption} / {args.severity}\n")
    print("before (clean evidence)")
    print(f"  ground truth {case.truth.value}")
    _print_decision(before, args.color, args.verbose)
    print(f"\nafter ({corrupted.truth_reason})")
    print(f"  ground truth {corrupted.truth.value}")
    print(f"  breaches     {', '.join(corrupted.breaches) or 'none'}")
    _print_decision(after, args.color, args.verbose)
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Evaluate a request supplied as JSON."""
    raw = sys.stdin.read() if args.path == "-" else Path(args.path).read_text()
    request = EvaluationRequest.model_validate_json(raw)
    response = evaluate(request)
    if args.json:
        print(json.dumps(response.model_dump(mode="json"), indent=2))
    else:
        print(f"{request.claim.type} for {request.subject_id}")
        _print_decision(response, args.color, args.verbose)
    return 0


def cmd_policies(args: argparse.Namespace) -> int:
    for policy in all_policies():
        print(f"{policy.claim_type}  [{policy.mode}]")
        print(f"  {policy.description}")
        print(f"  required signals: {', '.join(s.value for s in policy.required_signals) or 'none'}")
        print(f"  baseline: {policy.min_baseline_days} days required, "
              f"{policy.warn_baseline_days} minimum; wear coverage "
              f"{policy.min_wear_coverage:.0%}; data age <= "
              f"{policy.max_measurement_age_hours:.0f}h")
        print(f"  effect: contradicted at <= {policy.contradict_z} SD, warn at "
              f"{policy.warn_z} SD, show at {policy.show_z} SD, absolute floor "
              f"{policy.min_absolute_delta}")
        print(f"  maximum decision: {policy.max_decision.value}")
        if policy.known_confounders:
            print(f"  confounders: {'; '.join(policy.known_confounders)}")
        print()
    return 0


def cmd_reasons(args: argparse.Namespace) -> int:
    for code in sorted(ReasonCode, key=sort_key):
        s = SPECS[code]
        print(f"{code.value:38s} {s.dimension.value:16s} forces={s.outcome.value:6s} "
              f"decisiveness={s.decisiveness:.2f} retry={s.retry_after or '-'}")
    return 0


def cmd_corruptions(args: argparse.Namespace) -> int:
    for c in describe():
        tag = "" if c.degrades_evidence else "  (benign; engine must absorb it)"
        print(f"{c.name:26s} {c.dimension:16s}{tag}\n  {c.rationale}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engine.cli", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--no-color", dest="color", action="store_false",
                        default=sys.stdout.isatty())
    parser.add_argument("-v", "--verbose", action="store_true", help="print the full trace")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="every decision type, from synthetic fixtures")
    demo.add_argument("--claim", choices=sorted(GENERATORS))
    demo.add_argument("--seed", type=int, default=0)
    demo.set_defaults(func=cmd_demo)

    corrupt = sub.add_parser("corrupt", help="before/after for one injected failure")
    corrupt.add_argument("--claim", choices=sorted(GENERATORS),
                         default="RESTING_HEART_RATE_ELEVATED")
    corrupt.add_argument("--corruption", required=True)
    corrupt.add_argument("--severity", choices=list(SEVERITIES), default="moderate")
    corrupt.add_argument("--seed", type=int, default=0)
    corrupt.add_argument("--effect-absent", action="store_true")
    corrupt.set_defaults(func=cmd_corrupt)

    ev = sub.add_parser("evaluate", help="evaluate a request JSON file, or - for stdin")
    ev.add_argument("path")
    ev.add_argument("--json", action="store_true", help="print the full response as JSON")
    ev.set_defaults(func=cmd_evaluate)

    sub.add_parser("policies", help="print the claim contracts").set_defaults(func=cmd_policies)
    sub.add_parser("reasons", help="print the reason-code catalogue").set_defaults(func=cmd_reasons)
    sub.add_parser("corruptions", help="print the corruption catalogue").set_defaults(
        func=cmd_corruptions)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
