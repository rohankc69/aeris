# aeris_px4_bridge

The only ROS 2 code in AERIS. It translates between PX4 (via uXRCE-DDS and `px4_msgs`) and the
AERIS JSON WebSocket protocol defined in `backend/aeris/fleet/px4/protocol.py`.

```
PX4 instance i  ──uXRCE-DDS──▶  /px4_i/fmu/out/*  ──▶  bridge  ──WebSocket JSON──▶  PX4FleetAdapter (AERIS)
                ◀──────────────  /px4_i/fmu/in/*   ◀──         ◀──────────────────
```

Vehicle mapping is explicit: parameter `vehicles: ["drone-01:1", "drone-02:2", "drone-03:3"]`
means AERIS `drone-01` is PX4 instance 1 (topics under `/px4_1`, MAV_SYS_ID 2), and so on.

## Behaviour

| AERIS message | Bridge action |
|---|---|
| `send_mission` | stores the waypoints, streams `OffboardControlMode` + `TrajectorySetpoint` at 10 Hz, switches to OFFBOARD and arms after ~1 s of setpoints, advances waypoints within `waypoint_tolerance_m` |
| `return_to_base` | `VEHICLE_CMD_NAV_RETURN_TO_LAUNCH`; setpoint streaming stops |
| `hold` | streams the current local position as the setpoint |
| (4 Hz) | publishes `telemetry` from `vehicle_global_position`, `vehicle_local_position`, `battery_status`, `vehicle_status` |
| `/aeris/<drone>/observation` (`std_msgs/String` JSON) | forwarded as an `observation` message; this is the hook for a perception node or a simulated detector |

Everything AERIS considers safety-critical (battery floors, geofence, separation, lost link)
stays in the backend's Safety Governor. PX4's own failsafes remain active underneath.

## Status

Written against the PX4 v1.15 `px4_msgs` set. It has not been exercised against a live PX4
SITL in the authoring session; use `docker compose --profile px4 up` and report issues.

## Build

```bash
cd ros_ws
rosdep install --from-paths src --ignore-src -y
colcon build --symlink-install
source install/setup.bash
ros2 launch aeris_px4_bridge bridge.launch.py
```
