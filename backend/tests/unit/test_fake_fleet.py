from datetime import UTC, datetime

import pytest

from aeris.clock import SimClock
from aeris.domain import AssignmentTask, Drone, DroneCapability, GeoPoint, Waypoint, WaypointPlan
from aeris.fleet import FakeFleetAdapter, SimDroneConfig
from aeris.fleet.fake import SimFlightMode
from aeris.planning import LocalFrame, PlanProgressTracker

T0 = datetime(2026, 1, 1, tzinfo=UTC)
BASE = GeoPoint(latitude=47.0, longitude=8.0)
FRAME = LocalFrame(BASE)


def drone(drone_id: str = "drone-01", speed: float = 10.0) -> Drone:
    return Drone(
        drone_id=drone_id, name=drone_id, capability=DroneCapability(cruise_speed_mps=speed)
    )


def straight_plan(length_m: float = 1000, altitude: float = 50) -> WaypointPlan:
    return WaypointPlan(
        drone_id="drone-01",
        zone_id="A1",
        task=AssignmentTask.SEARCH,
        waypoints=(
            Waypoint(position=FRAME.to_geo(0, 0, altitude)),
            Waypoint(position=FRAME.to_geo(length_m, 0, altitude)),
        ),
    )


@pytest.fixture
def clock() -> SimClock:
    return SimClock(T0)


@pytest.fixture
def fleet(clock: SimClock) -> FakeFleetAdapter:
    return FakeFleetAdapter(
        base_position=BASE,
        clock=clock,
        drones=[SimDroneConfig(drone=drone(), battery_percent=80, battery_drain_percent_per_s=0.1)],
    )


async def test_idle_drone_reports_and_does_not_move(fleet: FakeFleetAdapter) -> None:
    frames = await fleet.get_telemetry()
    assert len(frames) == 1
    fleet.advance(10)
    after = (await fleet.get_telemetry())[0]
    assert after.position.distance_to(BASE) < 1e-6
    assert after.battery_percent == 80


async def test_mission_flight_follows_plan_and_drains_battery(fleet: FakeFleetAdapter) -> None:
    result = await fleet.send_mission("drone-01", straight_plan(1000))
    assert result.accepted
    fleet.advance(50)  # 10 m/s → 500 m
    frame = (await fleet.get_telemetry())[0]
    x, _ = FRAME.to_local(frame.position)
    assert x == pytest.approx(500, abs=1)
    assert frame.position.altitude_m == 50
    assert frame.velocity_mps == 10
    assert frame.battery_percent == pytest.approx(75)
    fleet.advance(60)
    assert fleet.flight_mode("drone-01") is SimFlightMode.HOLDING


async def test_return_to_base_lands(fleet: FakeFleetAdapter) -> None:
    await fleet.send_mission("drone-01", straight_plan(1000))
    fleet.advance(100)
    assert await fleet.return_to_base("drone-01")
    fleet.advance(101)
    assert fleet.flight_mode("drone-01") is SimFlightMode.LANDED
    assert fleet.position("drone-01").distance_to(BASE) < 1
    rejected = await fleet.send_mission("drone-01", straight_plan())
    assert not rejected.accepted


async def test_hold_stops_motion_but_keeps_draining(fleet: FakeFleetAdapter) -> None:
    await fleet.send_mission("drone-01", straight_plan(1000))
    fleet.advance(10)
    await fleet.hold("drone-01")
    before = (await fleet.get_telemetry())[0]
    fleet.advance(10)
    after = (await fleet.get_telemetry())[0]
    assert before.position == after.position
    assert after.battery_percent < before.battery_percent
    assert after.velocity_mps == 0


async def test_link_offline_hides_telemetry_and_rejects_commands(fleet: FakeFleetAdapter) -> None:
    fleet.set_link("drone-01", online=False)
    assert await fleet.get_telemetry() == []
    assert not (await fleet.send_mission("drone-01", straight_plan())).accepted
    assert not (await fleet.return_to_base("drone-01")).accepted
    fleet.set_link("drone-01", online=True)
    assert len(await fleet.get_telemetry()) == 1


async def test_battery_drain_multiplier_and_depletion_lands(fleet: FakeFleetAdapter) -> None:
    fleet.set_battery_drain_multiplier("drone-01", 10)
    await fleet.send_mission("drone-01", straight_plan(100_000))
    fleet.advance(100)  # 0.1 * 10 * 100 = 100% drained
    frame = (await fleet.get_telemetry())[0]
    assert frame.battery_percent == 0
    assert fleet.flight_mode("drone-01") is SimFlightMode.LANDED


async def test_unknown_drone_is_rejected(fleet: FakeFleetAdapter) -> None:
    assert not (await fleet.hold("ghost")).accepted


# ----------------------------------------------------------------- progress tracker


def test_progress_tracker_follows_path_monotonically() -> None:
    tracker = PlanProgressTracker(straight_plan(1000))
    assert tracker.fraction == 0
    assert tracker.update(FRAME.to_geo(250, 0)) == pytest.approx(0.25)
    assert tracker.update(FRAME.to_geo(100, 0)) == pytest.approx(0.25)  # never goes backwards
    assert tracker.update(FRAME.to_geo(500, 500)) == pytest.approx(0.25)  # off-track ignored
    assert tracker.update(FRAME.to_geo(1000, 0)) == pytest.approx(1.0)
    assert tracker.complete


def test_progress_tracker_respects_start_fraction() -> None:
    plan = straight_plan(1000).model_copy(update={"start_fraction": 0.5})
    tracker = PlanProgressTracker(plan)
    assert tracker.fraction == 0.5
    assert tracker.update(FRAME.to_geo(500, 0)) == pytest.approx(0.75)


def test_progress_tracker_single_waypoint() -> None:
    plan = straight_plan().model_copy(
        update={"waypoints": (Waypoint(position=FRAME.to_geo(0, 0, 50)),)}
    )
    tracker = PlanProgressTracker(plan)
    assert tracker.update(FRAME.to_geo(100, 0)) == 0
    assert tracker.update(FRAME.to_geo(5, 0)) == 1
