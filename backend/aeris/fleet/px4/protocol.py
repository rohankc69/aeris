"""Wire protocol between AERIS and the ``aeris_px4_bridge`` ROS 2 node.

JSON messages over one WebSocket. The bridge owns every PX4/ROS detail; this file is the only
shape the backend and the bridge share. Keep it small and versioned.

bridge → AERIS: ``telemetry``, ``observation``, ``ack``, ``hello``
AERIS → bridge: ``send_mission``, ``return_to_base``, ``hold``
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aeris.domain.enums import DetectionSource
from aeris.domain.geo import GeoPoint
from aeris.domain.models import DetectionObservation, TelemetryFrame, WaypointPlan

PROTOCOL_VERSION = 1


class _Msg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Hello(_Msg):
    type: Literal["hello"] = "hello"
    protocol_version: int
    vehicles: dict[str, int] = Field(description="AERIS drone id -> PX4 system/instance id")


class TelemetryMessage(_Msg):
    type: Literal["telemetry"] = "telemetry"
    drone_id: str
    timestamp: datetime
    latitude: float
    longitude: float
    altitude_m: float
    heading_deg: float = 0.0
    velocity_mps: float = 0.0
    battery_percent: float
    estimated_remaining_s: float
    connection_quality: float = 1.0
    camera_available: bool = True
    thermal_available: bool = False

    def to_frame(self) -> TelemetryFrame:
        return TelemetryFrame(
            drone_id=self.drone_id,
            timestamp=self.timestamp,
            position=GeoPoint(
                latitude=self.latitude, longitude=self.longitude, altitude_m=self.altitude_m
            ),
            heading_deg=self.heading_deg % 360,
            velocity_mps=max(0.0, self.velocity_mps),
            battery_percent=min(100.0, max(0.0, self.battery_percent)),
            estimated_remaining_s=max(0.0, self.estimated_remaining_s),
            connection_quality=min(1.0, max(0.0, self.connection_quality)),
            camera_available=self.camera_available,
            thermal_available=self.thermal_available,
        )


class ObservationMessage(_Msg):
    type: Literal["observation"] = "observation"
    drone_id: str
    timestamp: datetime
    source: DetectionSource
    confidence: float = Field(ge=0, le=1)
    latitude: float
    longitude: float
    movement_observed: bool = False

    def to_observation(self) -> DetectionObservation:
        return DetectionObservation(
            drone_id=self.drone_id,
            timestamp=self.timestamp,
            source=self.source,
            confidence=self.confidence,
            position=GeoPoint(latitude=self.latitude, longitude=self.longitude),
            movement_observed=self.movement_observed,
        )


class Ack(_Msg):
    type: Literal["ack"] = "ack"
    request_id: str
    drone_id: str
    accepted: bool
    message: str = ""


class WaypointMessage(_Msg):
    latitude: float
    longitude: float
    altitude_m: float
    speed_mps: float | None = None


class SendMission(_Msg):
    type: Literal["send_mission"] = "send_mission"
    request_id: str
    drone_id: str
    plan_id: str
    waypoints: list[WaypointMessage]

    @classmethod
    def from_plan(cls, request_id: str, plan: WaypointPlan) -> SendMission:
        return cls(
            request_id=request_id,
            drone_id=plan.drone_id,
            plan_id=plan.plan_id,
            waypoints=[
                WaypointMessage(
                    latitude=w.position.latitude,
                    longitude=w.position.longitude,
                    altitude_m=w.position.altitude_m,
                    speed_mps=w.speed_mps,
                )
                for w in plan.waypoints
            ],
        )


class ReturnToBase(_Msg):
    type: Literal["return_to_base"] = "return_to_base"
    request_id: str
    drone_id: str


class Hold(_Msg):
    type: Literal["hold"] = "hold"
    request_id: str
    drone_id: str


Inbound = Hello | TelemetryMessage | ObservationMessage | Ack
Outbound = SendMission | ReturnToBase | Hold


def parse_inbound(raw: str | bytes) -> Inbound:
    from pydantic import TypeAdapter  # noqa: PLC0415 - local to keep import time low

    adapter: TypeAdapter[Inbound] = TypeAdapter(Inbound)
    return adapter.validate_json(raw)
