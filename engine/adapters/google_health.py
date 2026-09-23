"""Adapter from Google Health API data points to the canonical observation model.

Deliberate boundaries:

- **No credentials, ever.** This module reads JSON that `google_health/gh_fetch.py` has
  already written. It does not import `gh_auth`, hold a token, or make a network call, so
  no part of the engine can touch an OAuth secret. The existing authentication flow is
  untouched.
- **No guessing.** Google Health nests each data point's payload under a field named
  after its data type, and the value key differs per type. Where this adapter cannot find
  a numeric value it records the point as unmapped and drops it, rather than inventing
  one. `AdapterReport` carries those counts so a caller can see what did not convert.
- **Field mapping is UNVERIFIED against live payloads.** It was written from the fetch
  client and the API's documented shape, not by inspecting personal health exports. The
  candidate-key lists below are intentionally generous for that reason. Run
  `demo/google_health_demo.py --report` against your own export to confirm, and record
  the result before relying on it.

What the supported API cannot provide, per google_health/README.md and
FITBIT_AIR_RESEARCH.md: raw accelerometer or gyroscope samples, at any tier. Motion
artifact features that need them must come from public datasets.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from engine.schemas import BaselineSample, Observation, QualityFlag, TimeWindow
from engine.signals import Signal, canonical_unit, is_plausible

U = timezone.utc

# Google Health data type -> canonical signal. Types absent here are not used by any
# claim policy and are skipped rather than coerced.
SIGNAL_BY_DATA_TYPE: dict[str, Signal] = {
    "steps": Signal.STEPS,
    "heart-rate": Signal.HEART_RATE,
    "daily-resting-heart-rate": Signal.RESTING_HEART_RATE,
    "resting-heart-rate": Signal.RESTING_HEART_RATE,
    "heart-rate-variability": Signal.HRV_RMSSD,
    "oxygen-saturation": Signal.SPO2,
    "respiratory-rate": Signal.RESPIRATORY_RATE,
    "active-minutes": Signal.ACTIVE_MINUTES,
    "active-zone-minutes": Signal.ACTIVE_MINUTES,
    "active-energy-burned": Signal.ACTIVE_ENERGY,
    "exercise": Signal.EXERCISE_MINUTES,
}

# Candidate numeric keys per signal, tried in order. Generous because the exact key is
# unverified; the first plausible numeric hit wins and the key used is reported.
VALUE_KEYS: dict[Signal, tuple[str, ...]] = {
    Signal.STEPS: ("count", "steps", "value"),
    Signal.HEART_RATE: ("beatsPerMinute", "beats_per_minute", "bpm", "value", "average"),
    Signal.RESTING_HEART_RATE: (
        "beatsPerMinute", "beats_per_minute", "bpm", "restingHeartRate", "value",
    ),
    Signal.HRV_RMSSD: ("rmssd", "rmssdMilliseconds", "milliseconds", "value", "dailyRmssd"),
    Signal.SPO2: ("percentage", "percent", "value", "average", "saturationPercent"),
    Signal.RESPIRATORY_RATE: ("breathsPerMinute", "breaths_per_minute", "value", "average"),
    Signal.ACTIVE_MINUTES: ("minutes", "activeMinutes", "duration", "value", "count"),
    Signal.ACTIVE_ENERGY: ("energy", "kilocalories", "calories", "value"),
    Signal.EXERCISE_MINUTES: ("duration", "activeDuration", "minutes", "value"),
}

SLEEP_DURATION_KEYS = (
    "totalSleepMinutes", "minutesAsleep", "totalMinutesAsleep", "asleepDuration",
    "sleepDuration", "duration",
)
SLEEP_EFFICIENCY_KEYS = ("efficiency", "efficiencyPercent", "sleepEfficiency")


@dataclass
class AdapterReport:
    """What converted, what did not, and why. Printed rather than silently discarded."""

    mapped: int = 0
    unmapped_data_type: dict[str, int] = field(default_factory=dict)
    unmapped_value: dict[str, int] = field(default_factory=dict)
    missing_interval: dict[str, int] = field(default_factory=dict)
    out_of_range: dict[str, int] = field(default_factory=dict)
    value_keys_used: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mapped": self.mapped,
            "unmapped_data_type": dict(sorted(self.unmapped_data_type.items())),
            "unmapped_value": dict(sorted(self.unmapped_value.items())),
            "missing_interval": dict(sorted(self.missing_interval.items())),
            "out_of_range": dict(sorted(self.out_of_range.items())),
            "value_keys_used": dict(sorted(self.value_keys_used.items())),
        }

    @property
    def clean(self) -> bool:
        return not (self.unmapped_value or self.missing_interval or self.unmapped_data_type)


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(U) if parsed.tzinfo else parsed.replace(tzinfo=U)


def _payload(point: dict[str, Any], data_type: str) -> dict[str, Any]:
    """The data-type-specific sub-object of a data point.

    Google Health names it after the data type with underscores; some responses use the
    hyphenated form or nest it under `value`. Falls back to the single dict-valued key
    that is not metadata.
    """
    for candidate in (data_type.replace("-", "_"), data_type, "value"):
        node = point.get(candidate)
        if isinstance(node, dict):
            return node
    dict_keys = [
        k for k, v in point.items()
        if isinstance(v, dict) and k not in {"interval", "metadata", "dataSource"}
    ]
    if len(dict_keys) == 1:
        return point[dict_keys[0]]
    return point


def _interval(point: dict[str, Any], payload: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    for node in (payload.get("interval"), point.get("interval"), payload, point):
        if not isinstance(node, dict):
            continue
        start = _parse_time(node.get("startTime") or node.get("start_time"))
        end = _parse_time(node.get("endTime") or node.get("end_time"))
        if start or end:
            return start, end
    return None, None


def _numeric(node: Any, keys: Iterable[str]) -> tuple[float | None, str | None]:
    """First numeric value found under `keys`, searching one level of nesting."""
    if not isinstance(node, dict):
        return None, None
    for key in keys:
        if key not in node:
            continue
        raw = node[key]
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            return float(raw), key
        if isinstance(raw, str):
            try:
                return float(raw), key
            except ValueError:
                continue
        if isinstance(raw, dict):
            for inner in ("value", "count", "amount", "seconds", "minutes"):
                candidate = raw.get(inner)
                if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                    return float(candidate), f"{key}.{inner}"
    return None, None


def observations_from_points(
    data_type: str,
    points: list[dict[str, Any]],
    source: str = "google_health",
    device: str | None = None,
    report: AdapterReport | None = None,
) -> tuple[list[Observation], AdapterReport]:
    """Convert one data type's data points into canonical observations."""
    report = report or AdapterReport()
    if data_type == "sleep":
        return _sleep_observations(points, source, device, report)

    signal = SIGNAL_BY_DATA_TYPE.get(data_type)
    if signal is None:
        report.unmapped_data_type[data_type] = len(points)
        return [], report

    out: list[Observation] = []
    for point in points:
        payload = _payload(point, data_type)
        start, end = _interval(point, payload)
        if start is None and end is None:
            _bump(report.missing_interval, data_type)
            continue
        value, key = _numeric(payload, VALUE_KEYS[signal])
        if value is None:
            _bump(report.unmapped_value, data_type)
            continue
        if signal is Signal.EXERCISE_MINUTES and key and "duration" in key:
            value = _seconds_to_minutes(value)
        report.value_keys_used[data_type] = key or ""
        if not is_plausible(signal, value):
            _bump(report.out_of_range, data_type)
            continue
        measured_at = end or start
        assert measured_at is not None
        out.append(
            Observation(
                signal=signal,
                value=value,
                unit=canonical_unit(signal),
                measured_at=measured_at,
                source=source,
                device=device,
                window_start=start if (start and end and end > start) else None,
                window_end=end if (start and end and end > start) else None,
            )
        )
        report.mapped += 1
    return out, report


