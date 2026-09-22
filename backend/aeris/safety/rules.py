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
        MissionNotActiveRule(),
    ]
