"""Infer coverage progress from telemetry.

Adapters only report position. Coverage is derived by projecting the drone onto its planned
path and taking the fraction of path length behind it. Progress is monotonic so brief
deviations do not un-cover ground. This works identically for simulated and PX4 fleets.
"""

from __future__ import annotations

from shapely.geometry import LineString, Point

from aeris.domain.geo import GeoPoint
from aeris.domain.models import WaypointPlan
from aeris.planning.geo_frame import LocalFrame


class PlanProgressTracker:
    def __init__(self, plan: WaypointPlan, *, on_track_tolerance_m: float = 25.0) -> None:
        self.plan = plan
        search = plan.search_waypoints
        self._frame = LocalFrame(search[0].position)
        points = [self._frame.to_local(w.position) for w in search]
        self._line = LineString(points) if len(points) > 1 else None
        self._single = points[0]
        self._tolerance = on_track_tolerance_m
        self._best = plan.start_fraction

    @property
    def fraction(self) -> float:
        return self._best

    @property
    def complete(self) -> bool:
        return self._best >= 1.0 - 1e-6

    def update(self, position: GeoPoint) -> float:
        """Return zone coverage fraction after observing ``position``."""
        x, y = self._frame.to_local(position)
        point = Point(x, y)
        if self._line is None or self._line.length == 0:
            reached = point.distance(Point(self._single)) <= self._tolerance
            if reached:
                self._best = 1.0
            return self._best
        if point.distance(self._line) > self._tolerance:
            return self._best
        along = self._line.project(point) / self._line.length
        remaining_start = self.plan.start_fraction
        coverage = remaining_start + (1.0 - remaining_start) * along
        self._best = max(self._best, min(1.0, coverage))
        return self._best