def _seconds_to_minutes(value: float) -> float:
    """A duration reported in seconds becomes minutes; one already in minutes is kept.

    The API's duration fields are seconds, but roll-ups and some exports report minutes.
    A single session over 1440 of anything cannot be minutes, so that is the discriminator.
    """
    return value / 60.0 if value > 1440 else value


def _sleep_observations(
    points: list[dict[str, Any]],
    source: str,
    device: str | None,
    report: AdapterReport,
) -> tuple[list[Observation], AdapterReport]:
    """Sleep yields two canonical signals: total duration and efficiency."""
    out: list[Observation] = []
    for point in points:
        payload = _payload(point, "sleep")
        start, end = _interval(point, payload)
        if start is None and end is None:
            _bump(report.missing_interval, "sleep")
            continue
        measured_at = end or start
        assert measured_at is not None
        duration, key = _numeric(payload, SLEEP_DURATION_KEYS)
        if duration is None and start and end:
            # A sleep period always carries its own interval; total time asleep is not
            # the same as time in bed, so this is recorded as a fallback and flagged.
            duration = (end - start).total_seconds() / 60.0
            key = "interval"
        if duration is None:
            _bump(report.unmapped_value, "sleep")
            continue
        if key and key != "interval" and duration > 1440:
            duration = duration / 60.0
        report.value_keys_used["sleep"] = key or ""
        if is_plausible(Signal.SLEEP_DURATION, duration):
            out.append(
                Observation(
                    signal=Signal.SLEEP_DURATION,
                    value=duration,
                    unit="minutes",
                    measured_at=measured_at,
                    source=source,
                    device=device,
                    window_start=start,
                    window_end=end,
                    # Derived from the in-bed interval rather than reported as time
                    # asleep: the claim policy should know the difference.
                    quality_flags=[QualityFlag.UNVALIDATED] if key == "interval" else [],
                )
            )
            report.mapped += 1
        else:
            _bump(report.out_of_range, "sleep")

        efficiency, _ = _numeric(payload, SLEEP_EFFICIENCY_KEYS)
        if efficiency is not None and is_plausible(Signal.SLEEP_EFFICIENCY, efficiency):
            out.append(
                Observation(
                    signal=Signal.SLEEP_EFFICIENCY,
                    value=efficiency,
                    unit="%",
                    measured_at=measured_at,
                    source=source,
                    device=device,
                    window_start=start,
                    window_end=end,
                )
            )
            report.mapped += 1
    return out, report


