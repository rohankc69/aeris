"""Phase 5: detections are triaged, investigated, escalated and confirmed only by a human."""

import pytest

from aeris.config import load_settings
from aeris.decisions import MockDecisionProvider
from aeris.domain import DroneStatus, MissionStatus, TriageDecision, ZoneStatus
from aeris.events import (
    CandidateEscalated,
    DomainEvent,
    DroneReturning,
    HumanReviewRequested,
    SurvivorConfirmed,
    SurvivorRejected,
    ZoneReassignmentRequested,
)
from aeris.simulation import ScenarioRunner, load_scenario

pytestmark = pytest.mark.integration


def settings():  # type: ignore[no-untyped-def]
    return load_settings(_env_file=None)


def collector(runner: ScenarioRunner) -> list[DomainEvent]:
    seen: list[DomainEvent] = []

    async def collect(event: DomainEvent) -> None:
        seen.append(event)

    runner.bus.subscribe(None, collect)
    return seen


async def test_candidate_detection_ends_person_located_after_operator_confirms() -> None:
    runner = ScenarioRunner(load_scenario("candidate_detection"), settings=settings())
    seen = collector(runner)
    summary = await runner.run()

    assert summary.final_status is MissionStatus.PERSON_LOCATED
    assert summary.person_located_at_s == pytest.approx(480)
    assert summary.detections == 1 and summary.candidates == 1
    snap = runner.world.snapshot()
    detection = snap.detections[0]
    assert detection.sensor_agreement
    assert detection.triage is TriageDecision.POSSIBLE_SURVIVOR
    candidate = snap.candidates[0]
    assert candidate.confirmed is True
    assert candidate.resolved_by_action_id is not None
    types = [e.type_name for e in seen]
    assert (
        types.index("DetectionCreated")
        < types.index("CandidateEscalated")
        < types.index("SurvivorConfirmed")
    )
    assert any(isinstance(e, HumanReviewRequested) for e in seen)
    # someone was sent to investigate, releasing their zone for others
    investigations = [a for a in snap.assignments if a.task.value == "INVESTIGATE"]
    assert investigations
    assert any(
        isinstance(e, ZoneReassignmentRequested) and e.reason.startswith("investigate:")
        for e in seen
    )
    # everyone goes home once the person is located
    assert all(
        v.state and v.state.status in {DroneStatus.RETURNING, DroneStatus.LANDED}
        for v in snap.drones
    )
    triage_records = [r for r in runner.world.decisions if r.decision_type == "detection_triage"]
    assert triage_records and all(r.provider == "mock" for r in triage_records)


async def test_multiple_detections_rejected_then_confirmed() -> None:
    runner = ScenarioRunner(load_scenario("multiple_detections"), settings=settings())
    seen = collector(runner)
    summary = await runner.run()

    assert summary.final_status is MissionStatus.PERSON_LOCATED
    assert summary.detections == 2 and summary.candidates == 2
    assert sum(isinstance(e, SurvivorRejected) for e in seen) == 1
    assert sum(isinstance(e, SurvivorConfirmed) for e in seen) == 1
    snap = runner.world.snapshot()
    resolutions = sorted(c.confirmed for c in snap.candidates if c.confirmed is not None)
    assert resolutions == [False, True]
    rejected = next(c for c in snap.candidates if c.confirmed is False)
    assert (
        snap.detections
        and next(d for d in snap.detections if d.detection_id == rejected.detection_id).triage
        is TriageDecision.IGNORE
    )


async def test_forest_search_demo_timeline() -> None:
    runner = ScenarioRunner(load_scenario("forest_search"), settings=settings())
    seen = collector(runner)
    stamps: dict[str, float] = {}

    async def stamp(event: DomainEvent) -> None:
        if isinstance(event, DroneReturning) and event.drone_id == "drone-02":
            stamps.setdefault("drone02_return", runner.elapsed_s)
        if isinstance(event, CandidateEscalated):
            stamps.setdefault("escalated", runner.elapsed_s)

    runner.bus.subscribe(None, stamp)
    summary = await runner.run()

    assert summary.final_status is MissionStatus.PERSON_LOCATED
    assert summary.person_located_at_s == pytest.approx(540)  # T+9 operator confirmation
    assert 300 <= stamps["drone02_return"] <= 420  # around T+6, driven by the safety governor
    assert 480 <= stamps["escalated"] <= 500  # T+8 strong evidence -> confirmation request
    d2_return = next(e for e in seen if isinstance(e, DroneReturning) and e.drone_id == "drone-02")
    assert d2_return.reason.startswith("safety:")
    assert any(
        isinstance(e, ZoneReassignmentRequested) and e.previous_drone_id == "drone-02" for e in seen
    )
    assert summary.reassignments <= 6, "investigation churn should be bounded"
    assert summary.safety_overrides >= 1


async def test_ai_alone_can_never_declare_a_person_located() -> None:
    """Even a provider that shouts POSSIBLE_SURVIVOR at everything cannot end the mission."""
    eager = MockDecisionProvider(fixed_choices={"detection_triage": "POSSIBLE_SURVIVOR"})
    scenario = load_scenario("candidate_detection")
    no_operator = scenario.model_copy(
        update={
            "events": tuple(e for e in scenario.events if e.type.value != "operator_confirm"),
            "max_duration_s": 900,
        }
    )
    runner = ScenarioRunner(no_operator, settings=settings(), decision_provider=eager)
    summary = await runner.run()
    assert summary.final_status is not MissionStatus.PERSON_LOCATED
    assert summary.candidates >= 1
    assert all(c.confirmed is None for c in runner.world.snapshot().candidates)


async def test_zones_still_complete_while_candidate_awaits_operator() -> None:
    scenario = load_scenario("candidate_detection")
    no_operator = scenario.model_copy(
        update={
            "events": tuple(e for e in scenario.events if e.type.value != "operator_confirm"),
            "max_duration_s": 1200,
        }
    )
    runner = ScenarioRunner(no_operator, settings=settings())
    summary = await runner.run()
    snap = runner.world.snapshot()
    assert all(z.status is ZoneStatus.COMPLETE for z in snap.zones)
    assert snap.open_candidates
    assert (
        summary.final_status is MissionStatus.ACTIVE
    )  # waits for the human, does not self-complete
