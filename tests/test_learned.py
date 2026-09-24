"""The learned baselines B3/B4 and the table they are trained on.

The real dataset is gitignored, so the tests that need it skip without the cache. The
split-hygiene tests run on synthetic groups and always execute -- those are the ones that
would silently invalidate the whole experiment if they broke.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from eval import learned
from eval.pmdata_dataset import CLAIMS, FEATURE_NAMES

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "data" / "datasets" / "pmdata_cache"
needs_pmdata = pytest.mark.skipif(
    not CACHE.is_dir() or not list(CACHE.glob("p*.json")),
    reason="PMData cache absent; run eval.pmdata_prepare",
)


def test_features_contain_no_band_scores():
    """A band score is the threshold comparison already done; handing one over is cheating.

    The whole experiment asks whether a model can learn where the contract's boundaries
    are. A feature named *_score would contain the answer.
    """
    for name in FEATURE_NAMES:
        assert not name.endswith("_score"), name
    assert "coverage_score" not in FEATURE_NAMES
    assert "baseline_maturity_score" not in FEATURE_NAMES


def test_features_do_not_name_a_corruption():
    from engine.corruptions import names

    for name in FEATURE_NAMES:
        assert name not in names(), name


def test_expected_calibration_error_is_zero_for_a_perfect_forecaster():
    y = np.array([0, 0, 1, 1])
    p = np.array([0.0, 0.0, 1.0, 1.0])
    assert learned.expected_calibration_error(y, p) == pytest.approx(0.0, abs=1e-9)


def test_expected_calibration_error_is_one_for_a_confidently_wrong_forecaster():
    y = np.array([0, 0, 1, 1])
    p = np.array([1.0, 1.0, 0.0, 0.0])
    assert learned.expected_calibration_error(y, p) == pytest.approx(1.0, abs=1e-9)


def test_risk_coverage_is_monotonic_in_the_cut():
    rng = np.random.default_rng(0)
    p = rng.random(500)
    y = (p + rng.normal(0, 0.2, 500) > 0.5).astype(int)
    points = learned.risk_coverage(y, p, np.linspace(0.05, 0.95, 19))
    coverages = [pt["coverage"] for pt in points]
    assert coverages == sorted(coverages, reverse=True)


def test_coverage_at_matched_risk_never_exceeds_the_risk_budget():
    points = [
        {"cut": 0.1, "coverage": 0.9, "unsupported_show_rate": 0.30},
        {"cut": 0.5, "coverage": 0.5, "unsupported_show_rate": 0.05},
        {"cut": 0.9, "coverage": 0.1, "unsupported_show_rate": 0.00},
    ]
    assert learned.coverage_at_matched_risk(points, 0.05) == 0.5
    assert learned.coverage_at_matched_risk(points, 0.0) == 0.1
    # No operating point meets an impossible budget.
    assert learned.coverage_at_matched_risk([{"cut": 0.1, "coverage": 0.9,
                                              "unsupported_show_rate": 0.3}], 0.0) == 0.0


def test_group_splits_never_share_a_subject():
    """Windows from one person share a baseline and a device. Mixing them is memorisation."""
    from sklearn.model_selection import GroupKFold

    groups = np.array([f"s{i // 10}" for i in range(160)])
    X = np.zeros((160, 3))
    y = (np.arange(160) % 5 == 0).astype(int)
    for train_idx, test_idx in GroupKFold(n_splits=4).split(X, y, groups):
        assert not (set(groups[train_idx]) & set(groups[test_idx]))


def test_the_verdict_demands_a_real_margin():
    """A model that merely ties must not be recorded as beating the rules engine."""
    tie = {
        "b2_rules_engine": {"coverage": 0.02, "unsupported_show_rate": 0.0},
        "models": {"m": {"coverage_at_engine_risk": 0.0201}},
    }
    assert learned.verdict(tie)["learned_component_beats_rules"] is False

    win = {
        "b2_rules_engine": {"coverage": 0.02, "unsupported_show_rate": 0.0},
        "models": {"m": {"coverage_at_engine_risk": 0.08}},
    }
    result = learned.verdict(win)
    assert result["learned_component_beats_rules"] is True
    assert "Adopt" in result["decision"]


@needs_pmdata
def test_the_table_carries_both_targets_and_the_engine_decision():
    from eval.pmdata_dataset import build

    rows = build(CACHE, max_days_per_subject=2)
    assert rows
    assert {r.claim_type for r in rows} <= set(CLAIMS)
    for row in rows[:50]:
        assert row.adequate in (0, 1)
        assert row.label in (0, 1)
        assert row.engine_shows in (0, 1)
        # A supportable claim requires adequate evidence; the converse need not hold.
        if row.label == 1:
            assert row.adequate == 1


@needs_pmdata
def test_a_claim_is_never_supportable_while_the_evidence_breaches_the_contract():
    from eval.pmdata_dataset import build

    for row in build(CACHE, max_days_per_subject=3):
        if row.breaches:
            assert row.label == 0, (row.claim_type, row.breaches)


@needs_pmdata
def test_the_matrix_has_one_column_per_feature_and_no_infinities():
    from eval.pmdata_dataset import build

    X, y, groups, engine_shows, names = learned.to_matrix(build(CACHE, max_days_per_subject=2))
    assert X.shape[1] == len(names) == len(FEATURE_NAMES) + len(CLAIMS)
    assert not np.isinf(X).any()
    assert set(np.unique(y)) <= {0, 1}
    assert len(groups) == X.shape[0] == len(engine_shows)
