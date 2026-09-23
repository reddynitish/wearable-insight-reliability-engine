"""Adapter for PMData: 16 subjects, ~5 months each, Fitbit Versa 2.

    Thambawita et al., "PMData: A Sports Logging Dataset", MMSys '20.
    https://dl.acm.org/doi/10.1145/3339825.3394926   CC BY 4.0, no changes to the data.

Why this dataset is the one that matters here: the engine's claims are defined against a
personal baseline of 7-14 valid days, so a dataset with one session per subject cannot
exercise them at all. PMData gives months per subject, which is what makes the
baseline-dependent claims testable on real measurements for the first time.

Two assumptions, both consequential, both recorded rather than buried:

1. **Timezone.** Fitbit exports timestamps as naive device-local wall-clock. PMData does
   not state a zone. Collection was run from Oslo, so `Europe/Oslo` is the default, and it
   is a parameter because it is a guess. It affects which samples land in which day, so a
   wrong zone shifts every daily aggregate by its offset. The engine has a gate for
   exactly this (`TIMEZONE_UNKNOWN`), and supplying a zone here means that gate is
   satisfied by an assumption rather than by knowledge.
2. **Sleep efficiency.** Fitbit reports its own `efficiency` field, which is not
   `minutesAsleep / timeInBed`. Both are carried: the reported field becomes the canonical
   signal, because it is what a consumer app displays and therefore what a claim would be
   about, and the computed ratio is kept in the summary so the gap can be inspected.

No PMData file, participant record, or derived per-subject table is committed:
`data/datasets/` is gitignored and the prepared cache lives beside it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

U = timezone.utc
DEFAULT_TZ = "Europe/Oslo"

CITATION = (
    "Thambawita et al., 'PMData: A Sports Logging Dataset', Proceedings of the 11th ACM "
    "Multimedia Systems Conference (MMSys '20), 2020, pp. 231-236. "
    "doi:10.1145/3339825.3394926. Licensed CC BY 4.0 "
    "(https://creativecommons.org/licenses/by/4.0/); no changes were made to the data."
)

# The overnight window used for the resting-heart-rate claim's overnight coverage check.
OVERNIGHT_START = time(0, 0)
OVERNIGHT_END = time(8, 0)
OVERNIGHT_MINUTES = 480.0

# How many raw heart-rate values to keep per day for the consistency rules.
HR_SAMPLE_SIZE = 24

ACTIVE_MINUTE_FILES = (
    "very_active_minutes.json",
    "moderately_active_minutes.json",
)


def stream_json_array(path: Path, chunk_size: int = 1 << 20) -> Iterator[dict[str, Any]]:
    """Yield objects from a large JSON array without holding the whole file in memory.

    `heart_rate.json` is ~114 MB per participant and roughly 1.8 GB across the dataset.
    Parsing one of those with json.load costs well over a gigabyte of Python objects, so
    this decodes incrementally with raw_decode and keeps only the buffer.
    """
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as handle:
        buffer = handle.read(chunk_size)
        index = buffer.find("[")
        if index < 0:
            return
        buffer = buffer[index + 1:]
        while True:
            buffer = buffer.lstrip()
            while buffer.startswith(","):
                buffer = buffer[1:].lstrip()
            if buffer.startswith("]") or (not buffer and not (chunk := handle.read(chunk_size))):
                return
            try:
                obj, end = decoder.raw_decode(buffer)
            except ValueError:
                more = handle.read(chunk_size)
                if not more:
                    return
                buffer += more
                continue
            yield obj
            buffer = buffer[end:]
            if len(buffer) < 4096:
                buffer += handle.read(chunk_size)


def _parse_local(raw: str, tz: ZoneInfo) -> datetime:
    """Fitbit's naive local wall-clock -> an aware UTC instant."""
    text = raw.strip().replace("T", " ")
    if "." in text:
        text = text.split(".")[0]
    naive = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=tz).astimezone(U)


