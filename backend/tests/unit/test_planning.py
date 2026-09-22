from datetime import UTC, datetime

import pytest
from shapely.geometry import LineString, Point

from aeris.domain import (
    Drone,
    DroneState,
    DroneStatus,
    GeoPoint,
    GeoPolygon,
    LinkState,
    SearchZone,
    TelemetryFrame,
    ZoneStatus,
)
from aeris.planning import (
    AssignmentCandidate,
    BoustrophedonPlanner,
    CoverageRequest,
    GreedyAssignmentStrategy,
    GridPartitioner,
    LocalFrame,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
ORIGIN = GeoPoint(latitude=47.0, longitude=8.0)


def rect(width_m: float, height_m: float, origin: GeoPoint = ORIGIN) -> GeoPolygon:
    frame = LocalFrame(origin)
    return GeoPolygon(
        vertices=(
            frame.to_geo(0, 0),
            frame.to_geo(width_m, 0),
            frame.to_geo(width_m, height_m),
            frame.to_geo(0, height_m),
        )
    )


# ----------------------------------------------------------------- local frame


def test_local_frame_round_trip_and_scale() -> None:
    frame = LocalFrame(ORIGIN)
    p = frame.to_geo(500, -300, 60)
    x, y = frame.to_local(p)
    assert x == pytest.approx(500, abs=1e-6)
    assert y == pytest.approx(-300, abs=1e-6)
    assert p.altitude_m == 60
    assert ORIGIN.distance_to(frame.to_geo(1000, 0)) == pytest.approx(1000, rel=1e-4)


# ----------------------------------------------------------------- partition


def test_grid_partition_covers_area_with_expected_cells() -> None:
    area = rect(1000, 1000)
    zones = GridPartitioner(cell_size_m=250).partition(area)
    assert len(zones) == 16
    assert zones[0].zone_id == "A1"
    assert zones[-1].zone_id == "D4"
    assert all(z.status is ZoneStatus.UNSEARCHED for z in zones)
    # row A is the northern row
    assert zones[0].polygon.centroid.latitude > zones[-1].polygon.centroid.latitude
    frame = LocalFrame.for_polygon(area)
    total = sum(frame.polygon_to_local(z.polygon).area for z in zones)
    assert total == pytest.approx(1_000_000, rel=1e-3)


def test_grid_partition_clips_to_polygon_and_drops_slivers() -> None:
    frame = LocalFrame(ORIGIN)
    triangle = GeoPolygon(
        vertices=(frame.to_geo(0, 0), frame.to_geo(1000, 0), frame.to_geo(0, 1000))
    )
    zones = GridPartitioner(cell_size_m=250, min_area_fraction=0.05).partition(triangle)
    assert 0 < len(zones) < 16
    total = sum(frame.polygon_to_local(z.polygon).area for z in zones)
    assert total == pytest.approx(500_000, rel=1e-2)


def test_grid_partition_rejects_bad_config() -> None:
    with pytest.raises(ValueError, match="cell_size_m"):
        GridPartitioner(cell_size_m=0)


# ----------------------------------------------------------------- coverage


def request(width: float = 200, height: float = 100, **overrides: object) -> CoverageRequest:
    params: dict[str, object] = {
        "drone_id": "d",
        "zone_id": "A1",
        "polygon": rect(width, height),
        "altitude_m": 50,
        "footprint_width_m": 40,
        "overlap_fraction": 0.0,
    }
    params.update(overrides)
    return CoverageRequest(**params)  # type: ignore[arg-type]


def plan(width: float = 200, height: float = 100, **overrides: object):  # type: ignore[no-untyped-def]
    return BoustrophedonPlanner().plan(request(width, height, **overrides))


def test_boustrophedon_sweeps_full_width_and_alternates_direction() -> None:
    p = plan(width=200, height=100)
    frame = LocalFrame.for_polygon(rect(200, 100))
    xy = [frame.to_local(w.position) for w in p.waypoints]
    # 100 m tall, 40 m spacing → tracks at y = 20, 60, 100 → 3 tracks, 6 points
    assert len(xy) == 6
    assert xy[0][0] < xy[1][0]  # first track west→east
    assert xy[2][0] > xy[3][0]  # second track east→west
    assert xy[4][0] < xy[5][0]  # third track west→east again
    assert xy[0][1] < xy[2][1] < xy[4][1]  # rows ordered south to north
    assert all(w.position.altitude_m == 50 for w in p.waypoints)
    assert p.length_m == pytest.approx(3 * 200 + 2 * 40, rel=1e-3)


def test_overlap_tightens_spacing() -> None:
    loose = plan(200, 200, overlap_fraction=0.0)
    tight = plan(200, 200, overlap_fraction=0.5)
    assert len(tight.waypoints) > len(loose.waypoints)


def test_resume_from_fraction_cuts_path_by_length() -> None:
    full = plan(width=200, height=100)
    half = plan(width=200, height=100, start_fraction=0.5)
    assert half.start_fraction == 0.5
    assert half.length_m == pytest.approx(full.length_m / 2, rel=1e-3)
    assert half.waypoints[-1] == full.waypoints[-1]
    done = plan(width=200, height=100, start_fraction=1.0)
    assert len(done.waypoints) == 1


def test_all_waypoints_lie_within_zone() -> None:
    polygon = rect(300, 170)
    p = plan(300, 170, overlap_fraction=0.2)
    frame = LocalFrame.for_polygon(polygon)
    local = frame.polygon_to_local(polygon).buffer(0.01)
    for w in p.waypoints:
        assert local.contains(Point(frame.to_local(w.position)))
    path = LineString([frame.to_local(w.position) for w in p.waypoints])
    assert local.contains(path)


def test_coverage_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError, match="overlap_fraction"):
        request(overlap_fraction=1.0)
    with pytest.raises(ValueError, match="footprint_width_m"):
        request(footprint_width_m=0)


