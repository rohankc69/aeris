"""Domain event catalogue.

Events are immutable facts about something that happened. They carry ids and small payloads,
never whole aggregates, so consumers look up current state in the World State service.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from aeris.domain.enums import LinkState, MissionStatus, OperatorActionType, ZoneStatus
from aeris.domain.geo import GeoPoint
from aeris.domain.models import new_id, utc_now


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: ClassVar[str] = "DomainEvent"

    event_id: str = Field(default_factory=new_id)
    timestamp: datetime = Field(default_factory=utc_now)
    mission_id: str

    @property
    def type_name(self) -> str:
        return type(self).event_type


# ----------------------------------------------------------------- mission lifecycle


class MissionCreated(DomainEvent):
    event_type: ClassVar[str] = "MissionCreated"
    name: str
    zone_count: int


class MissionStarted(DomainEvent):
    event_type: ClassVar[str] = "MissionStarted"


class MissionPaused(DomainEvent):
    event_type: ClassVar[str] = "MissionPaused"


class MissionResumed(DomainEvent):
    event_type: ClassVar[str] = "MissionResumed"


class MissionAborted(DomainEvent):
    event_type: ClassVar[str] = "MissionAborted"
    reason: str = ""


class MissionCompleted(DomainEvent):
    event_type: ClassVar[str] = "MissionCompleted"
    final_status: MissionStatus


# ----------------------------------------------------------------- fleet


class DroneRegistered(DomainEvent):
    event_type: ClassVar[str] = "DroneRegistered"
    drone_id: str


class TelemetryReceived(DomainEvent):
    event_type: ClassVar[str] = "TelemetryReceived"
    drone_id: str
    battery_percent: float
    position: GeoPoint


class DroneLinkStateChanged(DomainEvent):
    event_type: ClassVar[str] = "DroneLinkStateChanged"
    drone_id: str
    previous: LinkState
    current: LinkState
    telemetry_age_s: float


class DroneDisconnected(DomainEvent):
    event_type: ClassVar[str] = "DroneDisconnected"
    drone_id: str
    telemetry_age_s: float


class DroneReturning(DomainEvent):
    event_type: ClassVar[str] = "DroneReturning"
    drone_id: str
    reason: str


# ----------------------------------------------------------------- zones


class ZoneAssigned(DomainEvent):
    event_type: ClassVar[str] = "ZoneAssigned"
    zone_id: str
    drone_id: str
    assignment_id: str
    start_fraction: float = 0.0
    reason: str = ""


class ZoneCoverageUpdated(DomainEvent):
    event_type: ClassVar[str] = "ZoneCoverageUpdated"
    zone_id: str
    drone_id: str
    coverage: float


class ZoneStatusChanged(DomainEvent):
    event_type: ClassVar[str] = "ZoneStatusChanged"
    zone_id: str
    previous: ZoneStatus
    current: ZoneStatus


class ZoneReassignmentRequested(DomainEvent):
    event_type: ClassVar[str] = "ZoneReassignmentRequested"
    zone_id: str
    previous_drone_id: str | None
    remaining_fraction: float
    reason: str


# ----------------------------------------------------------------- detections


class DetectionCreated(DomainEvent):
    event_type: ClassVar[str] = "DetectionCreated"
    detection_id: str
    drone_id: str
    zone_id: str | None
    position: GeoPoint
    confidence: float


class DetectionUpdated(DomainEvent):
    event_type: ClassVar[str] = "DetectionUpdated"
    detection_id: str
    observation_count: int
    max_confidence: float


class CandidateEscalated(DomainEvent):
    event_type: ClassVar[str] = "CandidateEscalated"
    candidate_id: str
    detection_id: str
    position: GeoPoint


class HumanReviewRequested(DomainEvent):
    event_type: ClassVar[str] = "HumanReviewRequested"
    subject_type: str
    subject_id: str
    reason: str


class SurvivorConfirmed(DomainEvent):
    event_type: ClassVar[str] = "SurvivorConfirmed"
    candidate_id: str
    action_id: str


class SurvivorRejected(DomainEvent):
    event_type: ClassVar[str] = "SurvivorRejected"
    candidate_id: str
    action_id: str


# ----------------------------------------------------------------- decisions & safety


class DecisionRequested(DomainEvent):
    event_type: ClassVar[str] = "DecisionRequested"
    decision_type: str
    drone_id: str | None
    provider: str


class DecisionCompleted(DomainEvent):
    event_type: ClassVar[str] = "DecisionCompleted"
    decision_id: str
    decision_type: str
    drone_id: str | None
    provider: str
    selected_value: str
    final_action: str
    safety_override: bool


class SafetyOverrideTriggered(DomainEvent):
    event_type: ClassVar[str] = "SafetyOverrideTriggered"
    safety_event_id: str
    drone_id: str | None
    rule: str
    proposed_action: str
    safe_alternative: str


class OperatorActionReceived(DomainEvent):
    event_type: ClassVar[str] = "OperatorActionReceived"
    action_id: str
    action_type: OperatorActionType
    target_id: str | None
