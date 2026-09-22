"""Core AERIS domain models.

All models are immutable (``frozen=True``). State changes produce new instances via
``model_copy(update=...)``, which keeps World State updates explicit and snapshots cheap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aeris.domain.enums import (
    AssignmentTask,
    DetectionSource,
    DroneStatus,
    LinkState,
    MissionStatus,
    OperatorActionType,
    TriageDecision,
    ZoneStatus,
)
from aeris.domain.geo import GeoPoint, GeoPolygon


def new_id() -> str:
    return uuid4().hex


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


# --------------------------------------------------------------------------- fleet


class DroneCapability(_Frozen):
    """Static capabilities of an airframe. Endurance and speed are nominal, not measured."""

    camera: bool = True
    thermal: bool = False
    cruise_speed_mps: float = Field(8.0, gt=0)
    nominal_endurance_s: float = Field(1500.0, gt=0)
    camera_footprint_width_m: float = Field(40.0, gt=0)


class Drone(_Frozen):
    drone_id: str
    name: str
    capability: DroneCapability = Field(default_factory=DroneCapability)


class TelemetryFrame(_Frozen):
    """One telemetry sample exactly as an adapter delivered it. Not yet interpreted."""

    drone_id: str
    timestamp: datetime
    position: GeoPoint
    heading_deg: float = Field(0.0, ge=0, lt=360)
    velocity_mps: float = Field(0.0, ge=0)
    battery_percent: float = Field(ge=0, le=100)
    estimated_remaining_s: float = Field(ge=0)
    connection_quality: float = Field(1.0, ge=0, le=1)
    camera_available: bool = True
    thermal_available: bool = False


class DroneState(_Frozen):
    """Best known state of one drone: latest telemetry plus what AERIS knows it is doing."""

    drone_id: str
    timestamp: datetime
    position: GeoPoint
    heading_deg: float = Field(0.0, ge=0, lt=360)
    velocity_mps: float = Field(0.0, ge=0)
    battery_percent: float = Field(ge=0, le=100)
    estimated_remaining_s: float = Field(ge=0)
    connection_quality: float = Field(1.0, ge=0, le=1)
    link_state: LinkState = LinkState.CONNECTED
    status: DroneStatus = DroneStatus.IDLE
    assigned_zone_id: str | None = None
    coverage_completed: float = Field(0.0, ge=0, le=1)
    camera_available: bool = True
    thermal_available: bool = False

    @classmethod
    def from_telemetry(cls, frame: TelemetryFrame, previous: DroneState | None = None) -> Self:
        """Merge a telemetry frame with the previously known mission-level fields."""
        mission_fields = (
            {
                "status": previous.status,
                "assigned_zone_id": previous.assigned_zone_id,
                "coverage_completed": previous.coverage_completed,
            }
            if previous is not None
            else {}
        )
        return cls(**frame.model_dump(), **mission_fields)

    @property
    def is_available_for_assignment(self) -> bool:
        return self.link_state.is_usable and self.status.is_available_for_assignment

    @property
    def latitude(self) -> float:
        return self.position.latitude

    @property
    def longitude(self) -> float:
        return self.position.longitude

    @property
    def altitude_m(self) -> float:
        return self.position.altitude_m


# --------------------------------------------------------------------------- search area


class SearchArea(_Frozen):
    polygon: GeoPolygon
    search_altitude_m: float = Field(60.0, gt=0)
    overlap_fraction: float = Field(0.2, ge=0, lt=1)


class SearchZone(_Frozen):
    zone_id: str
    polygon: GeoPolygon
    priority: float = Field(0.5, ge=0, le=1)
    coverage: float = Field(0.0, ge=0, le=1)
    assigned_drone_id: str | None = None
    status: ZoneStatus = ZoneStatus.UNSEARCHED
    last_searched_at: datetime | None = None
    detection_ids: tuple[str, ...] = ()

    @property
    def remaining_fraction(self) -> float:
        return 1.0 - self.coverage


# --------------------------------------------------------------------------- planning


class Waypoint(_Frozen):
    position: GeoPoint
    speed_mps: float | None = None


class WaypointPlan(_Frozen):
    plan_id: str = Field(default_factory=new_id)
    drone_id: str
    zone_id: str | None
    task: AssignmentTask
    waypoints: tuple[Waypoint, ...] = Field(min_length=1)
    start_fraction: float = Field(
        0.0, ge=0, le=1, description="Fraction of the zone already covered"
    )

    @property
    def length_m(self) -> float:
        return sum(
            a.position.distance_to(b.position)
            for a, b in zip(self.waypoints, self.waypoints[1:], strict=False)
        )


class MissionAssignment(_Frozen):
    assignment_id: str = Field(default_factory=new_id)
    mission_id: str
    drone_id: str
    zone_id: str | None
    task: AssignmentTask
    plan_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    reason: str = ""


# --------------------------------------------------------------------------- detections


class DetectionObservation(_Frozen):
    observation_id: str = Field(default_factory=new_id)
    drone_id: str
    timestamp: datetime
    source: DetectionSource
    confidence: float = Field(ge=0, le=1)
    position: GeoPoint
    movement_observed: bool = False


class Detection(_Frozen):
    """A cluster of observations that may refer to the same person. Never a confirmed person."""

    detection_id: str = Field(default_factory=new_id)
    mission_id: str
    zone_id: str | None
    position: GeoPoint
    first_observed_at: datetime
    last_observed_at: datetime
    observations: tuple[DetectionObservation, ...] = Field(min_length=1)
    triage: TriageDecision | None = None
    investigating_drone_id: str | None = None

    @property
    def sources(self) -> frozenset[DetectionSource]:
        return frozenset(o.source for o in self.observations)

    @property
    def max_confidence(self) -> float:
        return max(o.confidence for o in self.observations)

    @property
    def sensor_agreement(self) -> bool:
        return len(self.sources - {DetectionSource.OPERATOR}) >= 2


class CandidateSurvivor(_Frozen):
    """A detection escalated for human confirmation. Only an operator can confirm it."""

    candidate_id: str = Field(default_factory=new_id)
    mission_id: str
    detection_id: str
    escalated_at: datetime
    position: GeoPoint
    confirmed: bool | None = None
    resolved_at: datetime | None = None
    resolved_by_action_id: str | None = None


# --------------------------------------------------------------------------- audit


class DecisionRecord(_Frozen):
    """Audit record for one AI-assisted decision. Written for every decision, every provider."""

    decision_id: str = Field(default_factory=new_id)
    timestamp: datetime = Field(default_factory=utc_now)
    mission_id: str
    drone_id: str | None
    decision_type: str
    provider: str
    model: str | None
    input_state: dict[str, object]
    input_state_hash: str
    selected_value: str
    probabilities: dict[str, float] = Field(default_factory=dict)
    score: float | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    estimated_cost_usd: float | None = None
    policy_value: str | None = None
    policy_reason: str | None = None
    safety_override: bool = False
    safety_event_id: str | None = None
    final_action: str
    fallback_reason: str | None = None
    outcome: str | None = None


class SafetyEvent(_Frozen):
    """Audit record for one Safety Governor block or override."""

    event_id: str = Field(default_factory=new_id)
    timestamp: datetime = Field(default_factory=utc_now)
    mission_id: str
    drone_id: str | None
    rule: str
    proposed_action: str
    safe_alternative: str
    reason: str
    snapshot_hash: str | None = None


class OperatorAction(_Frozen):
    action_id: str = Field(default_factory=new_id)
    timestamp: datetime = Field(default_factory=utc_now)
    mission_id: str
    action_type: OperatorActionType
    target_id: str | None = None
    operator_id: str = "operator"
    note: str = ""


# --------------------------------------------------------------------------- mission


class Mission(_Frozen):
    mission_id: str = Field(default_factory=new_id)
    name: str
    status: MissionStatus = MissionStatus.CREATED
    search_area: SearchArea
    base_position: GeoPoint
    drone_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _timestamps_ordered(self) -> Self:
        if self.started_at is not None and self.started_at < self.created_at:
            msg = "started_at cannot precede created_at"
            raise ValueError(msg)
        if (
            self.completed_at is not None
            and self.started_at is not None
            and self.completed_at < self.started_at
        ):
            msg = "completed_at cannot precede started_at"
            raise ValueError(msg)
        return self

    def transition(self, status: MissionStatus, at: datetime) -> Mission:
        """Return a copy in ``status`` if the transition is legal, else raise ``ValueError``."""
        if status not in _MISSION_TRANSITIONS[self.status]:
            msg = f"illegal mission transition {self.status} -> {status}"
            raise ValueError(msg)
        update: dict[str, object] = {"status": status}
        if status is MissionStatus.ACTIVE and self.started_at is None:
            update["started_at"] = at
        if status.is_terminal:
            update["completed_at"] = at
        return self.model_copy(update=update)


_MISSION_TRANSITIONS: dict[MissionStatus, frozenset[MissionStatus]] = {
    MissionStatus.CREATED: frozenset({MissionStatus.ACTIVE, MissionStatus.ABORTED}),
    MissionStatus.ACTIVE: frozenset(
        {
            MissionStatus.PAUSED,
            MissionStatus.PERSON_LOCATED,
            MissionStatus.COMPLETED,
            MissionStatus.ABORTED,
        }
    ),
    MissionStatus.PAUSED: frozenset({MissionStatus.ACTIVE, MissionStatus.ABORTED}),
    MissionStatus.PERSON_LOCATED: frozenset(),
    MissionStatus.COMPLETED: frozenset(),
    MissionStatus.ABORTED: frozenset(),
}
