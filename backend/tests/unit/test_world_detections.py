from datetime import UTC, datetime, timedelta

import pytest

from aeris.clock import SimClock
from aeris.config import SafetySettings
from aeris.domain import (
    DetectionObservation,
    DetectionSource,
    GeoPoint,
    GeoPolygon,
    Mission,
    SearchArea,
    TriageDecision,
)
from aeris.events import (
    CandidateEscalated,
    DetectionCreated,
    DetectionUpdated,
    DomainEvent,
    InMemoryEventBus,
)
from aeris.planning import GridPartitioner, LocalFrame
from aeris.world import WorldStateService

T0 = datetime(2026, 1, 1, tzinfo=UTC)
ORIGIN = GeoPoint(latitude=47.0, longitude=8.0)
FRAME = LocalFrame(ORIGIN)


@pytest.fixture
def events() -> list[DomainEvent]:
    return []


@pytest.fixture
def world(events: list[DomainEvent]) -> WorldStateService:
    bus = InMemoryEventBus()

    async def collect(e: DomainEvent) -> None:
        events.append(e)

    bus.subscribe(None, collect)
    poly = GeoPolygon(
        vertices=(
            FRAME.to_geo(0, 0),
            FRAME.to_geo(500, 0),
            FRAME.to_geo(500, 500),
            FRAME.to_geo(0, 500),
        )
    )
    mission = Mission(
        name="t", search_area=SearchArea(polygon=poly), base_position=ORIGIN, created_at=T0
    )
    zones = GridPartitioner(cell_size_m=250).partition(poly)
    return WorldStateService(
        mission=mission, zones=zones, bus=bus, clock=SimClock(T0), safety=SafetySettings()
    )


def obs(
    x: float,
    y: float,
    *,
    source: DetectionSource = DetectionSource.THERMAL,
    conf: float = 0.5,
    at: datetime = T0,
) -> DetectionObservation:
    return DetectionObservation(
        drone_id="drone-01",
        timestamp=at,
        source=source,
        confidence=conf,
        position=FRAME.to_geo(x, y),
    )


async def test_observations_cluster_within_radius(
    world: WorldStateService, events: list[DomainEvent]
) -> None:
    first, new1 = await world.ingest_observation(obs(100, 100), cluster_radius_m=30)
    second, new2 = await world.ingest_observation(
        obs(110, 100, source=DetectionSource.VISUAL, conf=0.7, at=T0 + timedelta(seconds=5)),
        cluster_radius_m=30,
    )
    third, new3 = await world.ingest_observation(obs(400, 400), cluster_radius_m=30)
    assert new1 and not new2 and new3
    assert second.detection_id == first.detection_id
    assert len(second.observations) == 2
    assert second.sensor_agreement and second.max_confidence == 0.7
    assert second.last_observed_at == T0 + timedelta(seconds=5)
    x, _ = FRAME.to_local(second.position)
    assert x == pytest.approx(105, abs=0.5)
    assert third.detection_id != first.detection_id
    kinds = [e.type_name for e in events if e.type_name.startswith("Detection")]
    assert kinds == ["DetectionCreated", "DetectionUpdated", "DetectionCreated"]
    assert isinstance(events[-1], DetectionCreated)
    assert any(isinstance(e, DetectionUpdated) and e.observation_count == 2 for e in events)


async def test_detection_is_attached_to_its_zone(world: WorldStateService) -> None:
    detection, _ = await world.ingest_observation(obs(100, 400), cluster_radius_m=30)
    assert detection.zone_id == "A1"
    assert detection.detection_id in world.zone("A1").detection_ids
    outside, _ = await world.ingest_observation(obs(5000, 5000), cluster_radius_m=30)
    assert outside.zone_id is None


async def test_escalation_is_idempotent_and_resolution_is_recorded(
    world: WorldStateService, events: list[DomainEvent]
) -> None:
    detection, _ = await world.ingest_observation(obs(100, 100), cluster_radius_m=30)
    a = await world.escalate_candidate(detection.detection_id)
    b = await world.escalate_candidate(detection.detection_id)
    assert a.candidate_id == b.candidate_id
    assert sum(isinstance(e, CandidateEscalated) for e in events) == 1
    assert world.snapshot().open_candidates == (a,)
    resolved = world.resolve_candidate(a.candidate_id, confirmed=True, action_id="act-1")
    assert resolved.confirmed is True and resolved.resolved_by_action_id == "act-1"
    assert world.snapshot().open_candidates == ()


async def test_triage_bookkeeping(world: WorldStateService) -> None:
    detection, _ = await world.ingest_observation(obs(100, 100), cluster_radius_m=30)
    d = world.set_detection_triage(
        detection.detection_id, TriageDecision.INVESTIGATE, investigating_drone_id="drone-02"
    )
    assert d.triage is TriageDecision.INVESTIGATE and d.investigating_drone_id == "drone-02"
    d = world.set_detection_triage(detection.detection_id, TriageDecision.IGNORE)
    assert d.investigating_drone_id is None
