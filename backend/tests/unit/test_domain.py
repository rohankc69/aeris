from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from aeris.domain import (
    DetectionObservation,
    DetectionSource,
    DroneState,
    DroneStatus,
    GeoPoint,
    GeoPolygon,
    LinkState,
    Mission,
    MissionStatus,
    SearchArea,
    TelemetryFrame,
)
from aeris.domain.models import Detection

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def square(size_deg: float = 0.01) -> GeoPolygon:
    return GeoPolygon(
        vertices=(
            GeoPoint(latitude=0, longitude=0),
            GeoPoint(latitude=0, longitude=size_deg),
            GeoPoint(latitude=size_deg, longitude=size_deg),
            GeoPoint(latitude=size_deg, longitude=0),
        )
    )


def mission() -> Mission:
    return Mission(
        name="test",
        search_area=SearchArea(polygon=square()),
        base_position=GeoPoint(latitude=0, longitude=0),
        created_at=T0,
    )


def test_geopoint_rejects_nan() -> None:
    with pytest.raises(ValidationError):
        GeoPoint(latitude=float("nan"), longitude=0)


def test_geopoint_distance_one_degree_latitude() -> None:
    a = GeoPoint(latitude=0, longitude=0)
    b = GeoPoint(latitude=1, longitude=0)
    assert a.distance_to(b) == pytest.approx(111_195, rel=1e-3)


def test_polygon_drops_closing_vertex_and_reports_bbox() -> None:
    p = GeoPolygon(vertices=(*square().vertices, GeoPoint(latitude=0, longitude=0)))
    assert len(p.vertices) == 4
    assert p.bounding_box.max_latitude == 0.01
    assert p.coordinates()[0] == p.coordinates()[-1]


def test_polygon_needs_three_vertices() -> None:
    with pytest.raises(ValidationError):
        GeoPolygon(vertices=(GeoPoint(latitude=0, longitude=0), GeoPoint(latitude=1, longitude=1)))


def test_drone_state_from_telemetry_preserves_mission_fields() -> None:
    frame = TelemetryFrame(
        drone_id="drone-01",
        timestamp=T0,
        position=GeoPoint(latitude=0, longitude=0, altitude_m=50),
        battery_percent=80,
        estimated_remaining_s=1000,
    )
    previous = DroneState.from_telemetry(frame).model_copy(
        update={
            "status": DroneStatus.SEARCHING,
            "assigned_zone_id": "A1",
            "coverage_completed": 0.4,
        }
    )
    later = frame.model_copy(update={"timestamp": T0 + timedelta(seconds=1), "battery_percent": 79})
    state = DroneState.from_telemetry(later, previous)
    assert state.battery_percent == 79
    assert state.status is DroneStatus.SEARCHING
    assert state.assigned_zone_id == "A1"
    assert state.coverage_completed == 0.4


def test_drone_availability_requires_connected_link() -> None:
    frame = TelemetryFrame(
        drone_id="d",
        timestamp=T0,
        position=GeoPoint(latitude=0, longitude=0),
        battery_percent=50,
        estimated_remaining_s=10,
    )
    state = DroneState.from_telemetry(frame)
    assert state.is_available_for_assignment
    assert not state.model_copy(
        update={"link_state": LinkState.DEGRADED}
    ).is_available_for_assignment
    assert not state.model_copy(
        update={"status": DroneStatus.RETURNING}
    ).is_available_for_assignment


def test_mission_transitions() -> None:
    m = mission()
    active = m.transition(MissionStatus.ACTIVE, T0 + timedelta(seconds=1))
    assert active.started_at == T0 + timedelta(seconds=1)
    located = active.transition(MissionStatus.PERSON_LOCATED, T0 + timedelta(seconds=2))
    assert located.completed_at is not None
    with pytest.raises(ValueError, match="illegal mission transition"):
        located.transition(MissionStatus.ACTIVE, T0)
    with pytest.raises(ValueError, match="illegal mission transition"):
        m.transition(MissionStatus.COMPLETED, T0)


def test_mission_status_terminal_flags() -> None:
    assert MissionStatus.PERSON_LOCATED.is_terminal
    assert not MissionStatus.PAUSED.is_terminal


def test_detection_sensor_agreement_ignores_operator_source() -> None:
    def obs(source: DetectionSource, conf: float) -> DetectionObservation:
        return DetectionObservation(
            drone_id="d",
            timestamp=T0,
            source=source,
            confidence=conf,
            position=GeoPoint(latitude=0, longitude=0),
        )

    base = {
        "mission_id": "m",
        "zone_id": "A1",
        "position": GeoPoint(latitude=0, longitude=0),
        "first_observed_at": T0,
        "last_observed_at": T0,
    }
    single = Detection(**base, observations=(obs(DetectionSource.THERMAL, 0.4),))
    assert not single.sensor_agreement
    assert single.max_confidence == 0.4
    fused = Detection(
        **base, observations=(obs(DetectionSource.THERMAL, 0.4), obs(DetectionSource.VISUAL, 0.7))
    )
    assert fused.sensor_agreement
    assert fused.max_confidence == 0.7
    op = Detection(
        **base, observations=(obs(DetectionSource.THERMAL, 0.4), obs(DetectionSource.OPERATOR, 1))
    )
    assert not op.sensor_agreement