def data_type_from_filename(path: Path) -> str | None:
    """`daily-resting-heart-rate-20260922-214530.json` -> `daily-resting-heart-rate`.

    gh_fetch.py names files `<data-type>-<YYYYmmdd>-<HHMMSS>.json`, and roll-ups
    `rollup-<data-type>-<window>-<stamp>.json`.
    """
    stem = path.stem
    if stem.startswith("rollup-"):
        stem = stem[len("rollup-"):]
        parts = stem.split("-")
        while parts and (parts[-1].isdigit() or parts[-1].endswith("s")):
            parts.pop()
        return "-".join(parts) or None
    parts = stem.split("-")
    while parts and parts[-1].isdigit():
        parts.pop()
    return "-".join(parts) or None


def load_directory(
    directory: Path, source: str = "google_health", device: str | None = None
) -> tuple[list[Observation], AdapterReport]:
    """Convert every `*.json` export in `directory`.

    The directory is expected to be gitignored personal data. Nothing here writes,
    copies, or transmits it; it is read into memory, converted, and discarded with the
    process.
    """
    report = AdapterReport()
    observations: list[Observation] = []
    for path in sorted(directory.glob("*.json")):
        data_type = data_type_from_filename(path)
        if data_type is None:
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            _bump(report.missing_interval, path.name)
            continue
        if not isinstance(payload, list):
            payload = payload.get("dataPoints", []) if isinstance(payload, dict) else []
        converted, report = observations_from_points(
            data_type, payload, source=source, device=device, report=report
        )
        observations.extend(converted)
    observations.sort(key=lambda o: (o.signal.value, o.measured_at))
    return observations, report


def daily_values(
    observations: list[Observation], signal: Signal, aggregation: str = "mean"
) -> dict[datetime, float]:
    """Collapse observations of one signal into one value per UTC day.

    Days are keyed in UTC, not in the subject's timezone. That is a known limitation of
    this adapter, not a design choice: the export carries no timezone, so a claim whose
    window is a subject-local day must supply the timezone separately. The engine gates
    on that with TIMEZONE_UNKNOWN rather than assuming one.
    """
    buckets: dict[datetime, list[float]] = {}
    for obs in observations:
        if obs.signal is not signal:
            continue
        anchor = obs.window_end or obs.measured_at
        day = anchor.astimezone(U).replace(hour=0, minute=0, second=0, microsecond=0)
        buckets.setdefault(day, []).append(obs.value)
    out: dict[datetime, float] = {}
    for day, values in buckets.items():
        if aggregation == "sum":
            out[day] = sum(values)
        elif aggregation == "max":
            out[day] = max(values)
        elif aggregation == "min":
            out[day] = min(values)
        else:
            out[day] = sum(values) / len(values)
    return dict(sorted(out.items()))


def baseline_from_observations(
    observations: list[Observation],
    signal: Signal,
    target_window: TimeWindow,
    aggregation: str = "mean",
    max_days: int = 60,
) -> list[BaselineSample]:
    """Trailing per-day baseline for `signal`, excluding the target window's own days.

    Excluding the target day is not a detail: including it would let today's value help
    define what normal is, which shrinks the very deviation the claim is about.
    """
    per_day = daily_values(observations, signal, aggregation)
    cutoff = target_window.start - timedelta(days=max_days)
    target_days = {
        (target_window.start + timedelta(days=i)).astimezone(U).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        for i in range(max(1, int(target_window.duration_minutes // 1440) + 1))
    }
    return [
        BaselineSample(signal=signal, value=value, day=day, valid=True)
        for day, value in per_day.items()
        if cutoff <= day < target_window.start and day not in target_days
    ]


def window_observations(
    observations: list[Observation], target_window: TimeWindow, allowance_hours: float = 12.0
) -> list[Observation]:
    """Observations that describe `target_window`, including post-window aggregates."""
    kept: list[Observation] = []
    for obs in observations:
        if obs.window_start is not None and obs.window_end is not None:
            if obs.window_start < target_window.end and obs.window_end > target_window.start:
                kept.append(obs)
            continue
        if target_window.contains(obs.measured_at):
            kept.append(obs)
            continue
        lag = (obs.measured_at - target_window.end).total_seconds() / 3600.0
        if 0 <= lag <= min(allowance_hours, 0.5 * target_window.duration_minutes / 60.0):
            kept.append(obs)
    return kept
