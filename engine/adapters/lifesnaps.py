"""Adapter for LifeSnaps: 71 participants, ~3 months each, Fitbit Sense.

    Yfantidou et al., "LifeSnaps, a 4-month multi-modal dataset capturing unobtrusive
    snapshots of our lives in the wild", Scientific Data 9, 663 (2022).
    doi:10.1038/s41597-022-01764-x   Data: doi:10.5281/zenodo.7229547   CC BY 4.0.
    No changes were made to the data.

This dataset has exactly one job here: **external validation.** The thresholds in
`claim-policy-0.2.0` were set from PMData, which is 16 largely athletic Norwegian adults on
a Fitbit Versa 2. `docs/limitations.md` states that a threshold tuned to one cohort is a
hypothesis about the next one. LifeSnaps is a larger, geographically distributed,
general-population cohort on a different Fitbit model, and it carries age, gender and BMI,
which makes the first subgroup analysis possible.

**Nothing here is used to set any threshold.** It is the dataset-held-out check that
`docs/evaluation-protocol.md` section 6 requires.

Three differences from PMData that matter when comparing numbers across the two, and which
are the reason coverage figures are not directly comparable:

1. **Wear time is a different measurement.** PMData's wear minutes are minutes in which the
   device actually produced a heart-rate sample. LifeSnaps has no such field, so wear is the
   sum of the four activity-level minute buckets, clamped at 1440. That is a proxy for
   tracked time, not the same quantity.
2. **No sleep interval.** The daily table gives time asleep and time in bed but no clock
   times, so a sleep window is reconstructed by anchoring time-in-bed to a nominal 07:00
   wake. Sleep results from this dataset are correspondingly weaker evidence.
3. **Timezone is unknown and the cohort is geographically distributed.** UTC is assumed.
   As with PMData, that means the engine's TIMEZONE_UNKNOWN gate is satisfied by an
   assumption rather than by knowledge.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.schemas import (
    BaselineSample,
    Claim,
    EvaluationContext,
    EvaluationRequest,
    Observation,
    TimeWindow,
)
from engine.signals import Signal

U = timezone.utc
DEFAULT_TZ = "UTC"

CITATION = (
    "Yfantidou et al., 'LifeSnaps, a 4-month multi-modal dataset capturing unobtrusive "
    "snapshots of our lives in the wild', Scientific Data 9, 663 (2022). "
    "doi:10.1038/s41597-022-01764-x; data doi:10.5281/zenodo.7229547. Licensed CC BY 4.0 "
    "(https://creativecommons.org/licenses/by/4.0/); no changes were made to the data."
)

DAILY_CSV = Path("rais_anonymized/csv_rais_anonymized/daily_fitbit_sema_df_unprocessed.csv")

BASELINE_VALID_WEAR_RATIO = 0.60
BASELINE_LOOKBACK_DAYS = 60
SUMMARY_LAG_HOURS = 2.0
EVALUATION_LAG_HOURS = 8.0
# Nominal wake time used to place a sleep window, since the daily table has no clock times.
NOMINAL_WAKE = time(7, 0)
ACTIVITY_COLUMNS = (
    "sedentary_minutes", "lightly_active_minutes",
    "moderately_active_minutes", "very_active_minutes",
)


def _f(value: Any) -> float | None:
    if value in (None, "", "NA", "nan"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


@dataclass
class LifeSnapsSubject:
    """One participant, with the same request interface as the PMData adapter."""

    subject_id: str
    timezone: str
    daily: dict[str, dict[str, float]]
    demographics: dict[str, str] = field(default_factory=dict)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def days(self) -> list[str]:
        return sorted(self.daily)

    def wear_ratio(self, day: str) -> float | None:
        worn = self.daily.get(day, {}).get("wear_minutes")
        return None if worn is None else worn / 1440.0

    def day_window(self, day: str) -> TimeWindow:
        start = datetime.combine(date.fromisoformat(day), time(0, 0), tzinfo=self.tz)
        return TimeWindow(start=start.astimezone(U),
                          end=(start + timedelta(days=1)).astimezone(U))

    def _context(self, day: str, window: TimeWindow, assume_sync: bool) -> EvaluationContext:
        worn = self.daily.get(day, {}).get("wear_minutes")
        return EvaluationContext(
            # As with PMData, no export carries a sync-completion time.
            sync_completed_at=window.end + timedelta(hours=SUMMARY_LAG_HOURS)
            if assume_sync else None,
            device_worn_minutes=worn,
            expected_worn_minutes=window.duration_minutes,
            timezone=self.timezone,
            device="fitbit-sense",
        )

    def _baseline(self, day: str, signal: Signal, key: str) -> list[BaselineSample]:
        target = date.fromisoformat(day)
        out: list[BaselineSample] = []
        for offset in range(1, BASELINE_LOOKBACK_DAYS + 1):
            prior = (target - timedelta(days=offset)).isoformat()
            value = self.daily.get(prior, {}).get(key)
            if value is None:
                continue
            ratio = self.wear_ratio(prior)
            out.append(
                BaselineSample(
                    signal=signal, value=float(value),
                    day=datetime.combine(date.fromisoformat(prior), time(0, 0),
                                         tzinfo=self.tz).astimezone(U),
                    valid=ratio is not None and ratio >= BASELINE_VALID_WEAR_RATIO,
                )
            )
        return sorted(out, key=lambda s: s.day)

    def _daily_request(
        self, day: str, claim_type: str, statement: str, signal: Signal, key: str,
        unit: str, assume_sync: bool, extra: list[tuple[Signal, str, str]] = (),
    ) -> EvaluationRequest | None:
        value = self.daily.get(day, {}).get(key)
        if value is None:
            return None
        window = self.day_window(day)
        stamp = window.end + timedelta(hours=SUMMARY_LAG_HOURS)
        observations = [
            Observation(signal=signal, value=float(value), unit=unit, measured_at=stamp,
                        source="lifesnaps", device="fitbit-sense",
                        window_start=window.start, window_end=window.end)
        ]
        for extra_signal, extra_key, extra_unit in extra:
            extra_value = self.daily.get(day, {}).get(extra_key)
            if extra_value is not None:
                observations.append(
                    Observation(signal=extra_signal, value=float(extra_value),
                                unit=extra_unit, measured_at=stamp, source="lifesnaps",
                                device="fitbit-sense", window_start=window.start,
                                window_end=window.end)
                )
        return EvaluationRequest(
            subject_id=self.subject_id,
            claim=Claim(type=claim_type, statement=statement, target_window=window),
            observations=observations,
            baseline=self._baseline(day, signal, key),
            context=self._context(day, window, assume_sync),
            evaluated_at=window.end + timedelta(hours=EVALUATION_LAG_HOURS),
        )

    def resting_heart_rate_request(self, day: str, assume_sync: bool = False):
        return self._daily_request(
            day, "RESTING_HEART_RATE_ELEVATED",
            "Your resting heart rate is elevated today.",
            Signal.RESTING_HEART_RATE, "resting_heart_rate", "bpm", assume_sync,
        )

    def activity_load_request(self, day: str, assume_sync: bool = False):
        return self._daily_request(
            day, "ACTIVITY_LOAD_HIGH", "Your activity load was high today.",
            Signal.ACTIVE_MINUTES, "active_minutes", "minutes", assume_sync,
            extra=[(Signal.STEPS, "steps", "count")],
        )

    def _sleep_request(self, day: str, claim_type: str, statement: str,
                       signal: Signal, key: str, assume_sync: bool):
        asleep = self.daily.get(day, {}).get("sleep_duration")
        in_bed = self.daily.get(day, {}).get("time_in_bed") or asleep
        value = self.daily.get(day, {}).get(key)
        if asleep is None or value is None or not in_bed:
            return None
        # No clock times in the daily table, so the window is time-in-bed anchored to a
        # nominal wake. This is an approximation and it is why sleep results from this
        # dataset are weaker evidence than its heart-rate results.
        end = datetime.combine(date.fromisoformat(day), NOMINAL_WAKE,
                               tzinfo=self.tz).astimezone(U)
        window = TimeWindow(start=end - timedelta(minutes=float(in_bed)), end=end)
        stamp = window.end + timedelta(minutes=20)
        observations = [
            Observation(signal=Signal.SLEEP_DURATION, value=float(asleep), unit="minutes",
                        measured_at=stamp, source="lifesnaps", device="fitbit-sense",
                        window_start=window.start, window_end=window.end),
        ]
        efficiency = self.daily.get(day, {}).get("sleep_efficiency")
        if efficiency is not None:
            observations.append(
                Observation(signal=Signal.SLEEP_EFFICIENCY, value=float(efficiency),
                            unit="%", measured_at=stamp, source="lifesnaps",
                            device="fitbit-sense", window_start=window.start,
                            window_end=window.end)
            )
        baseline = self._baseline(day, Signal.SLEEP_DURATION, "sleep_duration")
        baseline += self._baseline(day, Signal.SLEEP_EFFICIENCY, "sleep_efficiency")
        return EvaluationRequest(
            subject_id=self.subject_id,
            claim=Claim(type=claim_type, statement=statement, target_window=window),
            observations=observations,
            baseline=baseline,
            context=EvaluationContext(
                sync_completed_at=window.end + timedelta(hours=1) if assume_sync else None,
                device_worn_minutes=float(in_bed),
                expected_worn_minutes=window.duration_minutes,
                timezone=self.timezone, device="fitbit-sense",
            ),
            evaluated_at=window.end + timedelta(hours=3),
        )

    def sleep_duration_request(self, day: str, assume_sync: bool = False):
        return self._sleep_request(day, "SLEEP_DURATION_LOW", "You slept less than usual.",
                                   Signal.SLEEP_DURATION, "sleep_duration", assume_sync)

    def sleep_quality_request(self, day: str, assume_sync: bool = False):
        return self._sleep_request(day, "SLEEP_QUALITY_REDUCED",
                                   "Your sleep quality was reduced.",
                                   Signal.SLEEP_EFFICIENCY, "sleep_efficiency", assume_sync)

    BUILDERS = {
        "RESTING_HEART_RATE_ELEVATED": "resting_heart_rate_request",
        "ACTIVITY_LOAD_HIGH": "activity_load_request",
        "SLEEP_DURATION_LOW": "sleep_duration_request",
        "SLEEP_QUALITY_REDUCED": "sleep_quality_request",
    }

    def request(self, claim_type: str, day: str, assume_sync: bool = False):
        method = self.BUILDERS.get(claim_type)
        if method is None:
            raise KeyError(f"no LifeSnaps builder for {claim_type!r}")
        return getattr(self, method)(day, assume_sync)


def load(root: Path, tz_name: str = DEFAULT_TZ) -> list[LifeSnapsSubject]:
    """Read the daily Fitbit table into one subject per participant."""
    path = root / DAILY_CSV if (root / DAILY_CSV).is_file() else root
    subjects: dict[str, LifeSnapsSubject] = {}
    with path.open() as handle:
        for row in csv.DictReader(handle):
            pid = (row.get("id") or "").strip()
            day = (row.get("date") or "").strip()
            if not pid or not day:
                continue
            try:
                date.fromisoformat(day)
            except ValueError:
                continue
            subject = subjects.setdefault(
                pid,
                LifeSnapsSubject(
                    # The dataset's identifiers are already anonymised; they are shortened
                    # and prefixed so a decision trace never carries the raw token.
                    subject_id=f"lifesnaps-{pid[-8:]}",
                    timezone=tz_name,
                    daily={},
                    demographics={
                        "gender": (row.get("gender") or "").strip() or "unknown",
                        "age_band": (row.get("age") or "").strip() or "unknown",
                        "bmi_band": (row.get("bmi") or "").strip() or "unknown",
                    },
                ),
            )
            bucket: dict[str, float] = {}
            rhr = _f(row.get("resting_hr"))
            if rhr is not None and rhr > 0:
                bucket["resting_heart_rate"] = rhr
            active = sum(
                v for v in (_f(row.get("very_active_minutes")),
                            _f(row.get("moderately_active_minutes"))) if v is not None
            )
            if _f(row.get("very_active_minutes")) is not None:
                bucket["active_minutes"] = active
            steps = _f(row.get("steps"))
            if steps is not None:
                bucket["steps"] = steps
            asleep = _f(row.get("minutesAsleep"))
            if asleep is not None and asleep > 0:
                bucket["sleep_duration"] = asleep
            efficiency = _f(row.get("sleep_efficiency"))
            if efficiency is not None and efficiency > 0:
                bucket["sleep_efficiency"] = efficiency
            # sleep_duration in the source table is time in bed, in milliseconds.
            in_bed_ms = _f(row.get("sleep_duration"))
            if in_bed_ms is not None and in_bed_ms > 0:
                bucket["time_in_bed"] = in_bed_ms / 60000.0
            parts = [_f(row.get(c)) for c in ACTIVITY_COLUMNS]
            if all(p is not None for p in parts):
                # The buckets can sum past a day, so this is clamped. It is tracked time,
                # not the heart-rate-derived wear time PMData gives.
                bucket["wear_minutes"] = min(1440.0, sum(p for p in parts if p is not None))
            if bucket:
                subject.daily[day] = bucket
    return [s for s in subjects.values() if s.daily]
