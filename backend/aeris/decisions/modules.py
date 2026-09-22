"""Decision modules: one narrow typed question each.

Each module defines the compact state Jev sees, the question, and the bounded answer. There is
deliberately no "what should the fleet do?" module.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from aeris.decisions.base import ChoiceRequest, JsonDict, ProbabilityRequest, ScoreRequest
from aeris.domain.enums import Disposition, LinkState, TriageDecision

DRONE_DISPOSITION = "drone_disposition"
DETECTION_TRIAGE = "detection_triage"
ZONE_PRIORITY = "zone_priority"
HUMAN_REVIEW_GATE = "human_review_gate"


class _Input(BaseModel):
    model_config = ConfigDict(frozen=True)

    def to_state(self) -> JsonDict:
        return self.model_dump(mode="json")


class DroneDispositionInput(_Input):
    battery_percent: float = Field(ge=0, le=100)
    estimated_return_battery_percent: float = Field(ge=0)
    distance_to_base_m: float = Field(ge=0)
    zone_completion: float = Field(ge=0, le=1)
    connection_quality: float = Field(ge=0, le=1)
    link_state: LinkState
    telemetry_age_s: float = Field(ge=0)
    active_detection: bool = False

    def to_request(self, *, mission_id: str, drone_id: str) -> ChoiceRequest:
        return ChoiceRequest(
            decision_type=DRONE_DISPOSITION,
            question="What should this drone do next?",
            state=self.to_state(),
            choices=tuple(d.value for d in Disposition),
            mission_id=mission_id,
            drone_id=drone_id,
        )


class DetectionTriageInput(_Input):
    source_sensor: str
    thermal_confidence: float = Field(0.0, ge=0, le=1)
    visual_confidence: float = Field(0.0, ge=0, le=1)
    movement_observed: bool = False
    observation_count: int = Field(1, ge=1)
    sensor_agreement: bool = False
    distance_from_other_detections_m: float | None = None
    time_since_previous_detection_s: float | None = None
    environmental_conditions: str = "unknown"

    def to_request(self, *, mission_id: str, drone_id: str | None) -> ChoiceRequest:
        return ChoiceRequest(
            decision_type=DETECTION_TRIAGE,
            question=(
                "How should this candidate detection be handled? A detection is never a confirmed "
                "person; POSSIBLE_SURVIVOR means a human must confirm."
            ),
            state=self.to_state(),
            choices=tuple(t.value for t in TriageDecision),
            mission_id=mission_id,
            drone_id=drone_id,
        )


class ZonePriorityInput(_Input):
    zone_id: str
    coverage: float = Field(ge=0, le=1)
    time_since_last_search_s: float = Field(0.0, ge=0)
    terrain: str = "unknown"
    nearby_candidate_detection: bool = False
    distance_from_last_known_position_m: float = Field(ge=0)

    def to_request(self, *, mission_id: str) -> ScoreRequest:
        return ScoreRequest(
            decision_type=ZONE_PRIORITY,
            question=(
                "How urgently should this zone be searched next, from 0 (lowest) to 1 (highest)?"
            ),
            state=self.to_state(),
            mission_id=mission_id,
            min_score=0.0,
            max_score=1.0,
        )


class HumanReviewInput(_Input):
    model_uncertainty: float = Field(0.0, ge=0, le=1)
    sensors_disagree: bool = False
    state_incomplete: bool = False
    connection_quality: float = Field(1.0, ge=0, le=1)
    decisions_conflict: bool = False
    significant_behavior_change: bool = False
    candidate_count: int = Field(0, ge=0)

    def to_request(self, *, mission_id: str, drone_id: str | None = None) -> ProbabilityRequest:
        return ProbabilityRequest(
            decision_type=HUMAN_REVIEW_GATE,
            question="Does this situation require human intervention?",
            state=self.to_state(),
            mission_id=mission_id,
            drone_id=drone_id,
        )
