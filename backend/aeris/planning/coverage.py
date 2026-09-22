"""Coverage path generation.

``BoustrophedonPlanner`` produces a lawnmower sweep over a polygon. Track spacing is derived
from camera footprint width and desired overlap. The planner can resume from a fraction of
the path already flown so a handed-off zone does not get re-searched from the start.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.geometry.base import BaseGeometry

from aeris.domain.enums import AssignmentTask
from aeris.domain.geo import GeoPolygon
from aeris.domain.models import Waypoint, WaypointPlan
from aeris.planning.geo_frame import LocalFrame


@dataclass(frozen=True)
class CoverageRequest:
    """Everything a coverage planner needs to produce a plan for one drone over one zone."""

    drone_id: str
    zone_id: str
    polygon: GeoPolygon
    altitude_m: float
    footprint_width_m: float
    overlap_fraction: float
    start_fraction: float = 0.0
    speed_mps: float | None = None

    def __post_init__(self) -> None:
        if self.footprint_width_m <= 0:
            msg = "footprint_width_m must be positive"
            raise ValueError(msg)
        if not 0 <= self.overlap_fraction < 1:
            msg = "overlap_fraction must be in [0, 1)"
            raise ValueError(msg)
        if not 0 <= self.start_fraction <= 1:
            msg = "start_fraction must be in [0, 1]"
            raise ValueError(msg)
        if self.altitude_m <= 0:
            msg = "altitude_m must be positive"
            raise ValueError(msg)


class CoveragePlanner(Protocol):
    def plan(self, request: CoverageRequest) -> WaypointPlan: ...


class BoustrophedonPlanner:
    """Deterministic east-west lawnmower sweep, rows ordered south to north."""

    def plan(self, request: CoverageRequest) -> WaypointPlan:
        frame = LocalFrame.for_polygon(request.polygon)
        local = frame.polygon_to_local(request.polygon)
        spacing = request.footprint_width_m * (1.0 - request.overlap_fraction)
        points = _sweep(local, spacing)
        points = _resume_from(points, request.start_fraction)
        waypoints = tuple(
            Waypoint(position=frame.to_geo(x, y, request.altitude_m), speed_mps=request.speed_mps)
            for x, y in points
        )
        return WaypointPlan(
            drone_id=request.drone_id,
            zone_id=request.zone_id,
            task=AssignmentTask.SEARCH,
            waypoints=waypoints,
            start_fraction=request.start_fraction,
        )


def _sweep(polygon: Polygon, spacing: float) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = polygon.bounds
    # First track sits half a footprint inside the southern edge so the sensor sees the edge.
    y = min_y + spacing / 2
    points: list[tuple[float, float]] = []
    left_to_right = True
    while y <= max_y + 1e-9:
        segment = _longest_segment(
            LineString([(min_x - 1, y), (max_x + 1, y)]).intersection(polygon)
        )
        if segment is not None:
            (x0, _), (x1, _) = segment.coords[0], segment.coords[-1]
            if x0 > x1:
                x0, x1 = x1, x0
            row = [(x0, y), (x1, y)] if left_to_right else [(x1, y), (x0, y)]
            points.extend(row)
            left_to_right = not left_to_right
        y += spacing
    if not points:
        centroid = polygon.centroid
        points = [(centroid.x, centroid.y)]
    return points


def _longest_segment(geometry: BaseGeometry) -> LineString | None:
    if geometry.is_empty:
        return None
    if isinstance(geometry, LineString):
        return geometry
    if isinstance(geometry, MultiLineString):
        return max(geometry.geoms, key=lambda g: g.length)
    return None


def _resume_from(points: list[tuple[float, float]], fraction: float) -> list[tuple[float, float]]:
    """Drop the first ``fraction`` of the path by length, starting exactly at the cut point."""
    if fraction <= 0 or len(points) < 2:
        return points
    line = LineString(points)
    if fraction >= 1:
        last = points[-1]
        return [last]
    cut_distance = line.length * fraction
    cut = line.interpolate(cut_distance)
    travelled = 0.0
    for i in range(len(points) - 1):
        seg = LineString([points[i], points[i + 1]])
        if travelled + seg.length >= cut_distance:
            return [(cut.x, cut.y), *points[i + 1 :]]
        travelled += seg.length
    return [points[-1]]