def _local_day(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()


@dataclass
class DaySummary:
    """One subject-local day of measured coverage and heart-rate shape.

    These are *measured*, not assumed: wear minutes are minutes in which the device
    actually produced a heart-rate sample, and the gap is the real largest hole. The
    synthetic harness had to invent both.
    """

    day: date
    hr_samples: int = 0
    wear_minutes: int = 0
    overnight_wear_minutes: int = 0
    max_gap_minutes: float = 0.0
    hr_min: float | None = None
    hr_max: float | None = None
    hr_p10: float | None = None
    # A small even subsample of the day's heart rate, kept so the summary-versus-detail
    # consistency rule has raw values to work with. 24 values is enough for a tenth
    # percentile and keeps the cache small; the full 10,000-sample day never leaves here.
    hr_sample: list[float] = field(default_factory=list)
    steps_total: float = 0.0
    steps_active_minutes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "hr_samples": self.hr_samples,
            "wear_minutes": self.wear_minutes,
            "overnight_wear_minutes": self.overnight_wear_minutes,
            "max_gap_minutes": round(self.max_gap_minutes, 2),
            "hr_min": self.hr_min,
            "hr_max": self.hr_max,
            "hr_p10": self.hr_p10,
            "steps_total": self.steps_total,
            "steps_active_minutes": self.steps_active_minutes,
            "hr_sample": self.hr_sample,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> DaySummary:
        return cls(
            day=date.fromisoformat(row["day"]),
            hr_samples=row["hr_samples"],
            wear_minutes=row["wear_minutes"],
            overnight_wear_minutes=row["overnight_wear_minutes"],
            max_gap_minutes=row["max_gap_minutes"],
            hr_min=row["hr_min"],
            hr_max=row["hr_max"],
            hr_p10=row["hr_p10"],
            steps_total=row["steps_total"],
            steps_active_minutes=row["steps_active_minutes"],
            hr_sample=list(row.get("hr_sample") or []),
        )


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of an empty sequence")
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q / 100.0
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def summarise_days(participant: Path, tz_name: str = DEFAULT_TZ) -> dict[str, DaySummary]:
    """Stream heart_rate.json and steps.json once, reducing to per-day coverage."""
    tz = ZoneInfo(tz_name)
    days: dict[date, DaySummary] = {}
    minutes_seen: dict[date, set[int]] = {}
    overnight_seen: dict[date, set[int]] = {}
    hr_pool: dict[date, list[float]] = {}
    last_sample: dict[date, datetime] = {}

    hr_path = participant / "fitbit" / "heart_rate.json"
    if hr_path.is_file():
        for row in stream_json_array(hr_path):
            try:
                moment = _parse_local(row["dateTime"], tz)
                bpm = float(row["value"]["bpm"])
            except (KeyError, TypeError, ValueError):
                continue
            local = moment.astimezone(tz)
            day = local.date()
            summary = days.setdefault(day, DaySummary(day=day))
            summary.hr_samples += 1
            minute_index = local.hour * 60 + local.minute
            minutes_seen.setdefault(day, set()).add(minute_index)
            if OVERNIGHT_START <= local.time() < OVERNIGHT_END:
                overnight_seen.setdefault(day, set()).add(minute_index)
            summary.hr_min = bpm if summary.hr_min is None else min(summary.hr_min, bpm)
            summary.hr_max = bpm if summary.hr_max is None else max(summary.hr_max, bpm)
            # Reservoir-free subsample: every 20th reading is plenty to estimate a
            # percentile and keeps memory flat across a five-month record.
            if summary.hr_samples % 20 == 0:
                hr_pool.setdefault(day, []).append(bpm)
            previous = last_sample.get(day)
            if previous is not None:
                gap = (moment - previous).total_seconds() / 60.0
                if gap > summary.max_gap_minutes:
                    summary.max_gap_minutes = gap
            last_sample[day] = moment

    steps_path = participant / "fitbit" / "steps.json"
    if steps_path.is_file():
        for row in stream_json_array(steps_path):
            try:
                moment = _parse_local(row["dateTime"], tz)
                value = float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            day = _local_day(moment, tz)
            summary = days.setdefault(day, DaySummary(day=day))
            summary.steps_total += value
            if value > 0:
                summary.steps_active_minutes += 1

    for day, summary in days.items():
        summary.wear_minutes = len(minutes_seen.get(day, ()))
        summary.overnight_wear_minutes = len(overnight_seen.get(day, ()))
        pool = hr_pool.get(day)
        if pool:
            summary.hr_p10 = round(_percentile(pool, 10), 2)
            step = max(1, len(pool) // HR_SAMPLE_SIZE)
            summary.hr_sample = [round(v, 1) for v in pool[::step][:HR_SAMPLE_SIZE]]
    return {day.isoformat(): summary for day, summary in sorted(days.items())}


def load_daily_series(participant: Path, tz_name: str = DEFAULT_TZ) -> dict[str, dict[str, float]]:
    """Daily resting heart rate and active minutes, keyed by subject-local ISO date."""
    tz = ZoneInfo(tz_name)
    out: dict[str, dict[str, float]] = {}

    rhr_path = participant / "fitbit" / "resting_heart_rate.json"
    if rhr_path.is_file():
        for row in json.loads(rhr_path.read_text()):
            value = (row.get("value") or {}).get("value")
            if value is None:
                continue
            # A resting heart rate of exactly 0 is Fitbit reporting "no estimate", not a
            # measurement. Dropping it here keeps it out of the baseline entirely; leaving
            # it in would drag a personal mean down by several bpm.
            if float(value) <= 0:
                continue
            day = _local_day(_parse_local(row["dateTime"], tz), tz).isoformat()
            out.setdefault(day, {})["resting_heart_rate"] = float(value)
            error = (row.get("value") or {}).get("error")
            if error is not None:
                out[day]["resting_heart_rate_error"] = float(error)

    for filename in ACTIVE_MINUTE_FILES:
        path = participant / "fitbit" / filename
        if not path.is_file():
            continue
        for row in json.loads(path.read_text()):
            try:
                value = float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            day = _local_day(_parse_local(row["dateTime"], tz), tz).isoformat()
            bucket = out.setdefault(day, {})
            bucket["active_minutes"] = bucket.get("active_minutes", 0.0) + value
    return out


def load_sleep(participant: Path, tz_name: str = DEFAULT_TZ) -> list[dict[str, Any]]:
    """One record per sleep period, with its own interval in UTC."""
    tz = ZoneInfo(tz_name)
    path = participant / "fitbit" / "sleep.json"
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for row in json.loads(path.read_text()):
        try:
            start = _parse_local(row["startTime"], tz)
            end = _parse_local(row["endTime"], tz)
            asleep = float(row["minutesAsleep"])
            in_bed = float(row.get("timeInBed") or 0.0)
            efficiency = float(row["efficiency"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start or asleep <= 0:
            continue
        records.append(
            {
                "date_of_sleep": row.get("dateOfSleep"),
                "start": start,
                "end": end,
                "minutes_asleep": asleep,
                "time_in_bed": in_bed,
                # Fitbit's own efficiency score, which is what an app would display.
                "efficiency": efficiency,
                # The classical definition, kept so the two can be compared.
                "efficiency_computed": round(100.0 * asleep / in_bed, 2) if in_bed else None,
                "is_main_sleep": bool(row.get("mainSleep", True)),
                "type": row.get("type"),
            }
        )
    records.sort(key=lambda r: r["start"])
    return records


def load_wellness(participant: Path) -> dict[str, dict[str, float]]:
    """Self-reported daily wellness, keyed by the UTC date of the report.

    Used only as a weak exploratory signal. It is self-report, collected at a varying time
    of day, and it is not a reference standard for anything the engine decides.
    """
    import csv

    path = participant / "pmsys" / "wellness.csv"
    if not path.is_file():
        return {}
    out: dict[str, dict[str, float]] = {}
    with path.open() as handle:
        for row in csv.DictReader(handle):
            stamp = row.get("effective_time_frame")
            if not stamp:
                continue
            try:
                moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            values: dict[str, float] = {}
            for key in ("fatigue", "mood", "readiness", "sleep_quality", "soreness", "stress",
                        "sleep_duration_h"):
                raw = row.get(key)
                if raw not in (None, ""):
                    try:
                        values[key] = float(raw)
                    except ValueError:
                        continue
            if values:
                out[moment.date().isoformat()] = values
    return out


def participants(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "fitbit").is_dir())


# --------------------------------------------------------------------------- requests

from engine.schemas import (  # noqa: E402
    BaselineSample,
    Claim,
    EvaluationContext,
    EvaluationRequest,
    Observation,
    TimeWindow,
)
from engine.signals import Signal  # noqa: E402

# A baseline day counts as valid only if the device was actually worn for this share of it.
# The engine's own coverage floor is applied to history as well as to the target day:
# a personal "normal" built from days the watch spent in a drawer is not a normal.
BASELINE_VALID_WEAR_RATIO = 0.60
# How far back to look for baseline days.
BASELINE_LOOKBACK_DAYS = 60
# Assumed lag between the end of a day and the daily summary being computed, used only for
# the observation timestamp. The engine attributes post-window aggregates to their window.
SUMMARY_LAG_HOURS = 2.0
# Hours after the window end at which the engine is asked. Chosen so the window is closed
# and the data is fresh, which is the situation a morning insight would actually run in.
EVALUATION_LAG_HOURS = 8.0


@dataclass
class PreparedSubject:
    """One PMData participant, loaded from the prepared cache."""

    subject_id: str
    timezone: str
    day_summaries: dict[str, DaySummary]
    daily: dict[str, dict[str, float]]
    sleep: list[dict[str, Any]]
    wellness: dict[str, dict[str, float]]

    @classmethod
    def load(cls, path: Path) -> PreparedSubject:
        payload = json.loads(path.read_text())
        return cls(
            subject_id=payload["subject_id"],
            timezone=payload["timezone"],
            day_summaries={
                k: DaySummary.from_dict(v) for k, v in payload["day_summaries"].items()
            },
            daily=payload["daily"],
            sleep=[
                {
                    **record,
                    "start": datetime.fromisoformat(record["start"]),
                    "end": datetime.fromisoformat(record["end"]),
                }
                for record in payload["sleep"]
            ],
            wellness=payload.get("wellness", {}),
        )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def day_window(self, day: str) -> TimeWindow:
        start_local = datetime.combine(date.fromisoformat(day), time(0, 0), tzinfo=self.tz)
        return TimeWindow(
            start=start_local.astimezone(U),
            end=(start_local + timedelta(days=1)).astimezone(U),
        )

    def wear_ratio(self, day: str) -> float | None:
        summary = self.day_summaries.get(day)
        return None if summary is None else summary.wear_minutes / 1440.0

    def _context(
        self, day: str, window: TimeWindow, assume_sync: bool
    ) -> EvaluationContext:
        summary = self.day_summaries.get(day)
        return EvaluationContext(
            # PMData carries no sync-completion timestamp, because a Fitbit export does
            # not contain one. Leaving it None is the honest default and makes the engine
            # disclose SYNC_STATE_UNKNOWN; `assume_sync` exists so the effect of that one
            # missing field can be measured rather than argued about.
            sync_completed_at=window.end + timedelta(hours=SUMMARY_LAG_HOURS)
            if assume_sync
            else None,
            device_worn_minutes=None if summary is None else float(summary.wear_minutes),
            overnight_worn_minutes=None
            if summary is None
            else float(summary.overnight_wear_minutes),
            overnight_window_minutes=OVERNIGHT_MINUTES,
            expected_worn_minutes=1440.0,
            reported_gaps_minutes=[summary.max_gap_minutes] if summary else [],
            timezone=self.timezone,
            device="fitbit-versa-2",
        )

    def _hr_observations(self, day: str, window: TimeWindow) -> list[Observation]:
        summary = self.day_summaries.get(day)
        if summary is None or not summary.hr_sample:
            return []
        step = (window.end - window.start) / len(summary.hr_sample)
        return [
            Observation(
                signal=Signal.HEART_RATE,
                value=value,
                unit="bpm",
                measured_at=window.start + step * index,
                source="pmdata",
                device="fitbit-versa-2",
            )
            for index, value in enumerate(summary.hr_sample)
        ]

    def _baseline(
        self, day: str, signal: Signal, key: str, lookback: int = BASELINE_LOOKBACK_DAYS
    ) -> list[BaselineSample]:
        target = date.fromisoformat(day)
        samples: list[BaselineSample] = []
        for offset in range(1, lookback + 1):
            prior = (target - timedelta(days=offset)).isoformat()
            value = self.daily.get(prior, {}).get(key)
            if value is None:
                continue
            ratio = self.wear_ratio(prior)
            samples.append(
                BaselineSample(
                    signal=signal,
                    value=float(value),
                    day=datetime.combine(
                        date.fromisoformat(prior), time(0, 0), tzinfo=self.tz
                    ).astimezone(U),
                    valid=ratio is not None and ratio >= BASELINE_VALID_WEAR_RATIO,
                )
            )
        return sorted(samples, key=lambda s: s.day)

    def resting_heart_rate_request(
        self, day: str, assume_sync: bool = False
    ) -> EvaluationRequest | None:
        value = self.daily.get(day, {}).get("resting_heart_rate")
        if value is None:
            return None
        window = self.day_window(day)
        observations = [
            Observation(
                signal=Signal.RESTING_HEART_RATE,
                value=float(value),
                unit="bpm",
                measured_at=window.end + timedelta(hours=SUMMARY_LAG_HOURS),
                source="pmdata",
                device="fitbit-versa-2",
                window_start=window.start,
                window_end=window.end,
            )
        ]
        observations.extend(self._hr_observations(day, window))
        return EvaluationRequest(
            subject_id=self.subject_id,
            claim=Claim(
                type="RESTING_HEART_RATE_ELEVATED",
                statement="Your resting heart rate is elevated today.",
                target_window=window,
            ),
            observations=observations,
            baseline=self._baseline(day, Signal.RESTING_HEART_RATE, "resting_heart_rate"),
            context=self._context(day, window, assume_sync),
            evaluated_at=window.end + timedelta(hours=EVALUATION_LAG_HOURS),
        )

    def activity_load_request(
        self, day: str, assume_sync: bool = False
    ) -> EvaluationRequest | None:
        value = self.daily.get(day, {}).get("active_minutes")
        if value is None:
            return None
        window = self.day_window(day)
        summary = self.day_summaries.get(day)
        observations = [
            Observation(
                signal=Signal.ACTIVE_MINUTES,
                value=float(value),
                unit="minutes",
                measured_at=window.end + timedelta(hours=SUMMARY_LAG_HOURS),
                source="pmdata",
                device="fitbit-versa-2",
                window_start=window.start,
                window_end=window.end,
            )
        ]
        if summary is not None:
            observations.append(
                Observation(
                    signal=Signal.STEPS,
                    value=float(summary.steps_total),
                    unit="count",
                    measured_at=window.end + timedelta(hours=SUMMARY_LAG_HOURS),
                    source="pmdata",
                    device="fitbit-versa-2",
                    window_start=window.start,
                    window_end=window.end,
                )
            )
        observations.extend(self._hr_observations(day, window))
        return EvaluationRequest(
            subject_id=self.subject_id,
            claim=Claim(
                type="ACTIVITY_LOAD_HIGH",
                statement="Your activity load was high today.",
                target_window=window,
            ),
            observations=observations,
            baseline=self._baseline(day, Signal.ACTIVE_MINUTES, "active_minutes"),
            context=self._context(day, window, assume_sync),
            evaluated_at=window.end + timedelta(hours=EVALUATION_LAG_HOURS),
        )

    def sleep_records_by_date(self) -> dict[str, dict[str, Any]]:
        """The main sleep period per `dateOfSleep`, longest wins when several are logged."""
        out: dict[str, dict[str, Any]] = {}
        for record in self.sleep:
            key = record.get("date_of_sleep")
            if not key:
                continue
            existing = out.get(key)
            if existing is None or record["minutes_asleep"] > existing["minutes_asleep"]:
                out[key] = record
        return out

    def _sleep_baseline(
        self, day: str, signal: Signal, key: str, by_date: dict[str, dict[str, Any]]
    ) -> list[BaselineSample]:
        target = date.fromisoformat(day)
        samples: list[BaselineSample] = []
        for offset in range(1, BASELINE_LOOKBACK_DAYS + 1):
            prior = (target - timedelta(days=offset)).isoformat()
            record = by_date.get(prior)
            if record is None:
                continue
            samples.append(
                BaselineSample(
                    signal=signal,
                    value=float(record[key]),
                    day=datetime.combine(
                        date.fromisoformat(prior), time(0, 0), tzinfo=self.tz
                    ).astimezone(U),
                    valid=True,
                )
            )
        return sorted(samples, key=lambda s: s.day)

    def _sleep_request(
        self, day: str, claim_type: str, statement: str, assume_sync: bool
    ) -> EvaluationRequest | None:
        by_date = self.sleep_records_by_date()
        record = by_date.get(day)
        if record is None:
            return None
        window = TimeWindow(start=record["start"], end=record["end"])
        stamp = window.end + timedelta(minutes=20)
        observations = [
            Observation(
                signal=Signal.SLEEP_DURATION,
                value=float(record["minutes_asleep"]),
                unit="minutes",
                measured_at=stamp,
                source="pmdata",
                device="fitbit-versa-2",
                window_start=window.start,
                window_end=window.end,
            ),
            Observation(
                signal=Signal.SLEEP_EFFICIENCY,
                value=float(record["efficiency"]),
                unit="%",
                measured_at=stamp,
                source="pmdata",
                device="fitbit-versa-2",
                window_start=window.start,
                window_end=window.end,
            ),
        ]
        baseline = self._sleep_baseline(day, Signal.SLEEP_DURATION, "minutes_asleep", by_date)
        baseline += self._sleep_baseline(day, Signal.SLEEP_EFFICIENCY, "efficiency", by_date)
        # Wear during a sleep period is the period itself: the device recorded stages, so
        # it was on the wrist. Time in bed is the honest denominator here, not 1440.
        context = EvaluationContext(
            sync_completed_at=window.end + timedelta(hours=1) if assume_sync else None,
            device_worn_minutes=float(record["time_in_bed"] or window.duration_minutes),
            expected_worn_minutes=window.duration_minutes,
            timezone=self.timezone,
            device="fitbit-versa-2",
        )
        return EvaluationRequest(
            subject_id=self.subject_id,
            claim=Claim(type=claim_type, statement=statement, target_window=window),
            observations=observations,
            baseline=baseline,
            context=context,
            evaluated_at=window.end + timedelta(hours=3),
        )

    def sleep_duration_request(self, day: str, assume_sync: bool = False):
        return self._sleep_request(
            day, "SLEEP_DURATION_LOW", "You slept less than usual.", assume_sync
        )

    def sleep_quality_request(self, day: str, assume_sync: bool = False):
        return self._sleep_request(
            day, "SLEEP_QUALITY_REDUCED", "Your sleep quality was reduced.", assume_sync
        )

    BUILDERS = {
        "RESTING_HEART_RATE_ELEVATED": "resting_heart_rate_request",
        "ACTIVITY_LOAD_HIGH": "activity_load_request",
        "SLEEP_DURATION_LOW": "sleep_duration_request",
        "SLEEP_QUALITY_REDUCED": "sleep_quality_request",
    }

    def request(self, claim_type: str, day: str, assume_sync: bool = False):
        method = self.BUILDERS.get(claim_type)
        if method is None:
            raise KeyError(f"no PMData builder for {claim_type!r}")
        return getattr(self, method)(day, assume_sync)

    def days(self) -> list[str]:
        return sorted(set(self.daily) | set(self.day_summaries))


def load_prepared(cache: Path) -> list[PreparedSubject]:
    return [
        PreparedSubject.load(path)
        for path in sorted(cache.glob("p*.json"))
        if path.name != "manifest.json"
    ]
