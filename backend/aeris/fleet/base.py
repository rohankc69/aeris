from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from aeris.domain.models import DetectionObservation, TelemetryFrame, WaypointPlan


class CommandResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    drone_id: str
    accepted: bool
    message: str = ""


class FleetAdapter(Protocol):
    """Transport-agnostic interface to a fleet. Implementations translate to/from vehicles."""

    async def get_telemetry(self) -> list[TelemetryFrame]:
        """Latest frame for every drone currently reporting. Silent drones are simply absent."""
        ...

    async def get_observations(self) -> list[DetectionObservation]:
        """Sensor detections produced since the previous call. Perception itself lives on the
        vehicle side (or is simulated); AERIS only ever sees observations."""
        ...

    async def send_mission(self, drone_id: str, plan: WaypointPlan) -> CommandResult: ...

    async def return_to_base(self, drone_id: str) -> CommandResult: ...

    async def hold(self, drone_id: str) -> CommandResult: ...
