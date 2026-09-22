"""One PX4 vehicle: subscriptions, offboard waypoint following, and command handling.

Status: **real** code written against px4_msgs (PX4 v1.15 message set), not executed in the
authoring session. Run it through the Docker ``px4`` profile.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from px4_msgs.msg import (
    BatteryStatus,
    OffboardControlMode,
    TrajectorySetpoint,
    VehicleCommand,
    VehicleGlobalPosition,
    VehicleLocalPosition,
    VehicleStatus,
)
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

PX4_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
)

EARTH_RADIUS_M = 6_371_008.8


@dataclass
class Waypoint:
    latitude: float
    longitude: float
    altitude_m: float
    speed_mps: float | None = None


@dataclass
class VehicleState:
    latitude: float | None = None
    longitude: float | None = None
    altitude_amsl_m: float | None = None
    altitude_agl_m: float | None = None
    heading_deg: float = 0.0
    velocity_mps: float = 0.0
    battery_fraction: float | None = None
    battery_remaining_s: float | None = None
    armed: bool = False
    nav_state: int = 0
    ref_lat: float | None = None
    ref_lon: float | None = None
    ref_alt: float | None = None
    local_x: float = 0.0
    local_y: float = 0.0
    local_z: float = 0.0

    @property
    def has_fix(self) -> bool:
        return self.latitude is not None and self.ref_lat is not None


@dataclass
class Vehicle:
    node: Node
    drone_id: str
    instance: int
    waypoint_tolerance_m: float
    cruise_speed_mps: float
    state: VehicleState = field(default_factory=VehicleState)
    waypoints: list[Waypoint] = field(default_factory=list)
    next_index: int = 0
    mode: str = "idle"  # idle | offboard | hold | rtl
    offboard_ticks: int = 0
    hold_target: tuple[float, float, float] | None = None

    def __post_init__(self) -> None:
        ns = f"/px4_{self.instance}" if self.instance > 0 else ""
        n = self.node
        n.create_subscription(VehicleGlobalPosition, f"{ns}/fmu/out/vehicle_global_position", self._on_global, PX4_QOS)
        n.create_subscription(VehicleLocalPosition, f"{ns}/fmu/out/vehicle_local_position", self._on_local, PX4_QOS)
        n.create_subscription(BatteryStatus, f"{ns}/fmu/out/battery_status", self._on_battery, PX4_QOS)
        n.create_subscription(VehicleStatus, f"{ns}/fmu/out/vehicle_status", self._on_status, PX4_QOS)
        self._pub_mode = n.create_publisher(OffboardControlMode, f"{ns}/fmu/in/offboard_control_mode", 10)
        self._pub_setpoint = n.create_publisher(TrajectorySetpoint, f"{ns}/fmu/in/trajectory_setpoint", 10)
        self._pub_cmd = n.create_publisher(VehicleCommand, f"{ns}/fmu/in/vehicle_command", 10)

    # ------------------------------------------------------------- subscriptions

    def _on_global(self, msg: VehicleGlobalPosition) -> None:
        s = self.state
        s.latitude, s.longitude, s.altitude_amsl_m = float(msg.lat), float(msg.lon), float(msg.alt)

    def _on_local(self, msg: VehicleLocalPosition) -> None:
        s = self.state
        if msg.xy_global and msg.z_global:
            s.ref_lat, s.ref_lon, s.ref_alt = float(msg.ref_lat), float(msg.ref_lon), float(msg.ref_alt)
        s.local_x, s.local_y, s.local_z = float(msg.x), float(msg.y), float(msg.z)
        s.altitude_agl_m = -float(msg.z)
        s.velocity_mps = math.hypot(float(msg.vx), float(msg.vy))
        s.heading_deg = math.degrees(float(msg.heading)) % 360.0

    def _on_battery(self, msg: BatteryStatus) -> None:
        self.state.battery_fraction = float(msg.remaining) if msg.remaining >= 0 else None
        self.state.battery_remaining_s = float(msg.time_remaining_s) if msg.time_remaining_s > 0 else None

    def _on_status(self, msg: VehicleStatus) -> None:
        self.state.armed = msg.arming_state == VehicleStatus.ARMING_STATE_ARMED
        self.state.nav_state = int(msg.nav_state)

    # ------------------------------------------------------------- commands (from AERIS)

    def send_mission(self, waypoints: list[Waypoint]) -> tuple[bool, str]:
        if not self.state.has_fix:
            return False, "no position fix yet"
        if not waypoints:
            return False, "empty plan"
        self.waypoints = waypoints
        self.next_index = 0
        self.mode = "offboard"
        self.offboard_ticks = 0
        return True, f"{len(waypoints)} waypoints queued"

    def return_to_base(self) -> tuple[bool, str]:
        self.mode = "rtl"
        self.waypoints = []
        self._command(VehicleCommand.VEHICLE_CMD_NAV_RETURN_TO_LAUNCH)
        return True, "RTL commanded"

    def hold(self) -> tuple[bool, str]:
        if not self.state.has_fix:
            return False, "no position fix yet"
        self.hold_target = (self.state.local_x, self.state.local_y, self.state.local_z)
        self.mode = "hold"
        return True, "holding position"

    # ------------------------------------------------------------- control loop (10 Hz)

    def tick(self) -> None:
        if self.mode == "offboard":
            self._offboard_step()
        elif self.mode == "hold" and self.hold_target is not None:
            self._stream(self.hold_target)

    def _offboard_step(self) -> None:
        if self.next_index >= len(self.waypoints):
            self.hold_target = (self.state.local_x, self.state.local_y, self.state.local_z)
            self.mode = "hold"
            return
        target = self._to_local(self.waypoints[self.next_index])
        self._stream(target)
        self.offboard_ticks += 1
        if self.offboard_ticks == 10:  # ~1 s of setpoints before switching, as PX4 requires
            self._command(VehicleCommand.VEHICLE_CMD_DO_SET_MODE, 1.0, 6.0)  # custom: offboard
            if not self.state.armed:
                self._command(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, 1.0)
        dx = target[0] - self.state.local_x
        dy = target[1] - self.state.local_y
        dz = target[2] - self.state.local_z
        if math.sqrt(dx * dx + dy * dy + dz * dz) <= self.waypoint_tolerance_m:
            self.next_index += 1

    def _stream(self, target: tuple[float, float, float]) -> None:
        now = int(self.node.get_clock().now().nanoseconds / 1000)
        mode = OffboardControlMode()
        mode.timestamp = now
        mode.position = True
        self._pub_mode.publish(mode)
        sp = TrajectorySetpoint()
        sp.timestamp = now
        sp.position = [float(target[0]), float(target[1]), float(target[2])]
        sp.yaw = float("nan")
        self._pub_setpoint.publish(sp)

    def _command(self, command: int, param1: float = 0.0, param2: float = 0.0) -> None:
        msg = VehicleCommand()
        msg.timestamp = int(self.node.get_clock().now().nanoseconds / 1000)
        msg.command = command
        msg.param1 = param1
        msg.param2 = param2
        msg.target_system = self.instance + 1  # PX4 convention: MAV_SYS_ID = instance + 1
        msg.target_component = 1
        msg.source_system = 1
        msg.source_component = 1
        msg.from_external = True
        self._pub_cmd.publish(msg)

    def _to_local(self, wp: Waypoint) -> tuple[float, float, float]:
        """WGS84 -> PX4 local NED using the vehicle's own local-position reference."""
        s = self.state
        assert s.ref_lat is not None and s.ref_lon is not None and s.ref_alt is not None
        lat0, lon0 = math.radians(s.ref_lat), math.radians(s.ref_lon)
        north = (math.radians(wp.latitude) - lat0) * EARTH_RADIUS_M
        east = (math.radians(wp.longitude) - lon0) * math.cos(lat0) * EARTH_RADIUS_M
        down = -wp.altitude_m  # AERIS altitudes are metres above the launch reference
        return north, east, down

    # ------------------------------------------------------------- telemetry (to AERIS)

    def telemetry(self, timestamp_iso: str, camera: bool, thermal: bool) -> dict | None:
        s = self.state
        if s.latitude is None or s.longitude is None:
            return None
        fraction = s.battery_fraction if s.battery_fraction is not None else 1.0
        return {
            "type": "telemetry",
            "drone_id": self.drone_id,
            "timestamp": timestamp_iso,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "altitude_m": s.altitude_agl_m if s.altitude_agl_m is not None else 0.0,
            "heading_deg": s.heading_deg,
            "velocity_mps": s.velocity_mps,
            "battery_percent": 100.0 * fraction,
            "estimated_remaining_s": s.battery_remaining_s if s.battery_remaining_s is not None else 0.0,
            "connection_quality": 1.0,
            "camera_available": camera,
            "thermal_available": thermal,
        }
