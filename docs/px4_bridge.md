# PX4 / ROS 2 integration (Phase 4)

AERIS never depends on PX4 message structures. Everything PX4- or ROS-specific lives in two
places: the `aeris_px4_bridge` ROS 2 package (`ros_ws/src/aeris_px4_bridge/`) and the backend's
`PX4FleetAdapter` (`backend/aeris/fleet/px4/`). They share one small JSON protocol.

```
MissionManager ─▶ FleetAdapter protocol ─▶ PX4FleetAdapter ─WebSocket JSON─▶ aeris_px4_bridge ─uXRCE-DDS─▶ PX4 SITL ×3 ─▶ Gazebo (headless)
                                            (backend, no ROS)                (ROS 2 Jazzy, px4_msgs)
```

The same `MissionManager`, planners, decision engine and Safety Governor run unchanged; only
the adapter differs (`AERIS_FLEET_PROVIDER=px4`).

## Vehicle mapping

Explicit, never inferred:

| AERIS | PX4 instance (`px4 -i`) | ROS topics | MAV_SYS_ID |
|---|---|---|---|
| drone-01 | 1 | `/px4_1/fmu/...` | 2 |
| drone-02 | 2 | `/px4_2/fmu/...` | 3 |
| drone-03 | 3 | `/px4_3/fmu/...` | 4 |

Set with the bridge's `vehicles` parameter (`"drone-01:1"`, ...).

## Protocol

Defined in `backend/aeris/fleet/px4/protocol.py`, mirrored as plain dicts in the bridge.
`hello` (protocol version + vehicle map), `telemetry`, `observation`, `ack` flow to AERIS;
`send_mission`, `return_to_base`, `hold` flow to the bridge. Every command carries a
`request_id` and gets exactly one `ack`; the adapter times out otherwise and reports a
rejected `CommandResult`, which the Mission Manager treats like any other refusal.

While the bridge is unreachable the adapter reports **no telemetry**, so the World State
marks vehicles STALE and then LOST from telemetry age. That is deliberate: silence is never
read as health.

## Running the headless stack

Requires Docker (Colima, OrbStack or Docker Desktop). Images are prebuilt on GitHub Actions
(`.github/workflows/px4-images.yml`) and published to GHCR for amd64 and arm64.

```bash
docker compose --profile px4 pull           # prebuilt images; no PX4 compile
PX4_VEHICLES=2 docker compose --profile px4 up   # headless gazebo + PX4 SITL + XRCE agent + bridge
# host port 8765 taken? AERIS_BRIDGE_PORT=8766 docker compose --profile px4 up
# build locally instead (30-40 min): docker compose --profile px4 build

# in another terminal, against the same bridge
cd backend
AERIS_FLEET_PROVIDER=px4 AERIS_PX4_BRIDGE_URL=ws://localhost:8765 \
  uv run uvicorn aeris.api.app:create_app --factory
```

Create a mission from any scenario through the API or dashboard. With `px4` the scenario's
timeline events are ignored (they are simulation-only); the search area, base, restricted
regions and fleet roster are used. Home position for the SITL vehicles is set in
`docker/px4-sim/Dockerfile` and must lie near the scenario's base; `forest_search` matches.

### Resource notes (8 GB Apple Silicon)

- Allocate at least 5 GB to the Docker VM (`colima start --memory 5 --cpu 4`).
- Start with `PX4_VEHICLES=1`, then 2, then 3.
- Images are built for arm64; do not run x86 images under emulation.
- If it does not fit, run the `px4` profile on a Linux box or a small cloud VM and point
  `AERIS_PX4_BRIDGE_URL` at it.

## Perception hook

There is no on-board detector in the MVP. Publish a JSON `std_msgs/String` on
`/aeris/drone_01/observation` (hyphens in the id become underscores) (`{"source": "THERMAL", "confidence": 0.7, "latitude": ...,
"longitude": ...}`) and the bridge forwards it as an observation. A future perception node or
a Gazebo-side simulated sensor plugs in there without touching AERIS.

## Status

The adapter is tested against an in-process fake bridge (`backend/tests/unit/test_px4_adapter.py`).
The ROS 2 package and the Docker profile are written against PX4 v1.15 and ROS 2 Jazzy but were
not executed in the authoring session. Treat the first `docker compose --profile px4 up` as an
integration run and file what breaks.
