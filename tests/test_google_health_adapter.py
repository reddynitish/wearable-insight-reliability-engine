"""The Google Health adapter, against synthetic fixtures only.

No personal export is read here, and none is committed. tests/fixtures/google_health/
holds Google-Health-shaped JSON with invented values, including two deliberately awkward
files so the adapter's "did not convert" reporting is exercised rather than assumed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import ast
from pathlib import Path

import pytest

from engine.adapters import google_health as gh
from engine.engine import evaluate
from engine.schemas import Claim, EvaluationContext, EvaluationRequest, TimeWindow
from engine.signals import Signal

U = timezone.utc
FIXTURES = Path(__file__).parent / "fixtures" / "google_health"
DAY_END = datetime(2026, 9, 23, tzinfo=U)
WINDOW = TimeWindow(start=DAY_END - timedelta(days=1), end=DAY_END)


@pytest.fixture(scope="module")
def loaded():
    return gh.load_directory(FIXTURES, device="fitbit-air")


def test_the_adapter_imports_no_credential_or_network_machinery():
    """The engine must be structurally unable to reach an OAuth secret or the network.

    Checked against the module's import graph rather than its text, so the docstring can
    name the things it deliberately avoids.
    """
    import ast

    tree = ast.parse(Path(gh.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"requests", "urllib", "urllib3", "http", "httpx", "socket", "gh_auth", "os"}
    assert not (imported & forbidden), imported & forbidden

    # And no code path constructs a path to a secret file.
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and not _is_docstring(tree, node)
    }
    for secret in ("credentials.json", "token.json", "Authorization", "Bearer"):
        assert secret not in literals


def _is_docstring(tree, node) -> bool:
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = getattr(parent, "body", [])
            if body and isinstance(body[0], ast.Expr) and body[0].value is node:
                return True
    return False


def test_conversion_finds_the_expected_signals(loaded):
    observations, report = loaded
    signals = {o.signal for o in observations}
    assert Signal.RESTING_HEART_RATE in signals
    assert Signal.HEART_RATE in signals
    assert Signal.STEPS in signals
    assert Signal.ACTIVE_MINUTES in signals
    assert Signal.SLEEP_DURATION in signals
    assert Signal.SLEEP_EFFICIENCY in signals
    assert report.mapped == len(observations)


def test_unmapped_data_types_and_values_are_reported_not_guessed(loaded):
    """A point whose numeric key the adapter does not recognise must not become a number."""
    _, report = loaded
    assert report.unmapped_data_type.get("vo2-max") == 1
    assert report.unmapped_value.get("oxygen-saturation") == 1
    assert report.clean is False


def test_the_report_records_which_key_each_value_came_from(loaded):
    _, report = loaded
    assert report.value_keys_used["daily-resting-heart-rate"] == "beatsPerMinute"
    assert report.value_keys_used["steps"] == "count"
    assert report.value_keys_used["sleep"] == "minutesAsleep"


def test_every_converted_observation_is_timezone_aware_and_in_range(loaded):
    observations, _ = loaded
    from engine.signals import is_plausible

    for obs in observations:
        assert obs.measured_at.tzinfo is not None
        assert is_plausible(obs.signal, obs.value)


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("steps-20260922-214530.json", "steps"),
        ("daily-resting-heart-rate-20260922-214530.json", "daily-resting-heart-rate"),
        ("rollup-heart-rate-60s-20260922-214530.json", "heart-rate"),
        ("sleep-20260922-214530.json", "sleep"),
    ],
)
def test_data_type_is_recovered_from_the_filename(filename, expected):
    assert gh.data_type_from_filename(Path(filename)) == expected


def test_out_of_range_values_are_counted_not_passed_through():
    points = [
        {
            "daily_resting_heart_rate": {
                "interval": {"startTime": "2026-09-22T00:00:00Z", "endTime": "2026-09-23T00:00:00Z"},
                "beatsPerMinute": 400,
            }
        }
    ]
    observations, report = gh.observations_from_points("daily-resting-heart-rate", points)
    assert observations == []
    assert report.out_of_range["daily-resting-heart-rate"] == 1


def test_a_point_with_no_interval_is_skipped():
    points = [{"steps": {"count": 1000}}]
    observations, report = gh.observations_from_points("steps", points)
    assert observations == []
    assert report.missing_interval["steps"] == 1


def test_snake_case_and_hyphenated_payload_keys_both_work():
    interval = {"start_time": "2026-09-22T00:00:00Z", "end_time": "2026-09-23T00:00:00Z"}
    for key in ("daily_resting_heart_rate", "daily-resting-heart-rate", "value"):
        points = [{key: {"interval": interval, "beatsPerMinute": 60}}]
        observations, _ = gh.observations_from_points("daily-resting-heart-rate", points)
        assert len(observations) == 1, key
        assert observations[0].value == 60.0


def test_sleep_falls_back_to_the_interval_and_flags_it():
    """In-bed time is not time asleep, so the fallback is marked unvalidated."""
    points = [
        {
            "sleep": {
                "interval": {"startTime": "2026-09-22T23:00:00Z", "endTime": "2026-09-23T06:30:00Z"}
            }
        }
    ]
    observations, report = gh.observations_from_points("sleep", points)
    duration = next(o for o in observations if o.signal is Signal.SLEEP_DURATION)
    assert duration.value == pytest.approx(450.0)
    assert duration.quality_flags
    assert report.value_keys_used["sleep"] == "interval"


def test_baseline_excludes_the_target_day(loaded):
    """Today must not help define what normal is; that would shrink its own deviation."""
    observations, _ = loaded
    baseline = gh.baseline_from_observations(observations, Signal.RESTING_HEART_RATE, WINDOW)
    assert baseline
    assert all(sample.day < WINDOW.start for sample in baseline)


def test_daily_values_collapse_intraday_samples(loaded):
    observations, _ = loaded
    days = gh.daily_values(observations, Signal.HEART_RATE)
    assert len(days) >= 1
    for value in days.values():
        assert 40 <= value <= 160


def test_window_observations_keeps_post_window_aggregates():
    from engine.schemas import Observation

    summary = Observation(
        signal=Signal.RESTING_HEART_RATE,
        value=60,
        measured_at=WINDOW.end + timedelta(hours=6),
    )
    outside = Observation(
        signal=Signal.RESTING_HEART_RATE,
        value=60,
        measured_at=WINDOW.end + timedelta(days=3),
    )
    kept = gh.window_observations([summary, outside], WINDOW)
    assert kept == [summary]


def test_an_end_to_end_decision_can_be_built_from_an_export(loaded):
    """The whole point of the adapter: a real-shaped export produces a typed decision."""
    observations, _ = loaded
    in_window = gh.window_observations(observations, WINDOW)
    baseline = gh.baseline_from_observations(observations, Signal.RESTING_HEART_RATE, WINDOW)
    request = EvaluationRequest(
        subject_id="fixture-owner-001",
        claim=Claim(type="RESTING_HEART_RATE_ELEVATED", target_window=WINDOW),
        observations=in_window,
        baseline=baseline,
        context=EvaluationContext(timezone="UTC", device="fitbit-air"),
        evaluated_at=WINDOW.end + timedelta(hours=9),
    )
    response = evaluate(request)
    assert response.decision.value in {
        "SHOW", "SHOW_WITH_WARNING", "WAIT_FOR_MORE_DATA", "REJECT"
    }
    assert response.trace.features["baseline"]["valid_days"] >= 14


def test_an_export_without_wear_time_or_sync_is_disclosed_never_assumed():
    """Google Health exports carry neither, and the engine must say so rather than guess."""
    observations, _ = gh.load_directory(FIXTURES)
    request = EvaluationRequest(
        subject_id="fixture-owner-001",
        claim=Claim(type="RESTING_HEART_RATE_ELEVATED", target_window=WINDOW),
        observations=gh.window_observations(observations, WINDOW),
        baseline=gh.baseline_from_observations(observations, Signal.RESTING_HEART_RATE, WINDOW),
        context=EvaluationContext(timezone="UTC"),
        evaluated_at=WINDOW.end + timedelta(hours=9),
    )
    codes = {c.value for c in evaluate(request).reason_codes}
    assert "COVERAGE_UNKNOWN" in codes
    assert "SYNC_STATE_UNKNOWN" in codes


def test_fixtures_contain_no_real_identifiers():
    """Guard against a personal export being dropped into the fixture directory."""
    for path in FIXTURES.glob("*.json"):
        text = path.read_text()
        assert "@" not in text
        assert "access_token" not in text
        assert "Bearer" not in text
        json.loads(text)
