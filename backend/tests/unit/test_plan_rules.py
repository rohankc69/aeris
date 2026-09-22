from datetime import UTC, datetime

import pytest

from aeris.config import SafetySettings
from aeris.domain import (
    AssignmentTask,
    Drone,
    DroneCapability,
    DroneState,
    DroneStatus,
    GeoPoint,
    GeoPolygon,
    LinkState,
    Mission,
    SearchArea,
    TelemetryFrame,
    Waypoint,
    WaypointPlan,
)
from aeris.planning import GridPartitioner, LocalFrame
from aeris.safety import SafetyGovernor
from aeris.world.snapshot import DroneView, WorldSnapshot

T0 = datetime(2026, 1, 1, tzinfo=UTC)
BASE = GeoPoint(latitude=47.0, longitude=8.0)
FRAME = LocalFrame(BASE)
AREA = GeoPolygon(
    vertices=(
        FRAME.to_geo(0, 0),
        FRAME.to_geo(1000, 0),
        FRAME.to_geo(1000, 1000),
        FRAME.to_geo(0, 1000),
    )
)
NO_FLY = GeoPolygon(
    vertices=(
        FRAME.to_geo(400, 400),
        FRAME.to_geo(600, 400),
        FRAME.to_geo(600, 600),
        FRAME.to_geo(400, 600),
    )
)


def snapshot(restricted: tuple[GeoPolygon, ...] = ()) -> WorldSnapshot:
    mission = Mission(
        name="t",
        search_area=SearchArea(polygon=AREA),
        base_position=BASE,
        restricted_regions=restricted,
        created_at=T0,
    )
    return WorldSnapshot(taken_at=T0, mission=mission, drones=(), zones=())


def view(
    battery: float = 90,
    *,
    status: DroneStatus = DroneStatus.IDLE,
    link: LinkState = LinkState.CONNECTED,
) -> DroneView:
    drone = Drone(
        drone_id="d",
        name="d",
        capability=DroneCapability(cruise_speed_mps=10, nominal_endurance_s=1500),
    )
    frame = TelemetryFrame(
        drone_id="d",
        timestamp=T0,
        position=FRAME.to_geo(10, 10, 0),
        battery_percent=battery,
        estimated_remaining_s=100,
    )
    state = DroneState.from_telemetry(frame).model_copy(
        update={"status": status, "link_state": link}
    )
    return DroneView(drone=drone, state=state, telemetry_age_s=0.0)


def plan(*points: tuple[float, float], altitude: float = 60) -> WaypointPlan:
    return WaypointPlan(
        drone_id="d",
        zone_id="Z",
        task=AssignmentTask.SEARCH,
        waypoints=tuple(Waypoint(position=FRAME.to_geo(x, y, altitude)) for x, y in points),
    )


@pytest.fixture
def governor() -> SafetyGovernor:
    return SafetyGovernor(SafetySettings())


def test_good_plan_is_allowed(governor: SafetyGovernor) -> None:
    verdict = governor.validate_plan(
        plan((100, 100), (900, 100)), view(), snapshot(), source="planner"
    )
    assert verdict.allowed and verdict.event is None


def test_altitude_above_limit_is_rejected(governor: SafetyGovernor) -> None:
    verdict = governor.validate_plan(
        plan((100, 100), (900, 100), altitude=150), view(), snapshot(), source="planner"
    )
    assert not verdict.allowed
    assert verdict.violation is not None and verdict.violation.rule == "max_altitude"
    assert verdict.event is not None and verdict.event.safe_alternative == "plan_rejected"


def test_waypoint_outside_geofence_is_rejected_but_buffer_is_tolerated(
    governor: SafetyGovernor,
) -> None:
    outside = governor.validate_plan(
        plan((100, 100), (1200, 100)), view(), snapshot(), source="planner"
    )
    assert not outside.allowed and outside.violation.rule == "geofence"  # type: ignore[union-attr]
    edge = governor.validate_plan(
        plan((100, 100), (1040, 100)), view(), snapshot(), source="planner"
    )
    assert edge.allowed  # within the 50 m geofence buffer


def test_restricted_region_is_rejected(governor: SafetyGovernor) -> None:
    verdict = governor.validate_plan(
        plan((100, 100), (500, 500)), view(), snapshot((NO_FLY,)), source="planner"
    )
    assert not verdict.allowed and verdict.violation.rule == "restricted_region"  # type: ignore[union-attr]
    assert governor.validate_plan(
        plan((100, 100), (500, 500)), view(), snapshot(), source="planner"
    ).allowed


def test_unavailable_drone_cannot_receive_a_plan(governor: SafetyGovernor) -> None:
    returning = governor.validate_plan(
        plan((100, 100), (200, 100)),
        view(status=DroneStatus.RETURNING),
        snapshot(),
        source="planner",
    )
    assert not returning.allowed and returning.violation.rule == "unavailable_drone"  # type: ignore[union-attr]
    lost = governor.validate_plan(
        plan((100, 100), (200, 100)), view(link=LinkState.LOST), snapshot(), source="planner"
    )
    assert not lost.allowed and lost.violation.rule == "unavailable_drone"  # type: ignore[union-attr]


def test_unreachable_plan_is_rejected(governor: SafetyGovernor) -> None:
    # 20 km of track at 10 m/s = 2000 s, beyond a 1500 s endurance even at full battery.
    long_track = plan(*[(0 if i % 2 == 0 else 1000, i * 45) for i in range(21)])
    assert long_track.length_m > 15_000
    verdict = governor.validate_plan(long_track, view(100), snapshot(), source="planner")
    assert not verdict.allowed and verdict.violation.rule == "unreachable_plan"  # type: ignore[union-attr]
    # a short hop is fine at 90% but not at 30% once the return floor is respected
    short = plan((100, 100), (900, 100))
    assert governor.validate_plan(short, view(90), snapshot(), source="planner").allowed
    verdict = governor.validate_plan(short, view(31), snapshot(), source="planner")
    assert not verdict.allowed and verdict.violation.rule == "unreachable_plan"  # type: ignore[union-attr]


def test_partitioner_clips_restricted_regions_out_of_zones() -> None:
    partitioner = GridPartitioner(cell_size_m=250, restricted_clearance_m=20)
    with_hole = partitioner.partition(AREA, restricted=(NO_FLY,))
    without = partitioner.partition(AREA)
    frame = LocalFrame.for_polygon(AREA)
    area_with = sum(frame.polygon_to_local(z.polygon).area for z in with_hole)
    area_without = sum(frame.polygon_to_local(z.polygon).area for z in without)
    removed = area_without - area_with
    # pocket plus a 20 m standoff on each side; slivers may add a little more
    assert removed >= 240 * 240 * (1 - 1e-3)
    standoff = frame.polygon_to_local(NO_FLY).buffer(20 - 1e-6, join_style="mitre")
    assert all(
        frame.polygon_to_local(z.polygon).intersection(standoff).area < 1e-6 for z in with_hole
    )
    assert len(with_hole) > len(without) - 4  # cells around the pocket were split, not dropped
