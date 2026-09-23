"""Scoring primitives, including the invariant that makes the five scores comparable."""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.scoring import (
    band_score,
    clamp,
    duration_hours,
    interpolate,
    inverse_band_score,
    iso_duration,
    longest_duration,
    percentile,
    sample_sd,
)


def test_band_score_anchors():
    """The whole convention: 0.5 at the policy floor, 1.0 at the comfortable level."""
    assert band_score(0.60, 0.60, 0.90) == pytest.approx(0.5)
    assert band_score(0.90, 0.60, 0.90) == pytest.approx(1.0)
    assert band_score(0.75, 0.60, 0.90) == pytest.approx(0.75)
    assert band_score(0.30, 0.60, 0.90) == pytest.approx(0.0)


def test_inverse_band_score_anchors():
    assert inverse_band_score(36, 36, 6) == pytest.approx(0.5)
    assert inverse_band_score(6, 36, 6) == pytest.approx(1.0)
    assert inverse_band_score(1, 36, 6) == pytest.approx(1.0)
    assert inverse_band_score(66, 36, 6) == pytest.approx(0.0)


def test_band_score_degenerate_span():
    assert band_score(5, 5, 5) == 1.0
    assert band_score(4, 5, 5) == 0.0


@given(
    value=st.floats(-1e4, 1e4, allow_nan=False),
    minimum=st.floats(-1e3, 1e3, allow_nan=False),
    span=st.floats(0.001, 1e3, allow_nan=False),
)
def test_band_score_is_in_range_and_monotonic(value, minimum, span):
    good = minimum + span
    low = band_score(value, minimum, good)
    high = band_score(value + span / 10, minimum, good)
    assert 0.0 <= low <= 1.0
    assert high >= low


@given(values=st.lists(st.floats(-1e3, 1e3, allow_nan=False), min_size=1, max_size=40))
def test_percentile_is_within_the_data(values):
    for q in (0, 10, 50, 90, 100):
        assert min(values) <= percentile(values, q) <= max(values)


def test_sample_sd_uses_n_minus_one():
    assert sample_sd([2, 4]) == pytest.approx(1.4142135, abs=1e-6)
    assert sample_sd([5]) == 0.0
    assert sample_sd([]) == 0.0


def test_interpolate_clamps_at_the_ends():
    anchors = [(0.0, 0.1), (1.0, 0.9)]
    assert interpolate(-5, anchors) == 0.1
    assert interpolate(5, anchors) == 0.9
    assert interpolate(0.5, anchors) == pytest.approx(0.5)


@pytest.mark.parametrize(
    "hours,iso", [(168, "P7D"), (24, "P1D"), (6, "PT6H"), (12, "PT12H"), (0.5, "PT30M")]
)
def test_iso_duration_roundtrip(hours, iso):
    assert iso_duration(hours) == iso
    assert duration_hours(iso) == pytest.approx(hours)


def test_longest_duration_picks_the_longest():
    assert longest_duration(["PT6H", "P7D", "PT12H"]) == "P7D"
    assert longest_duration([]) is None
    assert longest_duration([None, "PT1H"]) == "PT1H"


def test_duration_hours_rejects_nonsense():
    with pytest.raises(ValueError):
        duration_hours("7 days")


def test_clamp():
    assert clamp(-1) == 0.0
    assert clamp(2) == 1.0
    assert clamp(0.5) == 0.5
