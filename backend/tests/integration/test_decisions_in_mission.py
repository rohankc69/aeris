"""AI-assisted decisions inside a running mission, with the Safety Governor authoritative."""

import pytest

from aeris.clock import SimClock
from aeris.config import SafetySettings, load_settings
from aeris.decisions import (
    ChoiceRequest,
    MockDecisionProvider,
    ProviderUnavailableError,
    ResilientDecisionProvider,
    RuleBasedDecisionProvider,
)
from aeris.domain import DroneStatus, MissionStatus, ZoneStatus
from aeris.events import DecisionCompleted, DomainEvent, DroneReturning, SafetyOverrideTriggered
from aeris.simulation import ScenarioRunner, load_scenario

pytestmark = pytest.mark.integration


def settings():  # type: ignore[no-untyped-def]
    return load_settings(_env_file=None)


async def test_low_battery_drone_is_returned_by_safety_and_zone_is_reassigned() -> None:
    runner = ScenarioRunner(load_scenario("low_battery"), settings=settings())
    returning: list[DroneReturning] = []

    async def collect(event: DomainEvent) -> None:
        if isinstance(event, DroneReturning):
            returning.append(event)

    runner.bus.subscribe(DroneReturning, collect)
    summary = await runner.run()

    assert summary.final_status is MissionStatus.COMPLETED
    assert summary.reassignments >= 1
    assert summary.safety_overrides >= 1
    assert summary.decisions > 0
    assert summary.decision_provider == "mock"

    d2 = [e for e in returning if e.drone_id == "drone-02"]
    assert d2 and d2[0].reason.startswith("safety:")
    rules = {e.rule for e in runner.world.safety_events if e.drone_id == "drone-02"}
    assert rules & {"mandatory_return_battery", "return_margin", "critical_battery"}
    snap = runner.world.snapshot()
    assert snap.drone("drone-02").state.status in {DroneStatus.RETURNING, DroneStatus.LANDED}  # type: ignore[union-attr]
    assert all(z.status is ZoneStatus.COMPLETE for z in snap.zones)


async def test_ai_recommendation_is_recorded_and_acted_on_when_safe() -> None:
    def script(request: ChoiceRequest) -> str:
        battery = float(request.state["battery_percent"])  # type: ignore[arg-type]
        return (
            "RETURN_TO_BASE"
            if request.drone_id == "drone-03" and battery < 90
            else "CONTINUE_SEARCH"
        )

    provider = MockDecisionProvider(scripts={"drone_disposition": script}, model="mock-jev")
    runner = ScenarioRunner(
        load_scenario("basic_search"), settings=settings(), decision_provider=provider
    )
    completed: list[DecisionCompleted] = []
    returning: list[DroneReturning] = []

    async def collect(event: DomainEvent) -> None:
        if isinstance(event, DecisionCompleted):
            completed.append(event)
        elif isinstance(event, DroneReturning):
            returning.append(event)

    runner.bus.subscribe(None, collect)
    await runner.run()

    early = [e for e in returning if e.drone_id == "drone-03" and e.reason.startswith("decision:")]
    assert early, "AI early-return recommendation should have been executed"
    decision_id = early[0].reason.removeprefix("decision:")
    record = next(r for r in runner.world.decisions if r.decision_id == decision_id)
    assert record.provider == "mock" and record.model == "mock-jev"
    assert record.selected_value == "RETURN_TO_BASE" and record.final_action == "RETURN_TO_BASE"
    assert record.policy_value == "CONTINUE_SEARCH"
    assert record.safety_override is False
    assert any(e.decision_id == decision_id and not e.safety_override for e in completed)
    # the returning drone's zone must have been picked up by someone else
    snap = runner.world.snapshot()
    assert all(z.status is ZoneStatus.COMPLETE for z in snap.zones)


async def test_safety_governor_overrides_unsafe_ai_output() -> None:
    reckless = MockDecisionProvider(
        fixed_choices={"drone_disposition": "CONTINUE_SEARCH"}, model="mock-jev"
    )
    every_tick = load_settings(_env_file=None, decision={"disposition_interval_s": 1})
    runner = ScenarioRunner(
        load_scenario("low_battery"), settings=every_tick, decision_provider=reckless
    )
    overrides: list[SafetyOverrideTriggered] = []

    async def collect(event: DomainEvent) -> None:
        if isinstance(event, SafetyOverrideTriggered):
            overrides.append(event)

    runner.bus.subscribe(SafetyOverrideTriggered, collect)
    summary = await runner.run()

    assert summary.final_status is MissionStatus.COMPLETED
    overridden = [r for r in runner.world.decisions if r.safety_override]
    assert overridden, "at least one AI CONTINUE_SEARCH must have been overridden at low battery"
    r = overridden[0]
    assert r.selected_value == "CONTINUE_SEARCH"
    assert r.final_action == "RETURN_TO_BASE"
    assert r.safety_event_id is not None
    event = next(e for e in runner.world.safety_events if e.event_id == r.safety_event_id)
    assert event.proposed_action == "mock:CONTINUE_SEARCH"
    assert event.safe_alternative == "RETURN_TO_BASE"
    assert any(o.safety_event_id == event.event_id for o in overrides)
    snap = runner.world.snapshot()
    assert snap.drone("drone-02").state.status in {DroneStatus.RETURNING, DroneStatus.LANDED}  # type: ignore[union-attr]


async def test_hosted_provider_outage_falls_back_to_rules_and_mission_completes() -> None:
    down = MockDecisionProvider(fail_with=ProviderUnavailableError("openrouter", "503"))
    clock = SimClock()
    provider = ResilientDecisionProvider(
        primary=down,
        fallback=RuleBasedDecisionProvider(safety=SafetySettings()),
        clock=clock,
        timeout_s=0.5,
        max_retries=0,
        circuit_failure_threshold=2,
        circuit_reset_s=600,
    )
    runner = ScenarioRunner(
        load_scenario("basic_search"), settings=settings(), decision_provider=provider
    )
    summary = await runner.run()

    assert summary.final_status is MissionStatus.COMPLETED
    assert summary.decisions > 0
    assert all(r.provider == "rules" for r in runner.world.decisions)
    reasons = {r.fallback_reason for r in runner.world.decisions}
    assert reasons <= {"ProviderUnavailableError", "circuit_open"}
    assert "circuit_open" in reasons  # breaker opened and stopped hammering the dead API
    assert len(down.calls) < summary.decisions


async def test_decisions_are_throttled_per_drone() -> None:
    mock = MockDecisionProvider()
    runner = ScenarioRunner(
        load_scenario("basic_search"), settings=settings(), decision_provider=mock
    )
    await runner.setup()
    await runner.start()
    for _ in range(60):
        await runner.step()
    per_drone: dict[str | None, int] = {}
    for r in runner.world.decisions:
        per_drone[r.drone_id] = per_drone.get(r.drone_id, 0) + 1
    interval = runner.settings.decision.disposition_interval_s
    assert all(count <= 60 / interval + 1 for count in per_drone.values())
    assert set(per_drone) == {"drone-01", "drone-02", "drone-03"}
