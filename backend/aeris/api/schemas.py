"""Request/response schemas for the REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from aeris.domain.enums import MissionStatus
from aeris.simulation.scenario import Scenario


class CreateMissionRequest(BaseModel):
    scenario: str | None = Field(default=None, description="Bundled scenario name")
    spec: Scenario | None = Field(default=None, description="Inline scenario definition")
    time_scale: float = Field(1.0, gt=0, le=1000, description="Simulated seconds per real second")
    autostart: bool = False

    @model_validator(mode="after")
    def _one_source(self) -> CreateMissionRequest:
        if (self.scenario is None) == (self.spec is None):
            msg = "provide exactly one of 'scenario' or 'spec'"
            raise ValueError(msg)
        return self


class MissionSummary(BaseModel):
    mission_id: str
    name: str
    status: MissionStatus
    scenario: str
    time_scale: float
    running: bool
    elapsed_s: float
    coverage_fraction: float
    zone_count: int
    drone_count: int


class CommandResponse(BaseModel):
    ok: bool
    mission_id: str
    status: MissionStatus
    detail: str = ""
