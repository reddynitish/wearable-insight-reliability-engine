"""Metric computation, including subject-level bootstrap intervals.

Bootstrapping resamples **subjects**, not cases. Cases from one subject share a
baseline, a device, and a window, so treating them as independent samples would produce
intervals several times too narrow -- the single most common way a small evaluation
overstates its own precision.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from engine.schemas import Decision
from engine.synth import GroundTruth

SHOW_DECISIONS = {Decision.SHOW, Decision.SHOW_WITH_WARNING}
DECISIONS = [d.value for d in Decision]
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260923


@dataclass
class Record:
    subject_id: str
    claim_type: str
    truth: GroundTruth
    reference_decision: str
    decision: Decision
    corruption: str | None
    severity: str | None
    dimension: str | None
    latency_ms: float | None = None
    support: float | None = None
    confidence: float | None = None


@dataclass
class Interval:
    point: float
    low: float
    high: float
    n: int

    def as_dict(self) -> dict:
        return {
            "point": round(self.point, 4),
            "ci95_low": round(self.low, 4),
            "ci95_high": round(self.high, 4),
            "n": self.n,
        }


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def unsupported_show_rate(records: list[Record], strict: bool = False) -> float:
    """Share of unsupportable cases that were nonetheless displayed.

    `strict` counts only a bare SHOW. The default counts SHOW_WITH_WARNING too: a
    warning beside an unjustified claim is still an unjustified claim on the screen.
    """
    bad = [r for r in records if r.truth is not GroundTruth.SUPPORTABLE]
    shown = [
        r for r in bad
        if (r.decision is Decision.SHOW if strict else r.decision in SHOW_DECISIONS)
    ]
    return _rate(len(shown), len(bad))


def over_abstention_rate(records: list[Record]) -> float:
    """Share of supportable cases the engine declined to display."""
    good = [r for r in records if r.truth is GroundTruth.SUPPORTABLE]
    withheld = [r for r in good if r.decision not in SHOW_DECISIONS]
    return _rate(len(withheld), len(good))


def decision_coverage(records: list[Record]) -> float:
    """Share of all cases answered with a displayed claim.

    Reported next to the unsupported-show rate always: an engine that abstains on
    everything scores a perfect 0.0 unsupported-show rate and is useless.
    """
    return _rate(len([r for r in records if r.decision in SHOW_DECISIONS]), len(records))


def _collapse(decision: Decision) -> str:
    """Collapse SHOW_WITH_WARNING into SHOW for four-way confusion metrics."""
    return "SHOW" if decision in SHOW_DECISIONS else decision.value


def per_class(records: list[Record]) -> dict[str, dict[str, float]]:
    classes = ["SHOW", "WAIT_FOR_MORE_DATA", "REJECT"]
    out: dict[str, dict[str, float]] = {}
    for cls in classes:
        tp = sum(1 for r in records if _collapse(r.decision) == cls and r.reference_decision == cls)
        fp = sum(1 for r in records if _collapse(r.decision) == cls and r.reference_decision != cls)
        fn = sum(1 for r in records if _collapse(r.decision) != cls and r.reference_decision == cls)
        precision = _rate(tp, tp + fp)
        recall = _rate(tp, tp + fn)
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        out[cls] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }
    return out


def macro_f1(records: list[Record]) -> float:
    scores = [v["f1"] for v in per_class(records).values() if v["support"] > 0]
    return 0.0 if not scores else sum(scores) / len(scores)


def bootstrap(records: list[Record], fn, resamples: int = BOOTSTRAP_RESAMPLES) -> Interval:
    """Percentile 95% interval for `fn`, resampling subjects with replacement."""
    by_subject: dict[str, list[Record]] = {}
    for r in records:
        by_subject.setdefault(r.subject_id, []).append(r)
    subjects = sorted(by_subject)
    point = fn(records)
    if len(subjects) < 2:
        return Interval(point, point, point, len(subjects))
    rng = random.Random(BOOTSTRAP_SEED)
    draws: list[float] = []
    for _ in range(resamples):
        sample: list[Record] = []
        for _ in subjects:
            sample.extend(by_subject[subjects[rng.randrange(len(subjects))]])
        draws.append(fn(sample))
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(0.975 * (len(draws) - 1))]
    return Interval(point, lo, hi, len(subjects))


def breakdown(records: list[Record], key) -> dict[str, dict]:
    groups: dict[str, list[Record]] = {}
    for r in records:
        groups.setdefault(str(key(r)), []).append(r)
    return {
        name: {
            "cases": len(group),
            "unsupported_show_rate": round(unsupported_show_rate(group), 4),
            "over_abstention_rate": round(over_abstention_rate(group), 4),
            "decision_coverage": round(decision_coverage(group), 4),
        }
        for name, group in sorted(groups.items())
    }


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100.0
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)
