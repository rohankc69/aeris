"""Simulated fleet (**simulated** behavior, no physics engine).

Each drone is a point that flies waypoint plans at cruise speed, drains battery at a
configurable rate, and can have its link switched off so it stops reporting. This is enough
to exercise every coordination, safety, and reassignment path without ROS or PX4.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from aeris.clock import Clock
from aeris.domain.geo import GeoPoint
from aeris.domain.models import Drone, TelemetryFrame, WaypointPlan
from aeris.fleet.base import CommandResult
from aeris.planning.geo_frame import LocalFrame


class SimFlightMode(StrEnum):
    IDLE = "IDLE"
    MISSION = "MISSION"
    RETURNING = "RETURNING"
    HOLDING = "HOLDING"
    LANDED = "LANDED"


@dataclass(frozen=True)
class SimDroneConfig:
    drone: Drone
    start_position: GeoPoint | None = None
    battery_percent: float = 100.0
    battery_drain_percent_per_s: float = 100.0 / 1500.0
    link_online: bool = True


@dataclass
class _SimDrone:
    drone: Drone
    x: float
    y: float
    altitude_m: float
    battery_percent: float
    drain_per_s: float
    link_online: bool
    mode: SimFlightMode = SimFlightMode.IDLE
    heading_deg: float = 0.0
    speed_mps: float = 0.0
    waypoints: list[tuple[float, float, float]] = field(default_factory=list)
    next_index: int = 0
    drain_multiplier: float = 1.0


class FakeFleetAdapter:
    def __init__(
        self, *, base_position: GeoPoint, clock: Clock, drones: list[SimDroneConfig]
    ) -> None:
        self._clock = clock
        self._frame = LocalFrame(base_position)
        self._base_xy = (0.0, 0.0)
        self._drones: dict[str, _SimDrone] = {}
        for cfg in drones:
            start = cfg.start_position or base_position
            x, y = self._frame.to_local(start)
            self._drones[cfg.drone.drone_id] = _SimDrone(
                drone=cfg.drone,
                x=x,
                y=y,
                altitude_m=start.altitude_m,
                battery_percent=cfg.battery_percent,
                drain_per_s=cfg.battery_drain_percent_per_s,
                link_online=cfg.link_online,
            )

    # ------------------------------------------------------------- FleetAdapter

    async def get_telemetry(self) -> list[TelemetryFrame]:
        now = self._clock.now()
        frames = []
        for sim in self._drones.values():
            if not sim.link_online:
                continue
            remaining_s = sim.battery_percent / (sim.drain_per_s * sim.drain_multiplier)
            frames.append(
                TelemetryFrame(
                    drone_id=sim.drone.drone_id,
                    timestamp=now,
                    position=self._frame.to_geo(sim.x, sim.y, sim.altitude_m),
                    heading_deg=sim.heading_deg,
                    velocity_mps=sim.speed_mps,
                    battery_percent=sim.battery_percent,
                    estimated_remaining_s=remaining_s,
                    camera_available=sim.drone.capability.camera,
                    thermal_available=sim.drone.capability.thermal,
                )
            )
        return frames

    async def send_mission(self, drone_id: str, plan: WaypointPlan) -> CommandResult:
        sim = self._drones.get(drone_id)
        if sim is None:
            return CommandResult(drone_id=drone_id, accepted=False, message="unknown drone")
        if not sim.link_online:
            return CommandResult(drone_id=drone_id, accepted=False, message="link offline")
        if sim.mode is SimFlightMode.LANDED:
            return CommandResult(drone_id=drone_id, accepted=False, message="drone is landed")
        sim.waypoints = [
            (*self._frame.to_local(w.position), w.position.altitude_m) for w in plan.waypoints
        ]
        sim.next_index = 0
        sim.mode = SimFlightMode.MISSION
        return CommandResult(drone_id=drone_id, accepted=True, message=f"plan {plan.plan_id}")

    async def return_to_base(self, drone_id: str) -> CommandResult:
        sim = self._drones.get(drone_id)
        if sim is None:
            return CommandResult(drone_id=drone_id, accepted=False, message="unknown drone")
        if not sim.link_online:
            return CommandResult(drone_id=drone_id, accepted=False, message="link offline")
        sim.waypoints = [(*self._base_xy, sim.altitude_m)]
        sim.next_index = 0
        sim.mode = SimFlightMode.RETURNING
        return CommandResult(drone_id=drone_id, accepted=True, message="returning")

    async def hold(self, drone_id: str) -> CommandResult:
        sim = self._drones.get(drone_id)
        if sim is None:
            return CommandResult(drone_id=drone_id, accepted=False, message="unknown drone")
        if not sim.link_online:
            return CommandResult(drone_id=drone_id, accepted=False, message="link offline")
        sim.mode = SimFlightMode.HOLDING
        sim.speed_mps = 0.0
        return CommandResult(drone_id=drone_id, accepted=True, message="holding")

    # ------------------------------------------------------------- simulation controls

    def advance(self, dt_s: float) -> None:
        """Advance every drone by ``dt_s`` seconds of simulated flight."""
        for sim in self._drones.values():
            self._step(sim, dt_s)

    def set_link(self, drone_id: str, online: bool) -> None:
        self._drones[drone_id].link_online = online

    def set_battery_drain_multiplier(self, drone_id: str, multiplier: float) -> None:
        self._drones[drone_id].drain_multiplier = multiplier

    def set_battery(self, drone_id: str, battery_percent: float) -> None:
        self._drones[drone_id].battery_percent = battery_percent

    def flight_mode(self, drone_id: str) -> SimFlightMode:
        return self._drones[drone_id].mode

    def position(self, drone_id: str) -> GeoPoint:
        sim = self._drones[drone_id]
        return self._frame.to_geo(sim.x, sim.y, sim.altitude_m)

    # ------------------------------------------------------------- internals

    def _step(self, sim: _SimDrone, dt_s: float) -> None:
        if sim.mode in {SimFlightMode.IDLE, SimFlightMode.LANDED}:
            sim.speed_mps = 0.0
            return
        if sim.mode is SimFlightMode.HOLDING:
            self._drain(sim, dt_s)
            return
        if self._arrived(sim):
            return

        budget = sim.drone.capability.cruise_speed_mps * dt_s
        while budget > 0 and sim.next_index < len(sim.waypoints):
            tx, ty, tz = sim.waypoints[sim.next_index]
            dx, dy = tx - sim.x, ty - sim.y
            dist = math.hypot(dx, dy)
            if dist <= budget:
                sim.x, sim.y, sim.altitude_m = tx, ty, tz
                budget -= dist
                sim.next_index += 1
            else:
                sim.x += dx / dist * budget
                sim.y += dy / dist * budget
                sim.altitude_m = tz
                sim.heading_deg = math.degrees(math.atan2(dx, dy)) % 360
                budget = 0
        sim.speed_mps = sim.drone.capability.cruise_speed_mps
        self._drain(sim, dt_s)
        if sim.battery_percent <= 0:
            sim.mode = SimFlightMode.LANDED
            sim.altitude_m = 0.0
            sim.speed_mps = 0.0
            return
        self._arrived(sim)

    @staticmethod
    def _arrived(sim: _SimDrone) -> bool:
        """Settle a drone that has consumed its whole plan: land if returning, else hold."""
        if sim.next_index < len(sim.waypoints):
            return False
        if sim.mode is SimFlightMode.RETURNING:
            sim.mode = SimFlightMode.LANDED
            sim.altitude_m = 0.0
        else:
            sim.mode = SimFlightMode.HOLDING
        sim.speed_mps = 0.0
        return True

    @staticmethod
    def _drain(sim: _SimDrone, dt_s: float) -> None:
        sim.battery_percent = max(
            0.0, sim.battery_percent - sim.drain_per_s * sim.drain_multiplier * dt_s
        )
