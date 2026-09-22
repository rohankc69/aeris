"""Phase 3: plan validation, restricted regions, separation, emergency stop, zone priority."""

import pytest
from shapely.geometry import Point

from aeris.config import load_settings
from aeris.decisions import MockDecisionProvider
from aeris.domain import DroneStatus, MissionStatus, ZoneStatus
from aeris.events import DomainEvent, SafetyOverrideTriggered, ZoneReassignmentRequested
from aeris.planning import LocalFrame
from aeris.simulation import ScenarioRunner, load_scenario

pytestmark = pytest.mark.integration


def settings():  # type: ignore[no-untyped-def]
    return load_settings(_env_file=None)


async def test_drone_failure_is_absorbed_by_the_rest_of_the_fleet() -> None:
    runner = ScenarioRunner(load_scenario("drone_failure"), settings=settings())
    summary = await runner.run()
    assert summary.final_status is MissionStatus.COMPLETED
    assert summary.reassignments >= 1
    snap = runner.world.snapshot()
    failed = snap.drone("drone-03")
    assert failed is not None and failed.state is not None
    assert failed.state.status is DroneStatus.UNAVAILABLE
    assert all(z.status is ZoneStatus.COMPLETE for z in snap.zones)
    assert all(
        z.assigned_drone_id != "drone-03" or z.status is ZoneStatus.COMPLETE for z in snap.zones
    )


async def test_dynamic_reassignment_absorbs_two_disruptions_and_avoids_no_fly_pocket() -> None:
    runner = ScenarioRunner(load_scenario("dynamic_reassignment"), settings=settings())
    releases: list[ZoneReassignmentRequested] = []
    rejected_plans: list[SafetyOverrideTriggered] = []
    positions: list[tuple[str, float, float]] = []

    async def collect(event: DomainEvent) -> None:
        if isinstance(event, ZoneReassignmentRequested):
            releases.append(event)
        elif (
            isinstance(event, SafetyOverrideTriggered) and event.safe_alternative == "plan_rejected"
        ):
            rejected_plans.append(event)

    runner.bus.subscribe(None, collect)
    frame = LocalFrame.for_polygon(runner.scenario.search_polygon)
    hole = frame.polygon_to_local(runner.scenario.restricted_regions[0])

    def track(snap):  # type: ignore[no-untyped-def]
        for v in snap.drones:
            if v.state and v.state.status is DroneStatus.SEARCHING:
                positions.append((v.drone.drone_id, *frame.to_local(v.state.position)))

    summary = await runner.run(on_tick=track)
    assert summary.final_status is MissionStatus.COMPLETED
    assert "drone-03" in {e.previous_drone_id for e in releases}
    assert any(
        e.rule in {"mandatory_return_battery", "return_margin"} and e.drone_id == "drone-02"
        for e in runner.world.safety_events
    )
    assert not rejected_plans, "planner should never propose a plan that the governor rejects"
    inside = [p for p in positions if hole.contains(Point(p[1], p[2]))]
    assert not inside, f"searching drone entered the restricted region: {inside[:3]}"
    snap = runner.world.snapshot()
    assert all(z.status is ZoneStatus.COMPLETE for z in snap.zones)
    # zone priorities were scored (last_known_position is set in this scenario)
    scored = [r for r in runner.world.decisions if r.decision_type == "zone_priority"]
    assert scored and all(r.drone_id is None and r.score is not None for r in scored)


async def test_separation_hold_resumes_automatically() -> None:
    runner = ScenarioRunner(load_scenario("basic_search"), settings=settings())
    summary = await runner.run()
    holds = [e for e in runner.world.safety_events if e.rule == "minimum_separation"]
    assert summary.final_status is MissionStatus.COMPLETED
    if holds:  # geometry-dependent, but if it fires the drone must have resumed and finished
        snap = runner.world.snapshot()
        assert all(v.state and v.state.status is not DroneStatus.HOLDING for v in snap.drones)


async def test_emergency_stop_freezes_everything_until_cleared() -> None:
    runner = ScenarioRunner(load_scenario("basic_search"), settings=settings())
    await runner.setup()
    await runner.start()
    for _ in range(40):
        await runner.step()
    before = runner.world.snapshot()
    assert any(v.state and v.state.status is DroneStatus.SEARCHING for v in before.drones)

    await runner.manager.emergency_stop()
    assert runner.manager.emergency_stop_active
    frozen = await runner.step()
    assert frozen.mission.status is MissionStatus.PAUSED
    assert all(v.state and v.state.status is DroneStatus.HOLDING for v in frozen.drones)
    for _ in range(20):
        snap = await runner.step()
    assert snap.coverage_fraction == pytest.approx(before.coverage_fraction, abs=0.02)
    assert len(snap.assignments) == len(before.assignments)

    with pytest.raises(ValueError, match="emergency stop"):
        await runner.manager.resume()

    await runner.manager.clear_emergency_stop()
    assert not runner.manager.emergency_stop_active
    assert runner.world.mission.status is MissionStatus.PAUSED  # operator must resume explicitly
    await runner.manager.resume()
    for _ in range(30):
        snap = await runner.step()
    assert snap.coverage_fraction > before.coverage_fraction
    kinds = [a.action_type.value for a in runner.world.operator_actions]
    assert "EMERGENCY_STOP" in kinds


async def test_zone_priority_score_influences_assignment_order() -> None:
    """A provider that rates the far south-east zone highest should get it assigned first."""

    scenario = load_scenario("basic_search")
    hot_zone = "C3"
    provider = MockDecisionProvider(
        fixed_scores={"zone_priority": 0.0},
    )

    async def hot_score(request):  # type: ignore[no-untyped-def]
        from aeris.decisions import ScoreResult  # noqa: PLC0415

        zone_id = request.state["zone_id"]
        return ScoreResult(
            provider="mock", model="mock", latency_ms=0, score=1.0 if zone_id == hot_zone else 0.0
        )

    provider.score = hot_score  # type: ignore[method-assign]
    runner = ScenarioRunner(scenario, settings=settings(), decision_provider=provider)
    await runner.setup()
    await runner.start()
    snap = await runner.step()
    first_zones = {z.zone_id for z in snap.zones if z.assigned_drone_id}
    assert hot_zone in first_zones
    assert snap.zone(hot_zone).priority == 1.0  # type: ignore[union-attr]
