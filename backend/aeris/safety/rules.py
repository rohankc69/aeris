"""Individual safety rules. Each is small, deterministic, and independently testable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aeris.config import SafetySettings
from aeris.domain.enums import Disposition, DroneStatus, LinkState, MissionStatus
from aeris.domain.models import DroneState
from aeris.safety.energy import estimate_return_battery_percent
from aeris.world.snapshot import DroneView, WorldSnapshot


@dataclass(frozen=True)
class Violation:
    rule: str
    reason: str
    required_action: Disposition


@dataclass(frozen=True)
class RuleContext:
    view: DroneView
    state: DroneState
    snapshot: WorldSnapshot
    settings: SafetySettings

    @property
    def return_battery_percent(self) -> float:
        return estimate_return_battery_percent(
            distance_to_base_m=self.state.position.distance_to(self.snapshot.mission.base_position),
            cruise_speed_mps=self.view.drone.capability.cruise_speed_mps,
            nominal_endurance_s=self.view.drone.capability.nominal_endurance_s,
        )


class SafetyRule(Protocol):
    name: str

    def evaluate(self, ctx: RuleContext) -> Violation | None: ...


class CriticalBatteryRule:
    name = "critical_battery"

    def evaluate(self, ctx: RuleContext) -> Violation | None:
        limit = ctx.settings.critical_battery_percent
        if ctx.state.battery_percent <= limit:
            reason = f"battery {ctx.state.battery_percent:.0f}% <= critical {limit:.0f}%"
            return Violation(self.name, reason, Disposition.RETURN_TO_BASE)
        return None


class MandatoryReturnBatteryRule:
    name = "mandatory_return_battery"

    def evaluate(self, ctx: RuleContext) -> Violation | None:
        limit = ctx.settings.min_return_battery_percent
        if ctx.state.battery_percent <= limit:
            reason = f"battery {ctx.state.battery_percent:.0f}% <= mandatory return {limit:.0f}%"
            return Violation(self.name, reason, Disposition.RETURN_TO_BASE)
        return None


class ReturnMarginRule:
    name = "return_margin"

    def evaluate(self, ctx: RuleContext) -> Violation | None:
        needed = ctx.return_battery_percent
        if ctx.state.battery_percent <= needed + ctx.settings.return_battery_margin_percent:
            battery = ctx.state.battery_percent
            reason = f"battery {battery:.0f}% within margin of return cost {needed:.0f}%"
            return Violation(self.name, reason, Disposition.RETURN_TO_BASE)
        return None


class LinkLostRule:
    """Preconfigured lost-link behaviour. Never delegated to a model."""

    name = "link_lost"

    def evaluate(self, ctx: RuleContext) -> Violation | None:
        if ctx.state.link_state is LinkState.LOST:
            return Violation(
                self.name, "telemetry timeout: lost-link behaviour", Disposition.RETURN_TO_BASE
            )
        return None


class MaxAltitudeRule:
    name = "max_altitude"

    def evaluate(self, ctx: RuleContext) -> Violation | None:
        limit = ctx.settings.max_altitude_m
        if ctx.state.position.altitude_m > limit:
            reason = f"altitude {ctx.state.position.altitude_m:.0f} m exceeds {limit:.0f} m"
            return Violation(self.name, reason, Disposition.RETURN_TO_BASE)
        return None


class MinimumSeparationRule:
    """Two airborne searching drones too close: the lexically later id holds until clear.

    Traffic inside ``separation_exempt_radius_m`` of base is exempt, as at any shared launch
    and recovery site.
    """

    name = "minimum_separation"

    def evaluate(self, ctx: RuleContext) -> Violation | None:
        me = ctx.state
        if me.status is not DroneStatus.SEARCHING or me.position.altitude_m <= 0:
            return None
        base = ctx.snapshot.mission.base_position
        if me.position.distance_to(base) <= ctx.settings.separation_exempt_radius_m:
            return None
        for other in ctx.snapshot.drones:
            o = other.state
            if o is None or other.drone.drone_id <= ctx.view.drone.drone_id:
                continue
            if (
                o.status not in {DroneStatus.SEARCHING, DroneStatus.HOLDING}
                or o.position.altitude_m <= 0
            ):
                continue
            if o.link_state is not LinkState.CONNECTED:
                continue
            if o.position.distance_to(base) <= ctx.settings.separation_exempt_radius_m:
                continue
            distance = me.position.distance_to(o.position)
            if distance < ctx.settings.min_separation_m:
                limit = ctx.settings.min_separation_m
                reason = f"{distance:.0f} m from {other.drone.drone_id}, below {limit:.0f} m"
                return Violation(self.name, reason, Disposition.HOLD)
        return None


class MissionNotActiveRule:
    name = "mission_not_active"

    def evaluate(self, ctx: RuleContext) -> Violation | None:
        status = ctx.snapshot.mission.status
        if status is not MissionStatus.ACTIVE and ctx.state.status is DroneStatus.SEARCHING:
            return Violation(self.name, f"mission is {status}", Disposition.HOLD)
        return None


def default_rules() -> list[SafetyRule]:
    """Ordered from most to least severe; the first violation wins."""
    return [
        CriticalBatteryRule(),
        MandatoryReturnBatteryRule(),
        ReturnMarginRule(),
        LinkLostRule(),
        MaxAltitudeRule(),
        MissionNotActiveRule(),
        MinimumSeparationRule(),
    ]
