"""End-to-end: three fake drones search a region with no ROS, PX4, database, or AI service."""

import pytest

from aeris.domain import DroneStatus, LinkState, MissionStatus, ZoneStatus
from aeris.events import DomainEvent, ZoneAssigned, ZoneReassignmentRequested
from aeris.simulation import ScenarioRunner, load_scenario

pytestmark = pytest.mark.integration


async def test_basic_search_completes_every_zone() -> None:
    runner = ScenarioRunner(load_scenario("basic_search"))
    summary = await runner.run()

    assert summary.final_status is MissionStatus.COMPLETED
    assert summary.zones_complete == summary.zones_total > 0
    assert summary.coverage_fraction == pytest.approx(1.0)
    assert summary.reassignments == 0
    assert summary.elapsed_s < runner.scenario.max_duration_s

    snap = runner.world.snapshot()
    assert all(z.status is ZoneStatus.COMPLETE for z in snap.zones)
    assert all(
        v.state and v.state.status in {DroneStatus.RETURNING, DroneStatus.LANDED}
        for v in snap.drones
    )
    assert all(v.state and v.state.battery_percent > 0 for v in snap.drones)
    assert len(snap.assignments) >= summary.zones_total


async def test_every_drone_gets_work_and_zones_are_never_double_assigned() -> None:
    runner = ScenarioRunner(load_scenario("basic_search"))
    assigned: list[ZoneAssigned] = []

    async def collect(event: DomainEvent) -> None:
        if isinstance(event, ZoneAssigned):
            assigned.append(event)

    runner.bus.subscribe(ZoneAssigned, collect)
    await runner.run()

    assert {e.drone_id for e in assigned} == {"drone-01", "drone-02", "drone-03"}
    # a zone is only ever assigned once in the baseline scenario
    zone_ids = [e.zone_id for e in assigned]
    assert len(zone_ids) == len(set(zone_ids))


async def test_lost_link_releases_zone_and_another_drone_finishes_it() -> None:
    runner = ScenarioRunner(load_scenario("lost_connection"))
    releases: list[ZoneReassignmentRequested] = []
    assigned: list[ZoneAssigned] = []

    async def collect(event: DomainEvent) -> None:
        if isinstance(event, ZoneReassignmentRequested):
            releases.append(event)
        elif isinstance(event, ZoneAssigned):
            assigned.append(event)

    runner.bus.subscribe(None, collect)
    summary = await runner.run()

    assert summary.final_status is MissionStatus.COMPLETED
    assert releases, "lost drone should have released its zone"
    released = releases[0]
    assert released.previous_drone_id == "drone-02"
    assert 0 < released.remaining_fraction < 1
    handoff = [e for e in assigned if e.zone_id == released.zone_id and e.drone_id != "drone-02"]
    assert handoff, "released zone should be reassigned to another drone"
    assert handoff[0].start_fraction == pytest.approx(1 - released.remaining_fraction)

    snap = runner.world.snapshot()
    lost = snap.drone("drone-02")
    assert lost is not None and lost.link_state is LinkState.LOST
    assert lost.state is not None and lost.state.status is DroneStatus.UNAVAILABLE
    assert all(z.status is ZoneStatus.COMPLETE for z in snap.zones)


async def test_pause_holds_drones_and_resume_continues() -> None:
    runner = ScenarioRunner(load_scenario("basic_search"))
    await runner.setup()
    await runner.start()
    for _ in range(30):
        await runner.step()
    before = runner.world.snapshot()
    assert any(v.state and v.state.status is DroneStatus.SEARCHING for v in before.drones)

    await runner.manager.pause()
    paused_cov = before.coverage_fraction
    for _ in range(30):
        snap = await runner.step()
    assert snap.mission.status is MissionStatus.PAUSED
    assert snap.coverage_fraction == pytest.approx(paused_cov)
    assert all(v.state and v.state.status is DroneStatus.HOLDING for v in snap.drones)

    await runner.manager.resume()
    for _ in range(30):
        snap = await runner.step()
    assert snap.coverage_fraction > paused_cov


async def test_abort_sends_everyone_home() -> None:
    runner = ScenarioRunner(load_scenario("basic_search"))
    await runner.setup()
    await runner.start()
    for _ in range(20):
        await runner.step()
    await runner.manager.abort("test abort")
    snap = await runner.step()
    assert snap.mission.status is MissionStatus.ABORTED
    assert all(v.state and v.state.status is DroneStatus.RETURNING for v in snap.drones)
    assert all(z.assigned_drone_id is None for z in snap.zones)


async def test_operator_return_reassigns_the_zone() -> None:
    runner = ScenarioRunner(load_scenario("basic_search"))
    await runner.setup()
    await runner.start()
    for _ in range(40):
        await runner.step()
    state = runner.world.drone_state("drone-01")
    assert state is not None and state.assigned_zone_id is not None
    zone_id = state.assigned_zone_id

    await runner.manager.return_drone("drone-01")
    snap = await runner.step()
    zone = snap.zone(zone_id)
    assert zone is not None
    assert zone.assigned_drone_id != "drone-01"
    assert snap.drone("drone-01").state.status is DroneStatus.RETURNING  # type: ignore[union-attr]
    summary = await runner.run() if False else None  # keep explicit; not rerun
    assert summary is None
