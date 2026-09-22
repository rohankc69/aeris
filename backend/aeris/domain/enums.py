"""Enumerations shared across the AERIS domain."""

from __future__ import annotations

from enum import StrEnum


class MissionStatus(StrEnum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    PERSON_LOCATED = "PERSON_LOCATED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            MissionStatus.PERSON_LOCATED,
            MissionStatus.COMPLETED,
            MissionStatus.ABORTED,
        }


class DroneStatus(StrEnum):
    """What a drone is currently doing, as understood by AERIS."""

    IDLE = "IDLE"
    SEARCHING = "SEARCHING"
    INVESTIGATING = "INVESTIGATING"
    HOLDING = "HOLDING"
    RETURNING = "RETURNING"
    LANDED = "LANDED"
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def is_available_for_assignment(self) -> bool:
        return self in {DroneStatus.IDLE, DroneStatus.SEARCHING}


class LinkState(StrEnum):
    """Communication state derived from telemetry age. Never assumed, always derived."""

    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    LOST = "LOST"

    @property
    def is_usable(self) -> bool:
        """True when the drone may receive new work."""
        return self is LinkState.CONNECTED


class ZoneStatus(StrEnum):
    UNSEARCHED = "UNSEARCHED"
    ASSIGNED = "ASSIGNED"
    SEARCHING = "SEARCHING"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    REQUIRES_RECHECK = "REQUIRES_RECHECK"

    @property
    def needs_work(self) -> bool:
        """True when the zone still has unsearched ground and no drone working on it."""
        return self in {ZoneStatus.UNSEARCHED, ZoneStatus.PARTIAL, ZoneStatus.REQUIRES_RECHECK}


class AssignmentTask(StrEnum):
    SEARCH = "SEARCH"
    INVESTIGATE = "INVESTIGATE"
    RETURN = "RETURN"
    HOLD = "HOLD"


class Disposition(StrEnum):
    """Bounded answer to 'what should this drone do next?'."""

    CONTINUE_SEARCH = "CONTINUE_SEARCH"
    RETURN_TO_BASE = "RETURN_TO_BASE"
    HANDOFF_ZONE = "HANDOFF_ZONE"
    HOLD = "HOLD"
    REQUEST_HUMAN_REVIEW = "REQUEST_HUMAN_REVIEW"


class TriageDecision(StrEnum):
    """Bounded answer to 'what should we do with this candidate detection?'.

    ``POSSIBLE_SURVIVOR`` is the strongest value an AI decision can produce. It never means a
    person has been found; it means a human must confirm.
    """

    IGNORE = "IGNORE"
    RECHECK = "RECHECK"
    INVESTIGATE = "INVESTIGATE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    POSSIBLE_SURVIVOR = "POSSIBLE_SURVIVOR"


class DetectionSource(StrEnum):
    THERMAL = "THERMAL"
    VISUAL = "VISUAL"
    FUSED = "FUSED"
    OPERATOR = "OPERATOR"


class OperatorActionType(StrEnum):
    START_MISSION = "START_MISSION"
    PAUSE_MISSION = "PAUSE_MISSION"
    RESUME_MISSION = "RESUME_MISSION"
    ABORT_MISSION = "ABORT_MISSION"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    CONFIRM_DETECTION = "CONFIRM_DETECTION"
    REJECT_DETECTION = "REJECT_DETECTION"
    RETURN_DRONE = "RETURN_DRONE"
    HOLD_DRONE = "HOLD_DRONE"
    RESUME_DRONE = "RESUME_DRONE"
