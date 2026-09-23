"""Typed request and response contract.

The response shape is public API: fields may be added, but existing field names,
types, and the `Decision` enum members are frozen for the 0.1 line.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from engine import MODEL_VERSION, POLICY_VERSION
from engine.reasons import Dimension, Outcome, ReasonCode
from engine.signals import Signal


class Decision(str, Enum):
    """The four typed decisions. Adding a member is a breaking change (ADR 0001)."""

    SHOW = "SHOW"
    SHOW_WITH_WARNING = "SHOW_WITH_WARNING"
    WAIT_FOR_MORE_DATA = "WAIT_FOR_MORE_DATA"
    REJECT = "REJECT"


class QualityFlag(str, Enum):
    """Upstream, device- or pipeline-reported quality annotations on one observation."""

    MOTION_ARTIFACT = "motion_artifact"
    LOW_PERFUSION = "low_perfusion"
    POOR_SKIN_CONTACT = "poor_skin_contact"
    SENSOR_DROPOUT = "sensor_dropout"
    INTERPOLATED = "interpolated"
    DEVICE_REMOVED = "device_removed"
    UNVALIDATED = "unvalidated"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "timestamps must be timezone-aware; a naive timestamp silently assumes a "
            "timezone and that is exactly the class of bug this engine exists to catch"
        )
    return value.astimezone(timezone.utc)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeWindow(Strict):
    start: datetime
    end: datetime

    _aware = field_validator("start", "end")(_require_aware)

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.end <= self.start:
            raise ValueError("time window end must be strictly after start")
        return self

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    def contains(self, moment: datetime) -> bool:
        """Half-open [start, end): a sample at exactly `end` belongs to the next window."""
        return self.start <= moment < self.end


class Observation(Strict):
    """One canonical measurement or interval aggregate."""

    signal: Signal
    value: float
    unit: str | None = None
    measured_at: datetime
    source: str = "unknown"
    device: str | None = None
    # Present when the value summarises an interval rather than an instant.
    window_start: datetime | None = None
    window_end: datetime | None = None
    quality_flags: list[QualityFlag] = Field(default_factory=list)

    _aware = field_validator("measured_at")(_require_aware)

    @field_validator("window_start", "window_end")
    @classmethod
    def _aware_opt(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _require_aware(v)

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("observation value must be finite")
        return v


class BaselineSample(Strict):
    """One day of personal history for one signal.

    `valid` records whether that day met the coverage bar upstream. Invalid days are
    counted but excluded from the baseline statistics, so a 30-day history with 25
    unworn days does not read as a mature baseline.
    """

    signal: Signal
    value: float
    day: datetime
    valid: bool = True

    _aware = field_validator("day")(_require_aware)

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("baseline value must be finite")
        return v


class EvaluationContext(Strict):
    """Everything about the collection situation that is not itself a measurement."""

    sync_completed_at: datetime | None = None
    device_worn_minutes: float | None = None
    overnight_worn_minutes: float | None = None
    expected_worn_minutes: float | None = None
    overnight_window_minutes: float | None = None
    # Declared baseline length, used only when no per-day baseline samples are supplied.
    baseline_days: int | None = None
    timezone: str | None = None
    device: str | None = None
    device_changed_recently: bool = False
    exercise_minutes_last_24h: float | None = None
    reported_gaps_minutes: list[float] = Field(default_factory=list)

    @field_validator("sync_completed_at")
    @classmethod
    def _aware_opt(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _require_aware(v)


_SUBJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$")


class Claim(Strict):
    """A proposed statement plus the window it is about.

    `type` is an unconstrained string on purpose: an unknown claim type must produce a
    typed REJECT with UNSUPPORTED_CLAIM_TYPE, not a schema validation error, because a
    caller sending an unrecognised type still deserves a decision it can branch on.
    """

    type: str
    statement: str | None = None
    target_window: TimeWindow
    # Required by claim types that name the signal they are about, e.g. the anomaly claim.
    signal: Signal | None = None

    @field_validator("type")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("claim type must not be empty")
        return v.strip()


class EvaluationRequest(Strict):
    subject_id: str
    claim: Claim
    observations: list[Observation] = Field(default_factory=list)
    baseline: list[BaselineSample] = Field(default_factory=list)
    context: EvaluationContext = Field(default_factory=EvaluationContext)
    evaluated_at: datetime | None = None

    @field_validator("evaluated_at")
    @classmethod
    def _aware_opt(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _require_aware(v)

    @field_validator("subject_id")
    @classmethod
    def _pseudonymous(cls, v: str) -> str:
        """Reject identifiers that are obviously personal.

        This is a guard rail, not anonymisation: it cannot tell a pseudonym from a
        username. It exists so that the easiest mistakes -- pasting an email address or
        a phone number into subject_id, where it would then land in every logged
        decision trace -- fail loudly at the boundary.
        """
        v = v.strip()
        if "@" in v:
            raise ValueError("subject_id must be pseudonymous; an email address is not")
        if re.fullmatch(r"[+()\d\s-]{7,}", v):
            raise ValueError("subject_id must be pseudonymous; this looks like a phone number")
        if not _SUBJECT_ID.match(v):
            raise ValueError(
                "subject_id must be 3-64 chars of letters, digits, dot, colon, dash, "
                "underscore"
            )
        return v


# --------------------------------------------------------------------------- response


class EvidenceScores(Strict):
    """The five evidence-dimension scores, each in [0, 1].

    Semantics are fixed and non-obvious, so they are restated wherever they surface:
    **0.5 means "exactly at this claim policy's minimum acceptable level"**, 1.0 means
    "at or above the level the policy considers comfortably sufficient", and values
    below 0.5 are below the policy floor. They are not probabilities and not
    percentages of anything.
    """

    coverage_score: float = Field(ge=0.0, le=1.0)
    freshness_score: float = Field(ge=0.0, le=1.0)
    signal_quality_score: float = Field(ge=0.0, le=1.0)
    consistency_score: float = Field(ge=0.0, le=1.0)
    baseline_maturity_score: float = Field(ge=0.0, le=1.0)


class Retry(Strict):
    recommended: bool
    after: str | None = None
    required_evidence: list[str] = Field(default_factory=list)


class GateRecord(Strict):
    """One fired gate, with the numbers that made it fire."""

    code: ReasonCode
    dimension: Dimension
    outcome: Outcome
    detail: str
    measurements: dict[str, Any] = Field(default_factory=dict)


class DecisionTrace(Strict):
    """Structured record the explanation is rendered from.

    Everything a caller might want to audit lives here rather than in prose.
    """

    claim_type: str
    target_window: TimeWindow
    evaluated_at: datetime
    gates: list[GateRecord] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    support_components: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)
    observation_counts: dict[str, int] = Field(default_factory=dict)
    integrity_notes: list[str] = Field(default_factory=list)


class DecisionResponse(Strict):
    decision: Decision
    confidence: float = Field(ge=0.0, le=1.0)
    claim_support_probability: float = Field(ge=0.0, le=1.0)
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    evidence: EvidenceScores
    explanation: str
    limitations: list[str] = Field(default_factory=list)
    retry: Retry
    trace: DecisionTrace
    claim_type: str
    subject_id: str
    model_version: str = MODEL_VERSION
    policy_version: str = POLICY_VERSION
    # v0.1 has no learned component, so claim_support_probability is a deterministic
    # score. This flag exists so no downstream reader can mistake it for a calibrated
    # probability (ADR 0002).
    support_is_calibrated: bool = False
