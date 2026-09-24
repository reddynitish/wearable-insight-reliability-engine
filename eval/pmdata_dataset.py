#!/usr/bin/env python3
"""Build a supervised table from real PMData evidence, for the learned baselines.

    ./.venv/bin/python -m eval.pmdata_dataset --summary

One row per (subject, day, claim, corruption) case. Features are what the engine measures
from the request; the label is whether the evidence meets the claim contract, decided by
`engine/measure.py`.

## The honesty problem with this experiment, stated before the numbers

Labels come from `measure.py`, which reads the policy thresholds. The rules engine reads
the same thresholds. So a model given the *band-scored* features -- where 0.5 means
"exactly at the policy floor" -- would be handed the answer and would score perfectly while
proving nothing.

Two things are done about that:

1. **Only raw measurements are used as features.** Wear ratio, not coverage score. Valid
   baseline days, not maturity score. The model has to learn where the boundaries are,
   from the same numbers a production caller would have.
2. **The question is framed so a tie is informative.** "Can a model reproduce deterministic
   gates?" is not worth asking. The question asked here is: *at matched unsupported-show
   rate, does a learned estimator answer more days than the rules engine?* If it does not,
   the rules engine is already at the frontier and the learned component should be dropped
   -- which is the outcome PROJECT_BRIEF.md section 13 asks to be published.

A model that merely ties is therefore the expected result, and it is a result.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from engine.adapters.pmdata import PreparedSubject, load_prepared
from engine.corruptions import apply as apply_corruption
from engine.engine import evaluate
from engine.features import extract
from engine.measure import measure
from engine.normalize import normalize
from engine.policies import get_policy
from engine.schemas import Decision
from engine.synth import GroundTruth, SyntheticCase

SHOWN = {Decision.SHOW, Decision.SHOW_WITH_WARNING}

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = REPO / "data" / "datasets" / "pmdata_cache"

CLAIMS = (
    "RESTING_HEART_RATE_ELEVATED",
    "ACTIVITY_LOAD_HIGH",
    "SLEEP_DURATION_LOW",
    "SLEEP_QUALITY_REDUCED",
)

# Corruptions sampled per case. Deliberately includes the two benign ones, so the model is
# penalised for treating duplication or reordering as a failure -- an over-abstaining model
# should not be able to hide.
CORRUPTIONS = (
    ("shorten_wear", "moderate"),
    ("shorten_wear", "severe"),
    ("truncate_baseline", "moderate"),
    ("truncate_baseline", "severe"),
    ("delay_sync", "moderate"),
    ("stale_data", "severe"),
    ("implausible_spike", "moderate"),
    ("drop_timezone", "severe"),
    ("motion_artifact", "severe"),
    ("dropout_flags", "severe"),
    ("drop_contiguous", "severe"),
    ("destabilize_baseline", "severe"),
    ("duplicate_samples", "severe"),
    ("shuffle_order", "severe"),
)

# Raw measurements only. Nothing here is a band score, because a band score is the
# threshold comparison already performed.
FEATURE_NAMES = (
    "wear_ratio", "overnight_ratio", "max_gap_minutes", "gap_count",
    "wear_known", "overnight_known",
    "measurement_age_hours", "sync_age_hours", "window_closed", "sync_known",
    "implausible_fraction", "flagged_fraction", "artifact_fraction", "dropout_fraction",
    "longest_flat_run", "flatline_detected",
    "consistency_findings", "raw_consistency",
    "baseline_valid_days", "baseline_submitted_days", "baseline_sd",
    "baseline_effective_sd", "baseline_half_shift", "baseline_from_samples",
    "support_available", "z_directional", "absolute_delta", "target_sample_count",
    "window_minutes", "observation_count",
)


@dataclass
class Row:
    subject_id: str
    day: str
    claim_type: str
    corruption: str | None
    severity: str | None
    features: dict[str, float | None]
    # Primary target: is this evidence adequate under the claim contract, regardless of
    # whether the effect happens to be present? That is the reliability question, and it is
    # the one a learned reliability estimator is supposed to answer.
    adequate: int
    # Secondary target: does the evidence actually support the claim (adequacy AND effect)?
    # Heavily imbalanced, because on most real days the claim is simply not true.
    label: int
    truth: str
    breaches: list[str]
    # What the shipped rules engine decided, so B2 is comparable row for row.
    engine_shows: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id, "day": self.day, "claim_type": self.claim_type,
            "corruption": self.corruption, "severity": self.severity,
            "adequate": self.adequate, "label": self.label, "truth": self.truth,
            "breaches": self.breaches, "engine_shows": self.engine_shows,
            **{f"f_{k}": v for k, v in self.features.items()},
        }


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return float(value) if isinstance(value, bool) else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def feature_row(request, policy) -> dict[str, float | None]:
    norm = normalize(request, now=request.evaluated_at)
    feats = extract(norm, request, policy)
    cov, fresh, qual, cons = feats.coverage, feats.freshness, feats.quality, feats.consistency
    base, sup = feats.baseline, feats.support
    return {
        "wear_ratio": _num(cov.wear_ratio),
        "overnight_ratio": _num(cov.overnight_ratio),
        "max_gap_minutes": _num(cov.max_gap_minutes),
        "gap_count": float(cov.gap_count),
        "wear_known": float(cov.known),
        "overnight_known": float(cov.overnight_known),
        "measurement_age_hours": _num(fresh.measurement_age_hours),
        "sync_age_hours": _num(fresh.sync_age_hours),
        "window_closed": float(fresh.window_closed),
        "sync_known": float(fresh.sync_known),
        "implausible_fraction": float(qual.implausible_fraction),
        "flagged_fraction": float(qual.flagged_fraction),
        "artifact_fraction": float(qual.artifact_fraction),
        "dropout_fraction": float(qual.dropout_fraction),
        "longest_flat_run": float(qual.longest_flat_run),
        "flatline_detected": float(qual.flatline_detected),
        "consistency_findings": float(len(cons.findings)),
        "raw_consistency": float(cons.raw_consistency),
        "baseline_valid_days": float(base.valid_days),
        "baseline_submitted_days": float(base.submitted_days),
        "baseline_sd": _num(base.sd),
        "baseline_effective_sd": _num(base.effective_sd),
        "baseline_half_shift": _num(base.half_shift),
        "baseline_from_samples": float(base.source == "samples"),
        "support_available": float(sup.available),
        "z_directional": _num(sup.z_directional),
        "absolute_delta": _num(sup.absolute_delta),
        "target_sample_count": float(sup.sample_count),
        "window_minutes": float(request.claim.target_window.duration_minutes),
        "observation_count": float(len(request.observations)),
    }


def build(cache: Path, max_days_per_subject: int = 60, seed: int = 0) -> list[Row]:
    subjects = load_prepared(cache)
    if not subjects:
        raise SystemExit(f"no prepared subjects in {cache}; run eval.pmdata_prepare first")
    rows: list[Row] = []
    for subject in subjects:
        days = subject.days()
        # Evenly spaced days rather than the first N, so a subject's later months are
        # represented and the baseline is mature for most cases.
        step = max(1, len(days) // max_days_per_subject)
        for day in days[::step][:max_days_per_subject]:
            for claim_type in CLAIMS:
                policy = get_policy(claim_type)
                assert policy is not None
                base_request = subject.request(claim_type, day, assume_sync=True)
                if base_request is None:
                    continue
                variants: list[tuple[str | None, str | None, Any]] = [(None, None, base_request)]
                case = SyntheticCase(
                    request=base_request, truth=GroundTruth.SUPPORTABLE,
                    truth_reason="real PMData subject-day", generator="pmdata", seed=seed,
                )
                for name, severity in CORRUPTIONS:
                    try:
                        corrupted = apply_corruption(case, name, severity, seed=seed)
                    except (KeyError, ValueError):
                        continue
                    variants.append((name, severity, corrupted.request))
                for name, severity, request in variants:
                    m = measure(request, policy)
                    supportable = (
                        not m.breaches
                        and m.z_directional is not None
                        and m.z_directional >= policy.show_z
                        and not (
                            policy.min_absolute_delta > 0
                            and m.absolute_delta is not None
                            and m.absolute_delta < policy.min_absolute_delta
                        )
                    )
                    decision = evaluate(request, policy=policy).decision
                    rows.append(
                        Row(
                            subject_id=subject.subject_id, day=day, claim_type=claim_type,
                            corruption=name, severity=severity,
                            features=feature_row(request, policy),
                            adequate=int(not m.breaches),
                            label=int(supportable),
                            truth="SUPPORTABLE" if supportable else "UNSUPPORTABLE",
                            breaches=list(m.breaches),
                            engine_shows=int(decision in SHOWN),
                        )
                    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--max-days", type=int, default=60)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    rows = build(args.cache, args.max_days)
    adequate = sum(r.adequate for r in rows)
    positives = sum(r.label for r in rows)
    print(f"rows: {len(rows)}")
    print(f"subjects: {len({r.subject_id for r in rows})}")
    print(f"primary target (evidence adequate): {adequate} ({adequate / len(rows):.1%} positive)")
    print(f"secondary target (claim supportable): {positives} ({positives / len(rows):.1%} positive)")
    print(f"rules engine displays: {sum(r.engine_shows for r in rows)} rows")
    by_claim: dict[str, list[int]] = {}
    for r in rows:
        by_claim.setdefault(r.claim_type, []).append(r.label)
    by_claim = {}
    for r in rows:
        by_claim.setdefault(r.claim_type, []).append(r.adequate)
    for claim, labels in sorted(by_claim.items()):
        print(f"  {claim:34s} {len(labels):6d} rows, {sum(labels) / len(labels):.1%} positive")
    if args.out:
        args.out.write_text("\n".join(json.dumps(r.as_dict()) for r in rows) + "\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
