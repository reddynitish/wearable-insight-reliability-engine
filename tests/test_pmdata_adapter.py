"""The PMData adapter, tested on synthetic files in PMData's own shape.

The real dataset is 1.4 GB and gitignored, so these tests build tiny fixtures with the
same JSON structure. Tests that need the real data skip when it is absent, so the suite
runs in CI without a 1.4 GB download.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine.adapters import pmdata
from engine.engine import evaluate
from engine.schemas import Decision
from engine.signals import Signal

U = timezone.utc
REPO = Path(__file__).resolve().parent.parent
REAL_CACHE = REPO / "data" / "datasets" / "pmdata_cache"


@pytest.fixture
def fake_participant(tmp_path: Path) -> Path:
    """A participant directory in PMData's exact layout, with invented values."""
    fitbit = tmp_path / "p99" / "fitbit"
    fitbit.mkdir(parents=True)
    (tmp_path / "p99" / "pmsys").mkdir()

    rhr = [
        {
            "dateTime": f"2019-11-{day:02d} 00:00:00",
            "value": {"date": f"11/{day:02d}/19", "value": 58.0 + (day % 5) * 0.4,
                      "error": 6.8},
        }
        for day in range(1, 31)
    ]
    (fitbit / "resting_heart_rate.json").write_text(json.dumps(rhr))

    heart = []
    for hour in range(24):
        for minute in (0, 30):
            heart.append(
                {
                    "dateTime": f"2019-11-20 {hour:02d}:{minute:02d}:05",
                    "value": {"bpm": 55 + (hour * 2) % 40, "confidence": 3},
                }
            )
    (fitbit / "heart_rate.json").write_text(json.dumps(heart))

    steps = [
        {"dateTime": f"2019-11-20 {hour:02d}:00:00", "value": str(hour * 40)}
        for hour in range(24)
    ]
    (fitbit / "steps.json").write_text(json.dumps(steps))

    active = [
        {"dateTime": f"2019-11-{day:02d} 00:00:00", "value": str(30 + day)}
        for day in range(1, 31)
    ]
    (fitbit / "very_active_minutes.json").write_text(json.dumps(active))
    (fitbit / "moderately_active_minutes.json").write_text(json.dumps(active))

    sleep = [
        {
            "logId": 1000 + day,
            "dateOfSleep": f"2019-11-{day:02d}",
            "startTime": f"2019-11-{day - 1:02d} 23:00:00",
            "endTime": f"2019-11-{day:02d}T06:30:00.000",
            "duration": 27000000,
            "minutesAsleep": 400 + day,
            "minutesAwake": 30,
            "timeInBed": 450,
            "efficiency": 92,
            "type": "stages",
        }
        for day in range(2, 30)
    ]
    (fitbit / "sleep.json").write_text(json.dumps(sleep))

    (tmp_path / "p99" / "pmsys" / "wellness.csv").write_text(
        "effective_time_frame,fatigue,mood,readiness,sleep_duration_h,sleep_quality,"
        "soreness,soreness_area,stress\n"
        "2019-11-20T08:00:00.000Z,2,3,5,7,3,2,[1],3\n"
    )
    return tmp_path / "p99"


def test_streaming_reader_matches_a_plain_json_load(tmp_path):
    """The streaming parser exists to avoid loading a 114 MB array; it must not differ."""
    payload = [{"i": i, "nested": {"v": i * 2}, "s": f"x{i}"} for i in range(500)]
    path = tmp_path / "array.json"
    path.write_text(json.dumps(payload))
    assert list(pmdata.stream_json_array(path)) == payload


def test_streaming_reader_handles_small_chunks(tmp_path):
    payload = [{"i": i} for i in range(200)]
    path = tmp_path / "array.json"
    path.write_text(json.dumps(payload))
    assert list(pmdata.stream_json_array(path, chunk_size=17)) == payload


def test_naive_local_timestamps_become_aware_utc(fake_participant):
    summaries = pmdata.summarise_days(fake_participant, "Europe/Oslo")
    assert "2019-11-20" in summaries
    daily = pmdata.load_daily_series(fake_participant, "Europe/Oslo")
    assert "2019-11-01" in daily


def test_wear_time_is_measured_from_samples_not_assumed(fake_participant):
    """48 half-hourly samples means 48 distinct minutes of evidence, not 1440."""
    summaries = pmdata.summarise_days(fake_participant, "Europe/Oslo")
    day = summaries["2019-11-20"]
    assert day.wear_minutes == 48
    assert day.overnight_wear_minutes == 16
    assert day.hr_samples == 48


def test_gaps_are_measured(fake_participant):
    day = pmdata.summarise_days(fake_participant, "Europe/Oslo")["2019-11-20"]
    assert day.max_gap_minutes == pytest.approx(30.0, abs=0.1)


def test_a_zero_resting_heart_rate_is_dropped_not_averaged_in(tmp_path):
    """Fitbit reports 0 for 'no estimate'; treating it as a measurement moves a baseline."""
    fitbit = tmp_path / "p98" / "fitbit"
    fitbit.mkdir(parents=True)
    (fitbit / "resting_heart_rate.json").write_text(json.dumps([
        {"dateTime": "2019-11-01 00:00:00", "value": {"value": 0.0}},
        {"dateTime": "2019-11-02 00:00:00", "value": {"value": 60.0}},
    ]))
    daily = pmdata.load_daily_series(tmp_path / "p98", "Europe/Oslo")
    assert "2019-11-01" not in daily
    assert daily["2019-11-02"]["resting_heart_rate"] == 60.0


