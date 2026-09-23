"""Deterministic construction of the synthetic evaluation suite.

The suite is a function of the seed range alone, so any two runs on any two machines
produce byte-identical cases. That is what makes the manifest a reproducible artifact
rather than a log.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.corruptions import SEVERITIES, apply, names
from engine.synth import GENERATORS, GroundTruth, SyntheticCase, clean_case


@dataclass
class Suite:
    cases: list[SyntheticCase]
    seeds: list[int]

    @property
    def scored(self) -> list[SyntheticCase]:
        return [c for c in self.cases if c.scored]

    @property
    def excluded(self) -> list[SyntheticCase]:
        return [c for c in self.cases if not c.scored]

    def summary(self) -> dict:
        by_truth: dict[str, int] = {}
        for c in self.cases:
            by_truth[c.truth.value] = by_truth.get(c.truth.value, 0) + 1
        return {
            "total_cases": len(self.cases),
            "scored_cases": len(self.scored),
            "excluded_borderline": len(self.excluded),
            "seeds": self.seeds,
            "claim_types": sorted(GENERATORS),
            "corruptions": names(),
            "severities": list(SEVERITIES),
            "cases_by_ground_truth": dict(sorted(by_truth.items())),
            "subjects": len({c.request.subject_id for c in self.cases}),
        }


def build(seeds: int = 5) -> Suite:
    """Clean and corrupted cases for every claim type, over `seeds` distinct subjects."""
    cases: list[SyntheticCase] = []
    seed_list = list(range(seeds))
    for claim_type in sorted(GENERATORS):
        for seed in seed_list:
            bases = [
                clean_case(claim_type, seed, supportable=True),
                clean_case(claim_type, seed, supportable=False),
            ]
            cases.extend(bases)
            for base in bases:
                for corruption in names():
                    for severity in SEVERITIES:
                        cases.append(apply(base, corruption, severity, seed=seed))
    return Suite(cases=cases, seeds=seed_list)


# Which single decision is *correct* for each ground-truth state, used for macro F1.
# The acceptable-but-not-ideal decisions in docs/evaluation-protocol.md section 2 are
# handled separately by the unsupported-show rate and the over-abstention rate; F1 needs
# one reference label per case.
REFERENCE_DECISION = {
    GroundTruth.SUPPORTABLE: "SHOW",
    GroundTruth.UNSUPPORTABLE_INSUFFICIENT: "WAIT_FOR_MORE_DATA",
    GroundTruth.UNSUPPORTABLE_UNTRUSTWORTHY: "WAIT_FOR_MORE_DATA",
    GroundTruth.UNSUPPORTABLE_CONTRADICTED: "REJECT",
}
