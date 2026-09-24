"""The LifeSnaps adapter and the external-validation analysis.

LifeSnaps exists in this project for one purpose: to test whether the PMData-derived
thresholds generalise. Tests that need the 615 MB download skip without it; the statistical
hygiene tests always run, because those are the ones that would let a sampling artefact be
published as a fairness finding.
"""
from __future__ import annotations

import csv
from datetime import timezone
from pathlib import Path

import pytest

from engine.adapters import lifesnaps
from engine.engine import evaluate
from engine.schemas import Decision
from engine.signals import Signal
from eval import lifesnaps_eval

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "data" / "datasets" / "lifesnaps"
needs_lifesnaps = pytest.mark.skipif(
    not (ROOT / lifesnaps.DAILY_CSV).is_file(),
    reason="LifeSnaps absent; see data/DATASETS.md for the download",
)

HEADER = [
    "id", "date", "resting_hr", "sleep_duration", "minutesAsleep", "sleep_efficiency",
    "very_active_minutes", "moderately_active_minutes", "lightly_active_minutes",
    "sedentary_minutes", "steps", "age", "gender", "bmi",
]


@pytest.fixture
def fake_csv(tmp_path: Path) -> Path:
    path = tmp_path / "daily.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for day in range(1, 41):
            writer.writerow([
                "abcdef0123456789", f"2021-05-{day:02d}", 60 + (day % 4) * 0.5,
                31260000.0, 430 + day, 93, 20, 15, 150, 700, 8000, "<30", "FEMALE", "21.0",
            ])
    return path


def test_load_reads_one_subject_per_participant(fake_csv):
    subjects = lifesnaps.load(fake_csv)
    assert len(subjects) == 1
    assert subjects[0].demographics == {
        "gender": "FEMALE", "age_band": "<30", "bmi_band": "21.0"
    }


def test_the_raw_participant_token_never_reaches_the_subject_id(fake_csv):
    """Decision traces carry subject_id, so it must not be the dataset's raw token."""
    subject = lifesnaps.load(fake_csv)[0]
    assert "abcdef0123456789" not in subject.subject_id
    assert subject.subject_id.startswith("lifesnaps-")


def test_time_in_bed_is_converted_from_milliseconds(fake_csv):
    subject = lifesnaps.load(fake_csv)[0]
    day = subject.days()[0]
    # 31,260,000 ms is 521 minutes, not 31 million of anything.
    assert subject.daily[day]["time_in_bed"] == pytest.approx(521.0)


def test_wear_minutes_are_clamped_to_a_day(tmp_path):
    """The activity buckets can sum past 1440, so the proxy has to be bounded."""
    path = tmp_path / "daily.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerow(["x" * 16, "2021-05-01", 60, 31260000.0, 430, 93,
                         600, 600, 600, 600, 8000, "<30", "MALE", "21.0"])
    subject = lifesnaps.load(path)[0]
    assert subject.daily["2021-05-01"]["wear_minutes"] == 1440.0


def test_a_zero_resting_heart_rate_is_dropped(tmp_path):
    path = tmp_path / "daily.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerow(["y" * 16, "2021-05-01", 0, 31260000.0, 430, 93,
                         20, 15, 150, 700, 8000, "<30", "MALE", "21.0"])
    subject = lifesnaps.load(path)[0]
    assert "resting_heart_rate" not in subject.daily.get("2021-05-01", {})


def test_requests_are_decidable_and_timezone_aware(fake_csv):
    subject = lifesnaps.load(fake_csv)[0]
    request = subject.resting_heart_rate_request(subject.days()[-1], assume_sync=True)
    assert request is not None
    assert request.claim.target_window.start.tzinfo is not None
    assert evaluate(request).decision in Decision


def test_the_baseline_excludes_the_target_day(fake_csv):
    subject = lifesnaps.load(fake_csv)[0]
    day = subject.days()[-1]
    request = subject.resting_heart_rate_request(day, assume_sync=True)
    assert request is not None and request.baseline
    for sample in request.baseline:
        assert sample.day.astimezone(subject.tz).date().isoformat() < day


def test_the_citation_travels_with_the_adapter():
    assert "10.5281/zenodo.7229547" in lifesnaps.CITATION
    assert "CC BY 4.0" in lifesnaps.CITATION


# --------------------------------------------------------------------- statistics


@pytest.mark.parametrize(
    "raw,expected",
    [("21.0", "under 25"), ("<19", "under 25"), ("25.0", "25 or over"),
     (">=30", "25 or over"), (">=19", "under 25"), ("", "unknown"), ("junk", "unknown")],
)
def test_bmi_bands_collapse_the_sources_mixed_encoding(raw, expected):
    """The column mixes bare numbers and bands; groups have to be comparable."""
    assert lifesnaps_eval._bmi_band(raw) == expected


def test_a_difference_that_is_noise_is_reported_as_not_distinguishable():
    """The guard against publishing a sampling artefact as a fairness finding.

    Two samples drawn from the same distribution must not come back distinguishable.
    """
    import random

    rng = random.Random(1)
    a = [rng.gauss(0.07, 0.05) for _ in range(41)]
    b = [rng.gauss(0.07, 0.05) for _ in range(26)]
    result = lifesnaps_eval._bootstrap_difference(a, b, resamples=2000)
    assert result["distinguishable_from_zero"] is False
    assert result["ci95_low"] <= 0 <= result["ci95_high"]


def test_a_real_difference_is_detected():
    import random

    rng = random.Random(2)
    a = [rng.gauss(0.30, 0.03) for _ in range(40)]
    b = [rng.gauss(0.05, 0.03) for _ in range(40)]
    result = lifesnaps_eval._bootstrap_difference(a, b, resamples=2000)
    assert result["distinguishable_from_zero"] is True
    assert result["point"] > 0.2


def test_a_group_too_small_to_compare_returns_no_verdict():
    assert lifesnaps_eval._bootstrap_difference([0.1], [0.2, 0.3])["point"] is None


# --------------------------------------------------------------------- real data


@needs_lifesnaps
def test_the_real_dataset_loads_seventy_one_participants():
    subjects = lifesnaps.load(ROOT)
    assert len(subjects) == 71
    assert sum(len(s.days()) for s in subjects) > 5000


@needs_lifesnaps
def test_the_gates_hold_on_the_second_cohort():
    """The safety property must survive a cohort the thresholds were not tuned on."""
    report = lifesnaps_eval.corruption_report(lifesnaps.load(ROOT)[:12], limit=3)
    assert report["corrupted_cases"] > 50
    assert report["unsupported_show_rate"] == 0.0
    assert report["benign_decision_changes"] == 0