def test_sleep_keeps_both_efficiency_definitions(fake_participant):
    records = pmdata.load_sleep(fake_participant, "Europe/Oslo")
    assert records
    record = records[0]
    # Fitbit's own score and the classical ratio disagree; both are carried so the gap
    # can be inspected rather than silently resolved.
    assert record["efficiency"] == 92.0
    assert record["efficiency_computed"] != record["efficiency"]


def test_sleep_periods_carry_their_own_interval(fake_participant):
    record = pmdata.load_sleep(fake_participant, "Europe/Oslo")[0]
    assert record["end"] > record["start"]
    assert record["start"].tzinfo is not None


def test_a_prepared_subject_builds_a_decidable_request(fake_participant, tmp_path):
    from eval.pmdata_prepare import prepare

    cache = tmp_path / "cache"
    prepare(fake_participant.parent, cache, "Europe/Oslo")
    subject = pmdata.PreparedSubject.load(cache / "p99.json")
    request = subject.resting_heart_rate_request("2019-11-20", assume_sync=True)
    assert request is not None
    assert request.subject_id == "pmdata-p99"
    response = evaluate(request)
    assert response.decision in Decision


def test_the_baseline_excludes_the_target_day(fake_participant, tmp_path):
    from eval.pmdata_prepare import prepare

    cache = tmp_path / "cache"
    prepare(fake_participant.parent, cache, "Europe/Oslo")
    subject = pmdata.PreparedSubject.load(cache / "p99.json")
    request = subject.resting_heart_rate_request("2019-11-20", assume_sync=True)
    assert request is not None
    target_day = date(2019, 11, 20)
    for sample in request.baseline:
        assert sample.day.astimezone(subject.tz).date() < target_day


def test_baseline_days_are_invalidated_by_poor_wear(fake_participant, tmp_path):
    """A personal normal built from days the watch was in a drawer is not a normal."""
    from eval.pmdata_prepare import prepare

    cache = tmp_path / "cache"
    prepare(fake_participant.parent, cache, "Europe/Oslo")
    subject = pmdata.PreparedSubject.load(cache / "p99.json")
    request = subject.resting_heart_rate_request("2019-11-20", assume_sync=True)
    assert request is not None
    # The fixture only has heart rate on one day, so every other day has no wear evidence
    # and must therefore be marked invalid rather than silently trusted.
    assert request.baseline
    assert all(not sample.valid for sample in request.baseline)


def test_the_missing_sync_field_is_left_unset_not_invented(fake_participant, tmp_path):
    from eval.pmdata_prepare import prepare

    cache = tmp_path / "cache"
    prepare(fake_participant.parent, cache, "Europe/Oslo")
    subject = pmdata.PreparedSubject.load(cache / "p99.json")
    honest = subject.resting_heart_rate_request("2019-11-20", assume_sync=False)
    assumed = subject.resting_heart_rate_request("2019-11-20", assume_sync=True)
    assert honest is not None and assumed is not None
    assert honest.context.sync_completed_at is None
    assert assumed.context.sync_completed_at is not None
    assert "SYNC_STATE_UNKNOWN" in [c.value for c in evaluate(honest).reason_codes]


def test_the_citation_is_carried_with_the_adapter():
    """CC BY 4.0 requires attribution wherever the data is used."""
    assert "10.1145/3339825.3394926" in pmdata.CITATION
    assert "CC BY 4.0" in pmdata.CITATION


# --------------------------------------------------------------------------- real data

real_data = pytest.mark.skipif(
    not REAL_CACHE.is_dir() or not list(REAL_CACHE.glob("p*.json")),
    reason="PMData cache absent; run eval.pmdata_prepare (needs the 1.4 GB download)",
)


@real_data
def test_the_real_cache_covers_sixteen_subjects():
    subjects = pmdata.load_prepared(REAL_CACHE)
    assert len(subjects) == 16
    assert sum(len(s.day_summaries) for s in subjects) > 2000


@real_data
def test_real_subjects_produce_every_decision_type():
    subjects = pmdata.load_prepared(REAL_CACHE)
    seen = set()
    for subject in subjects[:4]:
        for day in subject.days()[:120]:
            request = subject.request("RESTING_HEART_RATE_ELEVATED", day, assume_sync=True)
            if request is None:
                continue
            seen.add(evaluate(request).decision)
    assert {Decision.SHOW, Decision.WAIT_FOR_MORE_DATA, Decision.REJECT} <= seen


@real_data
def test_the_engine_withholds_corrupted_real_evidence():
    """The safety property, on real measurements rather than Gaussian ones."""
    from engine.corruptions import apply as apply_corruption
    from engine.synth import GroundTruth, SyntheticCase

    subjects = pmdata.load_prepared(REAL_CACHE)
    checked = 0
    for subject in subjects[:3]:
        for day in subject.days():
            if checked >= 10:
                break
            request = subject.request("RESTING_HEART_RATE_ELEVATED", day, assume_sync=True)
            if request is None:
                continue
            base = evaluate(request)
            if base.decision not in (Decision.SHOW, Decision.SHOW_WITH_WARNING):
                continue
            checked += 1
            case = SyntheticCase(
                request=request, truth=GroundTruth.SUPPORTABLE,
                truth_reason="real day", generator="pmdata", seed=0,
            )
            for name in ("shorten_wear", "truncate_baseline", "implausible_spike"):
                corrupted = apply_corruption(case, name, "severe", seed=0)
                decision = evaluate(corrupted.request).decision
                assert decision not in (Decision.SHOW, Decision.SHOW_WITH_WARNING), name
    assert checked > 0
