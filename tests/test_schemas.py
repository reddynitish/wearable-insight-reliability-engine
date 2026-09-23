"""The request contract must fail loudly at the boundary, not quietly downstream."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from engine.schemas import (
    Claim,
    EvaluationRequest,
    Observation,
    TimeWindow,
)
from engine.signals import Signal

U = timezone.utc
W = TimeWindow(start=datetime(2026, 9, 22, tzinfo=U), end=datetime(2026, 9, 23, tzinfo=U))


def test_naive_timestamps_are_rejected():
    """A naive timestamp assumes a timezone, and that is the bug class this engine exists for."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        Observation(signal=Signal.STEPS, value=1, measured_at=datetime(2026, 9, 22, 12))


def test_timestamps_are_normalised_to_utc():
    eastern = timezone(timedelta(hours=-4))
    obs = Observation(
        signal=Signal.STEPS, value=1, measured_at=datetime(2026, 9, 22, 8, tzinfo=eastern)
    )
    assert obs.measured_at == datetime(2026, 9, 22, 12, tzinfo=U)


def test_window_must_be_ordered():
    with pytest.raises(ValidationError, match="strictly after"):
        TimeWindow(start=datetime(2026, 9, 23, tzinfo=U), end=datetime(2026, 9, 22, tzinfo=U))
    with pytest.raises(ValidationError):
        TimeWindow(start=datetime(2026, 9, 23, tzinfo=U), end=datetime(2026, 9, 23, tzinfo=U))


def test_window_containment_is_half_open():
    assert W.contains(W.start)
    assert not W.contains(W.end)


@pytest.mark.parametrize(
    "bad",
    ["someone@example.com", "+1 555 010 0100", "ab", "", "a" * 65, "has space"],
)
def test_personal_or_malformed_subject_ids_are_rejected(bad):
    with pytest.raises(ValidationError):
        EvaluationRequest(
            subject_id=bad, claim=Claim(type="RESTING_HEART_RATE_ELEVATED", target_window=W)
        )


@pytest.mark.parametrize("good", ["demo-user-001", "synth-rhr-0001", "ppg-dalia:s3", "abc"])
def test_pseudonymous_subject_ids_are_accepted(good):
    request = EvaluationRequest(
        subject_id=good, claim=Claim(type="RESTING_HEART_RATE_ELEVATED", target_window=W)
    )
    assert request.subject_id == good


def test_unknown_fields_are_rejected():
    """Silently ignoring an unknown field lets a caller think evidence was submitted."""
    with pytest.raises(ValidationError):
        Observation(
            signal=Signal.STEPS,
            value=1,
            measured_at=datetime(2026, 9, 22, 12, tzinfo=U),
            confidence=0.9,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_rejected(value):
    with pytest.raises(ValidationError, match="finite"):
        Observation(signal=Signal.STEPS, value=value, measured_at=datetime(2026, 9, 22, tzinfo=U))


def test_unknown_claim_type_passes_validation():
    """It must reach the engine, which answers with a typed REJECT rather than a 4xx."""
    request = EvaluationRequest(
        subject_id="demo-001", claim=Claim(type="YOU_ARE_STRESSED", target_window=W)
    )
    assert request.claim.type == "YOU_ARE_STRESSED"


def test_requests_are_immutable():
    request = EvaluationRequest(
        subject_id="demo-001", claim=Claim(type="RESTING_HEART_RATE_ELEVATED", target_window=W)
    )
    with pytest.raises(ValidationError):
        request.subject_id = "someone-else"
