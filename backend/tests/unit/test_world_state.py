from datetime import UTC, datetime

import pytest

from aeris.clock import SimClock
from aeris.config import SafetySettings
from aeris.domain import (
    Drone,
    DroneStatus,
    GeoPoint,
    GeoPolygon,
    LinkState,
    Mission,
    MissionStatus,
    SearchArea,
    TelemetryFrame,
    ZoneStatus,
)
from aeris.events import DomainEvent, DroneDisconnected, DroneLinkStateChanged, InMemoryEventBus
from aeris.planning import GridPartitioner, LocalFrame
from aeris.world import WorldStateService

T0 = datetime(2026, 1, 1, tzinfo=UTC)
ORIGIN = GeoPoint(latitude=47.0, longitude=8.0)


def square(size_m: float = 500) -> GeoPolygon:
    f = LocalFrame(ORIGIN)
    return GeoPolygon(
        vertices=(
            f.to_geo(0, 0),
            f.to_geo(size_m, 0),
            f.to_geo(size_m, size_m),
            f.to_geo(0, size_m),
        )
    )


@pytest.fixture
def clock() -> SimClock:
    return SimClock(T0)


@pytest.fixture
def events() -> list[DomainEvent]:
    return []


@pytest.fixture
async def world(clock: SimClock, events: list[DomainEvent]) -> WorldStateService:
    bus = InMemoryEventBus()

    async def collect(event: DomainEvent) -> None:
        events.append(event)

    bus.subscribe(None, collect)
    mission = Mission(
        name="t", search_area=SearchArea(polygon=square()), base_position=ORIGIN, created_at=T0
    )
    zones = GridPartitioner(cell_size_m=250).partition(mission.search_area.polygon)
    service = WorldStateService(
        mission=mission, zones=zones, bus=bus, clock=clock, safety=SafetySettings()
    )
    await service.register_drone(Drone(drone_id="drone-01", name="Drone 01"))
    return service


def frame(ts: datetime, battery: float = 90) -> TelemetryFrame:
    return TelemetryFrame(
        drone_id="drone-01",
        timestamp=ts,
        position=ORIGIN,
        battery_percent=battery,
        estimated_remaining_s=1000,
    )


async def test_snapshot_reports_missing_telemetry_as_lost(world: WorldStateService) -> None:
    snap = world.snapshot()
    view = snap.drone("drone-01")
    assert view is not None
    assert view.state is None
    assert view.telemetry_age_s is None
    assert view.link_state is LinkState.LOST
    assert not view.is_available_for_assignment
    assert len(snap.zones) == 4
    assert snap.snapshot_hash


async def test_link_state_degrades_with_telemetry_age(
    world: WorldStateService, clock: SimClock, events: list[DomainEvent]
) -> None:
    await world.apply_telemetry(frame(T0))
    assert world.snapshot().drone("drone-01").link_state is LinkState.CONNECTED  # type: ignore[union-attr]

    clock.advance(3)  # > degraded_after (2s)
    await world.refresh_link_states()
    assert world.snapshot().drone("drone-01").link_state is LinkState.DEGRADED  # type: ignore[union-attr]

    clock.advance(4)  # 7s > stale_after (5s)
    await world.refresh_link_states()
    assert world.snapshot().drone("drone-01").link_state is LinkState.STALE  # type: ignore[union-attr]

    clock.advance(5)  # 12s > lost_after (10s)
    await world.refresh_link_states()
    view = world.snapshot().drone("drone-01")
    assert view is not None
    assert view.link_state is LinkState.LOST
    assert view.telemetry_age_s == pytest.approx(12)

    changes = [e for e in events if isinstance(e, DroneLinkStateChanged)]
    assert [c.current for c in changes] == [LinkState.DEGRADED, LinkState.STALE, LinkState.LOST]
    assert any(isinstance(e, DroneDisconnected) for e in events)


async def test_fresh_telemetry_restores_connection(
    world: WorldStateService, clock: SimClock
) -> None:
    await world.apply_telemetry(frame(T0))
    clock.advance(20)
    await world.refresh_link_states()
    assert world.snapshot().drone("drone-01").link_state is LinkState.LOST  # type: ignore[union-attr]
    await world.apply_telemetry(frame(clock.now()))
    assert world.snapshot().drone("drone-01").link_state is LinkState.CONNECTED  # type: ignore[union-attr]


async def test_telemetry_for_unregistered_drone_is_rejected(world: WorldStateService) -> None:
    with pytest.raises(KeyError):
        await world.apply_telemetry(frame(T0).model_copy(update={"drone_id": "ghost"}))


async def test_zone_coverage_is_monotonic_and_marks_drone(world: WorldStateService) -> None:
    await world.apply_telemetry(frame(T0))
    world.set_drone_status("drone-01", DroneStatus.SEARCHING, assigned_zone_id="A1")
    await world.update_zone_coverage("A1", "drone-01", 0.4)
    await world.update_zone_coverage("A1", "drone-01", 0.3)  # regression ignored
    snap = world.snapshot()
    assert snap.zone("A1").coverage == 0.4  # type: ignore[union-attr]
    assert snap.zone("A1").last_searched_at == T0  # type: ignore[union-attr]
    assert snap.drone("drone-01").state.coverage_completed == 0.4  # type: ignore[union-attr]
    assert snap.coverage_fraction == pytest.approx(0.1)


async def test_zone_status_change_publishes_event(
    world: WorldStateService, events: list[DomainEvent]
) -> None:
    await world.set_zone_status("A1", ZoneStatus.ASSIGNED, assigned_drone_id="drone-01")
    assert world.snapshot().zone("A1").assigned_drone_id == "drone-01"  # type: ignore[union-attr]
    assert events[-1].type_name == "ZoneStatusChanged"
    await world.set_zone_status("A1", ZoneStatus.ASSIGNED)  # no-op status keeps quiet
    assert sum(e.type_name == "ZoneStatusChanged" for e in events) == 1


async def test_mission_lifecycle_events(
    world: WorldStateService, events: list[DomainEvent]
) -> None:
    await world.start_mission()
    await world.pause_mission()
    await world.resume_mission()
    await world.complete_mission(MissionStatus.PERSON_LOCATED)
    assert world.mission.status is MissionStatus.PERSON_LOCATED
    assert world.mission.completed_at == T0
    names = [e.type_name for e in events if e.type_name.startswith("Mission")]
    assert names == ["MissionStarted", "MissionPaused", "MissionResumed", "MissionCompleted"]
    with pytest.raises(ValueError, match="illegal"):
        await world.start_mission()


async def test_snapshot_hash_changes_with_state(world: WorldStateService) -> None:
    before = world.snapshot().snapshot_hash
    await world.apply_telemetry(frame(T0))
    assert world.snapshot().snapshot_hash != before
