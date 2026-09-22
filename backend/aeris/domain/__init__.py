"""AERIS domain model.

Pure, ROS-free, framework-free Pydantic models and enums. Nothing in this package performs
I/O or imports transport, database, or autopilot libraries. Every other package speaks this
vocabulary.
"""

from aeris.domain.enums import (
    AssignmentTask,
    DetectionSource,
    Disposition,
    DroneStatus,
    LinkState,
    MissionStatus,
    OperatorActionType,
    TriageDecision,
    ZoneStatus,
)
from aeris.domain.geo import BoundingBox, GeoPoint, GeoPolygon
from aeris.domain.models import (
    CandidateSurvivor,
    DecisionRecord,
    Detection,
    DetectionObservation,
    Drone,
    DroneCapability,
    DroneState,
    Mission,
    MissionAssignment,
    OperatorAction,
    SafetyEvent,
    SearchArea,
    SearchZone,
    TelemetryFrame,
    Waypoint,
    WaypointPlan,
)

__all__ = [
    "AssignmentTask",
    "BoundingBox",
    "CandidateSurvivor",
    "DecisionRecord",
    "Detection",
    "DetectionObservation",
    "DetectionSource",
    "Disposition",
    "Drone",
    "DroneCapability",
    "DroneState",
    "DroneStatus",
    "GeoPoint",
    "GeoPolygon",
    "LinkState",
    "Mission",
    "MissionAssignment",
    "MissionStatus",
    "OperatorAction",
    "OperatorActionType",
    "SafetyEvent",
    "SearchArea",
    "SearchZone",
    "TelemetryFrame",
    "TriageDecision",
    "Waypoint",
    "WaypointPlan",
    "ZoneStatus",
]
