"""The corruption harness itself: determinism, no label leakage, benign cases benign."""
from __future__ import annotations

import json

import pytest

from engine.corruptions import SEVERITIES, apply, describe, names
from engine.engine import evaluate
from engine.measure import measure
from engine.policies import get_policy
from engine.schemas import Decision
from engine.synth import GENERATORS, GroundTruth, clean_case

CLAIM_TYPES = sorted(GENERATORS)


def test_every_corruption_has_a_rationale_and_a_dimension():
    """PROJECT_BRIEF.md section 12: every corruption needs a documented rationale."""
    for spec in describe():
        assert len(spec.rationale) > 30, f"{spec.name} has a thin rationale"
        assert spec.dimension in {
            "coverage", "freshness", "signal_quality", "consistency", "baseline", "integrity",
        }


def test_the_catalogue_covers_every_failure_mode_the_brief_names():
    catalogue = set(names())
    required = {
        "drop_contiguous", "drop_random",          # contiguous and random missingness
        "shorten_wear",                            # shortened wear duration
        "delay_sync", "stale_data",                # delayed sync, stale timestamps
        "motion_artifact",                         # motion-contaminated measurement
        "dropout_flags", "flatline",               # sensor dropout and flatlining
        "implausible_spike",                       # implausible spikes
        "conflict_summary_detail",                 # conflicting summaries
        "truncate_baseline", "destabilize_baseline",  # insufficient / unstable baselines
        "drop_timezone", "shift_day_boundary",     # timezone and day-boundary errors
        "duplicate_samples", "shuffle_order",      # duplicated and out-of-order samples
    }
    assert required <= catalogue, required - catalogue


@pytest.mark.parametrize("claim_type", CLAIM_TYPES)
def test_corruptions_are_deterministic(claim_type):
    base = clean_case(claim_type, 3)
    for name in names():
        for severity in SEVERITIES:
            first = apply(base, name, severity, seed=3)
            second = apply(base, name, severity, seed=3)
            assert first.truth is second.truth
            assert first.breaches == second.breaches
            assert first.request.model_dump(mode="json") == second.request.model_dump(mode="json")


@pytest.mark.parametrize("claim_type", CLAIM_TYPES)
def test_corruptions_do_not_mutate_the_original_case(claim_type):
    base = clean_case(claim_type, 1)
    before = base.request.model_dump(mode="json")
    for name in names():
        apply(base, name, "severe", seed=1)
    assert base.request.model_dump(mode="json") == before


# Words that would reveal how a case was built rather than what its evidence contains.
# Note that a *physical* term a corruption is named after -- "flatline" -- is legitimate
# in a feature name, because a stuck sensor exists independently of this harness. What
# must never appear is the harness's own bookkeeping: severities, ground-truth labels,
# and whether a corruption was applied at all.
LEAKY_TOKENS = (
    set(SEVERITIES)
    | {t.value.lower() for t in GroundTruth}
    | {"corrupt", "ground_truth", "truth_reason", "manifest", "supportable"}
)


def test_the_harness_bookkeeping_never_leaks_into_the_decision():
    """docs/evaluation-protocol.md section 6: corruption labels must not become features."""
    for claim_type in CLAIM_TYPES:
        base = clean_case(claim_type, 0)
        for name in names():
            case = apply(base, name, "severe", seed=0)
            blob = json.dumps(evaluate(case.request).model_dump(mode="json")).lower()
            for token in LEAKY_TOKENS:
                assert token not in blob, f"{claim_type}/{name}: the decision leaked {token!r}"


def test_the_trace_has_the_same_shape_whatever_was_corrupted():
    """A corruption may change the numbers in the trace; it may not change its structure.

    If the feature set itself varied by corruption, a learned model could read the
    presence of a field as a hint about which failure was injected.
    """

    def shape(node):
        if isinstance(node, dict):
            return {k: shape(v) for k, v in sorted(node.items())}
        if isinstance(node, list):
            return "list"
        return type(node).__name__ if node is not None else "none"

    base = clean_case("RESTING_HEART_RATE_ELEVATED", 0)
    reference = None
    for name in names():
        for severity in SEVERITIES:
            case = apply(base, name, severity, seed=0)
            keys = shape(evaluate(case.request).trace.features)
            flattened = json.dumps(_keys_only(keys), sort_keys=True)
            if reference is None:
                reference = flattened
            assert flattened == reference, f"{name}/{severity} changed the feature shape"


def _keys_only(node):
    if isinstance(node, dict):
        return {k: _keys_only(v) for k, v in node.items()}
    return None


def test_feature_names_are_not_taken_from_the_harness():
    response = evaluate(clean_case("RESTING_HEART_RATE_ELEVATED", 0).request)

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key.lower() not in LEAKY_TOKENS, f"feature {path}{key} leaks the harness"
                walk(value, f"{path}{key}.")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    walk(response.trace.features)


@pytest.mark.parametrize("severity", list(SEVERITIES))
@pytest.mark.parametrize("claim_type", CLAIM_TYPES)
def test_benign_corruptions_keep_the_decision(claim_type, severity):
    """Duplication and reordering must be absorbed, not answered with an abstention."""
    for supportable in (True, False):
        base = clean_case(claim_type, 0, supportable=supportable)
        expected = evaluate(base.request).decision
        for name in ("duplicate_samples", "shuffle_order"):
            case = apply(base, name, severity, seed=0)
            assert evaluate(case.request).decision is expected, (
                f"{claim_type}/{name}/{severity} changed the decision"
            )


@pytest.mark.parametrize("claim_type", CLAIM_TYPES)
def test_benign_corruptions_do_not_change_what_the_evidence_breaches(claim_type):
    """Deduplication and sorting must leave the measured evidence exactly as it was.

    Stated relative to the clean base rather than as "breaches nothing", because the
    inverted recovery claim's clean base already breaches by design: inadequate inputs
    are what it asserts.
    """
    policy = get_policy(claim_type)
    assert policy is not None
    base = clean_case(claim_type, 0)
    reference = measure(base.request, policy).breaches
    for name in ("duplicate_samples", "shuffle_order"):
        for severity in SEVERITIES:
            case = apply(base, name, severity, seed=0)
            assert case.breaches == reference, f"{claim_type}/{name}/{severity}: {case.breaches}"


def test_severity_increases_the_measured_damage():
    """A 'severe' corruption must actually be worse than a 'mild' one, or it is mislabelled."""
    base = clean_case("RESTING_HEART_RATE_ELEVATED", 0)
    policy = get_policy("RESTING_HEART_RATE_ELEVATED")
    assert policy is not None
    mild = measure(apply(base, "shorten_wear", "mild", seed=0).request, policy)
    severe = measure(apply(base, "shorten_wear", "severe", seed=0).request, policy)
    assert severe.wear_ratio is not None and mild.wear_ratio is not None
    assert severe.wear_ratio < mild.wear_ratio


def test_the_labeller_agrees_with_itself_about_clean_cases():
    for claim_type in CLAIM_TYPES:
        policy = get_policy(claim_type)
        assert policy is not None
        clean = clean_case(claim_type, 0, supportable=True)
        assert measure(clean.request, policy).breaches == [] or policy.mode == "insufficiency"


def test_an_unknown_corruption_or_severity_is_an_error():
    base = clean_case("RESTING_HEART_RATE_ELEVATED", 0)
    with pytest.raises(KeyError):
        apply(base, "make_it_worse", "mild", seed=0)
    with pytest.raises(ValueError):
        apply(base, "shorten_wear", "catastrophic", seed=0)  # type: ignore[arg-type]
