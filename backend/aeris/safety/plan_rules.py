"""Rules that validate a waypoint plan before it is sent to a drone."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from shapely.geometry import LineString, Point, Polygon

from aeris.config import SafetySettings
from aeris.domain.models import DroneState, WaypointPlan
from aeris.planning.geo_frame import LocalFrame
from aeris.safety.energy import RETURN_SAFETY_FACTOR
from aeris.world.snapshot import DroneView, WorldSnapshot


@dataclass(frozen=True)
class PlanViolation:
    rule: str
    reason: str


@dataclass(frozen=True)
class PlanContext:
    plan: WaypointPlan
    view: DroneView
    state: DroneState
    snapshot: WorldSnapshot
    settings: SafetySettings
    frame: LocalFrame
    fence: Polygon
    restricted: tuple[Polygon, ...]

    @property
    def path(self) -> LineString:
        """The full flight path: current position, then every waypoint."""
        points = [self.frame.to_local(self.state.position)]
        points.extend(self.frame.to_local(w.position) for w in self.plan.waypoints)
        if len(points) == 1:
            points.append(points[0])
        return LineString(points)

    @classmethod
    def build(
        cls,
        plan: WaypointPlan,
        view: DroneView,
        state: DroneState,
        snapshot: WorldSnapshot,
        settings: SafetySettings,
    ) -> PlanContext:
        area = snapshot.mission.search_area.polygon
        frame = LocalFrame.for_polygon(area)
        fence = frame.polygon_to_local(area).buffer(settings.geofence_buffer_m)
        restricted = tuple(frame.polygon_to_local(r) for r in snapshot.mission.restricted_regions)
        return cls(plan, view, state, snapshot, settings, frame, fence, restricted)


class PlanRule(Protocol):
    name: str

    def evaluate(self, ctx: PlanContext) -> PlanViolation | None: ...


class InvalidCoordinatesRule:
    name = "invalid_coordinates"

    def evaluate(self, ctx: PlanContext) -> PlanViolation | None:
        for i, wp in enumerate(ctx.plan.waypoints):
            p = wp.position
            if not all(math.isfinite(v) for v in (p.latitude, p.longitude, p.altitude_m)):
                return PlanViolation(self.name, f"waypoint {i} has non-finite coordinates")
        return None


class MaxAltitudeRule:
    name = "max_altitude"

    def evaluate(self, ctx: PlanContext) -> PlanViolation | None:
        limit = ctx.settings.max_altitude_m
        for i, wp in enumerate(ctx.plan.waypoints):
            if wp.position.altitude_m > limit:
                return PlanViolation(
                    self.name,
                    f"waypoint {i} at {wp.position.altitude_m:.0f} m exceeds {limit:.0f} m",
                )
        return None


class GeofenceRule:
    name = "geofence"

    def evaluate(self, ctx: PlanContext) -> PlanViolation | None:
        for i, wp in enumerate(ctx.plan.waypoints):
            if not ctx.fence.covers(Point(ctx.frame.to_local(wp.position))):
                return PlanViolation(self.name, f"waypoint {i} lies outside the geofence")
        return None


class RestrictedRegionRule:
    name = "restricted_region"

    def evaluate(self, ctx: PlanContext) -> PlanViolation | None:
        if not ctx.restricted:
            return None
        for i, wp in enumerate(ctx.plan.waypoints):
            point = Point(ctx.frame.to_local(wp.position))
            if any(region.intersects(point) for region in ctx.restricted):
                return PlanViolation(self.name, f"waypoint {i} lies inside a restricted region")
        path = ctx.path
        if any(path.intersects(region) for region in ctx.restricted):
            return PlanViolation(self.name, "flight path crosses a restricted region")
        return None


class UnavailableDroneRule:
    name = "unavailable_drone"

    def evaluate(self, ctx: PlanContext) -> PlanViolation | None:
        if not ctx.view.is_available_for_assignment:
            status = ctx.state.status
            return PlanViolation(self.name, f"drone is {status} with link {ctx.state.link_state}")
        return None


class UnreachablePlanRule:
    """Energy to reach the plan, fly it, and return home must fit within battery minus margin."""

    name = "unreachable_plan"

    def evaluate(self, ctx: PlanContext) -> PlanViolation | None:
        cap = ctx.view.drone.capability
        if cap.cruise_speed_mps <= 0 or cap.nominal_endurance_s <= 0:
            return None
        first = ctx.plan.waypoints[0].position
        last = ctx.plan.waypoints[-1].position
        transit = ctx.state.position.distance_to(first)
        home = last.distance_to(ctx.snapshot.mission.base_position) * RETURN_SAFETY_FACTOR
        total_m = (transit + ctx.plan.length_m + home) * ctx.settings.plan_energy_safety_factor
        needed = 100.0 * (total_m / cap.cruise_speed_mps) / cap.nominal_endurance_s
        available = ctx.state.battery_percent - ctx.settings.min_return_battery_percent
        if needed > available:
            reason = f"plan needs ~{needed:.0f}% battery, {available:.0f}% above the return floor"
            return PlanViolation(self.name, reason)
        return None


def default_plan_rules() -> list[PlanRule]:
    return [
        InvalidCoordinatesRule(),
        UnavailableDroneRule(),
        MaxAltitudeRule(),
        GeofenceRule(),
        RestrictedRegionRule(),
        UnreachablePlanRule(),
    ]