# ----------------------------------------------------------------- assignment


def candidate(drone_id: str, position: GeoPoint, battery: float = 90) -> AssignmentCandidate:
    frame_state = TelemetryFrame(
        drone_id=drone_id,
        timestamp=T0,
        position=position,
        battery_percent=battery,
        estimated_remaining_s=1000,
    )
    return AssignmentCandidate(
        drone=Drone(drone_id=drone_id, name=drone_id), state=DroneState.from_telemetry(frame_state)
    )


def test_greedy_prefers_nearby_zones_and_assigns_each_drone_once() -> None:
    zones = GridPartitioner(cell_size_m=500).partition(rect(1000, 1000))
    assert [z.zone_id for z in zones] == ["A1", "A2", "B1", "B2"]
    # place drone-01 at A1 (north-west) and drone-02 at B2 (south-east)
    c1 = candidate("drone-01", zones[0].polygon.centroid)
    c2 = candidate("drone-02", zones[3].polygon.centroid)
    proposals = GreedyAssignmentStrategy().assign([c1, c2], zones, base_position=ORIGIN)
    by_drone = {p.drone_id: p.zone_id for p in proposals}
    assert by_drone == {"drone-01": "A1", "drone-02": "B2"}
    assert len({p.zone_id for p in proposals}) == 2


def test_greedy_skips_unavailable_drones_and_zones_not_needing_work() -> None:
    zones = GridPartitioner(cell_size_m=500).partition(rect(1000, 1000))
    zones[0] = zones[0].model_copy(update={"status": ZoneStatus.COMPLETE, "coverage": 1.0})
    zones[1] = zones[1].model_copy(
        update={"status": ZoneStatus.SEARCHING, "assigned_drone_id": "x"}
    )
    busy = candidate("busy", ORIGIN)
    busy = AssignmentCandidate(
        busy.drone, busy.state.model_copy(update={"status": DroneStatus.RETURNING})
    )
    lost = candidate("lost", ORIGIN)
    lost = AssignmentCandidate(
        lost.drone, lost.state.model_copy(update={"link_state": LinkState.LOST})
    )
    ok = candidate("ok", ORIGIN)
    proposals = GreedyAssignmentStrategy().assign([busy, lost, ok], zones, base_position=ORIGIN)
    assert len(proposals) == 1
    assert proposals[0].drone_id == "ok"
    assert proposals[0].zone_id in {"B1", "B2"}


def test_greedy_priority_can_outweigh_distance() -> None:
    zones = GridPartitioner(cell_size_m=500).partition(rect(1000, 1000))
    far = zones[3].model_copy(update={"priority": 1.0})
    near = zones[0].model_copy(update={"priority": 0.0})
    c = candidate("d", near.polygon.centroid)
    proposals = GreedyAssignmentStrategy().assign([c], [near, far], base_position=ORIGIN)
    assert proposals[0].zone_id == far.zone_id


def test_partial_zone_is_reassignable() -> None:
    zone = SearchZone(
        zone_id="B7", polygon=rect(250, 250), coverage=0.58, status=ZoneStatus.PARTIAL
    )
    c = candidate("drone-01", ORIGIN)
    proposals = GreedyAssignmentStrategy().assign([c], [zone], base_position=ORIGIN)
    assert proposals[0].zone_id == "B7"
